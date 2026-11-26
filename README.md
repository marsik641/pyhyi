# pyhyi

Repository hygiene checker. It catches the mess that autonomous coding agents
(and hurried humans) leave behind: junk in the repository root, broken doc
links, forgotten TODOs, commits that ignore the convention, dump folders and
stale docs.

> Status: pre-alpha.

## Idea

Strong models know the conventions. They just don't follow the implicit ones
unless someone enforces them. pyhyi turns those implicit expectations into
explicit, checkable rules, so a tool slaps the agent's hands instead of a human.

- **Language-agnostic core** — git history, file layout, text references between
  files. Works on any repository, not only Python ones.
- **Language adapters** — Python imports and literal JS/TypeScript module paths
  provide evidence that a local file has a consumer.
- **Precision over recall** — every finding should be worth reading. Suppressions
  require a reason; a baseline lets legacy repositories adopt it gradually.
- **Agent-friendly output** — JSON reports an agent can act on directly.

## Install

```sh
pip install pyhyi
# or run without installing
uvx pyhyi check
```

Requires Python 3.10+ and `git` on `PATH`.

## Usage

```sh ci=check
pyhyi check
```

```sh ci=json
pyhyi check --format json
```

```sh
pyhyi check path/to/repo
pyhyi check --select rule-a,rule-b --ignore rule-c
pyhyi baseline              # record current issues; only new ones fail afterwards
pyhyi fix --dry-run         # preview repairs of renamed Markdown link targets
```

```sh ci=rules
pyhyi rules
```

Exit codes: `0` no issues, `1` issues found, `2` usage, configuration or
internal error.

pyhyi scans tracked files plus untracked files that are not git-ignored, so
junk is caught before it is committed.

## Rules

Five rules run by default and fail the check:

| Rule | What it reports |
| --- | --- |
| `commit-convention` | Commit subjects that do not follow [Conventional Commits](https://www.conventionalcommits.org/). |
| `root-clutter` | Files in the repository root that are neither well-known project files nor source code: stray reports, dumps, logs, screenshots, a committed `.env`. |
| `broken-doc-link` | Relative Markdown links to paths git does not track, with a hint when the file was renamed or the letter case differs. |
| `stale-todo` | TODO, FIXME, HACK and XXX comments older than 14 days according to `git blame`. |
| `bad-suppression` | Inline suppressions without a reason or naming unknown rules. |

Five opt-in rules report advice for review. Advice alone leaves the exit
status at 0.

| Rule | What it reports |
| --- | --- |
| `dump-folder` | A directory that keeps receiving numbered dump, export, report or snapshot files, each written once. |
| `orphan-file` | An old module with a scratch, backup, obsolete or unused name and no detected local consumer. |
| `stale-doc` | A Python declaration quoted in Markdown, such as `LIMIT = 20`, that has since changed in the linked source. |
| `complexity-growth` | A Python function whose complexity grew in several separate commits. Needs Ruff. |
| `test-weakening` | Test changes that accept more: `== 120` became `> 0`, `ValueError` became `Exception`, a check was removed or replaced by `assert True`, a skip was added. |

```sh
pyhyi check --select dump-folder,orphan-file,stale-doc
pyhyi check --select test-weakening --base origin/main
```

Options, matching details and known limits of every rule are in
[docs/rules.md](https://github.com/marsik641/pyhyi/blob/main/docs/rules.md).
On an existing repository, start with `pyhyi baseline` so that only new issues fail.

## Integrations

- **Git hooks.** `pyhyi install-hooks` adds `commit-msg` and `pre-push` hooks.
  They check the commit subject and, with a `[doc-map]` from source paths to
  documents, ask for a documentation update when mapped sources change.
- **pre-commit and GitHub Actions.** The repository provides the pre-commit
  hooks `pyhyi` and `pyhyi-commit-msg` and the action `marsik641/pyhyi`.
- **pytest.** With the `pyhyi[pytest]` extra installed, `pytest --pyhyi` runs
  the check together with the tests and lists repository files the tests opened.

```sh ci=pytest
pytest --pyhyi
```

Setup and details are in
[docs/integrations.md](https://github.com/marsik641/pyhyi/blob/main/docs/integrations.md).

## Configuration

Settings live in the repository root, in the first file found of
`pyhyi.toml`, `.pyhyi.toml` (top-level keys) or `pyproject.toml`
(`[tool.pyhyi]` table). Non-Python projects do not need a `pyproject.toml`.

```toml
[tool.pyhyi]
select = ["rule-a", "rule-b"]   # default: every rule enabled by default
ignore = ["rule-c"]
exclude = ["vendor", "*.min.js", "docs/generated/**"]
baseline = ".pyhyi-baseline.json"

[tool.pyhyi.per-path-ignores]
"tests/fixtures/**" = ["rule-a"]

[tool.pyhyi.rules.rule-a]
some-option = 10
```

Unknown keys, unknown rule ids and options of the wrong type are errors, so
typos do not silently disable checks.

Path patterns use `/`. `*` matches within one path segment, `**` across
segments. A pattern without `/` matches a name at any depth (`node_modules`);
a pattern with `/` is anchored at the root (`docs/*.md`, `/build`). Matching a
directory covers everything inside it.

## Suppressing a finding

Put a marker in a comment on the reported line, naming the rule and the reason:

```python
data = load()  # pyhyi: ignore[rule-a] generated by the release script
```

```markdown
<!-- pyhyi: ignore[rule-a] link to an external mirror -->
```

The comment opener (`#`, `//`, `/*`, `<!--`, `--`, `;`, `%`, `{#`) must start
the line or follow whitespace. A suppression without a reason is not applied,
and the `bad-suppression` rule reports it together with markers that name
unknown rules. Findings without a line (commits, repository layout) are
silenced through `ignore` or `per-path-ignores` instead.

## Baseline

On an existing repository the first run may report hundreds of issues.
`pyhyi baseline` writes them to `.pyhyi-baseline.json`; `pyhyi check` then fails
only on new ones. Entries are matched by rule, file and a line-independent key,
so editing elsewhere in a file does not bring them back. When baselined issues
get fixed, `check` says so; run `pyhyi baseline` again to shrink the file.
Commit the baseline file.

## Writing rules

A rule is a class with a kebab-case `id`, a one-line `summary` and a `check`
method that yields findings:

```python
from pyhyi.rules import Rule


class NoTmpFiles(Rule):
    id = "no-tmp-files"
    summary = "Temporary files must not be in the repository."
    default_options = {"suffixes": [".tmp", ".bak"]}

    def check(self, ctx):
        for path in ctx.files:
            if path.endswith(tuple(self.options["suffixes"])):
                yield self.finding("Temporary file in the repository", path=path)
```

`ctx` gives read-only access to the repository: `files`, `exists()`,
`read_text()`, `lines()` and `commits`. Keep line numbers out of messages: the
message doubles as the baseline key unless `key=` is passed.

Third-party packages register rules through an entry point:

```toml
[project.entry-points."pyhyi.rules"]
no-tmp-files = "my_package.rules:NoTmpFiles"
```

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyhyi check
```

CI runs README fences marked `sh ci=<name>` with the built wheel and a temporary
consumer repository. Each marked fence contains one command. Its expected
output is checked by `scripts/check_readme_examples.py`.

Releases are described in [docs/releasing.md](https://github.com/marsik641/pyhyi/blob/main/docs/releasing.md).

## License

MIT
