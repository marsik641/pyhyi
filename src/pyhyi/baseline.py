"""Baseline: known findings that do not fail the check.

Legacy repositories start with hundreds of findings. `pyhyi baseline` records
them; afterwards only new findings fail, and the baseline can be shrunk as
issues get fixed. Entries are matched by (rule, path, key) with counts, so
moving code around inside a file does not invalidate them.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Iterable
from pathlib import Path

from pyhyi.errors import PyhyiError
from pyhyi.findings import Finding

VERSION = 1

Fingerprint = tuple[str, str | None, str]


def fingerprint(finding: Finding) -> Fingerprint:
    key = finding.key if finding.key is not None else finding.message
    return (finding.rule, finding.path, key)


def load(path: Path) -> Counter[Fingerprint] | None:
    """Read a baseline file; None if it does not exist."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PyhyiError(f"cannot read baseline {path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise PyhyiError(f"{path.name}: unsupported baseline format (expected version {VERSION})")
    counter: Counter[Fingerprint] = Counter()
    for entry in data.get("findings", []):
        try:
            fp = (str(entry["rule"]), entry["path"], str(entry["key"]))
            count = int(entry.get("count", 1))
            if count < 1:
                raise ValueError("count must be positive")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PyhyiError(f"{path.name}: malformed entry {entry!r}") from exc
        counter[fp] += count
    return counter


def dump(path: Path, findings: Iterable[Finding]) -> None:
    counter = Counter(fingerprint(f) for f in findings)
    entries = [
        {"rule": rule, "path": file, "key": key, "count": count}
        for (rule, file, key), count in sorted(
            counter.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
        )
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump({"version": VERSION, "findings": entries}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def apply(
    findings: Iterable[Finding],
    known: Counter[Fingerprint],
    active_rules: Collection[str],
) -> tuple[list[Finding], list[Finding], int]:
    """Split findings into (new, baselined) and count stale baseline entries.

    Only entries of rules that actually ran are counted as stale, so running a
    subset of rules does not make the rest of the baseline look fixed.
    """
    remaining = Counter(known)
    new: list[Finding] = []
    baselined: list[Finding] = []
    for finding in findings:
        fp = fingerprint(finding)
        if remaining[fp] > 0:
            remaining[fp] -= 1
            baselined.append(finding)
        else:
            new.append(finding)
    stale = sum(count for (rule, _, _), count in remaining.items() if rule in active_rules)
    return new, baselined, stale
