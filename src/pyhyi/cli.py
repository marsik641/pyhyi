"""Command-line entry point.

Exit codes: 0 - no issues, 1 - issues found, 2 - usage, configuration or
internal error.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from pyhyi import __version__, baseline, engine, git, hooks, report
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
    check.add_argument(
        "--base",
        metavar="REF",
        help="compare test weakening and complexity growth against a Git ref",
    )

    base = sub.add_parser(
        "baseline", help="record current issues so that only new ones fail the check"
    )
    _add_path(base)

    fix = sub.add_parser("fix", help="repair unambiguous Markdown links")
    _add_path(fix)
    fix.add_argument("--dry-run", action="store_true", help="preview changes")

    message = sub.add_parser("commit-msg", help="check a proposed Git commit")
    message.add_argument("message_file", type=Path)
    push = sub.add_parser("pre-push", help="check documentation for outgoing Git commits")
    push.add_argument("remote", help="Git remote name. Ref updates are read from stdin.")
    install = sub.add_parser("install-hooks", help="install local commit-msg and pre-push hooks")
    _add_path(install)
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
        options = dict(config.rules)
        for rule in ("test-weakening", "complexity-growth"):
            options[rule] = {**options.get(rule, {}), "base": args.base}
        config = replace(config, rules=options)
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
    from pyhyi.rules.commits import CommitConvention, is_autosquash, subject_problem

    root, config = _open_repo(".")
    registry = load_rules()
    engine.validate_config(config, registry)
    if hooks.merge_in_progress(root):
        return EXIT_OK
    raw_message = args.message_file.read_bytes()
    message = git.run_git_bytes(["stripspace", "--strip-comments"], root, stdin=raw_message)
    if CommitConvention in engine.select_rules(registry, config):
        subject = message.decode("utf-8", "replace").partition("\n")[0]
        options = resolve_options(CommitConvention, config)
        # `git commit --fixup` is fine locally; `check` reports it once published.
        problem = (
            None if is_autosquash(subject) else subject_problem(subject, tuple(options["types"]))
        )
        if problem:
            print(f"commit-convention: proposed subject {problem}: {subject!r}", file=sys.stderr)
            return EXIT_ISSUES
    return EXIT_OK if hooks.check_staged(root, message) else EXIT_ISSUES


def cmd_pre_push(args: argparse.Namespace) -> int:
    root = git.find_root(Path.cwd())
    return EXIT_OK if hooks.check_push(root, args.remote, sys.stdin) else EXIT_ISSUES


def cmd_install_hooks(args: argparse.Namespace) -> int:
    root, _ = _open_repo(args.path)
    for path in hooks.install(root):
        print(f"Git hook ready: {path}")
    return EXIT_OK


def cmd_rules(args: argparse.Namespace) -> int:
    registry = load_rules()
    width = max(len(rule_id) for rule_id in registry)
    for rule_id, cls in registry.items():
        state = "" if cls.default_enabled else "  (off by default)"
        print(f"{rule_id:<{width}}  {cls.summary}{state}")
    return EXIT_OK


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "check": cmd_check,
    "commit-msg": cmd_commit_msg,
    "pre-push": cmd_pre_push,
    "install-hooks": cmd_install_hooks,
    "fix": cmd_fix,
    "baseline": cmd_baseline,
    "rules": cmd_rules,
}


def _configure_streams() -> None:
    # Git, CI and pre-commit read the output as UTF-8, but Python on Windows writes
    # pipes in the ANSI code page, and the hooks run with -I, which ignores
    # PYTHONUTF8. An explicit PYTHONIOENCODING still wins.
    explicit = (
        not sys.flags.ignore_environment
        and os.environ.get("PYTHONIOENCODING", "").partition(":")[0].strip()
    )
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                # Never crash on characters the stream cannot represent.
                reconfigure(encoding=None if explicit else "utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
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
