"""Rule base class.

A rule is a class with a kebab-case `id`, a one-line `summary` and a `check`
method that yields findings. Options come from `[tool.pyhyi.rules.<id>]` and
are validated against `default_options`: unknown keys and wrong value types
are configuration errors.

Third-party rules are registered through the `pyhyi.rules` entry point group;
an entry point may point to a Rule subclass or a list of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from pyhyi.errors import ConfigError
from pyhyi.findings import Finding, Severity

if TYPE_CHECKING:
    from pyhyi.config import Config
    from pyhyi.context import Context

RULE_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


class Rule:
    id: ClassVar[str]
    summary: ClassVar[str]
    default_options: ClassVar[Mapping[str, Any]] = {}
    default_enabled: ClassVar[bool] = True
    severity: ClassVar[Severity] = Severity.ERROR

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = {**self.default_options, **(options or {})}

    def check(self, ctx: Context) -> Iterable[Finding]:
        raise NotImplementedError

    def finding(
        self,
        message: str,
        *,
        path: str | None = None,
        line: int | None = None,
        key: str | None = None,
        hint: str | None = None,
    ) -> Finding:
        """Build a finding attributed to this rule. Keep line numbers out of `message`."""
        return Finding(
            rule=self.id,
            message=message,
            path=path,
            line=line,
            key=key,
            hint=hint,
            severity=self.severity,
        )


def _kind(value: Any) -> tuple[type, ...]:
    if isinstance(value, bool):
        return (bool,)
    if isinstance(value, int):
        return (int,)
    if isinstance(value, float):
        return (int, float)
    if isinstance(value, (list, tuple)):
        return (list,)
    return (type(value),)


def resolve_options(rule: type[Rule], config: Config) -> dict[str, Any]:
    """Defaults merged with configured options; raises ConfigError on mistakes."""
    given = config.rules.get(rule.id, {})
    unknown = sorted(set(given) - set(rule.default_options))
    if unknown:
        raise ConfigError(config.where(f"rules.{rule.id}: unknown option(s): {', '.join(unknown)}"))
    options = dict(rule.default_options)
    for name, value in given.items():
        default = rule.default_options[name]
        if default is not None:
            kinds = _kind(default)
            if not isinstance(value, kinds) or (bool not in kinds and isinstance(value, bool)):
                expected = " or ".join(k.__name__ for k in kinds)
                raise ConfigError(
                    config.where(
                        f"rules.{rule.id}.{name}: expected {expected}, got {type(value).__name__}"
                    )
                )
        options[name] = value
    return options


def validate_rule_class(obj: Any, origin: str) -> type[Rule]:
    if not (isinstance(obj, type) and issubclass(obj, Rule)):
        raise ConfigError(f"{origin}: {obj!r} is not a pyhyi Rule subclass")
    rule_id = getattr(obj, "id", None)
    if not isinstance(rule_id, str) or not RULE_ID.match(rule_id):
        raise ConfigError(f"{origin}: rule {obj.__name__} has invalid id {rule_id!r}")
    if not isinstance(getattr(obj, "summary", None), str):
        raise ConfigError(f"{origin}: rule {rule_id} has no summary")
    return obj
