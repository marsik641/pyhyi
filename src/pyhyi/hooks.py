"""Git hooks for the configured map from changed paths to documentation."""

from __future__ import annotations

import re
import shlex
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import suppress
from itertools import islice
from pathlib import Path

from pyhyi import git
from pyhyi.config import CONFIG_FILES, Config, load_config
from pyhyi.errors import GitError, PyhyiError
from pyhyi.paths import compile_pattern

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TRAILER = "Docs-Unchanged"


def _snapshot_configs(root: Path, revisions: Sequence[str]) -> Iterator[Config]:
    if not revisions:
        return
    queries = "".join(f"{revision}:{name}\n" for revision in revisions for name in CONFIG_FILES)
    objects = git.run_git(["cat-file", "--batch-check"], root, stdin=queries.encode()).splitlines()
    if len(objects) != len(revisions) * len(CONFIG_FILES):
        raise GitError("unexpected Git response while reading hook configuration")
    entries = iter(objects)
    blobs: dict[str, bytes] = {}

    def read(path: Path) -> bytes | None:
        entry = snapshot[path.name]
        if entry.endswith(" missing"):
            return None
        oid, kind, _ = entry.split()
        if kind != "blob":
            return None
        if oid not in blobs:
            blobs[oid] = git.run_git_bytes(["cat-file", "blob", oid], root)
        return blobs[oid]

    for _ in revisions:
        snapshot = dict(zip(CONFIG_FILES, islice(entries, len(CONFIG_FILES)), strict=True))
        yield load_config(root, read=read)


def merge_in_progress(root: Path) -> bool:
    merge_head = git.run_git(["rev-parse", "--git-path", "MERGE_HEAD"], root).rstrip("\n")
    return (root / merge_head).is_file()


def _missing_docs(
    root: Path, config: Config, changes: Iterable[git.BlobChange], message: bytes
) -> dict[tuple[str, ...], list[str]]:
    regular = [change for change in changes if change.old_blob or change.new_blob]
    updated = {
        change.new_path
        for change in regular
        if change.new_blob and change.new_blob != change.old_blob
    }
    rules = [(compile_pattern(pattern), docs) for pattern, docs in config.doc_map.items()]
    required = {}
    for change in regular:
        path = change.new_path
        alternatives = (doc for match, docs in rules if match.fullmatch(path) for doc in docs)
        docs = tuple(dict.fromkeys(alternatives))
        if docs and updated.isdisjoint(docs):
            required[path] = docs
    missing: dict[tuple[str, ...], list[str]] = defaultdict(list)
    if not required:
        return missing
    trailers = git.run_git(["interpret-trailers", "--parse", "--no-divider"], root, stdin=message)
    for line in trailers.splitlines():
        key, _, value = line.partition(":")
        doc, separator, reason = value.strip().partition(":")
        if key.casefold() == _TRAILER.casefold() and separator and reason.strip():
            updated.add(doc.strip())
    for path, docs in sorted(required.items()):
        if updated.isdisjoint(docs):
            missing[docs].append(path)
    return missing


def _report(missing: dict[tuple[str, ...], list[str]], commit: str | None = None) -> bool:
    if not missing:
        return True
    subject = f"outgoing commit {commit[:12]}" if commit else "the staged changes"
    print(f"doc-map: review documentation for {subject}:", file=sys.stderr)
    for docs, paths in missing.items():
        print(f"  {', '.join(map(repr, paths))} -> {' or '.join(map(repr, docs))}", file=sys.stderr)
    example = f"{_TRAILER}: {next(iter(missing))[0]}: <reason>"
    hint = example if commit else f"git commit --trailer {shlex.quote(example)} ..."
    print(
        "Include the documentation change in the commit, or record why it is unnecessary:\n"
        f"  {hint}",
        file=sys.stderr,
    )
    return False


def check_staged(root: Path, message: bytes) -> bool:
    config = next(_snapshot_configs(root, ("",)))
    return not config.doc_map or _report(
        _missing_docs(root, config, git.hook_blob_changes(root), message)
    )


def _branch_updates(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    for line in lines:
        fields = line.split()
        if len(fields) != 4 or not all(_OID.fullmatch(fields[i]) for i in (1, 3)):
            raise GitError("expected pre-push ref updates from Git on stdin")
        _, local, ref, remote = fields
        if ref.startswith("refs/heads/") and local.strip("0"):
            yield local, remote


def check_push(root: Path, remote_name: str, lines: Iterable[str]) -> bool:
    updates = list(_branch_updates(lines))
    tips = tuple(dict.fromkeys(local for local, _ in updates))
    configs = dict(zip(tips, _snapshot_configs(root, tips), strict=True))
    tracked_remote = False
    if any(configs[local].doc_map and not remote.strip("0") for local, remote in updates):
        # Git passes the destination itself as the name when pushing to a URL.
        tracked_remote = remote_name in git.run_git(["remote"], root).splitlines()
    outgoing: dict[str, None] = {}
    for local, remote in updates:
        if not configs[local].doc_map:
            continue
        exclusions = []
        if remote.strip("0"):
            exclusions = [f"^{remote}"]
        elif tracked_remote:
            exclusions = ["--not", f"--remotes={remote_name}"]
        commits = git.run_git(["rev-list", "--no-merges", local, *exclusions, "--"], root)
        outgoing.update(dict.fromkeys(commits.splitlines()))
    passed = True
    revisions = tuple(outgoing)
    for commit, config in zip(revisions, _snapshot_configs(root, revisions), strict=True):
        if config.doc_map:
            message = git.run_git_bytes(["show", "-s", "--format=%B", commit, "--"], root)
            missing = _missing_docs(root, config, git.hook_blob_changes(root, commit), message)
            if not _report(missing, commit):
                passed = False
    return passed


def install(root: Path) -> tuple[Path, ...]:
    hooks = (root / git.run_git(["rev-parse", "--git-path", "hooks"], root).rstrip("\n")).resolve()
    common = (root / git.run_git(["rev-parse", "--git-common-dir"], root).rstrip("\n")).resolve()
    if hooks != common / "hooks":
        raise PyhyiError("custom core.hooksPath. Add pyhyi to your hooks manually.")

    python = shlex.quote(Path(sys.executable).as_posix())
    scripts = {
        hooks / stage: f'#!/bin/sh\nexec {python} -I -m pyhyi {stage} "$1"\n'.encode()
        for stage in ("commit-msg", "pre-push")
    }
    pending = {}
    for path, contents in scripts.items():
        if not path.is_symlink() and not path.exists():
            pending[path] = contents
        elif path.is_symlink() or not path.is_file() or path.read_bytes() != contents:
            raise PyhyiError(f"hook already exists at {path}. Add pyhyi to it manually.")

    hooks.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, contents in pending.items():
            with path.open("xb") as stream:
                created.append(path)
                stream.write(contents)
            path.chmod(0o755)
    except OSError:
        for path in created:
            with suppress(OSError):
                path.unlink()
        raise
    return tuple(scripts)
