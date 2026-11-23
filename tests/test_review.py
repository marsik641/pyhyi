import json

from pyhyi.cli import main


def inspect(repo, capsys, rule):
    return {f["path"] for f in findings(repo, capsys, rule)}


def findings(repo, capsys, rule):
    assert main(["check", str(repo.root), "--select", rule, "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == []
    assert all(f["severity"] == "advice" for f in result["findings"])
    return result["findings"]


def test_dump_folder_requires_repeated_additions_and_no_consumer(repo, capsys):
    for n in range(5):
        repo.commit("chore: export", {f"archive/report-{n}.csv": "col\n"})
    assert inspect(repo, capsys, "dump-folder") == {"archive"}
    repo.write("reader.py", "from pathlib import Path\nlist(Path('archive').glob('*.csv'))")
    assert inspect(repo, capsys, "dump-folder") == set()


def test_stale_doc_file_history_requires_explicit_opt_in(repo, capsys, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    repo.commit("docs: usage", {"README.md": "[api](src/api.py)", "src/api.py": "x = 0"})
    for n in range(1, 4):
        monkeypatch.setenv("GIT_COMMITTER_DATE", f"2020-02-0{n}T00:00:00Z")
        repo.commit("feat: change api", {"src/api.py": f"x = {n}"})
    assert inspect(repo, capsys, "stale-doc") == set()
    repo.write("pyhyi.toml", "[rules.stale-doc]\nfile-history = true\n")
    assert inspect(repo, capsys, "stale-doc") == {"README.md"}
    repo.write("README.md", "Updated [api](src/api.py)")
    assert inspect(repo, capsys, "stale-doc") == set()


def test_stale_doc_reports_changed_quoted_declarations_after_one_commit(repo, capsys):
    doc = (
        "# API\n\n"
        "The [implementation](src/api.py) defines `PAGE_LIMIT = 5` "
        "and `def search(query: str, limit: int = 5)`.\n"
    )
    repo.commit(
        "docs: quote declarations",
        {
            "README.md": doc,
            "src/api.py": "PAGE_LIMIT: int = 5\ndef search(query: str, limit: int = 5): ...\n",
        },
    )
    repo.commit(
        "feat: change the page size",
        {"src/api.py": "PAGE_LIMIT = 8\ndef search(query: str, limit: int = 8): ...\n"},
    )
    result = findings(repo, capsys, "stale-doc")
    assert [finding["line"] for finding in result] == [3, 3]
    assert [finding["message"] for finding in result] == [
        "src/api.py changed from `PAGE_LIMIT = 5` to `PAGE_LIMIT = 8` "
        "after this declaration was documented.",
        "src/api.py changed from `def search(query: str, limit: int=5)` to "
        "`def search(query: str, limit: int=8)` after this declaration was documented.",
    ]
    repo.write("src/api.py", "PAGE_LIMIT = 5\ndef search(query: str, limit: int = 5): ...\n")
    assert inspect(repo, capsys, "stale-doc") == set()
    repo.commit("fix: restore the original declarations")
    assert inspect(repo, capsys, "stale-doc") == set()


def test_stale_doc_skips_unrelated_edits_and_unverified_claims(repo, capsys):
    repo.commit(
        "docs: describe the API",
        {
            "README.md": (
                "The [implementation](src/api.py) declares `PAGE_LIMIT = 5`, "
                "`def search(query: str)` and mentions `MAX_RETRIES = 100`.\n"
            ),
            "src/api.py": (
                "PAGE_LIMIT = 5\nMAX_RETRIES = 6\ndef search(query: str): return query\n"
            ),
        },
    )
    repo.commit(
        "feat: clean up results",
        {
            "src/api.py": (
                "PAGE_LIMIT: int = 0x5\nMAX_RETRIES = 7\n"
                "def search(query: str): return query.strip()\n"
            ),
        },
    )
    assert inspect(repo, capsys, "stale-doc") == set()


def test_stale_doc_skips_ambiguous_sources_and_bindings(repo, capsys):
    repo.commit(
        "docs: describe the API",
        {
            "README.md": (
                "A shared limit `PAGE_LIMIT = 5` applies in [a.py](a.py) and [b.py](b.py).\n\n"
                "There is a [retry limit](a.py) called `MAX_RETRIES = 2`.\n"
            ),
            "a.py": "PAGE_LIMIT = 5\nMAX_RETRIES = 2\n",
            "b.py": "PAGE_LIMIT = 5\n",
        },
    )
    repo.commit(
        "feat: adjust the limits",
        {"a.py": "PAGE_LIMIT = 8\nMAX_RETRIES = 9\nif flag:\n    MAX_RETRIES = 2\n"},
    )
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


def test_orphan_file_recognizes_documented_paths(repo, capsys, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    repo.commit(
        "feat: add sample modules",
        {
            "notes/helpers/backup_grid.py": "pass",
            "pkg/scratch_curve.ts": "export {}",
            "examples-old/backup_grid.py": "pass",
            "pkg/obsolete_table.py": "pass",
            "notes/guide.md": (
                "Run `helpers/backup_grid.py` to draw a grid.\n\n"
                "See also ``pkg/scratch_curve.ts``.\n\n"
                "<!-- Example: `pkg/obsolete_table.py` -->\n\n"
                "```text\nSee `pkg/obsolete_table.py`.\n```\n"
            ),
        },
    )
    assert inspect(repo, capsys, "orphan-file") == {
        "examples-old/backup_grid.py",
        "pkg/obsolete_table.py",
    }


def test_ambiguous_inline_path_does_not_select_a_module(repo, capsys, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    paths = {"pkg/backup_grid.py", "notes/pkg/backup_grid.py"}
    repo.commit("feat: modules", {path: "pass" for path in paths})
    repo.write("notes/guide.md", "See `pkg/backup_grid.py`.")
    assert inspect(repo, capsys, "orphan-file") == paths
    repo.write("notes/guide.md", "See `./pkg/backup_grid.py`.")
    assert inspect(repo, capsys, "orphan-file") == {"pkg/backup_grid.py"}


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
