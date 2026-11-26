from __future__ import annotations

import subprocess

from pyhyi.cli import main

SOURCE = "src/widget.py"
DOC = "docs/guide.md"
CONFIG = "pyhyi.toml"
MAP = f'[doc-map]\n"src/*.py" = ["{DOC}", "README.md"]\n'
TRAILER = f"Docs-Unchanged: {DOC}: The interface is unchanged"


def git_result(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo.root, capture_output=True, encoding="utf-8", check=False
    )


def test_commit_uses_the_index_prepared_by_git(repo):
    repo.commit("feat: start", {SOURCE: "value = 1\n", DOC: "Guide\n", CONFIG: MAP})
    assert main(["install-hooks", str(repo.root)]) == 0
    assert main(["install-hooks", str(repo.root)]) == 0

    repo.write(SOURCE, "value = 2\n")
    repo.write(DOC, "Updated guide\n")
    repo.write(CONFIG, "")
    repo.git("add", SOURCE)
    blocked = git_result(repo, "commit", "-m", "fix: change value")
    assert blocked.returncode != 0
    assert "doc-map:" in blocked.stderr and DOC in blocked.stderr

    repo.git("add", DOC)
    blocked = git_result(repo, "commit", "-m", "fix: change only source", "--", SOURCE)
    assert blocked.returncode != 0 and "doc-map:" in blocked.stderr
    repo.git("commit", "-m", "fix: change value and guide")
    assert repo.git("show", "HEAD:pyhyi.toml") == MAP


def test_initial_commit_accepts_an_alternative_and_rejects_an_empty_note(repo):
    assert main(["install-hooks", str(repo.root)]) == 0
    repo.write(CONFIG, MAP)
    repo.write(SOURCE, "value = 1\n")
    repo.git("add", CONFIG, SOURCE)
    blocked = git_result(
        repo, "commit", "-m", "feat: start", "--trailer", f"Docs-Unchanged: {DOC}:"
    )
    assert blocked.returncode != 0 and "doc-map:" in blocked.stderr
    repo.write("README.md", "Usage\n")
    repo.git("add", "README.md")
    repo.git("commit", "-m", "feat: start")


def test_push_checks_actual_outgoing_commit_and_accepts_persisted_note(repo, tmp_path):
    repo.commit("feat: start", {SOURCE: "value = 1\n", DOC: "Guide\n"})
    repo.commit("chore: configure hook", {CONFIG: MAP})
    assert main(["install-hooks", str(repo.root)]) == 0
    remote = tmp_path / "remote repo.git"
    repo.git("init", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.git("push", "-u", "origin", "main")

    repo.write(SOURCE, "value = 2\n")
    repo.git("rm", DOC)
    repo.git("add", SOURCE)
    repo.git("commit", "--no-verify", "-m", "refactor: change value")
    repo.write(CONFIG, "")
    repo.write(DOC, "Untracked replacement\n")
    blocked = git_result(repo, "push", "origin", "main")
    assert blocked.returncode != 0
    assert "doc-map: review documentation for outgoing" in blocked.stderr

    repo.git("commit", "--amend", "--no-edit", "--trailer", TRAILER)
    assert TRAILER in repo.git("log", "-1", "--format=%B")
    repo.git("push", "origin", "main")
    repo.git("branch", "another")
    repo.git("push", "origin", "another")
    repo.git("push", str(remote), "another:refs/heads/direct")


def test_push_to_url_skips_commits_already_on_a_remote(repo, tmp_path):
    repo.commit("chore: configure hook", {SOURCE: "value = 1\n", DOC: "Guide\n", CONFIG: MAP})
    remote = tmp_path / "remote.git"
    repo.git("init", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.commit("refactor: change value", {SOURCE: "value = 2\n"})
    repo.git("push", "-u", "origin", "main")
    assert main(["install-hooks", str(repo.root)]) == 0

    mirror = tmp_path / "mirror.git"
    repo.git("init", "--bare", str(mirror))
    repo.git("push", str(mirror), "main")
    repo.write(SOURCE, "value = 3\n")
    repo.git("commit", "--no-verify", "-qam", "refactor: change again")
    blocked = git_result(repo, "push", str(mirror), "main:refs/heads/next")
    assert blocked.returncode != 0
    assert blocked.stderr.count("doc-map: review documentation for outgoing") == 1


def test_installer_preserves_an_existing_hook_and_custom_hook_dir(repo, capsys):
    original = repo.write(".git/hooks/pre-push", "#!/bin/sh\nexit 0\n")
    assert main(["install-hooks", str(repo.root)]) == 2
    assert "hook already exists" in capsys.readouterr().err
    assert not original.with_name("commit-msg").exists()
    assert original.read_text() == "#!/bin/sh\nexit 0\n"

    repo.git("config", "core.hooksPath", "other-hooks")
    assert main(["install-hooks", str(repo.root)]) == 2
    assert "custom core.hooksPath" in capsys.readouterr().err
    assert not (repo.root / "other-hooks").exists()
