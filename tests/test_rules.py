"""Behaviour of the built-in hygiene rules on synthetic repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from pyhyi import engine
from pyhyi.config import parse_config
from pyhyi.errors import ConfigError
from pyhyi.registry import load_rules
from pyhyi.rules.commits import DEFAULT_TYPES, subject_problem
from pyhyi.rules.layout import is_known_root_file
from pyhyi.rules.links import link_targets, local_path

if TYPE_CHECKING:
    from conftest import GitRepo

OLD_DATE = "2020-01-01T12:00:00+0000"


def run_rule(repo: GitRepo, rule_id: str, **options: Any) -> engine.Result:
    config = parse_config({"rules": {rule_id: options}} if options else {})
    return engine.run(repo.root, config, load_rules(plugins=False), select=[rule_id])


def found(result: engine.Result) -> list[tuple[str | None, int | None]]:
    return [(f.path, f.line) for f in result.findings]


# --- commit-convention ------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "feat: add thing",
        "fix(parser): handle empty input",
        "feat!: drop Python 3.9",
        "refactor(core)!: rename module",
        "docs: исправить опечатку",
        'Revert "feat: add thing"',
    ],
)
def test_subject_ok(subject: str) -> None:
    assert subject_problem(subject, DEFAULT_TYPES) is None


@pytest.mark.parametrize(
    ("subject", "problem"),
    [
        ("init", "does not match"),
        ("Update README", "does not match"),
        ("feat:missing space", "does not match"),
        ("test, docs: two types", "does not match"),
        ("Feat: capitalised type", "unknown type 'Feat'"),
        ("data: import prices", "unknown type 'data'"),
        ("feat(): empty scope", "empty scope"),
        ("feat:  ", "empty description"),
        ("fixup! feat: add thing", "fixup/squash"),
        ("squash! fix: x", "fixup/squash"),
    ],
)
def test_subject_problem(subject: str, problem: str) -> None:
    assert problem in (subject_problem(subject, DEFAULT_TYPES) or "")


def test_commit_convention_on_history(repo: GitRepo) -> None:
    init = repo.commit("init", {"a.txt": "1"})
    repo.commit("feat: real work", {"a.txt": "2"})
    wip = repo.commit("WIP", {"a.txt": "3"})
    result = run_rule(repo, "commit-convention")
    assert {f.key for f in result.findings} == {init, wip}
    assert all(f.path is None and f.line is None for f in result.findings)


def test_commit_convention_skips_merges(repo: GitRepo) -> None:
    repo.commit("chore: base", {"a.txt": "1"})
    repo.git("checkout", "--quiet", "-b", "side")
    repo.commit("feat: side", {"b.txt": "1"})
    repo.git("checkout", "--quiet", "main")
    repo.commit("fix: main", {"c.txt": "1"})
    repo.git("merge", "--quiet", "--no-ff", "-m", "Merge branch 'side'", "side")
    assert run_rule(repo, "commit-convention").findings == []


def test_commit_convention_custom_types_and_since(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", OLD_DATE)
    repo.commit("old junk", {"a.txt": "1"})
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    repo.commit("data: import prices", {"a.txt": "2"})
    assert len(run_rule(repo, "commit-convention").findings) == 2
    assert run_rule(repo, "commit-convention", types=["data"], since="2021-01-01").findings == []


def test_commit_convention_bad_since(repo: GitRepo) -> None:
    repo.commit("feat: x")
    with pytest.raises(ConfigError, match="since"):
        run_rule(repo, "commit-convention", since="last week")


# --- root-clutter -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "readme",
        "LICENSE",
        "LICENSE-MIT",
        "CHANGELOG.rst",
        "CODE_OF_CONDUCT.md",
        "AGENTS.md",
        "pyproject.toml",
        "uv.lock",
        "requirements-dev.txt",
        "package.json",
        "tsconfig.node.json",
        "vite.config.ts",
        ".eslintrc.json",
        ".markdownlint.json",
        ".markdownlint.jsonc",
        "schema.sql",
        ".prettierrc",
        ".dockerignore",
        "Dockerfile",
        "docker-compose.yml",
        "Makefile",
        ".env.example",
        "main.py",
        "index.html",
        "go.mod",
        ".pyhyi-baseline.json",
    ],
)
def test_known_root_files(name: str) -> None:
    assert is_known_root_file(name)


@pytest.mark.parametrize(
    "name",
    [
        "notes.md",
        "PLAN.md",
        "IMPLEMENTATION_SUMMARY.md",
        "output.json",
        "debug.log",
        "data.csv",
        "dump.sql",
        "screenshot.png",
        "temp.txt",
        "backup.zip",
        ".env",
        "old_main.py.bak",
    ],
)
def test_clutter(name: str) -> None:
    assert not is_known_root_file(name)


def test_root_clutter_rule(repo: GitRepo) -> None:
    repo.commit(
        "chore: init",
        {"README.md": "", "report.json": "{}", "docs/notes.md": "", "keep.csv": ""},
    )
    repo.write("debug.log", "untracked but not ignored")
    result = run_rule(repo, "root-clutter")
    assert found(result) == [("debug.log", None), ("keep.csv", None), ("report.json", None)]
    allowed = run_rule(repo, "root-clutter", allow=["*.csv", "debug.log"])
    assert found(allowed) == [("report.json", None)]


# --- broken-doc-link --------------------------------------------------------------


def test_link_targets_skip_code() -> None:
    text = "\n".join(
        [
            "[a](a.md) and ![img](img/x.png 'title')",
            "`[not](code.md)` [b](<with space.md>)",
            "```",
            "[inside](fence.md)",
            "```",
            "[ref]: ref.md",
            '<img src="html.png"> <a href="page.md">x</a>',
        ]
    )
    assert list(link_targets(text)) == [
        (1, "a.md"),
        (1, "img/x.png"),
        (2, "with space.md"),
        (6, "ref.md"),
        (7, "html.png"),
        (7, "page.md"),
    ]


@pytest.mark.parametrize(
    ("target", "source", "expected"),
    [
        ("b.md", "docs/a.md", "docs/b.md"),
        ("../README.md", "docs/a.md", "README.md"),
        ("/src/x.py", "docs/a.md", "src/x.py"),
        ("sub/#section", "a.md", "sub"),
        ("with%20space.md", "a.md", "with space.md"),
        ("https://example.com/x.md", "a.md", None),
        ("mailto:me@example.com", "a.md", None),
        ("#anchor", "a.md", None),
        ("../../outside.md", "docs/a.md", None),
        ("{{ site.url }}/x", "a.md", None),
    ],
)
def test_local_path(target: str, source: str, expected: str | None) -> None:
    assert local_path(target, source) == expected


def test_broken_doc_link_rule(repo: GitRepo) -> None:
    repo.commit("docs: old name", {"docs/old.md": "x", "src/Module.py": ""})
    repo.git("mv", "docs/old.md", "docs/new.md")
    repo.commit("docs: rename")
    repo.write(".gitignore", "secret.md\n")
    repo.write("secret.md", "local only")
    repo.commit(
        "docs: links",
        {
            "README.md": "\n".join(
                [
                    "[ok](docs/new.md) [dir](src/) [anchor](docs/new.md#part) [web](https://x.y)",
                    "[gone](missing.md)",
                    "[moved](docs/old.md)",
                    "[case](src/module.py)",
                    "[ignored](secret.md)",
                ]
            )
        },
    )
    result = run_rule(repo, "broken-doc-link")
    assert found(result) == [("README.md", 2), ("README.md", 3), ("README.md", 4), ("README.md", 5)]
    gone, moved, case, ignored = result.findings
    assert gone.hint is None
    assert "renamed" in (moved.hint or "") and "docs/new.md" in (moved.hint or "")
    assert "src/Module.py" in (case.hint or "")
    assert "not tracked by git" in ignored.message


def test_link_lookalikes_do_not_report_broken_targets(repo: GitRepo) -> None:
    repo.commit(
        "docs: show link syntax",
        {
            "README.md": (
                "<!-- [draft](absent.md) -->\n\n"
                "`An example spans\n[example](absent.md)`\n\n"
                "A stray bracket ](absent.md) precedes [a real link](absent.md).\n"
            )
        },
    )
    result = run_rule(repo, "broken-doc-link")
    assert [(f.line, f.key) for f in result.findings] == [(6, "absent.md")]


def test_link_target_must_be_in_the_index(repo: GitRepo) -> None:
    repo.commit("docs: add links", {"README.md": "[file](docs/local.md)\n[dir](docs/)\n"})
    repo.write("docs/local.md", "draft")
    result = run_rule(repo, "broken-doc-link")
    assert found(result) == [("README.md", 1), ("README.md", 2)]
    assert all("not tracked by git" in f.message for f in result.findings)
    repo.git("add", "docs/local.md")
    result = run_rule(repo, "broken-doc-link")
    assert not result.errors and not result.findings


def test_link_with_parentheses(repo: GitRepo) -> None:
    repo.commit(
        "docs: link to editions",
        {
            "docs/guide(v2).md": "",
            "README.md": "[ok](docs/guide(v2).md)\n[gone](docs/guide(v1).md)\n",
        },
    )
    result = run_rule(repo, "broken-doc-link")
    assert [(f.line, f.key) for f in result.findings] == [(2, "docs/guide(v1).md")]


def test_indented_markdown_code(repo: GitRepo) -> None:
    repo.commit(
        "docs: add examples",
        {
            "README.md": (
                "Example:\n\n    [example](code.md)\n\n\t[more](sample.md)\n\n"
                "[ordinary](missing.md)\n    [paragraph continuation](also-missing.md)\n"
                "\n- A list item\n\n    [nested paragraph](list-missing.md)\n"
            )
        },
    )
    result = run_rule(repo, "broken-doc-link")
    assert [(f.line, f.key) for f in result.findings] == [
        (7, "missing.md"),
        (8, "also-missing.md"),
        (12, "list-missing.md"),
    ]


# --- stale-todo -------------------------------------------------------------------


def test_python_todo_in_strings(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", OLD_DATE)
    repo.commit(
        "feat: render markers",
        {
            "main.py": 's = "example # TODO printed text"\n'
            'doc = """\n# TODO inside a multiline string\n"""\n'
            'x = "example # TODO"  # FIXME: real comment\n'
        },
    )
    result = run_rule(repo, "stale-todo")
    assert [(f.line, f.message.split()[0]) for f in result.findings] == [(5, "FIXME")]


