"""The changelog section of a version becomes its GitHub Release notes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / "scripts" / "release_notes.py"
)
assert _spec is not None and _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """\
# Changelog

## 0.2.0 - 2026-02-01

### Fixed

- Second.

## 0.1.0 - 2026-01-01

- First.
"""


def test_section_stops_at_the_next_version() -> None:
    assert release_notes.section(CHANGELOG, "0.2.0") == "### Fixed\n\n- Second.\n"
    assert release_notes.section(CHANGELOG, "0.1.0") == "- First.\n"


@pytest.mark.parametrize("version", ["0.3.0", "0.1", "Changelog"])
def test_missing_section_is_an_error(version: str) -> None:
    with pytest.raises(LookupError, match=version):
        release_notes.section(CHANGELOG, version)


def test_empty_section_is_an_error() -> None:
    with pytest.raises(LookupError, match="empty"):
        release_notes.section("## 0.1.0\n\n## 0.0.1\n- x\n", "0.1.0")
