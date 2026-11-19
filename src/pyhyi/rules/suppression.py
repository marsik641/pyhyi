"""bad-suppression: inline suppressions must be well-formed."""

from __future__ import annotations

from collections.abc import Iterator

from pyhyi.context import Context
from pyhyi.findings import Finding
from pyhyi.rules import Rule

_FORMAT = "Write it as `<comment> pyhyi: ignore[rule-id] why this is fine`."


class BadSuppression(Rule):
    id = "bad-suppression"
    summary = "Inline suppressions must name known rules and give a reason."

    def check(self, ctx: Context) -> Iterator[Finding]:
        for path in ctx.files:
            for sup in ctx.suppressions(path).values():
                if not sup.rules:
                    yield self.finding(
                        "Suppression names no rules", path=path, line=sup.line, hint=_FORMAT
                    )
                    continue
                unknown = [r for r in sup.rules if r not in ctx.known_rules]
                if unknown:
                    yield self.finding(
                        f"Suppression names unknown rule(s): {', '.join(unknown)}",
                        path=path,
                        line=sup.line,
                        hint="Run `pyhyi rules` to list rule ids.",
                    )
                if not sup.reason:
                    yield self.finding(
                        f"Suppression of {', '.join(sup.rules)} has no reason and is not applied",
                        path=path,
                        line=sup.line,
                        hint=_FORMAT,
                    )
