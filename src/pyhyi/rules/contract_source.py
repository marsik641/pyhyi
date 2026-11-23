"""Source selection and lexical masking for cross-language contract observations."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePosixPath

from pyhyi.context import Context

_SKIP_DIRS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "fixtures",
        "vendor",
        "node_modules",
        "dist",
        "build",
        "migrations",
        "examples",
        "coverage",
        "third_party",
    }
)


def sources(ctx: Context, suffixes: tuple[str, ...]) -> Iterator[tuple[str, str]]:
    root = ctx.root.resolve()
    for path in ctx.files:
        if not path.lower().endswith(suffixes):
            continue
        file = PurePosixPath(path.lower())
        if (
            _SKIP_DIRS.intersection(file.parts)
            or file.name.startswith("test_")
            or file.stem.endswith(("_test", ".test", ".spec", ".min", ".d"))
        ):
            continue
        full = ctx.root / path
        try:
            if full.is_symlink() or not full.resolve().is_relative_to(root):
                continue
        except (OSError, RuntimeError):
            continue
        source = ctx.read_text(path, cache=file.suffix != ".json")
        if source:
            yield path, source


# Keep delimiters inside literals from being mistaken for code or comments.
# Regex literals are recognized in expression-start positions. Mask the entire
# template, including interpolation: partial evidence is worse than a missed pair.
_JS = re.compile(
    r"""//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|"""
    r"""'(?:\\[\s\S]|[^'\\\n])*(?:'|(?=\n)|\Z)|"""
    r""""(?:\\[\s\S]|[^"\\\n])*(?:"|(?=\n)|\Z)|"""
    r"""`(?:\\[\s\S]|[^`\\])*(?:`|\Z)|"""
    r"""(?P<regex>(?<=[=(:,!;?\[])\s*/(?![*/])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\[\n])+/[a-z]*)"""
)


def masked_js(text: str) -> str:
    """Blank quoted material and comments without moving the surrounding code."""
    return _JS.sub(lambda match: re.sub(r"[^\n]", " ", match[0]), text)
