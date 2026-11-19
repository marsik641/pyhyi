"""Alias packages must stay in lockstep with pyhyi."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from pyhyi import __version__

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_aliases", ROOT / "scripts" / "build_aliases.py"
)
assert _spec is not None and _spec.loader is not None
build_aliases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_aliases)


def test_read_version_matches_package() -> None:
    assert build_aliases.read_version() == __version__


def test_alias_names_are_valid_and_distinct() -> None:
    names = build_aliases.ALIASES
    assert len(set(names)) == len(names)
    assert "pyhyi" not in names
    assert all(re.fullmatch(r"[a-z][a-z0-9]*", n) for n in names)


@pytest.mark.parametrize("name", build_aliases.ALIASES)
def test_alias_pins_exact_pyhyi_version(tmp_path: Path, name: str) -> None:
    src = build_aliases.write_alias(name, __version__, tmp_path)
    meta = tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))
    project = meta["project"]
    assert project["name"] == name
    assert project["version"] == __version__
    assert project["dependencies"] == [f"pyhyi=={__version__}"]
    assert meta["tool"]["hatch"]["build"]["targets"]["wheel"]["bypass-selection"] is True
    assert f"pip install {name}" in (src / "README.md").read_text(encoding="utf-8")
    assert (src / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
