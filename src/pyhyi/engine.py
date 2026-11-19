"""Runs selected rules over a repository and filters their findings.

Filtering order: `exclude` -> `per-path-ignores` -> inline suppressions ->
baseline. Excluded and per-path-ignored findings are dropped silently;
suppressed and baselined ones are counted in the summary.
"""

from __future__ import annotations

import traceback
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyhyi import baseline
from pyhyi.config import Config
from pyhyi.context import Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding, Severity
from pyhyi.paths import PathMatcher
from pyhyi.rules import Rule, resolve_options

Registry = Mapping[str, type[Rule]]


@dataclass(frozen=True)
class RuleError:
    """A rule crashed. Other rules still run; the check exits with an error."""

    rule: str
    traceback: str


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    suppressed: list[Finding] = field(default_factory=list)
    baselined: list[Finding] = field(default_factory=list)
    stale_baseline: int = 0
    errors: list[RuleError] = field(default_factory=list)
    rules: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return any(f.severity != Severity.ADVICE for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "summary": {
                "issues": sum(f.severity != Severity.ADVICE for f in self.findings),
                "advice": sum(f.severity == Severity.ADVICE for f in self.findings),
                "suppressed": len(self.suppressed),
                "baselined": len(self.baselined),
                "stale_baseline": self.stale_baseline,
                "rules": list(self.rules),
            },
            "errors": [{"rule": e.rule, "traceback": e.traceback} for e in self.errors],
        }


def _check_ids(ids: Iterable[str], registry: Registry, where: str) -> None:
    unknown = sorted(set(ids) - set(registry))
    if unknown:
        raise ConfigError(f"{where}: unknown rule(s): {', '.join(unknown)}")


def validate_config(config: Config, registry: Registry) -> None:
    """Reject references to unknown rules and invalid rule options."""
    _check_ids(config.select or (), registry, config.where("select"))
    _check_ids(config.ignore, registry, config.where("ignore"))
    for pattern, ids in config.per_path_ignores.items():
        _check_ids(ids, registry, config.where(f"per-path-ignores.{pattern}"))
    _check_ids(config.rules, registry, config.where("rules"))
    for rule_id in config.rules:
        resolve_options(registry[rule_id], config)


def select_rules(
    registry: Registry,
    config: Config,
    *,
    select: Collection[str] | None = None,
    ignore: Collection[str] = (),
) -> list[type[Rule]]:
    """`select` (CLI) replaces the configured selection; `ignore` adds to it."""
    if select is not None:
        _check_ids(select, registry, "--select")
    _check_ids(ignore, registry, "--ignore")
    chosen = select if select is not None else config.select
    if chosen is None:
        ids = [rule_id for rule_id, cls in registry.items() if cls.default_enabled]
    else:
        ids = list(chosen)
    skip = set(config.ignore) | set(ignore)
    return [registry[rule_id] for rule_id in sorted(set(ids)) if rule_id not in skip]


def run(
    root: Path,
    config: Config,
    registry: Registry,
    *,
    select: Collection[str] | None = None,
    ignore: Collection[str] = (),
    use_baseline: bool = True,
) -> Result:
    validate_config(config, registry)
    rules = select_rules(registry, config, select=select, ignore=ignore)
    ctx = Context(root, config, known_rules=frozenset(registry))
    per_path = [
        (PathMatcher([pattern]), set(ids)) for pattern, ids in config.per_path_ignores.items()
    ]
    result = Result(rules=tuple(cls.id for cls in rules))

    for cls in rules:
        rule = cls(resolve_options(cls, config))
        try:
            produced = list(rule.check(ctx))
        except Exception:
            result.errors.append(RuleError(cls.id, traceback.format_exc()))
            continue
        for finding in produced:
            if finding.path is not None:
                if ctx.is_excluded(finding.path):
                    continue
                if any(finding.rule in ids and m.match(finding.path) for m, ids in per_path):
                    continue
                if _suppressed(ctx, finding):
                    result.suppressed.append(finding)
                    continue
            result.findings.append(finding)

    result.findings.sort(key=Finding.sort_key)
    if use_baseline:
        known = baseline.load(root / config.baseline)
        if known is not None:
            result.findings, result.baselined, result.stale_baseline = baseline.apply(
                result.findings, known, result.rules
            )
    return result


def _suppressed(ctx: Context, finding: Finding) -> bool:
    if finding.path is None or finding.line is None:
        return False
    sup = ctx.suppressions(finding.path).get(finding.line)
    return sup is not None and bool(sup.reason) and finding.rule in sup.rules
