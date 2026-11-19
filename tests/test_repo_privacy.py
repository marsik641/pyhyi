"""Guard: private files and agent tooling must never be tracked by git."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PRIVATE_PATHS = [
    "private",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude",
    ".codex",
    ".agents",
    ".skills",
    ".cursor",
]


def _git_ls_files(*paths: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", *paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.splitlines()


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_private_path_is_not_tracked(path: str) -> None:
    assert _git_ls_files(path) == []
