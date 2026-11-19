"""Configuration: `pyhyi.toml`, `.pyhyi.toml` or `[tool.pyhyi]` in `pyproject.toml`.

The first file found in the repository root wins. Standalone files hold the
settings at the top level, so non-Python projects need no `pyproject.toml`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyhyi.errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

DEFAULT_BASELINE = ".pyhyi-baseline.json"
CONFIG_FILES = ("pyhyi.toml", ".pyhyi.toml", "pyproject.toml")

Where = Callable[[str], str]

_KEYS = frozenset({"select", "ignore", "exclude", "per-path-ignores", "baseline", "rules"})


@dataclass(frozen=True)
class Config:
    source: Path | None = None
    select: tuple[str, ...] | None = None
    ignore: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    per_path_ignores: dict[str, tuple[str, ...]] = field(default_factory=dict)
    baseline: str = DEFAULT_BASELINE
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)

    def where(self, key: str) -> str:
        """Human-readable location of a setting, for error messages."""
        return f"{self.source.name}: {key}" if self.source else key


def load_config(root: Path) -> Config:
    for name in CONFIG_FILES:
        path = root / name
        if not path.is_file():
            continue
        data = _read_toml(path)
        if name == "pyproject.toml":
            tool = data.get("tool")
            if not isinstance(tool, dict) or "pyhyi" not in tool:
                continue
            data = tool["pyhyi"]
        return parse_config(data, source=path)
    return Config()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid TOML: {exc}") from exc


def parse_config(data: Any, source: Path | None = None) -> Config:
    where = Config(source=source).where
    if not isinstance(data, dict):
        raise ConfigError(where("pyhyi settings must be a table"))
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise ConfigError(where(f"unknown key(s): {', '.join(unknown)}"))
    return Config(
        source=source,
        select=_str_list(data, "select", where) if "select" in data else None,
        ignore=_str_list(data, "ignore", where),
        exclude=_str_list(data, "exclude", where),
        per_path_ignores=_per_path(data.get("per-path-ignores", {}), where),
        baseline=_baseline(data, where),
        rules=_rules(data.get("rules", {}), where),
    )


def _str_list(data: dict[str, Any], key: str, where: Where) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(where(f"{key} must be a list of strings"))
    return tuple(value)


def _per_path(value: Any, where: Where) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ConfigError(where("per-path-ignores must be a table of pattern = [rules]"))
    return {
        pattern: _str_list(value, pattern, lambda msg: where(f"per-path-ignores.{msg}"))
        for pattern in value
    }


def _baseline(data: dict[str, Any], where: Where) -> str:
    value = data.get("baseline", DEFAULT_BASELINE)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(where("baseline must be a non-empty path string"))
    return value


def _rules(value: Any, where: Where) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ConfigError(where("rules must be a table of rule-id tables"))
    for rule_id, options in value.items():
        if not isinstance(options, dict):
            raise ConfigError(where(f"rules.{rule_id} must be a table of options"))
    return {rule_id: dict(options) for rule_id, options in value.items()}
