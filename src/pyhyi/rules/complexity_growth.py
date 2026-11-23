"""Find repeated increases in Python function complexity across Git revisions."""

from __future__ import annotations

import ast
import io
import json
import re
import shutil
import subprocess
import tokenize
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar

from pyhyi import git
from pyhyi.context import MAX_TEXT_BYTES, Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding, Severity
from pyhyi.rules import Rule

_SCORE = re.compile(r"\(([0-9]+) > 0\)$")
_DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
Symbol = tuple[str, ...]


@dataclass(frozen=True)
class Function:
    line: int
    score: int


def _definitions(tree: ast.AST, scope: Symbol = ()) -> Iterator[tuple[Symbol, ast.AST]]:
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, _DEFINITIONS):
            name = (*scope, child.name)
            yield name, child
            yield from _definitions(child, name)
        else:
            yield from _definitions(child, scope)


def _functions(root: Path, ruff: str, blob: str) -> dict[Symbol, Function]:
    if int(git.run_git(["cat-file", "-s", blob], root)) > MAX_TEXT_BYTES:
        return {}
    data = git.run_git_bytes(["cat-file", "blob", blob], root)
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        source = data.decode(encoding)
        definitions = list(_definitions(ast.parse(source)))
    except (SyntaxError, UnicodeError, RecursionError):
        return {}
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for _, node in definitions
    ):
        return {}

    proc = subprocess.run(
        [
            ruff,
            "check",
            "--isolated",
            "--no-cache",
            "--ignore-noqa",
            "--select",
            "C901",
            "--config",
            "lint.mccabe.max-complexity=0",
            "--output-format",
            "json",
            "--stdin-filename",
            "source.py",
            "-",
        ],
        input=source.encode(),
        capture_output=True,
        cwd=root,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"Ruff failed: {proc.stderr.decode(errors='replace').strip()}")
    scores = {}
    for item in json.loads(proc.stdout):
        if item["code"] != "C901":
            return {}  # Ruff could not parse this revision.
        match = _SCORE.search(item["message"])
        if match is None:
            raise RuntimeError("Ruff returned an unsupported C901 diagnostic")
        scores[item["location"]["row"]] = int(match[1])

    counts = Counter(name for name, _ in definitions)
    return {
        name: Function(node.lineno, scores[node.lineno])
        for name, node in definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno in scores
        and all(counts[name[:depth]] == 1 for depth in range(1, len(name) + 1))
    }


def _python_path(ctx: Context, path: str) -> bool:
    return path.endswith(".py") and not ctx.is_excluded(path)


def _histories(ctx: Context, revisions: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Walk backwards and carry each HEAD path through unambiguous file renames."""
    paths = git.run_git_bytes(["ls-tree", "-r", "-z", "--name-only", revisions[0]], ctx.root)
    active = {
        path: path
        for path in paths.decode(errors="surrogateescape").split("\0")
        if _python_path(ctx, path)
    }
    histories: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rev, parent in pairwise(revisions):
        older_paths = {}
        for change in git.blob_changes(ctx.root, parent, rev):
            path = active.pop(change.new_path, None)
            if path is None or not change.new_blob:
                continue
            snapshots = histories[path]
            if snapshots:
                snapshots[-1] = (rev, change.new_blob)
            else:
                snapshots.append((rev, change.new_blob))
            if change.old_blob and _python_path(ctx, change.old_path):
                snapshots.append((parent, change.old_blob))
                older_paths[change.old_path] = path
        active.update(older_paths)
        if not active:
            break
    return histories


def _series(
    snapshots: list[tuple[str, dict[Symbol, Function]]], name: Symbol
) -> list[tuple[str, int]]:
    """Return the current run of increases, including its starting value."""
    growth: list[tuple[str, int]] = []
    for rev, functions in snapshots:
        function = functions.get(name)
        if function is None:
            growth.clear()
        elif not growth or function.score < growth[-1][1]:
            growth = [(rev, function.score)]
        elif function.score > growth[-1][1]:
            growth.append((rev, function.score))
    return growth


class ComplexityGrowth(Rule):
    id = "complexity-growth"
    summary = "Review repeated increases in Python function complexity across Git revisions."
    default_enabled = False
    severity = Severity.ADVICE
    default_options: ClassVar = {
        "base": "",
        "max-commits": 50,
        "min-increases": 3,
        "min-growth": 5,
        "min-complexity": 12,
    }

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options)
        for name in ("max-commits", "min-increases", "min-growth", "min-complexity"):
            if self.options[name] < 1:
                raise ConfigError(f"rules.{self.id}.{name} must be positive")

    def check(self, ctx: Context) -> Iterator[Finding]:
        ref = self.options["base"]
        base = None
        if ref or "base" in ctx.config.rules.get(self.id, {}):
            base = git.comparison_base(ctx.root, ref, rule=self.id)
        if not git.has_commits(ctx.root):
            return
        revisions = git.run_git(
            [
                "rev-list",
                "--first-parent",
                f"--max-count={self.options['max-commits'] + 1}",
                "HEAD",
            ],
            ctx.root,
        ).splitlines()
        min_increases = self.options["min-increases"]
        if len(revisions) <= min_increases:
            return
        base_blobs = {}
        if base is not None:
            base_blobs = {
                change.new_path: change.old_blob
                for change in git.blob_changes(ctx.root, base, revisions[0])
                if change.old_blob and change.new_blob and _python_path(ctx, change.old_path)
            }
            if not base_blobs:
                return

        ruff = shutil.which("ruff")

        @cache
        def functions(blob: str) -> dict[Symbol, Function]:
            if ruff is None:
                raise ConfigError("complexity-growth requires Ruff on PATH")
            return _functions(ctx.root, ruff, blob)

        for path, history in sorted(_histories(ctx, revisions).items()):
            if len(history) <= min_increases or (base is not None and path not in base_blobs):
                continue
            snapshots = [(rev, functions(blob)) for rev, blob in reversed(history)]
            prior = functions(base_blobs[path]) if base is not None else None
            for name, current in snapshots[-1][1].items():
                if current.score < self.options["min-complexity"]:
                    continue
                if prior is not None and (name not in prior or current.score <= prior[name].score):
                    continue
                growth = _series(snapshots, name)
                if (
                    len(growth) <= min_increases
                    or current.score - growth[0][1] < self.options["min-growth"]
                ):
                    continue
                qualified = ".".join(name)
                scores = " -> ".join(str(score) for _, score in growth)
                evidence = ", ".join(f"{rev[:12]} ({score})" for rev, score in growth)
                yield self.finding(
                    f"`{qualified}` McCabe complexity grew {scores}.",
                    path=path,
                    line=current.line,
                    key=qualified,
                    hint=f"Review the branches added across these revisions: {evidence}.",
                )
