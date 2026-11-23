"""Opt-in comparisons of repository implementations of a calculation."""

from __future__ import annotations

from collections.abc import Iterator

from pyhyi.context import Context
from pyhyi.findings import Finding, Severity
from pyhyi.rules import Rule, dimension_contracts, format_contracts, numeric_contracts


class CalculationDrift(Rule):
    id = "calculation-drift"
    summary = "Review paired calculations, validation inputs and displayed precision."
    default_enabled = False
    severity = Severity.ADVICE

    def check(self, ctx: Context) -> Iterator[Finding]:
        yield from numeric_contracts.observations(ctx, self)
        yield from dimension_contracts.observations(ctx, self)
        yield from format_contracts.observations(ctx, self)
