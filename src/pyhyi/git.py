"""Thin wrapper around the git command line (no third-party dependencies)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pyhyi.errors import ComparisonBaseError, GitError

_BASE = ("git", "-c", "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8")


def run_git_bytes(args: list[str], cwd: Path, *, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            [*_BASE, *args], cwd=cwd, input=stdin, capture_output=True, check=False
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return proc.stdout


def run_git(args: list[str], cwd: Path, *, stdin: bytes | None = None) -> str:
    """Run git and return stdout decoded as UTF-8."""
    return run_git_bytes(args, cwd, stdin=stdin).decode("utf-8", "replace")


def comparison_base(root: Path, ref: str, *, rule: str = "test-weakening") -> str:
    if not ref.strip() or "\0" in ref:
        raise ComparisonBaseError(f"{rule}: base must name a Git revision")
    try:
        commit = run_git(["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], root)
    except GitError as exc:
        raise ComparisonBaseError(f"{rule}: cannot resolve base {ref!r} to a commit") from exc
    try:
        bases = run_git(["merge-base", "--all", commit.strip(), "HEAD"], root).splitlines()
    except GitError as exc:
        raise ComparisonBaseError(
            f"{rule}: no merge base for {ref!r} and HEAD; check refs and fetch history"
        ) from exc
    if len(bases) != 1:
        raise ComparisonBaseError(f"{rule}: expected a single merge base for {ref!r} and HEAD")
    return bases[0]


@dataclass(frozen=True)
class BlobChange:
    old_path: str
    new_path: str
    old_blob: str | None
    new_blob: str | None
    status: str


def blob_changes(root: Path, base: str, target: str | None = None) -> Iterator[BlobChange]:
    """Compare two trees (or a tree and the worktree), retaining regular-file blob IDs."""
    raw = run_git_bytes(
        [
            "diff",
            "--raw",
            "-z",
            "--no-abbrev",
            "-M",
            *(["--diff-filter=MR"] if target is None else []),
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            base,
            *([target] if target is not None else []),
            "--",
        ],
        root,
    )
    yield from _parse_blob_changes(raw)


def hook_blob_changes(root: Path, commit: str | None = None) -> Iterator[BlobChange]:
    """Read a commit's changes or the index prepared for the current commit."""
    options = ["--raw", "-z", "--no-renames", "--no-abbrev", "--ignore-submodules=all"]
    if commit is None:
        args = ["diff", "--cached", "--no-ext-diff", "--no-textconv", *options]
    else:
        args = ["diff-tree", "--root", "--no-commit-id", "-r", *options, commit]
    yield from _parse_blob_changes(run_git_bytes([*args, "--"], root))


def _parse_blob_changes(raw: bytes) -> Iterator[BlobChange]:
    fields = iter(raw.split(b"\0"))
    for header in fields:
        if not header:
            break
        old_mode, new_mode, old_blob, new_blob, status = header[1:].split()
        old = next(fields)
        new = next(fields) if status[:1] in {b"R", b"C"} else old
        try:
            yield BlobChange(
                old.decode(),
                new.decode(),
                old_blob.decode("ascii") if old_mode.startswith(b"100") else None,
                new_blob.decode("ascii") if new_mode.startswith(b"100") else None,
                chr(status[0]),
            )
        except UnicodeDecodeError:
            continue


def changed_blob_pairs(root: Path, base: str) -> Iterator[tuple[str, str, str]]:
    """Yield old path, current path and base blob ID for modified or renamed regular files."""
    for change in blob_changes(root, base):
        if change.status in {"M", "R"} and change.old_blob and change.new_blob:
            yield change.old_path, change.new_path, change.old_blob


def find_root(start: Path) -> Path:
    """Return the top-level directory of the work tree containing `start`."""
    try:
        out = run_git(["rev-parse", "--show-toplevel"], cwd=start)
    except GitError as exc:
        raise GitError(f"not inside a git work tree: {start}") from exc
    return Path(out.strip())


def list_files(root: Path, *, tracked_only: bool = False) -> list[str]:
    """Tracked files plus untracked files that are not ignored.

    Untracked files are included so that junk is caught before it is
    committed. Files deleted from disk but still in the index are skipped.
    """
    args = ["ls-files", "-z", "--cached"]
    if not tracked_only:
        args.extend(["--others", "--exclude-standard"])
    out = run_git(args, cwd=root)
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


def parse_date(value: str) -> datetime:
    """Parse git's strict ISO 8601 date (`%aI`).

    git writes UTC as `Z`, which `datetime.fromisoformat` accepts only since
    Python 3.11.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


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
                date=parse_date(date),
                subject=subject,
                body=body.strip(),
            )
        )
    return commits


_BLAME_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$")


def blame_author_times(root: Path, path: str) -> dict[int, int]:
    """Author time (Unix seconds) of every committed line of a file, by line number.

    Lines not committed yet are absent. Raises GitError for files git cannot
    blame, such as untracked ones.
    """
    out = run_git(["blame", "--line-porcelain", "--", path], cwd=root)
    times: dict[int, int] = {}
    sha = ""
    line_no = 0
    for row in out.splitlines():
        if row.startswith("\t"):
            continue
        header = _BLAME_HEADER.match(row)
        if header:
            sha, line_no = header[1], int(header[2])
        elif row.startswith("author-time ") and sha.strip("0"):
            times[line_no] = int(row.split(" ", 1)[1])
    return times


def file_history(root: Path) -> dict[str, list[tuple[str, int]]]:
    """Commit IDs and timestamps per path, with no guesses across renames or shallow boundaries."""
    if (
        not has_commits(root)
        or run_git(["rev-parse", "--is-shallow-repository"], root).strip() == "true"
    ):
        return {}
    raw = run_git(
        ["log", "--format=%x00%H %ct", "--name-only", "-z", "--no-renames", "--no-merges"],
        root,
    )
    records = re.split(r"(?:^|\0)\0([a-f0-9]{40,64}) ([0-9]+)\0", raw)[1:]
    history: dict[str, list[tuple[str, int]]] = {}
    for i in range(0, len(records), 3):
        sha, stamp, paths = records[i : i + 3]
        for path in paths.removeprefix("\n").split("\0"):
            if path:
                history.setdefault(path, []).append((sha, int(stamp)))
    return history


def renames(root: Path) -> dict[str, str]:
    """Map every path git saw renamed to the path it was finally renamed to."""
    if not has_commits(root):
        return {}
    out = run_git(["log", "--format=", "--name-status", "--diff-filter=R", "-M", "-z"], cwd=root)
    tokens = [t.strip("\n") for t in out.split("\0")]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i + 2 < len(tokens):
        if tokens[i].startswith("R"):
            pairs.append((tokens[i + 1], tokens[i + 2]))
            i += 3
        else:
            i += 1
    steps: dict[str, set[str]] = {}
    for old, new in pairs:
        steps.setdefault(old, set()).add(new)
    final: dict[str, str] = {}
    for old in steps:
        current = old
        seen: set[str] = set()
        while current in steps:
            if current in seen or len(steps[current]) != 1:
                break
            seen.add(current)
            current = next(iter(steps[current]))
        else:
            final[old] = current
    return final
