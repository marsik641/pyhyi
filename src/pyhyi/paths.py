"""Glob-style path patterns used by `exclude` and `per-path-ignores`."""

from __future__ import annotations

import re
from collections.abc import Iterable


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one pattern matching repository-relative POSIX paths.

    - `*` matches within one path segment, `?` one character,
      `**` any number of segments;
    - a pattern without `/` matches a file or directory name at any depth
      (`node_modules`, `*.log`);
    - a pattern containing `/` is anchored at the repository root
      (`docs/*.md`, `/build`);
    - matching a directory covers everything inside it.
    """
    body = pattern.strip().rstrip("/")
    anchored = "/" in body
    body = body.lstrip("/")
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + _translate(body) + r"(?:/.*)?\Z")


class PathMatcher:
    """Matches a path against any of several patterns."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = tuple(patterns)
        self._compiled = [compile_pattern(p) for p in self.patterns]

    def match(self, path: str) -> bool:
        return any(rx.match(path) for rx in self._compiled)
