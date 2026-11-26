"""commit-convention: commit subjects follow Conventional Commits."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import date
from typing import Any, ClassVar

from pyhyi import git
from pyhyi.context import Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding
from pyhyi.rules import Rule

DEFAULT_TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)

_SUBJECT = re.compile(r"(?P<type>[^\s(:!]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<desc>.*)\Z")
_AUTOSQUASH = ("fixup!", "squash!", "amend!")


def is_autosquash(subject: str) -> bool:
    """Whether `git rebase --autosquash` is meant to fold this commit into another."""
    return subject.startswith(_AUTOSQUASH)


def subject_problem(subject: str, types: tuple[str, ...]) -> str | None:
    """What is wrong with a commit subject, or None if it is fine."""
    if is_autosquash(subject):
        return "is a fixup/squash commit left in the history"
    if subject.startswith('Revert "'):  # git's own revert message
        return None
    m = _SUBJECT.match(subject)
    if m is None:
        return "does not match `type(scope): description`"
    if m["type"] not in types:
        return f"has unknown type {m['type']!r}"
    if m["scope"] is not None and not m["scope"].strip():
        return "has an empty scope"
    if not m["desc"].strip():
        return "has an empty description"
    return None


class CommitConvention(Rule):
    id = "commit-convention"
    summary = "Commit subjects must follow Conventional Commits (merge commits are skipped)."
    default_options: ClassVar[Mapping[str, Any]] = {
        "types": list(DEFAULT_TYPES),
        "since": "",
    }

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options)
        since = self.options["since"]
        try:
            self.since = date.fromisoformat(since) if since else None
        except ValueError as exc:
            raise ConfigError(
                f"rules.{self.id}.since: expected a date like 2026-01-31, got {since!r}"
            ) from exc

    def check(self, ctx: Context) -> Iterator[Finding]:
        types = tuple(self.options["types"])
        unpublished: set[str] | None = None
        for commit in ctx.commits:
            if commit.is_merge:
                continue
            if self.since is not None and commit.date.date() < self.since:
                continue
            problem = subject_problem(commit.subject, types)
            if problem is None:
                continue
            if is_autosquash(commit.subject):
                # Local fixups are work in progress until a remote-tracking ref has them.
                if unpublished is None:
                    unpublished = set(
                        git.run_git(
                            ["rev-list", "HEAD", "--not", "--remotes", "--"], ctx.root
                        ).split()
                    )
                if commit.sha in unpublished:
                    continue
            yield self.finding(
                f"Commit {commit.sha[:7]} {problem}: {commit.subject!r}",
                key=commit.sha,
                hint=f"Use `type(scope): description` with type one of: {', '.join(types)}.",
            )
