"""Language-agnostic detection of comments on a single line.

Without parsing each language this is a heuristic tuned for precision: a
comment opener counts only at the start of the line or after whitespace, and
markup files (Markdown, HTML, XML) only have `<!--` comments, so a Markdown
heading `# TODO list` is not a comment.
"""

from __future__ import annotations

import re
import tokenize
from collections.abc import Iterator
from io import StringIO
from pathlib import PurePosixPath

MARKUP_SUFFIXES = frozenset({".md", ".markdown", ".mdx", ".html", ".htm", ".xml", ".svg"})

_CODE = re.compile(r"(?:^|(?<=\s))(?:<!--|//|/\*|--|\{#|#|;|%)|^\s*\*(?!/)")
_MARKUP = re.compile(r"(?:^|(?<=\s))<!--")


def comment_text(line: str, path: str) -> str | None:
    """Text after the first comment opener on the line, or None if there is none."""
    suffix = PurePosixPath(path).suffix.lower()
    rx = _MARKUP if suffix in MARKUP_SUFFIXES else _CODE
    m = rx.search(line)
    return None if m is None else line[m.end() :]


def iter_comments(source: str, path: str) -> Iterator[tuple[int, str, str]]:
    """Yield the line number, original line and comment body."""
    if PurePosixPath(path).suffix.lower() in {".py", ".pyi", ".pyw"}:
        reader = StringIO(source, newline=None).readline
        try:
            for token in tokenize.generate_tokens(reader):
                if token.type == tokenize.COMMENT:
                    yield token.start[0], token.line, token.string[1:]
        except (tokenize.TokenError, SyntaxError):
            # A work in progress can be incomplete. Only use recognized comments.
            return
    else:
        for number, line in enumerate(source.splitlines(), start=1):
            body = comment_text(line, path)
            if body is not None:
                yield number, line, body
