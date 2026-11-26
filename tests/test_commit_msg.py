from pyhyi.cli import main


def test_proposed_subject_uses_git_cleanup_and_config(repo, monkeypatch):
    monkeypatch.chdir(repo.root)
    repo.git("config", "core.commentChar", ";")
    repo.write("pyhyi.toml", '[rules.commit-convention]\ntypes = ["change"]\n')
    path = repo.write(".git/COMMIT_EDITMSG", "; editor comment\n\nchange: good\n\nBody\n")
    assert main(["commit-msg", str(path)]) == 0
    path.write_text("feat: unknown here\n")
    assert main(["commit-msg", str(path)]) == 1
    repo.write("pyhyi.toml", 'ignore = ["commit-convention"]\n')
    assert main(["commit-msg", str(path)]) == 0


def test_autosquash_subjects_are_accepted(repo, monkeypatch):
    monkeypatch.chdir(repo.root)
    for prefix in ("fixup!", "squash!", "amend!"):
        path = repo.write(".git/COMMIT_EDITMSG", f"{prefix} legacy subject\n")
        assert main(["commit-msg", str(path)]) == 0


def test_empty_subject_and_merge(repo, monkeypatch):
    monkeypatch.chdir(repo.root)
    path = repo.write(".git/COMMIT_EDITMSG", "# comments only\n")
    assert main(["commit-msg", str(path)]) == 1
    repo.write(".git/MERGE_HEAD", "abc\n")
    assert main(["commit-msg", str(path)]) == 0
