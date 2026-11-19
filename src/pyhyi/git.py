"""Thin wrapper around the git command line (no third-party dependencies)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pyhyi.errors import GitError

_BASE = ("git", "-c", "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8")


def run_git(args: list[str], cwd: Path) -> str:
    """Run git and return stdout decoded as UTF-8."""
    try:
        proc = subprocess.run([*_BASE, *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return proc.stdout.decode("utf-8", "replace")


def find_root(start: Path) -> Path:
    """Return the top-level directory of the work tree containing `start`."""
    try:
        out = run_git(["rev-parse", "--show-toplevel"], cwd=start)
    except GitError as exc:
        raise GitError(f"not inside a git work tree: {start}") from exc
    return Path(out.strip())


def list_files(root: Path) -> list[str]:
    """Tracked files plus untracked files that are not ignored.

    Untracked files are included so that junk is caught before it is
    committed. Files deleted from disk but still in the index are skipped.
    """
    out = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
    paths = dict.fromkeys(p for p in out.split("\0") if p)
    return sorted(p for p in paths if (root / p).is_file())


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    author: str
    date: datetime
    subject: str
    body: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


# %x1f separates fields, %x1e terminates records; neither occurs in normal text.
_LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%aI%x1f%s%x1f%b%x1e"


def has_commits(root: Path) -> bool:
    try:
        run_git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=root)
    except GitError:
        return False
    return True


def log(root: Path, *, max_count: int | None = None) -> list[Commit]:
    """Commits reachable from HEAD, newest first. Empty for a fresh repository."""
    if not has_commits(root):
        return []
    args = ["log", f"--format={_LOG_FORMAT}"]
    if max_count is not None:
        args.append(f"--max-count={max_count}")
    out = run_git(args, cwd=root)
    commits = []
    for record in out.split("\x1e"):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, parents, author, date, subject, body = record.split("\x1f", 5)
        commits.append(
            Commit(
                sha=sha,
                parents=tuple(parents.split()),
                author=author,
                date=datetime.fromisoformat(date),
                subject=subject,
                body=body.strip(),
            )
        )
    return commits
