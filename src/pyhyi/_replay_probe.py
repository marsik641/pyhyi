"""Standalone observer executed with the target checkout on the import path."""

from __future__ import annotations

import hashlib
import json
import linecache
import sys
from pathlib import Path

import pytest


class Probe:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.assertions: list[dict[str, str]] = []
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
        elif report.failed:
            exc = call.excinfo
            if report.when == "call" and exc and isinstance(exc.value, AssertionError):
                for entry in reversed(exc.traceback):
                    if Path(entry.path) == item.path:
                        statement = linecache.getline(str(entry.path), entry.lineno + 1).strip()
                        self.assertions.append(
                            {
                                "test": report.nodeid,
                                "statement": statement,
                                "error": str(exc.value).replace(str(Path.cwd()), "<checkout>"),
                            }
                        )
                        return
            self.other = True


def main() -> int:
    dest, selector = sys.argv[1:]
    probe = Probe()
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
    count = len(probe.calls)
    if not probe.other and count and code == 0 and not probe.assertions:
        outcome = "passed"
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
            }
        ),
        encoding="utf-8",
    )
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