def test_stale_todo_rule(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", OLD_DATE)
    repo.commit(
        "chore: old",
        {
            "a.py": "x = 1  # TODO: old one\ns = '# TODO in a string'\n# NOTE: TODO later\n",
            "README.md": "# TODO list\n<!-- FIXME: old doc note -->\n",
            "b.js": "// XXX legacy\n// TODOS are not markers\n",
        },
    )
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    repo.commit("chore: new", {"c.py": "# TODO: fresh\n"})
    repo.write("d.py", "# FIXME: not committed\n")
    result = run_rule(repo, "stale-todo")
    assert found(result) == [("README.md", 2), ("a.py", 1), ("b.js", 1)]
    assert result.findings[1].message.startswith("TODO comment is ")
    assert result.findings[1].key == "TODO: x = 1  # TODO: old one"

    every_age = run_rule(repo, "stale-todo", **{"max-age-days": 0})
    assert ("c.py", 1) in found(every_age)
    assert ("d.py", 1) not in found(every_age)

    only_fixme = run_rule(repo, "stale-todo", markers=["FIXME"])
    assert found(only_fixme) == [("README.md", 2)]


def test_stale_todo_negative_age(repo: GitRepo) -> None:
    repo.commit("chore: x")
    with pytest.raises(ConfigError, match="max-age-days"):
        run_rule(repo, "stale-todo", **{"max-age-days": -1})
