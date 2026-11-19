"""Opt-in cleanup candidates supported by several independent signals."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from pathlib import PurePosixPath
from typing import Any, ClassVar

from pyhyi.context import Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding, Severity
from pyhyi.references import SOURCE_SUFFIXES
from pyhyi.rules import Rule
from pyhyi.rules.links import MARKDOWN_SUFFIXES, link_targets, local_path

_DAY = 86400
_ARTIFACT = re.compile(r"(?:dump|export|report|snapshot)[-_].*\d", re.I)
_SCRATCH = re.compile(r"(?:^|[_./-])(?:backup|obsolete|scratch|unused)(?:[_./-]|$)", re.I)
_DISCOVERED = {
    "tests",
    "test",
    "fixtures",
    "migrations",
    "examples",
    "scripts",
    "bin",
    "pages",
    "routes",
}


class ReviewRule(Rule):
    default_enabled = False
    severity = Severity.ADVICE

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options)
        for option, value in self.options.items():
            if value < 1:
                raise ConfigError(f"rules.{self.id}.{option} must be positive")


class DumpFolder(ReviewRule):
    id = "dump-folder"
    summary = "Review directories accumulating numbered, write-once artifacts."
    default_options: ClassVar = {"min-files": 5, "min-commits": 3}

    def check(self, ctx: Context) -> Iterator[Finding]:
        groups: dict[str, list[str]] = defaultdict(list)
        for path in ctx.files:
            file = PurePosixPath(path)
            if (
                not file.parent.name
                or _DISCOVERED.intersection(file.parts)
                or not _ARTIFACT.match(file.stem)
            ):
                continue
            if file.suffix.lower() not in {".csv", ".json", ".log", ".md", ".sql", ".txt", ".xml"}:
                continue
            if len(ctx.file_history.get(path, ())) == 1 and path not in ctx.changed:
                groups[str(file.parent)].append(path)
        for directory, paths in sorted(groups.items()):
            if len(paths) < self.options["min-files"]:
                continue
            commits = {ctx.file_history[p][0][0] for p in paths}
            if len(commits) < self.options["min-commits"]:
                continue
            prefix = directory + "/"
            if directory in ctx.referenced or any(p.startswith(prefix) for p in ctx.referenced):
                continue
            yield self.finding(
                f"Review {len(paths)} numbered artifacts added across {len(commits)} commits. "
                "Score 3/3: generated names, one change per file, repeated additions.",
                path=directory,
                key=directory,
                hint="Check retention and external consumers before archiving anything.",
            )


class StaleDoc(ReviewRule):
    id = "stale-doc"
    summary = "Review documents whose linked source files changed repeatedly after the document."
    default_options: ClassVar = {"min-days": 30, "min-commits": 3}

    def check(self, ctx: Context) -> Iterator[Finding]:
        for doc in ctx.files:
            if (
                not doc.lower().endswith(MARKDOWN_SUFFIXES)
                or not ctx.file_history.get(doc)
                or doc in ctx.changed
            ):
                continue
            last_doc = max(t for _, t in ctx.file_history[doc])
            seen: set[str] = set()
            for line, url in link_targets(ctx.read_text(doc) or ""):
                target = local_path(url, doc)
                if (
                    not target
                    or not target.endswith(SOURCE_SUFFIXES)
                    or target in seen
                    or not ctx.exists(target)
                ):
                    continue
                changes = [t for _, t in ctx.file_history.get(target, ()) if t > last_doc]
                if len(changes) < self.options["min-commits"]:
                    continue
                days = (max(changes) - last_doc) // _DAY
                if days < self.options["min-days"]:
                    continue
                seen.add(target)
                yield self.finding(
                    f"Review {target}: {len(changes)} later source changes over {days} days. "
                    "Score 2/2: change count and elapsed time exceed the thresholds.",
                    path=doc,
                    line=line,
                    key=target,
                    hint="Compare the linked code with the documented behavior.",
                )


class OrphanFile(ReviewRule):
    id = "orphan-file"
    summary = "Review old scratch or backup modules without a detected local consumer."
    default_options: ClassVar = {"min-days": 30}

    def check(self, ctx: Context) -> Iterator[Finding]:
        cutoff = time.time() - self.options["min-days"] * _DAY
        for path in ctx.files:
            file = PurePosixPath(path)
            if not path.endswith(SOURCE_SUFFIXES) or not _SCRATCH.search(path):
                continue
            if (
                _DISCOVERED.intersection(file.parts)
                or file.stem in {"__init__", "__main__", "conftest", "index", "main"}
                or file.name.startswith("test_")
            ):
                continue
            history = ctx.file_history.get(path)
            if (
                not history
                or max(t for _, t in history) > cutoff
                or path in ctx.changed
                or path in ctx.referenced
            ):
                continue
            yield self.finding(
                "Review this module. Score 3/3: scratch or backup name, old revision, "
                "no detected local reference.",
                path=path,
                key=path,
                hint="Check dynamic imports, framework discovery and external callers.",
            )
