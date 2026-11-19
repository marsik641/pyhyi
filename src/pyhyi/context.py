"""Read-only view of the repository shared by all rules during one run."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path, PurePosixPath

from pyhyi import git
from pyhyi.config import Config
from pyhyi.paths import PathMatcher
from pyhyi.suppressions import Suppression, scan

# Larger files are treated as non-text: rules almost never need them and
# reading generated multi-megabyte dumps would dominate run time.
MAX_TEXT_BYTES = 5_000_000


class Context:
    def __init__(
        self,
        root: Path,
        config: Config,
        known_rules: frozenset[str] = frozenset(),
    ) -> None:
        self.root = root
        self.config = config
        self.known_rules = known_rules
        self.excluded = PathMatcher(config.exclude)
        self.baseline_path = str(PurePosixPath(config.baseline))
        self._text: dict[str, str | None] = {}
        self._suppressions: dict[str, dict[int, Suppression]] = {}
        self._blame: dict[str, dict[int, int]] = {}

    # --- files -------------------------------------------------------------

    @cached_property
    def all_files(self) -> tuple[str, ...]:
        """Every file in the work tree that git knows about or would add."""
        return tuple(git.list_files(self.root))

    def is_excluded(self, path: str) -> bool:
        """Configured excludes, plus pyhyi's own baseline file."""
        return path == self.baseline_path or self.excluded.match(path)

    @cached_property
    def files(self) -> tuple[str, ...]:
        """Files rules should scan: `all_files` minus excluded ones."""
        return tuple(p for p in self.all_files if not self.is_excluded(p))

    @cached_property
    def _file_set(self) -> frozenset[str]:
        return frozenset(self.all_files)

    @cached_property
    def _dir_set(self) -> frozenset[str]:
        dirs: set[str] = set()
        for path in self.all_files:
            dirs.update(str(parent) for parent in PurePosixPath(path).parents)
        dirs.discard(".")
        return frozenset(dirs)

    def exists(self, path: str) -> bool:
        """Whether a repository-relative path is a file or directory in the repo.

        Excluded files still exist: `exclude` means "do not scan", not "absent".
        """
        norm = str(PurePosixPath(path))
        if norm == ".":
            return True
        return norm in self._file_set or norm in self._dir_set

    def read_text(self, path: str) -> str | None:
        """File content as text, or None for binary, non-UTF-8 or huge files."""
        if path not in self._text:
            self._text[path] = self._load_text(path)
        return self._text[path]

    def lines(self, path: str) -> list[str]:
        text = self.read_text(path)
        return [] if text is None else text.splitlines()

    def _load_text(self, path: str) -> str | None:
        full = self.root / path
        try:
            if full.stat().st_size > MAX_TEXT_BYTES:
                return None
            data = full.read_bytes()
        except OSError:
            return None
        if b"\0" in data[:8192]:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # --- suppressions ------------------------------------------------------

    def suppressions(self, path: str) -> dict[int, Suppression]:
        if path not in self._suppressions:
            text = self.read_text(path)
            self._suppressions[path] = {} if text is None else scan(text)
        return self._suppressions[path]

    # --- history -----------------------------------------------------------

    @cached_property
    def commits(self) -> tuple[git.Commit, ...]:
        """Commits reachable from HEAD, newest first."""
        return tuple(git.log(self.root))

    @cached_property
    def file_history(self) -> dict[str, list[tuple[str, int]]]:
        return git.file_history(self.root)

    @cached_property
    def referenced(self) -> set[str]:
        from pyhyi.references import referenced_paths

        return referenced_paths(self)

    @cached_property
    def changed(self) -> set[str]:
        if not git.has_commits(self.root):
            return set()
        return set(git.run_git(["diff", "--name-only", "-z", "HEAD", "--"], self.root).split("\0"))

    def blame_times(self, path: str) -> dict[int, int]:
        """Author time of each committed line; empty for untracked files."""
        if path not in self._blame:
            try:
                self._blame[path] = git.blame_author_times(self.root, path)
            except git.GitError:
                self._blame[path] = {}
        return self._blame[path]

    @cached_property
    def renames(self) -> dict[str, str]:
        """Old path -> final path, for every rename in the history."""
        return git.renames(self.root)
