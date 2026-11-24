"""Configuration: `pyhyi.toml`, `.pyhyi.toml` or `[tool.pyhyi]` in `pyproject.toml`.

The first file found in the repository root wins. Standalone files hold the
settings at the top level, so non-Python projects need no `pyproject.toml`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pyhyi.errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

DEFAULT_BASELINE = ".pyhyi-baseline.json"
CONFIG_FILES = ("pyhyi.toml", ".pyhyi.toml", "pyproject.toml")

Where = Callable[[str], str]

_KEYS = frozenset(
    {"select", "ignore", "exclude", "per-path-ignores", "baseline", "rules", "doc-map"}
)


@dataclass(frozen=True)
class Config:
    source: Path | None = None
    select: tuple[str, ...] | None = None
    ignore: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    per_path_ignores: dict[str, tuple[str, ...]] = field(default_factory=dict)
    baseline: str = DEFAULT_BASELINE
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    doc_map: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def where(self, key: str) -> str:
        """Human-readable location of a setting, for error messages."""
        return f"{self.source.name}: {key}" if self.source else key


def _read_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def load_config(root: Path, *, read: Callable[[Path], bytes | None] = _read_file) -> Config:
    for name in CONFIG_FILES:
        path = root / name
        content = read(path)
        if content is None:
            continue
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{name}: invalid TOML: {exc}") from exc
        if name == "pyproject.toml":
            tool = data.get("tool")
            if not isinstance(tool, dict) or "pyhyi" not in tool:
                continue
            data = tool["pyhyi"]
        return parse_config(data, source=path)
    return Config()


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
        doc_map=_doc_map(data.get("doc-map", {}), where),
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


def _doc_map(value: Any, where: Where) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ConfigError(where("doc-map must be a table of pattern = [document paths]"))
    mapping = {}
    for pattern in value:
        location = f"doc-map.{pattern}"
        docs = _str_list(value, pattern, lambda msg: where(f"doc-map.{msg}"))
        if not pattern.strip() or not docs:
            raise ConfigError(where(f"{location} needs a non-empty pattern and document list"))
        for doc in docs:
            path = PurePosixPath(doc)
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != doc
                or doc != doc.strip()
                or any(char in doc for char in "\\:\r\n\0")
            ):
                raise ConfigError(where(f"{location} needs a literal repository path: {doc!r}"))
        mapping[pattern] = docs
    return mapping


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
