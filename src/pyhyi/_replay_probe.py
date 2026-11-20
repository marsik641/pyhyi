"""Standalone observer executed with the target checkout on the import path."""

from __future__ import annotations

import hashlib
import json
import linecache
import sys
from pathlib import Path

import pytest


class Probe:
    def __init__(self, sources: list[str]) -> None:
        self.sources = {Path.cwd() / name: name for name in sources}
        self.calls: list[str] = []
        self.assertions: list[dict[str, str]] = []
        self.exceptions: list[dict[str, str]] = []
        self.other = False

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.other = True

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = (yield).get_result()
        if report.when == "call":
            self.calls.append(report.nodeid)
        if report.skipped or hasattr(report, "wasxfail"):
            self.other = True
            return
        if report.failed and (report.when != "call" or not self.record_failure(item, call.excinfo)):
            self.other = True

    def record_failure(self, item, exc) -> bool:
        if exc is None or not isinstance(exc.value, Exception):
            return False
        entry = next((e for e in reversed(exc.traceback) if Path(e.path) == item.path), None)
        if entry is None:
            return False
        failure = {
            "test": item.nodeid,
            "statement": linecache.getline(str(entry.path), entry.lineno + 1).strip(),
            "error": str(exc.value).replace(str(Path.cwd()), "<checkout>"),
        }
        if isinstance(exc.value, AssertionError):
            self.assertions.append(failure)
            return True
        origin = exc.traceback[-1]
        source = self.sources.get(Path(origin.path))
        if source is None:
            return False
        self.exceptions.append(
            {
                **failure,
                "type": f"{exc.type.__module__}.{exc.type.__qualname__}",
                "source": source,
                "origin": linecache.getline(str(origin.path), origin.lineno + 1).strip(),
            }
        )
        return True


def main() -> int:
    dest, selector, *sources = sys.argv[1:]
    probe = Probe(sources)
    code = pytest.main(
        [
            "-q",
            "--tb=short",
            "--color=no",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(Path(dest).parent / "tmp"),
            "--",
            selector,
        ],
        plugins=[probe],
    )
    # Pytest can emit several call reports for one unittest item with subtests.
    count = len(set(probe.calls))
    if not probe.other and count and code == 0 and not (probe.assertions or probe.exceptions):
        outcome = "passed"
    elif not probe.other and code == 1 and probe.exceptions:
        outcome = "failure"
    elif not probe.other and code == 1 and probe.assertions:
        outcome = "assertion"
    else:
        outcome = "inconclusive"
    dest_path = Path(dest)
    dest_path.write_text(
        json.dumps(
            {
                "outcome": outcome,
                "count": count,
                "selection": hashlib.sha256("\0".join(sorted(probe.calls)).encode()).hexdigest(),
                "assertions": probe.assertions,
                "exceptions": probe.exceptions,
            }
        ),
        encoding="utf-8",
    )
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
