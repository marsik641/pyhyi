# pyhyi

Repository hygiene checker. It catches the mess that autonomous coding agents
(and hurried humans) leave behind: dump folders, stale docs, broken doc links,
forgotten TODOs, junk in the repo root, commits that ignore the convention.

> Status: pre-alpha. Five rules so far; more are coming.

## Idea

Strong models know the conventions. They just don't follow the implicit ones
unless someone enforces them. pyhyi turns those implicit expectations into
explicit, checkable rules, so a tool slaps the agent's hands instead of a human.

- **Language-agnostic core** — git history, file layout, text references between
  files. Works on any repository, not only Python ones.
- **Thin language adapters** (planned) — precise dependency graphs where needed,
  built on existing tools rather than custom parsers.
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

```sh
pyhyi check                 # check the repository containing the current directory
pyhyi check path/to/repo --format json
pyhyi check --select rule-a,rule-b --ignore rule-c
pyhyi baseline              # record current issues; only new ones fail afterwards
pyhyi rules                 # list available rules
```

Exit codes: `0` no issues, `1` issues found, `2` usage, configuration or
internal error.

pyhyi scans tracked files plus untracked files that are not git-ignored, so
junk is caught before it is committed.

### Repair Markdown links (on main)

Run `pyhyi fix --dry-run` to preview each file, line and destination. Run
`pyhyi fix` to apply the edits. The command repairs case mismatches and
unambiguous Git renames, preserving titles, queries and fragments. It
respects the check's configured selection, ignores, suppressions and baseline.

### Run with pytest

Install the pytest extra in your project's test environment:

```sh
python -m pip install "pyhyi[pytest]>=0.2.0"
pytest --pyhyi
```

The plugin runs the same check as `pyhyi check` before collection, using
the Git repository containing pytest's root directory. Tests run even when
hygiene findings exist. New findings fail an otherwise successful run.
The terminal summary includes the findings, suppressions and baseline counts.
To enable this for every run in a project, append `--pyhyi` to pytest's
`addopts`.

The summary also lists repository-relative paths observed in Python's
`open` audit events during collection and test execution, including fixtures.
It lists each path once, using pyhyi's scannable files at session start.
The list covers attempted opens (including writes) in the pytest process
and participating pytest-xdist workers. Other subprocesses and native I/O
are outside its scope. A cached import may only open bytecode. A missing
entry therefore says nothing about whether a file is safe to delete.

## Rules

All rules are enabled by default; `pyhyi rules` lists them.

| Rule | What it reports | Options |
| --- | --- | --- |
| `commit-convention` | Commit subjects that do not follow [Conventional Commits](https://www.conventionalcommits.org/). Merge commits and git's own `Revert "..."` subjects are skipped; `fixup!`/`squash!` commits are reported. | `types` (default: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert), `since` (a date; older commits are skipped) |
| `root-clutter` | Files in the repository root other than well-known project files (README, LICENSE, CHANGELOG, manifests, lockfiles, tool configs, environment templates) and source code. Catches stray reports, dumps, logs, screenshots and committed `.env` files. | `allow` (extra glob patterns) |
| `broken-doc-link` | Relative links in Markdown that point to paths not tracked by git, with a hint when git history shows the file was renamed or the letter case differs. External URLs and code blocks are ignored. | — |
| `stale-todo` | TODO, FIXME, HACK and XXX at the start of a comment that are older than `max-age-days` according to `git blame`. Uncommitted lines are ignored; in Markdown only `<!-- -->` comments count. | `markers`, `max-age-days` (default: 14) |
| `bad-suppression` | Inline suppressions without a reason or naming unknown rules. | — |

On an existing repository, start with `pyhyi baseline` so that only new issues fail.

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

Releases are described in [docs/releasing.md](https://github.com/marsik641/pyhyi/blob/main/docs/releasing.md).

## License

MIT
