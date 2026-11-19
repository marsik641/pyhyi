"""broken-doc-link: relative links in Markdown point to files that exist in the repo."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkDestination

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


def link_targets(text: str) -> Iterator[tuple[int, str]]:
    """(line number, raw target) of every link outside code blocks and code spans."""
    code_lines = {
        i
        for block in _BLOCKS.parse(text)
        if block.type in {"code_block", "fence"} and block.map
        for i in range(*block.map)
    }
    for index, line in enumerate(text.splitlines()):
        if index in code_lines:
            continue
        line = _CODE_SPAN.sub("", line)
        for opener in _INLINE.finditer(line):
            dest = parseLinkDestination(line, opener.end(), len(line))
            if dest.ok and dest.str:
                yield index + 1, dest.str
        for rx in (_REFERENCE, _HTML):
            for match in rx.finditer(line):
                target = next((g for g in match.groups() if g), "")
                if target:
                    yield index + 1, target


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


class BrokenDocLink(Rule):
    id = "broken-doc-link"
    summary = "Relative links in Markdown files must point to files that exist in the repository."

    def check(self, ctx: Context) -> Iterator[Finding]:
        tracked = {"."}
        for file in git.list_files(ctx.root, tracked_only=True):
            tracked.add(file)
            parent = posixpath.dirname(file)
            while parent and parent not in tracked:
                tracked.add(parent)
                parent = posixpath.dirname(parent)
        lowercase: dict[str, str] | None = None
        for path in ctx.files:
            if not path.lower().endswith(MARKDOWN_SUFFIXES):
                continue
            text = ctx.read_text(path)
            if text is None:
                continue
            for line, target in link_targets(text):
                resolved = local_path(target, path)
                if resolved is None or resolved in tracked:
                    continue
                if lowercase is None:
                    lowercase = {p.lower(): p for p in sorted(tracked)}
                message, hint = self._explain(ctx, tracked, lowercase, path, target, resolved)
                yield self.finding(message, path=path, line=line, key=target, hint=hint)

    @staticmethod
    def _explain(
        ctx: Context,
        tracked: set[str],
        lowercase: dict[str, str],
        source: str,
        target: str,
        resolved: str,
    ) -> tuple[str, str | None]:
        missing = f"Link target {target!r} does not exist"
        # Case first: on case-insensitive filesystems the wrong-case path "exists" locally.
        actual = lowercase.get(resolved.lower())
        if actual is not None:
            return missing, f"Letter case differs: did you mean {_relative(actual, source)!r}?"
        moved = ctx.renames.get(resolved)
        if moved in tracked:
            return (
                missing,
                f"git history shows it was renamed: link to {_relative(moved, source)!r}.",
            )
        if (ctx.root / resolved).exists():
            return (
                f"Link target {target!r} exists locally but is not tracked by git",
                "Everyone else gets a broken link: commit the file or stop linking to it.",
            )
        return missing, None
