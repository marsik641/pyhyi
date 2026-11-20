"""Command-line entry point.

Exit codes: 0 - no issues, 1 - issues found, 2 - usage, configuration or
internal error.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from pyhyi import __version__, baseline, engine, git, report
from pyhyi.config import Config, load_config
from pyhyi.errors import PyhyiError
from pyhyi.registry import load_rules

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_ERROR = 2


def _id_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _add_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="any path inside the repository (default: current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pyhyi", description="Repository hygiene checker.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = sub.add_parser("check", help="check the repository and report issues")
    _add_path(check)
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument(
        "--select", type=_id_list, metavar="IDS", help="comma-separated rules to run instead"
    )
    check.add_argument(
        "--ignore", type=_id_list, default=(), metavar="IDS", help="comma-separated rules to skip"
    )
    check.add_argument("--no-baseline", action="store_true", help="report baselined issues as well")
    check.add_argument("--base", metavar="REF", help="compare test-weakening against a Git ref")

    replay = sub.add_parser(
        "replay", help="reintroduce a historical fix's defect in scratch clones"
    )
    _add_path(replay)
    action = replay.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="list candidate fix commits")
    action.add_argument("--fix", metavar="REF", help="single-parent fix reachable from HEAD")
    replay.add_argument(
        "--source", action="append", help="changed production .py file (repeatable)"
    )
    replay.add_argument("--hunk", type=int, help="replay one numbered diff hunk of a single file")
    replay.add_argument("--test", help="historical pytest node ID that demonstrates the defect")
    replay.add_argument(
        "--fixture",
        action="append",
        default=[],
        help="historical test support file to restore with the witness (repeatable)",
    )
    replay.add_argument("--current-test", help="current pytest node ID or test directory")
    replay.add_argument("--timeout", type=int, default=120, help="seconds per pytest invocation")
    replay.add_argument("--format", choices=("text", "json"), default="text")

    base = sub.add_parser(
        "baseline", help="record current issues so that only new ones fail the check"
    )
    _add_path(base)

    fix = sub.add_parser("fix", help="repair unambiguous Markdown links")
    _add_path(fix)
    fix.add_argument("--dry-run", action="store_true", help="preview changes")

    message = sub.add_parser("commit-msg", help="check a proposed Git commit message")
    message.add_argument("message_file", type=Path)
    sub.add_parser("rules", help="list available rules")
    return parser


def _open_repo(path_arg: str) -> tuple[Path, Config]:
    start = Path(path_arg).resolve()
    if start.is_file():
        start = start.parent
    if not start.is_dir():
        raise PyhyiError(f"path does not exist: {path_arg}")
    root = git.find_root(start)
    return root, load_config(root)


def _print_errors(result: engine.Result) -> None:
    for error in result.errors:
        print(f"pyhyi: rule {error.rule} crashed:\n{error.traceback}", file=sys.stderr)


def cmd_check(args: argparse.Namespace) -> int:
    root, config = _open_repo(args.path)
    if args.base is not None:
        options = {**config.rules.get("test-weakening", {}), "base": args.base}
        config = replace(config, rules={**config.rules, "test-weakening": options})
    result = engine.run(
        root,
        config,
        load_rules(),
        select=args.select,
        ignore=args.ignore,
        use_baseline=not args.no_baseline,
    )
    render = report.render_json if args.format == "json" else report.render_text
    sys.stdout.write(render(result))
    _print_errors(result)
    if result.errors:
        return EXIT_ERROR
    return EXIT_ISSUES if result.blocking else EXIT_OK


def cmd_baseline(args: argparse.Namespace) -> int:
    root, config = _open_repo(args.path)
    result = engine.run(root, config, load_rules(), use_baseline=False)
    if result.errors:
        _print_errors(result)
        print("pyhyi: baseline not written because some rules crashed", file=sys.stderr)
        return EXIT_ERROR
    baseline.dump(root / config.baseline, result.findings)
    issues = "1 issue" if len(result.findings) == 1 else f"{len(result.findings)} issues"
    print(f"Recorded {issues} in {config.baseline}.")
    return EXIT_OK


def cmd_fix(args: argparse.Namespace) -> int:
    from pyhyi.fix import fix_links

    root, config = _open_repo(args.path)
    count = fix_links(root, config, load_rules(), dry_run=args.dry_run)
    print(f"{count} link edit(s) {'available' if args.dry_run else 'applied'}.")
    return EXIT_OK


def cmd_commit_msg(args: argparse.Namespace) -> int:
    from pyhyi.rules import resolve_options
    from pyhyi.rules.commits import CommitConvention, subject_problem

    root, config = _open_repo(".")
    registry = load_rules()
    engine.validate_config(config, registry)
    if CommitConvention not in engine.select_rules(registry, config):
        return EXIT_OK
    merge_head = git.run_git(["rev-parse", "--git-path", "MERGE_HEAD"], root).strip()
    if (root / merge_head).is_file():
        return EXIT_OK
    message = git.run_git(
        ["stripspace", "--strip-comments"], root, stdin=args.message_file.read_bytes()
    )
    subject = message.partition("\n")[0]
    options = resolve_options(CommitConvention, config)
    problem = subject_problem(subject, tuple(options["types"]))
    if problem:
        print(f"commit-convention: proposed subject {problem}: {subject!r}", file=sys.stderr)
        return EXIT_ISSUES
    return EXIT_OK


def cmd_rules(args: argparse.Namespace) -> int:
    registry = load_rules()
    width = max(len(rule_id) for rule_id in registry)
    for rule_id, cls in registry.items():
        state = "" if cls.default_enabled else "  (off by default)"
        print(f"{rule_id:<{width}}  {cls.summary}{state}")
    return EXIT_OK


def cmd_replay(args: argparse.Namespace) -> int:
    from pyhyi.replay import command

    root, _ = _open_repo(args.path)
    return command(root, args)


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "check": cmd_check,
    "commit-msg": cmd_commit_msg,
    "fix": cmd_fix,
    "baseline": cmd_baseline,
    "rules": cmd_rules,
    "replay": cmd_replay,
}


def _tolerant_streams() -> None:
    # Never crash on characters the console encoding cannot represent.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _tolerant_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    try:
        return COMMANDS[args.command](args)
    except (PyhyiError, OSError) as exc:
        print(f"pyhyi: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
