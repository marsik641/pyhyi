"""The unit of output: one finding reported by one rule."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One issue found in the repository.

    `path` is a repository-relative POSIX path, or None for repository-level
    findings (commits, layout as a whole). `line` is 1-based.

    `key` identifies the finding within its rule and path for the baseline.
    It must not depend on line numbers, so that unrelated edits do not
    resurrect baselined findings. When omitted, `message` is used, which is why
    messages must not contain line numbers either.
    """

    rule: str
    message: str
    path: str | None = None
    line: int | None = None
    key: str | None = None
    hint: str | None = None
    severity: Severity = Severity.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
        }

    def sort_key(self) -> tuple[str, int, str, str]:
        return (self.path or "", self.line or 0, self.rule, self.message)
