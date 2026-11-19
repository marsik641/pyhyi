from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from pyhyi import baseline, engine
from pyhyi.config import Config, parse_config
from pyhyi.context import Context
from pyhyi.errors import ConfigError
from pyhyi.findings import Finding
from pyhyi.rules import Rule
from pyhyi.rules.suppression import BadSuppression

if TYPE_CHECKING:
    from conftest import GitRepo


class WordRule(Rule):
    """Flags every line containing a word (default: 'smell')."""

    id = "word"
    summary = "Test rule."
    default_options: ClassVar[dict[str, Any]] = {"word": "smell", "limit": 100}

    def check(self, ctx: Context) -> Iterator[Finding]:
        word = self.options["word"]
        for path in ctx.files:
            for number, line in enumerate(ctx.lines(path), start=1):
                if word in line:
                    yield self.finding(f"Found {word!r}", path=path, line=number)


class RepoRule(Rule):
    id = "repo-level"
    summary = "Always reports one repository-level finding."

    def check(self, ctx: Context) -> Iterator[Finding]:
        yield self.finding(f"{len(ctx.commits)} commits")


class OptInRule(Rule):
    id = "opt-in"
    summary = "Disabled unless selected."
    default_enabled = False

    def check(self, ctx: Context) -> Iterator[Finding]:
        yield self.finding("opted in")


class CrashingRule(Rule):
    id = "crash"
    summary = "Raises."

    def check(self, ctx: Context) -> Iterator[Finding]:
        raise RuntimeError("boom")


REGISTRY: dict[str, type[Rule]] = {
    cls.id: cls for cls in (WordRule, RepoRule, OptInRule, BadSuppression)
}


def run(repo: GitRepo, config: Config | None = None, **kw) -> engine.Result:
    return engine.run(repo.root, config or Config(), REGISTRY, **kw)


def ids(result: engine.Result) -> list[tuple[str, str | None, int | None]]:
    return [(f.rule, f.path, f.line) for f in result.findings]


def test_runs_default_rules_and_sorts(repo: GitRepo) -> None:
    repo.commit("init", {"b.txt": "smell\n", "a.txt": "ok\nsmell\n"})
    result = run(repo)
    assert ids(result) == [
        ("repo-level", None, None),
        ("word", "a.txt", 2),
        ("word", "b.txt", 1),
    ]
    assert result.rules == ("bad-suppression", "repo-level", "word")


def test_select_and_ignore(repo: GitRepo) -> None:
    repo.commit("init", {"a.txt": "smell\n"})
    assert ids(run(repo, select=["opt-in"])) == [("opt-in", None, None)]
    assert [r for r, _, _ in ids(run(repo, ignore=["repo-level"]))] == ["word"]
    config = parse_config({"select": ["word", "opt-in"], "ignore": ["opt-in"]})
    assert [r for r, _, _ in ids(run(repo, config))] == ["word"]


def test_unknown_rule_ids_are_errors(repo: GitRepo) -> None:
    with pytest.raises(ConfigError, match="--select: unknown rule"):
        run(repo, select=["nope"])
    with pytest.raises(ConfigError, match="ignore: unknown rule"):
        run(repo, parse_config({"ignore": ["nope"]}))
    with pytest.raises(ConfigError, match="per-path-ignores"):
        run(repo, parse_config({"per-path-ignores": {"x": ["nope"]}}))
    with pytest.raises(ConfigError, match="rules: unknown rule"):
        run(repo, parse_config({"rules": {"nope": {}}}))


def test_rule_options(repo: GitRepo) -> None:
    repo.commit("init", {"a.txt": "smell\nstink\n"})
    config = parse_config({"rules": {"word": {"word": "stink"}}})
    assert ids(run(repo, config, select=["word"])) == [("word", "a.txt", 2)]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"wrod": "x"}, "unknown option"),
        ({"word": 1}, "expected str, got int"),
        ({"limit": True}, "expected int, got bool"),
        ({"limit": "5"}, "expected int, got str"),
    ],
)
def test_invalid_rule_options(repo: GitRepo, options: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        run(repo, parse_config({"rules": {"word": options}}))


def test_exclude_and_per_path_ignores(repo: GitRepo) -> None:
    repo.commit(
        "init",
        {"vendor/lib.txt": "smell\n", "tests/t.txt": "smell\n", "src/s.txt": "smell\n"},
    )
    config = parse_config({"exclude": ["vendor"], "per-path-ignores": {"tests/**": ["word"]}})
    assert ids(run(repo, config, select=["word"])) == [("word", "src/s.txt", 1)]


def test_inline_suppression(repo: GitRepo) -> None:
    repo.commit(
        "init",
        {
            "a.txt": "\n".join(
                [
                    "smell  # pyhyi: ignore[word] intentional sample",
                    "smell  # pyhyi: ignore[word]",
                    "smell  # pyhyi: ignore[other-rule] wrong rule",
                    "smell  # pyhyi: ignore[] nothing named",
                    "smell",
                ]
            )
        },
    )
    result = run(repo, select=["word", "bad-suppression"])
    assert [f.line for f in result.suppressed] == [1]
    assert ids(result) == [
        ("bad-suppression", "a.txt", 2),  # no reason
        ("word", "a.txt", 2),  # ...so it is not applied
        ("bad-suppression", "a.txt", 3),  # unknown rule
        ("word", "a.txt", 3),
        ("bad-suppression", "a.txt", 4),  # names no rules
        ("word", "a.txt", 4),
        ("word", "a.txt", 5),
    ]


def test_crashing_rule_is_reported_and_others_run(repo: GitRepo) -> None:
    repo.commit("init", {"a.txt": "smell\n"})
    registry = {**REGISTRY, "crash": CrashingRule}
    result = engine.run(repo.root, Config(), registry, select=["crash", "word"])
    assert [e.rule for e in result.errors] == ["crash"]
    assert "RuntimeError: boom" in result.errors[0].traceback
    assert ids(result) == [("word", "a.txt", 1)]


def test_baseline_filters_known_findings(repo: GitRepo) -> None:
    repo.commit("init", {"a.txt": "smell\nsmell\n", "b.txt": "smell\n"})
    first = run(repo, select=["word"])
    baseline.dump(repo.root / Config().baseline, first.findings)

    # one fixed in b.txt, one new in c.txt, a.txt lines moved
    (repo.root / "b.txt").write_text("fine\n", encoding="utf-8")
    repo.write("a.txt", "header\nsmell\nmiddle\nsmell\n")
    repo.write("c.txt", "smell\n")
    result = run(repo, select=["word"])
    assert ids(result) == [("word", "c.txt", 1)]
    assert len(result.baselined) == 2
    assert result.stale_baseline == 1

    assert len(run(repo, select=["word"], use_baseline=False).findings) == 3


def test_custom_baseline_path(repo: GitRepo) -> None:
    repo.commit("init", {"a.txt": "smell\n"})
    config = parse_config({"baseline": "ci/known.json"})
    baseline.dump(repo.root / "ci" / "known.json", run(repo, config, select=["word"]).findings)
    assert run(repo, config, select=["word"]).findings == []
