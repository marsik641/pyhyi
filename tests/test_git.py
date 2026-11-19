from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pyhyi import git
from pyhyi.errors import GitError

if TYPE_CHECKING:
    from conftest import GitRepo


def test_find_root_from_subdirectory(repo: GitRepo) -> None:
    sub = repo.root / "a" / "b"
    sub.mkdir(parents=True)
    assert git.find_root(sub).resolve() == repo.root.resolve()


def test_find_root_outside_repo(tmp_path: Path, git_env: None) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(GitError, match="not inside a git work tree"):
        git.find_root(outside)


def test_list_files(repo: GitRepo) -> None:
    repo.commit("init", {".gitignore": "*.tmp\n", "src/app.py": "", "gone.txt": "x"})
    (repo.root / "gone.txt").unlink()
    repo.write("new.md", "untracked but not ignored")
    repo.write("junk.tmp", "ignored")
    repo.write("данные/отчёт.txt", "unicode path")
    assert git.list_files(repo.root) == [
        ".gitignore",
        "new.md",
        "src/app.py",
        "данные/отчёт.txt",
    ]


def test_log_empty_repository(repo: GitRepo) -> None:
    assert git.log(repo.root) == []


def test_log_parses_commits(repo: GitRepo) -> None:
    first = repo.commit("feat: first", {"a.txt": "1"})
    second = repo.commit("fix: второй\n\nBody line one.\nBody line two.", {"a.txt": "2"})
    commits = git.log(repo.root)
    assert [c.sha for c in commits] == [second, first]
    newest = commits[0]
    assert newest.subject == "fix: второй"
    assert newest.body == "Body line one.\nBody line two."
    assert newest.parents == (first,)
    assert newest.author == "Test"
    assert newest.date.tzinfo is not None
    assert commits[1].parents == ()


def test_log_merge_commit(repo: GitRepo) -> None:
    repo.commit("chore: base", {"a.txt": "1"})
    repo.git("checkout", "--quiet", "-b", "feature")
    side = repo.commit("feat: side", {"b.txt": "1"})
    repo.git("checkout", "--quiet", "main")
    main_tip = repo.commit("feat: main", {"c.txt": "1"})
    repo.git("merge", "--quiet", "--no-ff", "-m", "Merge branch 'feature'", "feature")
    merge = git.log(repo.root)[0]
    assert merge.is_merge
    assert set(merge.parents) == {main_tip, side}


@pytest.mark.parametrize(
    ("value", "offset_hours"),
    [
        ("2026-11-19T08:53:23Z", 0),
        ("2026-11-19T08:53:23+00:00", 0),
        ("2026-11-19T11:53:23+03:00", 3),
    ],
)
def test_parse_date(value: str, offset_hours: int) -> None:
    parsed = git.parse_date(value)
    assert parsed.utcoffset() == timedelta(hours=offset_hours)
    assert parsed.astimezone(timezone.utc) == datetime(2026, 11, 19, 8, 53, 23, tzinfo=timezone.utc)


def test_log_utc_commit(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    # git prints UTC dates with a `Z` suffix; Python 3.10 cannot parse it natively.
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-11-19T08:53:23+0000")
    repo.commit("chore: utc")
    (commit,) = git.log(repo.root)
    assert commit.date == datetime(2026, 11, 19, 8, 53, 23, tzinfo=timezone.utc)


def test_log_max_count(repo: GitRepo) -> None:
    for i in range(3):
        repo.commit(f"chore: {i}")
    assert len(git.log(repo.root, max_count=2)) == 2
