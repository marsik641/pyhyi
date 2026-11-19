from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class GitRepo:
    """A throwaway git repository for tests."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout

    def write(self, path: str, content: str = "") -> Path:
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8", newline="\n")
        return full

    def commit(self, message: str, files: dict[str, str] | None = None) -> str:
        for path, content in (files or {}).items():
            self.write(path, content)
        self.git("add", "--all")
        self.git("commit", "--allow-empty", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate git from the developer's global and system configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Never discover a repository above the test's temporary directory.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


@pytest.fixture
def repo(tmp_path: Path, git_env: None) -> GitRepo:
    root = tmp_path / "repo"
    root.mkdir()
    r = GitRepo(root)
    r.git("init", "--quiet", "-b", "main")
    return r
