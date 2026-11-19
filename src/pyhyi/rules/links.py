"""broken-doc-link: relative links in Markdown point to files that exist in the repo."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkDestination
from markdown_it.tree import SyntaxTreeNode

from pyhyi import git
from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.rules import Rule

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")

_BLOCKS = MarkdownIt("commonmark").disable("inline")
_INLINE = re.compile(r"\]\(\s*")
_REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(?:<([^>\n]+)>|(\S+))")
_HTML = re.compile(r"""<(?:a|img|source)\b[^>]*?\s(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True)
class Destination:
    line: int
    start: int
    end: int
    target: str


def _candidates(text: str) -> Iterator[Destination]:
    """Source spans of candidate destinations, excluding fenced and inline code."""
    code_lines = {
        i
        for block in _BLOCKS.parse(text)
        if block.type in {"code_block", "fence"} and block.map
        for i in range(*block.map)
    }
    offset = 0
    for index, raw in enumerate(text.splitlines(keepends=True), 1):
        base = offset
        offset += len(raw)
        if index - 1 in code_lines:
            continue
        line = _CODE_SPAN.sub(lambda m: " " * len(m[0]), raw.rstrip("\r\n"))
        for opener in _INLINE.finditer(line):
            start = opener.end()
            dest = parseLinkDestination(line, start, len(line))
            if dest.ok and dest.str:
                angle = int(line[start] == "<")
                yield Destination(index, base + start + angle, base + dest.pos - angle, dest.str)
        for rx in (_REFERENCE, _HTML):
            for match in rx.finditer(line):
                group = match.lastindex
                if group:
                    yield Destination(
                        index, base + match.start(group), base + match.end(group), match[group]
                    )


class _HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"a", "img", "source"}:
            self.urls.update(v for k, v in attrs if k in {"src", "href"} and v)


def destinations(text: str) -> Iterator[Destination]:
    """Yield source spans that CommonMark actually interprets as destinations."""
    candidates = sorted(_candidates(text), key=lambda dest: dest.start)
    if not candidates:
        return
    # Let CommonMark verify source positions in one parse. Lookalikes in prose,
    # comments, titles and multiline code spans never become URL attributes.
    prefix = "pyhyi-probe-"
    while prefix in text:
        prefix += "x"
    parts = []
    last = 0
    probes = {}
    for dest in candidates:
        if dest.start < last:
            continue
        marker = f"{prefix}{dest.start}"
        probes[marker] = dest
        parts.extend((text[last : dest.start], marker))
        last = dest.end
    parts.append(text[last:])
    env: dict = {}
    tree = SyntaxTreeNode(MarkdownIt("commonmark").parse("".join(parts), env))
    found = {ref["href"] for ref in env.get("references", {}).values()}
    for node in tree.walk():
        if node.type in {"link", "image"}:
            attr = "href" if node.type == "link" else "src"
            found.add(str(node.attrs.get(attr, "")))
        if node.type in {"html_inline", "html_block"} and prefix in node.content:
            parser = _HTMLTargets()
            parser.feed(node.content)
            found.update(parser.urls)
    for marker, dest in probes.items():
        if marker in found:
            yield dest


def link_targets(text: str) -> Iterator[tuple[int, str]]:
    for dest in destinations(text):
        yield dest.line, dest.target


def local_path(target: str, source: str) -> str | None:
    """Repository-relative path a link points to, or None if it is not a local link.

    External URLs, pure anchors, templated links and paths leaving the
    repository are not checked.
    """
    if _SCHEME.match(target) or target.startswith(("//", "#")) or "{{" in target or "{%" in target:
        return None
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not target:
        return None
    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if resolved == ".." or resolved.startswith("../"):
        return None
    return resolved


def _relative(path: str, source: str) -> str:
    return posixpath.relpath(path, posixpath.dirname(source) or ".")


class LinkIndex:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.tracked = {"."}
        self.folded: dict[str, set[str]] = defaultdict(set)
        for file in git.list_files(ctx.root, tracked_only=True):
            path = file
            while path and path not in self.tracked:
                self.tracked.add(path)
                self.folded[path.casefold()].add(path)
                path = posixpath.dirname(path)

    def replacement(self, resolved: str) -> str | None:
        candidates = self.folded.get(resolved.casefold(), set()).copy()
        moved = self.ctx.renames.get(resolved)
        if moved in self.tracked:
            candidates.add(moved)
        if len(candidates) == 1:
            return candidates.pop()
        return None


class BrokenDocLink(Rule):
    id = "broken-doc-link"
    summary = "Relative links in Markdown files must point to files that exist in the repository."

    def check(self, ctx: Context) -> Iterator[Finding]:
        index = LinkIndex(ctx)
        for path in ctx.files:
            if not path.lower().endswith(MARKDOWN_SUFFIXES):
                continue
            text = ctx.read_text(path)
            if text is None:
                continue
            for line, target in link_targets(text):
                resolved = local_path(target, path)
                if resolved is None or resolved in index.tracked:
                    continue
                message = f"Link target {target!r} does not exist"
                hint = None
                actual = index.replacement(resolved)
                if actual:
                    relative = _relative(actual, path)
                    if actual.casefold() == resolved.casefold():
                        hint = f"Letter case differs: did you mean {relative!r}?"
                    else:
                        hint = f"git history shows it was renamed: link to {relative!r}."
                elif (ctx.root / resolved).exists():
                    message = f"Link target {target!r} exists locally but is not tracked by git"
                    hint = (
                        "Everyone else gets a broken link: commit the file or stop linking to it."
                    )
                yield self.finding(message, path=path, line=line, key=target, hint=hint)
