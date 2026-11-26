"""Print the CHANGELOG.md section of a version, the notes of its GitHub Release."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `## 0.4.0 - 2026-11-26`: the first word after the marker is the version.
_HEADING = re.compile(r"## (\S+)")


def section(changelog: str, version: str) -> str:
    """Body of the `## <version>` section, without its heading."""
    body: list[str] | None = None
    for line in changelog.splitlines():
        heading = _HEADING.match(line)
        if heading:
            if body is not None:
                break
            if heading[1] == version:
                body = []
        elif body is not None:
            body.append(line)
    if body is None:
        raise LookupError(f"CHANGELOG.md has no section for {version}")
    text = "\n".join(body).strip()
    if not text:
        raise LookupError(f"CHANGELOG.md section for {version} is empty")
    return text + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="version without the v prefix, e.g. 0.4.0")
    args = parser.parse_args(argv)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        print(section(changelog, args.version), end="")
    except LookupError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
