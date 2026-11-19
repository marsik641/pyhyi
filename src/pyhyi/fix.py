"""Apply unambiguous Markdown destination edits to the original UTF-8 bytes."""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from pyhyi import engine
from pyhyi.config import Config
from pyhyi.context import Context
from pyhyi.errors import PyhyiError
from pyhyi.rules.links import BrokenDocLink, Destination, LinkIndex, destinations, local_path


class _HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"a", "img", "source"}:
            self.urls.update(v for k, v in attrs if k in {"src", "href"} and v)


def _verified(text: str, candidates: list[Destination]) -> list[Destination]:
    # Let CommonMark verify source positions in one parse. Lookalikes in prose,
    # comments, titles and multiline code spans never become URL attributes.
    prefix = "pyhyi-probe-"
    while prefix in text:
        prefix += "x"
    parts = []
    last = 0
    probes = {}
    for dest in sorted(candidates, key=lambda d: d.start):
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
        if node.type in {"html_inline", "html_block"}:
            parser = _HTMLTargets()
            parser.feed(node.content)
            found.update(parser.urls)
    return [dest for marker, dest in probes.items() if marker in found]


def _replace_file(path: Path, original: bytes, replacement: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        scratch = Path(temp.name)
    try:
        scratch.write_bytes(replacement)
        shutil.copymode(path, scratch)
        if path.is_symlink() or path.read_bytes() != original:
            raise PyhyiError(f"file changed during fix: {path.name}")
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def fix_links(root: Path, config: Config, registry: engine.Registry, *, dry_run: bool) -> int:
    engine.validate_config(config, registry)
    if BrokenDocLink not in engine.select_rules(registry, config):
        return 0
    result = engine.run(root, config, registry, select=[BrokenDocLink.id])
    if result.errors:
        raise PyhyiError(result.errors[0].traceback)
    eligible: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for finding in result.findings:
        if finding.path and finding.line and finding.key:
            eligible[finding.path].add((finding.line, finding.key))
    index = LinkIndex(Context(root, config))
    count = 0
    for source, keys in eligible.items():
        path = root / source
        if path.resolve() != root.resolve() / source or path.is_symlink():
            continue
        original = path.read_bytes()
        text = original.decode("utf-8")
        candidates = [d for d in destinations(text) if (d.line, d.target) in keys]
        edits = []
        for dest in _verified(text, candidates):
            raw = text[dest.start : dest.end]
            resolved = local_path(dest.target, source)
            if raw != dest.target or resolved is None or resolved in index.tracked:
                continue
            actual = index.replacement(resolved)
            if actual is None:
                continue
            new_path = (
                "/" + actual
                if raw.startswith("/")
                else posixpath.relpath(actual, posixpath.dirname(source) or ".")
            )
            suffix = raw[len(re.split("[?#]", raw, maxsplit=1)[0]) :]
            replacement = quote(new_path, safe="/-._~") + suffix
            edits.append((dest, replacement))
            print(f"{source}:{dest.line}: {raw} -> {replacement}")
        if edits and not dry_run:
            for dest, replacement in reversed(edits):
                text = text[: dest.start] + replacement + text[dest.end :]
            _replace_file(path, original, text.encode("utf-8"))
        count += len(edits)
    return count
