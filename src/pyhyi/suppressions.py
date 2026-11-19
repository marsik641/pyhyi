"""Inline suppressions: `# pyhyi: ignore[rule-id, other-rule] reason`.

The marker is recognised only right after a comment opener (`#`, `//`, `/*`,
`<!--`, `--`, `;`, `%`, `{#`) that starts the line or follows whitespace, so
most mentions in prose and quoted code are not mistaken for suppressions.
Without parsing each language a string literal that looks exactly like a
comment cannot be told apart; exclude such fixtures via `per-path-ignores`.
A suppression without a reason is not applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MARKER = "pyhyi:"

_RX = re.compile(
    r"(?:^|\s)(?:#|//|/\*|<!--|--|;|%|\{#)\s*pyhyi:\s*ignore\[(?P<rules>[^\]]*)\](?P<reason>.*)"
)
_CLOSERS = ("-->", "*/", "#}")
# Separators allowed before the reason: colon, hyphen, en dash, em dash.
_LEADING = " \t:-\N{EN DASH}\N{EM DASH}"


@dataclass(frozen=True)
class Suppression:
    line: int
    rules: tuple[str, ...]
    reason: str


def parse_suppression(text: str, line: int) -> Suppression | None:
    """Parse one line of text; return None if it has no suppression marker."""
    if MARKER not in text:
        return None
    m = _RX.search(text)
    if m is None:
        return None
    rules = tuple(r.strip() for r in m["rules"].split(",") if r.strip())
    reason = m["reason"].strip()
    for closer in _CLOSERS:
        if reason.endswith(closer):
            reason = reason[: -len(closer)]
            break
    return Suppression(line=line, rules=rules, reason=reason.strip(_LEADING).strip())


def scan(text: str) -> dict[int, Suppression]:
    """All suppressions in a file, keyed by 1-based line number."""
    if MARKER not in text:
        return {}
    found = {}
    for number, line in enumerate(text.splitlines(), start=1):
        sup = parse_suppression(line, number)
        if sup is not None:
            found[number] = sup
    return found
