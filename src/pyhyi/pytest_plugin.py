"""Opt-in repository checks and file-open reporting for pytest."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter

    from pyhyi.engine import Result


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("pyhyi").addoption(
        "--pyhyi",
        action="store_true",
        help="Check repository hygiene and report repository files opened during pytest.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--pyhyi"):
        config.pluginmanager.register(_SessionCheck(), "pyhyi-session")


def _path_key(path: str) -> str:
    if os.name == "nt" and "~" in path:
        # Expand DOS short names used by some Windows temp directories.
        path = os.path.realpath(path)
    return os.path.normcase(path)


@cache
def _open_listeners() -> list[Callable[[str], None]]:
    # CPython audit hooks live until interpreter shutdown. Install one dispatcher
    # even if a host calls pytest.main() repeatedly. Only active sessions subscribe.
    listeners: list[Callable[[str], None]] = []

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event != "open" or not listeners or not isinstance(args[0], (str, bytes)):
            return
        try:
            path = _path_key(os.path.abspath(os.fsdecode(args[0])))
        except (OSError, ValueError):
            return  # e.g. a relative open after the working directory was removed
        for record in listeners:
            record(path)

    sys.addaudithook(audit)
    return listeners


class _SessionCheck:
    def __init__(self) -> None:
        self.status = pytest.ExitCode.OK
        self.result: Result | None = None
        self.opened: set[str] = set()
        self.unsubscribe: Callable[[], None] | None = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        # Keep the engine (and its dependencies) off the plain pytest startup path.
        from pyhyi import engine, git
        from pyhyi.config import load_config
        from pyhyi.context import Context
        from pyhyi.errors import PyhyiError
        from pyhyi.registry import load_rules

        try:
            root = git.find_root(session.config.rootpath)
            config = load_config(root)
            paths = Context(root, config).files
            if not hasattr(session.config, "workerinput"):
                result = engine.run(root, config, load_rules())
                self.result = result
                if result.errors:
                    self.status = pytest.ExitCode.INTERNAL_ERROR
                elif result.blocking:
                    self.status = pytest.ExitCode.TESTS_FAILED
        except (PyhyiError, OSError) as exc:
            raise pytest.UsageError(f"pyhyi: {exc}") from exc

        # Index only paths eligible for scanning at startup. The auditor keeps
        # neither file contents nor any paths from outside this snapshot.
        by_path = {_path_key(os.fspath(root / p)): p for p in paths}

        def record(absolute: str) -> None:
            if (relative := by_path.get(absolute)) is not None:
                self.opened.add(relative)

        listeners = _open_listeners()
        listeners.append(record)
        self.unsubscribe = lambda: listeners.remove(record)
        session.config.add_cleanup(self._stop_recording)

    def _stop_recording(self) -> None:
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self._stop_recording()
        if hasattr(session.config, "workeroutput"):
            session.config.workeroutput["pyhyi_opened"] = sorted(self.opened)
        elif session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = self.status

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:
        # pytest-xdist workers have separate interpreters and audit hooks.
        self.opened.update(getattr(node, "workeroutput", {}).get("pyhyi_opened", ()))

    def pytest_terminal_summary(self, terminalreporter: TerminalReporter) -> None:
        from pyhyi.report import render_text

        terminalreporter.write_sep("=", "pyhyi")
        if self.result is not None:
            # A recorded open supplies positive evidence for review candidates.
            self.result.findings = [
                f
                for f in self.result.findings
                if not (f.rule == "orphan-file" and f.path in self.opened)
                and not (
                    f.rule == "dump-folder"
                    and f.path
                    and any(p.startswith(f.path + "/") for p in self.opened)
                )
            ]
            for line in render_text(self.result).splitlines():
                terminalreporter.write_line(line)
            for error in self.result.errors:
                terminalreporter.write_line(f"Rule {error.rule} crashed:\n{error.traceback}")
        terminalreporter.write_line(f"Repository files with Python open events: {len(self.opened)}")
        for path in sorted(self.opened):
            terminalreporter.write_line(f"  {path}")
