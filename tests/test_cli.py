from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pyhyi import __version__
from pyhyi.cli import main
from pyhyi.errors import ConfigError
from pyhyi.rules import Rule
from pyhyi.rules.suppression import BadSuppression

if TYPE_CHECKING:
    from conftest import GitRepo

BAD_LINE = "x = 1  # pyhyi: ignore[no-such-rule] sample\n"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: pyhyi" in capsys.readouterr().out


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pyhyi", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout


def test_check_clean_repo(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init", {"a.py": "x = 1\n"})
    assert main(["check", str(repo.root)]) == 0
    assert capsys.readouterr().out == "No issues found.\n"


def test_check_reports_issues(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init", {"src/a.py": BAD_LINE})
    assert main(["check", str(repo.root / "src")]) == 1
    out = capsys.readouterr().out
    assert "src/a.py:1: bad-suppression: Suppression names unknown rule(s): no-such-rule" in out
    assert "    hint: " in out
    assert out.endswith("Found 1 issue.\n")


def test_check_json(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init", {"a.py": BAD_LINE})
    assert main(["check", str(repo.root), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == 1
    assert data["summary"]["issues"] == 1
    assert data["findings"][0]["path"] == "a.py"
    assert data["findings"][0]["line"] == 1
    assert data["errors"] == []


def test_check_select_ignore(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init", {"a.py": BAD_LINE})
    assert main(["check", str(repo.root), "--ignore", "bad-suppression"]) == 0
    assert main(["check", str(repo.root), "--select", "nope"]) == 2
    assert "unknown rule(s): nope" in capsys.readouterr().err


def test_baseline_then_check(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init", {"a.py": BAD_LINE})
    assert main(["baseline", str(repo.root)]) == 0
    assert "Recorded 1 issue in .pyhyi-baseline.json." in capsys.readouterr().out
    assert (repo.root / ".pyhyi-baseline.json").is_file()

    assert main(["check", str(repo.root)]) == 0
    assert capsys.readouterr().out == "No issues found. (1 baselined)\n"

    assert main(["check", str(repo.root), "--no-baseline"]) == 1
    capsys.readouterr()

    repo.write("a.py", "x = 1\n")
    assert main(["check", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert "1 baseline entry no longer occurs" in out


def test_config_error_exits_2(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.write("pyhyi.toml", "bogus = true\n")
    assert main(["check", str(repo.root)]) == 2
    assert "pyhyi: error: pyhyi.toml: unknown key(s): bogus" in capsys.readouterr().err


def test_plugin_config_error_preserves_json_and_later_rules(repo, capsys, monkeypatch):
    class BrokenPlugin(Rule):
        id = "a-plugin"

        def check(self, ctx):
            raise ConfigError("invalid plugin setting")

    registry = {rule.id: rule for rule in [BrokenPlugin, BadSuppression]}
    monkeypatch.setattr("pyhyi.cli.load_rules", lambda: registry)
    repo.commit("test: sample", {"sample.py": BAD_LINE})
    assert main(["check", str(repo.root), "--format", "json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in report["findings"]] == ["bad-suppression"]
    assert [e["rule"] for e in report["errors"]] == ["a-plugin"]
    assert "invalid plugin setting" in report["errors"][0]["traceback"]


def test_not_a_repo(tmp_path: Path, git_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(tmp_path)]) == 2
    assert "not inside a git work tree" in capsys.readouterr().err


def test_missing_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "definitely/not/here"]) == 2
    assert "path does not exist" in capsys.readouterr().err


def test_rules_lists_builtins(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0
    assert "bad-suppression" in capsys.readouterr().out
