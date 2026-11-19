"""Positive evidence of local file use from Python, JS/TS, docs and manifests."""

from __future__ import annotations

import ast
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import PurePosixPath

from markdown_it import MarkdownIt

from pyhyi.context import Context
from pyhyi.rules.links import MARKDOWN_SUFFIXES, link_targets, local_path

JS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
SOURCE_SUFFIXES = (".py", *JS_SUFFIXES)
_JS_STRING = re.compile(r"""(?:"([^"\n]+)"|'([^'\n]+)')""")
_PATH_WORD = re.compile(r"[\w@./-]+")
_MARKDOWN = MarkdownIt("commonmark")


def _documented_files(text: str, source: str, files: set[str]) -> Iterator[str]:
    """Resolve literal paths in Markdown code spans against the document or repository root."""
    if "`" not in text:
        return
    for block in _MARKDOWN.parse(text):
        for token in block.children or ():
            if token.type != "code_inline":
                continue
            path = token.content
            if not path or path.startswith("/") or "\n" in path:
                continue
            relative = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
            candidates = {relative}
            if not path.startswith(("./", "../")):
                candidates.add(posixpath.normpath(path))
            matches = candidates & files
            if len(matches) == 1 and source not in matches:
                yield from matches


def _python_names(text: str) -> Iterator[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield module
            for alias in node.names:
                yield f"{module}.{alias.name}".strip(".")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def referenced_paths(ctx: Context) -> set[str]:
    """Ambiguous imports protect every matching local module from being flagged."""
    names: dict[str, set[str]] = defaultdict(set)
    for path in ctx.files:
        file = PurePosixPath(path)
        names[file.name].add(path)
        names[path].add(path)
        for parent in file.parents:
            if parent.name:
                names[parent.name].add(str(parent))
        if path.endswith(SOURCE_SUFFIXES):
            parts = list(file.with_suffix("").parts)
            if parts[-1] in {"__init__", "index"}:
                parts.pop()
            for start in range(len(parts)):
                tail = parts[start:]
                names[".".join(tail)].add(path)
                names["/".join(tail)].add(path)
    files = set(ctx.all_files)
    used: set[str] = set()
    for source in ctx.files:
        suffix = PurePosixPath(source).suffix.lower()
        if suffix not in (
            *SOURCE_SUFFIXES,
            *MARKDOWN_SUFFIXES,
            ".toml",
            ".json",
            ".yaml",
            ".yml",
            ".sh",
        ):
            continue
        text = ctx.read_text(source)
        if not text:
            continue
        if suffix in MARKDOWN_SUFFIXES:
            used.update(_documented_files(text, source, files))
            for _, target in link_targets(text):
                local = local_path(target, source)
                if local and local != source:
                    used.add(local)
            continue
        values: Iterator[str]
        if suffix == ".py":
            values = _python_names(text)
        elif suffix in JS_SUFFIXES:
            values = (m[1] or m[2] for m in _JS_STRING.finditer(text))
        else:
            values = (text,)
        for value in values:
            for match in _PATH_WORD.finditer(value):
                word = match[0].strip("/")
                # Protect literal paths, import specifiers and manifest entry points.
                keys = [word, posixpath.normpath(posixpath.join(posixpath.dirname(source), word))]
                if suffix in JS_SUFFIXES:
                    module = word.removesuffix(".js").removesuffix(".mjs").removesuffix(".cjs")
                    keys.extend((module.lstrip("./"), posixpath.basename(module)))
                elif suffix == ".py":
                    keys.append(posixpath.basename(word))
                for key in keys:
                    used.update(target for target in names.get(key, ()) if target != source)
    return used
