"""stale-todo: TODO/FIXME/HACK/XXX comments must not linger."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Mapping
from typing import Any, ClassVar

from pyhyi.comments import comment_text
from pyhyi.context import Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding
from pyhyi.rules import Rule

DAY = 86400


class StaleTodo(Rule):
    id = "stale-todo"
    summary = (
        "Debt markers (TODO, FIXME, HACK, XXX) at the start of a comment must not be "
        "older than max-age-days, judged by git blame."
    )
    default_options: ClassVar[Mapping[str, Any]] = {
        "markers": ["TODO", "FIXME", "HACK", "XXX"],
        "max-age-days": 14,
    }

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options)
        if self.options["max-age-days"] < 0:
            raise ConfigError(f"rules.{self.id}.max-age-days must not be negative")
        markers = [re.escape(m) for m in self.options["markers"] if m]
        self._marker = re.compile(rf"\s*({'|'.join(markers)})(?!\w)") if markers else None

    def check(self, ctx: Context) -> Iterator[Finding]:
        if self._marker is None:
            return
        max_age = self.options["max-age-days"]
        now = time.time()
        for path in ctx.files:
            hits = []
            for number, line in enumerate(ctx.lines(path), start=1):
                text = comment_text(line, path)
                if text is None:
                    continue
                m = self._marker.match(text)
                if m:
                    hits.append((number, m[1], line.strip()))
            if not hits:
                continue
            written = ctx.blame_times(path)
            for number, marker, content in hits:
                if number not in written:  # not committed yet
                    continue
                age = int((now - written[number]) // DAY)
                if age < max_age:
                    continue
                yield self.finding(
                    f"{marker} comment is {age} days old",
                    path=path,
                    line=number,
                    key=f"{marker}: {content}",
                    hint="Do it, move it to the issue tracker, or delete it.",
                )
