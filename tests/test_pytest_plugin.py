"""End-to-end checks using the installed pytest11 entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import GitRepo


def run(repo: GitRepo, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, *args],
        cwd=repo.root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_open_events_are_scoped_to_tests_and_project_files(repo: GitRepo) -> None:
    repo.commit(
        "test: read samples",
        {
            ".gitignore": "local-only/\n__pycache__/\n",
            "pyhyi.toml": 'exclude = ["excluded"]\n',
            "data/sample.txt": "sample",
            "data/untouched.txt": "",
            "excluded/a.txt": "",
            "test_demo.py": (
                "from pathlib import Path\n"
                "def test_open(monkeypatch):\n"
                "    monkeypatch.chdir('data')\n"
                "    for name in ('sample.txt', 'sample.txt', 'draft.txt',\n"
                "                 '../excluded/a.txt', '../local-only/secret.txt'):\n"
                "        Path(name).read_text()\n"
            ),
        },
    )
    repo.write("data/draft.txt")
    repo.write("local-only/secret.txt")
    result = run(repo, "-m", "pytest", "-q", "--pyhyi")
    assert result.returncode == 0, result.stdout
    assert "No issues found." in result.stdout
    listing = result.stdout.split("Repository files with Python open events: ")[1].splitlines()
    paths = [line[2:] for line in listing[1:] if line.startswith("  ")]
    assert int(listing[0]) == len(paths)
    assert paths == sorted(set(paths))
    assert {"data/sample.txt", "data/draft.txt"} <= set(paths)
    assert not {"data/untouched.txt", "excluded/a.txt", "local-only/secret.txt"} & set(paths)


@pytest.mark.skipif(os.name != "nt", reason="DOS short paths require Windows")
def test_open_via_short_directory_name(repo: GitRepo) -> None:
    import ctypes

    name = "a long directory"
    directory = repo.root / name
    directory.mkdir()
    buf = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.kernel32.GetShortPathNameW(str(directory), buf, len(buf))
    if "~" not in buf.value:
        pytest.skip("8.3 names disabled on this volume")
    repo.commit(
        "test: short paths",
        {
            f"{name}/sample.txt": "sample",
            "test_demo.py": (
                "def test_read():\n"
                f"    with open({buf.value!r} + '/sample.txt') as f:\n"
                "        assert f.read() == 'sample'\n"
            ),
        },
    )
    result = run(repo, "-m", "pytest", "-q", "--pyhyi")
    assert result.returncode == 0, result.stdout
    assert f"  {name}/sample.txt" in result.stdout.splitlines()


def test_findings_and_baseline_match_cli(repo: GitRepo) -> None:
    repo.commit("test: pass", {"test_demo.py": "def test_demo(): pass\n", "stray.log": ""})
    plain = run(repo, "-m", "pytest", "-q")
    assert plain.returncode == 0, plain.stdout
    assert "Repository files with Python open events:" not in plain.stdout
    for expected in (1, 0):
        cli = run(repo, "-m", "pyhyi", "check")
        tests = run(repo, "-m", "pytest", "-q", "--pyhyi")
        assert cli.returncode == tests.returncode == expected, tests.stdout
        assert cli.stdout.strip() in tests.stdout
        assert "1 passed" in tests.stdout
        if expected:
            assert run(repo, "-m", "pyhyi", "baseline").returncode == 0


@pytest.mark.parametrize(("test_code", "status"), [("def test_fail(): assert False\n", 1), ("", 5)])
def test_preserves_pytest_exit_status(repo: GitRepo, test_code: str, status: int) -> None:
    repo.commit("test: exit status", {"test_demo.py": test_code})
    result = run(repo, "-m", "pytest", "-q", "--pyhyi")
    assert result.returncode == status, result.stdout
    assert "No issues found." in result.stdout


def test_bad_config_is_visible(repo: GitRepo) -> None:
    repo.commit("test: invalid config", {"pyhyi.toml": "typo = 1"})
    result = run(repo, "-m", "pytest", "-q", "--pyhyi")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout
    assert "unknown key(s): typo" in result.stdout


def test_configured_test_weakening_compares_against_base(repo: GitRepo) -> None:
    repo.commit("test: add example", {"test_demo.py": "def test_demo(): assert value\n"})
    repo.write("test_demo.py", "def test_demo(): assert True\n")
    repo.write(
        "pyproject.toml",
        '[tool.pyhyi]\nselect = ["test-weakening"]\n'
        '[tool.pyhyi.rules.test-weakening]\nbase = "HEAD"\n',
    )
    result = run(repo, "-m", "pytest", "-q", "--pyhyi")
    assert result.returncode == 0, result.stdout
    assert "test_demo replaced a nonconstant assertion with 'assert True'" in result.stdout
    assert "1 passed" in result.stdout
