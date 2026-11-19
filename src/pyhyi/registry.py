"""Discovery of built-in and third-party rules."""

from __future__ import annotations

from importlib.metadata import entry_points

from pyhyi.errors import ConfigError
from pyhyi.rules import Rule, validate_rule_class
from pyhyi.rules.commits import CommitConvention
from pyhyi.rules.layout import RootClutter
from pyhyi.rules.links import BrokenDocLink
from pyhyi.rules.suppression import BadSuppression
from pyhyi.rules.todos import StaleTodo

ENTRY_POINT_GROUP = "pyhyi.rules"

BUILTIN_RULES: tuple[type[Rule], ...] = (
    BadSuppression,
    BrokenDocLink,
    CommitConvention,
    RootClutter,
    StaleTodo,
)


def load_rules(*, plugins: bool = True) -> dict[str, type[Rule]]:
    """Map rule id to rule class: built-ins first, then entry-point plugins."""
    registry: dict[str, type[Rule]] = {}
    for cls in BUILTIN_RULES:
        _add(registry, cls, "pyhyi")
    if plugins:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            origin = f"plugin {ep.name} ({ep.value})"
            try:
                obj = ep.load()
            except Exception as exc:
                raise ConfigError(f"{origin}: failed to load: {exc}") from exc
            for item in obj if isinstance(obj, (list, tuple)) else [obj]:
                _add(registry, validate_rule_class(item, origin), origin)
    return dict(sorted(registry.items()))


def _add(registry: dict[str, type[Rule]], cls: type[Rule], origin: str) -> None:
    if cls.id in registry and registry[cls.id] is not cls:
        raise ConfigError(f"{origin}: duplicate rule id {cls.id!r}")
    registry[cls.id] = cls
