"""Replay a production patch against the fixing commit and committed HEAD."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from pyhyi import git
from pyhyi.errors import GitError, PyhyiError


@dataclass
class Trial:
    phase: str
    outcome: str
    count: int = 0
    selection: str = ""
    assertions: list[dict[str, str]] = field(default_factory=list)
    detail: str = ""


@dataclass
class Replay:
    fix: str
    head: str
    sources: list[str]
    test: str
    current_test: str
    fixtures: list[str]
    hunk: int | None
    status: str = "inconclusive"
    reason: str = ""
    trials: list[Trial] = field(default_factory=list)


def _path(value: str) -> str:
    if (
        not value
        or PureWindowsPath(value).anchor
        or "\\" in value
        or ":" in value
        or "\0" in value
        or value.startswith("-")
        or any(p in {"", ".", "..", ".git"} for p in value.lower().split("/"))
    ):
        raise PyhyiError(f"expected a repository-relative Git path: {value!r}")
    return value


def _production(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        name.endswith(".py")
        and not name.startswith("test_")
        and not name.endswith("_test.py")
        and name != "conftest.py"
        and not {"test", "tests"}.intersection(parts[:-1])
    )


def _patch(root: Path, fix: str, sources: list[str], hunk: int | None) -> bytes:
    parent = fix + "^"
    for path in sources:
        _path(path)
        spec = ":(literal)" + path
        modes = git.run_git(["diff", "--no-renames", "--raw", parent, fix, "--", spec], root).split(
            maxsplit=5
        )
        if not (
            _production(path)
            and len(modes) == 6
            and modes[0] in {":100644", ":100755"}
            and modes[1] == modes[0][1:]
            and modes[4] == "M"
            and modes[2] != modes[3]
        ):
            raise PyhyiError(f"expected a modified regular production Python file: {path}")
    patch = git.run_git_bytes(
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--binary",
            "--full-index",
            "--unified=3",
            "--inter-hunk-context=0",
            "--diff-algorithm=myers",
            "--no-renames",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            parent,
            fix,
            "--",
            *(":(literal)" + path for path in sources),
        ],
        root,
    )
    if hunk is None:
        return patch
    chunks = re.split(rb"(?m)(?=^@@ )", patch)
    if len(sources) != 1 or not 1 <= hunk < len(chunks):
        raise PyhyiError("--hunk requires a single source and an existing one-based diff hunk")
    return chunks[0] + chunks[hunk]


def _pytest(copy: Path, phase: str, selector: str, timeout: int) -> Trial:
    scratch = copy.parent
    output = scratch / "probe.json"
    log = scratch / "pytest.log"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(map(str, [copy / "src", copy])),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    env.pop("PYTEST_ADDOPTS", None)
    # Keep imports in subprocesses started by tests on the same snapshot.
    probe = scratch / "probe.py"
    shutil.copyfile(Path(__file__).with_name("_replay_probe.py"), probe)
    with log.open("wb") as stream:
        try:
            proc = subprocess.run(
                [sys.executable, "-B", str(probe), str(output), selector],
                cwd=copy,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Trial(phase, "inconclusive", detail=f"pytest exceeded {timeout} seconds")
    trial = Trial(phase, "inconclusive")
    if output.is_file():
        trial = Trial(phase, **json.loads(output.read_text(encoding="utf-8")))
        if (proc.returncode, trial.outcome) in {(0, "passed"), (1, "assertion")}:
            return trial
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 1600))
        tail = stream.read().decode("utf-8", errors="replace").strip()
    trial.outcome = "inconclusive"
    trial.detail = f"pytest exit {proc.returncode}\n{tail}"
    return trial


def run(
    root: Path,
    ref: str,
    sources: list[str],
    test: str,
    current_test: str,
    timeout: int,
    fixtures: tuple[str, ...] = (),
    hunk: int | None = None,
) -> Replay:
    # Git inherits these even when cwd points at a scratch clone.
    if any(
        os.environ.get(key)
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
        )
    ):
        raise PyhyiError("run replay outside a Git command with repository override variables")
    witness, sep, name = test.partition("::")
    _path(witness)
    if not sep or not name or not witness.endswith(".py"):
        raise PyhyiError("--test must select one historical pytest node ID")
    if current_test != ".":
        _path(current_test.partition("::")[0])
    if timeout < 1 or not sources:
        raise PyhyiError("replay requires --source and a positive --timeout")
    fix = git.run_git(
        ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"], root
    ).strip()
    head = git.run_git(["rev-parse", "--verify", "HEAD"], root).strip()
    parents = git.run_git(["rev-list", "--parents", "-1", fix], root).split()
    if len(parents) != 2 or git.run_git(["merge-base", head, fix], root).strip() != fix:
        raise PyhyiError("--fix must be a single-parent ancestor of HEAD (inclusive)")
    patch = _patch(root, fix, sources, hunk)
    for path in fixtures:
        _path(path)
        if Path(path).name != "conftest.py" and not {"test", "tests"}.intersection(
            path.split("/")[:-1]
        ):
            raise PyhyiError(f"--fixture must be a conftest.py or a file under tests: {path}")
    witness_paths = [":(literal)" + p for p in (witness, *fixtures)]
    for p, spec in zip((witness, *fixtures), witness_paths, strict=True):
        entry = git.run_git(["ls-tree", fix, "--", spec], root)
        if p in sources or not entry.startswith(("100644 blob ", "100755 blob ")):
            raise PyhyiError(f"expected a separate historical test or fixture file: {p}")
    report = Replay(fix, head, sources, test, current_test, list(fixtures), hunk)

    def trial(phase: str, rev: str, selector: str, *, reverse=False, witness_file=False) -> Trial:
        with TemporaryDirectory(prefix="pyhyi-replay-") as tmp:
            copy = Path(tmp).resolve() / "repo"
            git.run_git(
                ["clone", "--quiet", "--shared", "--no-checkout", "--", str(root), str(copy)], root
            )
            git.run_git(["remote", "remove", "origin"], copy)
            git.run_git(["checkout", "--quiet", "--detach", rev], copy)
            if witness_file:
                git.run_git(["restore", "--source", fix, "--", *witness_paths], copy)
            if reverse:
                try:
                    git.run_git(
                        [
                            "apply",
                            "--reverse",
                            "--3way" if hunk is None else "--index",
                            "--whitespace=nowarn",
                            "-",
                        ],
                        copy,
                        stdin=patch,
                    )
                except GitError as exc:
                    result = Trial(phase, "inconclusive", detail=str(exc))
                    report.trials.append(result)
                    return result
            result = _pytest(copy, phase, selector, timeout)
            report.trials.append(result)
            return result

    def stop(reason: str, status: str = "inconclusive") -> Replay:
        report.status = status
        report.reason = reason
        return report

    old = trial("fix", fix, test)
    if old.outcome != "passed" or old.count != 1:
        return stop("The historical selector must pass exactly one case on the fix.")
    bad = trial("fix-reversed", fix, test, reverse=True)
    if bad.outcome != "assertion" or old.selection != bad.selection:
        return stop("Reversing the selected patch did not reproduce a historical test assertion.")
    current = trial("head", head, current_test)
    if current.outcome != "passed":
        return stop("The selected current tests did not all pass on clean HEAD.")
    changed = trial("head-reversed", head, current_test, reverse=True)
    if changed.selection != current.selection or changed.outcome == "inconclusive":
        return stop("Could not compare the same current cases with the patch reversed.")
    if changed.outcome == "assertion":
        if bad.assertions[0] in changed.assertions:
            return stop("The historical assertion still catches the reversed patch.", "caught")
        return stop("The reversed patch failed a different assertion; inspect its evidence.")

    # A green mutant may be unreachable on HEAD. Recheck the old witness in today's context.
    control = trial("head-witness", head, test, witness_file=True)
    if control.outcome != "passed" or control.selection != old.selection:
        return stop("The historical test module is incompatible with clean HEAD.")
    witness_bad = trial("head-reversed-witness", head, test, reverse=True, witness_file=True)
    if witness_bad.outcome != "assertion" or witness_bad.assertions != bad.assertions:
        return stop("The historical witness no longer demonstrates the same failure on HEAD.")
    return stop(
        "Current selection passed a defect still rejected by the historical test.", "survived"
    )


def candidates(root: Path) -> list[dict]:
    if not git.has_commits(root):
        return []
    raw = git.run_git(
        [
            "log",
            "--no-merges",
            "--full-history",
            "--format=%x00%H%x00%P%x00%s",
            "--name-status",
            "--no-renames",
            "--diff-filter=AM",
            "-z",
            "--",
            "*.py",
        ],
        root,
    )
    fields = iter(raw.split("\0"))
    next(fields)  # Leading record delimiter.
    fixes = []
    for sha in fields:
        parents, subject = next(fields), next(fields)
        sources = []
        tests = []
        while status := next(fields, ""):
            path = next(fields)
            if _production(path):
                if status.strip() == "M":
                    sources.append(path)
            else:
                tests.append(path)
        if parents and sources and (tests or re.match(r"^fix(?:\([^)]*\))?!?: ", subject)):
            fixes.append({"fix": sha, "subject": subject, "sources": sources, "tests": tests})
    return fixes


def command(root: Path, args: argparse.Namespace) -> int:
    if args.list:
        fixes = candidates(root)
        if args.format == "json":
            print(json.dumps(fixes, indent=2))
        else:
            for item in fixes:
                print(item["fix"][:12], item["subject"])
                print("  source:", ", ".join(item["sources"]))
                if item["tests"]:
                    print("  tests:", ", ".join(item["tests"]))
        return 0
    if not args.test:
        raise PyhyiError("replay requires --test with --fix")
    report = run(
        root,
        args.fix,
        args.source or [],
        args.test,
        args.current_test or args.test,
        args.timeout,
        tuple(args.fixture),
        args.hunk,
    )
    if args.format == "json":
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"{report.status}: {report.reason}")
        print(f"fix {report.fix} -> HEAD {report.head} | {', '.join(report.sources)}")
        if report.hunk:
            print(f"hunk {report.hunk}")
        print(f"historical {report.test} | current {report.current_test}")
        if report.fixtures:
            print(f"historical fixtures: {', '.join(report.fixtures)}")
        for t in report.trials:
            print(f"  {t.phase}: {t.outcome} ({t.count} cases)")
            for failure in t.assertions:
                print(f"    {failure['test']} | {failure['statement']} | {failure['error']}")
            if t.detail:
                print(t.detail)
    return {"caught": 0, "survived": 1, "inconclusive": 2}[report.status]
