import json

from pyhyi.cli import main


def inspect(repo, capsys, rule):
    assert main(["check", str(repo.root), "--select", rule, "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == []
    assert all(f["severity"] == "advice" for f in result["findings"])
    return {f["path"] for f in result["findings"]}


def test_dump_folder_requires_repeated_additions_and_no_consumer(repo, capsys):
    for n in range(5):
        repo.commit("chore: export", {f"archive/report-{n}.csv": "col\n"})
    assert inspect(repo, capsys, "dump-folder") == {"archive"}
    repo.write("reader.py", "from pathlib import Path\nlist(Path('archive').glob('*.csv'))")
    assert inspect(repo, capsys, "dump-folder") == set()


def test_stale_doc_requires_both_signals_and_skips_local_edits(repo, capsys, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    repo.commit("docs: usage", {"README.md": "[api](src/api.py)", "src/api.py": "x = 0"})
    for n in range(1, 4):
        monkeypatch.setenv("GIT_COMMITTER_DATE", f"2020-02-0{n}T00:00:00Z")
        repo.commit("feat: change api", {"src/api.py": f"x = {n}"})
    assert inspect(repo, capsys, "stale-doc") == {"README.md"}
    repo.write("README.md", "Updated [api](src/api.py)")
    assert inspect(repo, capsys, "stale-doc") == set()


def test_orphan_evidence_includes_python_and_typescript(repo, capsys, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    repo.commit(
        "feat: modules",
        {
            "src/pkg/backup_alpha.py": "x = 1",
            "src/pkg/obsolete_beta.py": "x = 2",
            "web/scratch_old.ts": "export const x = 1",
            "web/scratch_used.ts": "export const x = 2",
            "src/pkg/api.py": "from . import backup_alpha",
            "web/current.ts": 'export { x } from "./scratch_used.js"',
            "tests/test_backup.py": "def test_ok(): pass",
        },
    )
    assert inspect(repo, capsys, "orphan-file") == {
        "src/pkg/obsolete_beta.py",
        "web/scratch_old.ts",
    }
    repo.write("README.md", "[source](web/scratch_old.ts)")
    assert inspect(repo, capsys, "orphan-file") == {"src/pkg/obsolete_beta.py"}


def test_pytest_open_removes_review_candidate(repo, capsys, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    repo.commit(
        "test: dynamic consumer",
        {
            "pyhyi.toml": 'select = ["orphan-file"]',
            "lib/backup_tool.py": "x = 1",
            "test_app.py": "from pathlib import Path\n"
            "def test_file():\n"
            "    assert next(Path('lib').glob('*.py')).read_text()\n",
        },
    )
    assert inspect(repo, capsys, "orphan-file") == {"lib/backup_tool.py"}
    outcome = subprocess.run(
        [sys.executable, "-m", "pytest", "--pyhyi", "-q"],
        cwd=repo.root,
        text=True,
        capture_output=True,
    )
    assert outcome.returncode == 0, outcome.stdout
    assert "  lib/backup_tool.py" in outcome.stdout.splitlines()
    assert "orphan-file:" not in outcome.stdout
