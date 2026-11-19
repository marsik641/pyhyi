"""Rendering of check results as text (humans) or JSON (agents, CI)."""

from __future__ import annotations

import json

from pyhyi import __version__
from pyhyi.engine import Result
from pyhyi.findings import Severity

JSON_FORMAT_VERSION = 1


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def summary(result: Result) -> str:
    advice = sum(f.severity == Severity.ADVICE for f in result.findings)
    n = len(result.findings) - advice
    head = "No issues found." if n == 0 else f"Found {_plural(n, 'issue')}."
    extras = []
    if advice:
        extras.append(f"{advice} for review")
    if result.baselined:
        extras.append(f"{len(result.baselined)} baselined")
    if result.suppressed:
        extras.append(f"{len(result.suppressed)} suppressed")
    return f"{head} ({', '.join(extras)})" if extras else head


def render_text(result: Result) -> str:
    lines = []
    for f in result.findings:
        location = f.path or "<repo>"
        if f.line is not None:
            location += f":{f.line}"
        lines.append(f"{location}: {f.rule}: {f.message}")
        if f.hint:
            lines.append(f"    hint: {f.hint}")
    lines.append(summary(result))
    if result.stale_baseline:
        n = result.stale_baseline
        entries = (
            "1 baseline entry no longer occurs"
            if n == 1
            else f"{n} baseline entries no longer occur"
        )
        lines.append(f"{entries}; run `pyhyi baseline` to shrink the baseline.")
    return "\n".join(lines) + "\n"


def render_json(result: Result) -> str:
    # ASCII-only output survives any console or pipe encoding.
    payload = {"version": JSON_FORMAT_VERSION, "pyhyi": __version__, **result.to_dict()}
    return json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
