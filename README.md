# pyhyi

Repository hygiene checker. It catches the mess that autonomous coding agents
(and hurried humans) leave behind: dump folders, stale docs, broken doc links,
forgotten TODOs, junk in the repo root, commits that ignore the convention.

> Status: pre-alpha. Five default checks and four opt-in review rules on the main branch.

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

### pre-commit and GitHub Actions (on main)

Add both hooks to `.pre-commit-config.yaml`. Set `rev` to a commit containing
the hooks. Install them with `pre-commit install -t pre-commit -t commit-msg`.

```yaml
repos:
  - repo: https://github.com/marsik641/pyhyi
    rev: <commit-sha>
    hooks:
      - id: pyhyi
      - id: pyhyi-commit-msg
```

In a GitHub Actions job, check out the consumer repository with history and
run the action from the pyhyi revision you want to use:

```yaml
steps:
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0
  - uses: marsik641/pyhyi@<commit-sha>
```

### Run with pytest

Install the pytest extra in your project's test environment:

```sh
python -m pip install "pyhyi[pytest]>=0.2.0"
pytest --pyhyi
```

The plugin runs the same check as `pyhyi check` before collection, using
the Git repository containing pytest's root directory. Tests run even when
hygiene findings exist. New blocking findings fail an otherwise successful run.
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

The five checks below are enabled by default. Run `pyhyi rules` to see all available rules.

| Rule | What it reports | Options |
| --- | --- | --- |
| `commit-convention` | Commit subjects that do not follow [Conventional Commits](https://www.conventionalcommits.org/). Merge commits and git's own `Revert "..."` subjects are skipped; `fixup!`/`squash!` commits are reported. | `types` (default: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert), `since` (a date; older commits are skipped) |
| `root-clutter` | Files in the repository root other than well-known project files (README, LICENSE, CHANGELOG, manifests, lockfiles, tool configs, environment templates) and source code. Catches stray reports, dumps, logs, screenshots and committed `.env` files. | `allow` (extra glob patterns) |
| `broken-doc-link` | Relative links in Markdown that point to paths not tracked by git, with a hint when git history shows the file was renamed or the letter case differs. External URLs and code blocks are ignored. | — |
| `stale-todo` | TODO, FIXME, HACK and XXX at the start of a comment that are older than `max-age-days` according to `git blame`. Uncommitted lines are ignored; in Markdown only `<!-- -->` comments count. | `markers`, `max-age-days` (default: 14) |
| `bad-suppression` | Inline suppressions without a reason or naming unknown rules. | — |

On an existing repository, start with `pyhyi baseline` so that only new issues fail.

### Cleanup candidates (on main)

Run `pyhyi check --select dump-folder,stale-doc,orphan-file` to get a review
list. These opt-in rules report their evidence and leave the exit status
successful. They use committed history and skip shallow clones.

| Rule | Why a candidate appears | Options |
| --- | --- | --- |
| `dump-folder` | A directory accumulates numbered dump, export, report or snapshot files, each changed once, across several commits. A detected reference to the directory or its contents clears the candidate. | `min-files` (5), `min-commits` (3) |
| `stale-doc` | A source file linked by a Markdown document changed repeatedly after the document's last committed edit. | `min-days` (30), `min-commits` (3) |
| `orphan-file` | An old Python or JS/TS module has a scratch, backup, obsolete or unused name and no detected local consumer. | `min-days` (30) |

The reference scan for orphan files and dump folders uses Python's AST, literal
JS/TS paths, Markdown links and manifest strings. A path in a Markdown code
span also counts when it identifies a single repository file relative to the
document or repository root. Dynamic imports and external callers need human review.
With these rules enabled in configuration, `pytest --pyhyi` removes orphan
and dump candidates observed during that run from its final report.

### Review changes to Python tests

```sh
pyhyi check --select test-weakening
pyhyi check --select test-weakening --base origin/main
```

The first command reviews current test files, including eligible untracked files.
It reports unconditional pytest skip decorators, direct `assert True` statements
and broad exception expectations such as `with self.assertRaises(Exception):`.
A broad expectation is a review candidate even when the test makes other checks.
Each finding is advice and on its own leaves the exit code at 0.

The second command compares changed tracked files in the worktree with the
merge base of `origin/main` and `HEAD`. On a clean PR checkout this covers the
net PR change. Git pairs file renames. Both paths must pass the configured
excludes. Within each pair the rule matches
unique test names, including the class name for methods.

| Before | After | What the change admits |
| --- | --- | --- |
| `assert total == 120` | `assert total > 0` | An integer such as `1` |
| `assert status in {200, 201}` | `assert status in {200, 201, 404}` | `404` |
| `assert ready and valid` | `assert ready` | A ready result that fails validation |
| `with pytest.raises(ValueError)` | `with pytest.raises(Exception)` | Exceptions of other types from the same operation |

The comparator looks for a strict widening over a bounded model of built-in
values. It checks integer bounds, literal equality and membership, truth tests,
and combinations with `and`, `or` and `not`. It reports a concrete assignment
accepted by the new predicate. Merely changing `== 120` to `== 100` changes
the contract in both directions and produces no widening finding. Recognized
`unittest` assertions such as `self.assertEqual` and `self.assertIsNotNone`
participate in the same comparison. The rule can also report a deleted direct
check, a broader built-in exception type, or a removed message requirement.
Comparisons cover matched statements and unchanged enclosing control flow.
The example is evidence about the assertion itself. Fixtures, other tests,
domain requirements and custom Python operators may affect its significance.

The rule also reports a newly
added unconditional skip or a single assertion replaced in place by
`assert True` when the rest of the test body has the same syntax tree.
The earlier assertion must reference a name in its predicate. For an ordinary
`unittest` instance method the earlier statement may also be a direct
`self.assertEqual(actual, expected)` call with a name in at least one operand.
This comparison requires an instance method with a `self` receiver and
an unambiguous inherited assertion method. Unmatched tests and ambiguous edits
supply no comparison finding. Snapshot and comparison findings use distinct
baseline keys. A comparison key for a replaced assertion identifies the prior
predicate, so a different lost check in the same test remains visible.

Discovery covers `test_*.py` and `*_test.py`, top-level sync or async functions
whose names start with `test`, and direct test methods. It recognizes plain
`Test*` classes without bases or a custom constructor, and classes of any name
with a single directly imported `unittest.TestCase` or
`unittest.IsolatedAsyncioTestCase` base. Module-level imports may use aliases.
Discovery skips pytest fixtures and honors explicit `__test__ = False` flags
on modules, classes, and individual tests.
The rule does not run collection, follow project-specific inheritance, or
infer skips from fixtures, class markers, or conditional decorators.

To use the same comparison in `pytest --pyhyi`, select the rule in configuration
and set its base. The CLI's `--base` overrides that value. An unavailable ref
or missing common ancestor fails the check. In shallow CI checkouts, fetch
enough history for Git to find the common ancestor.

```toml
[tool.pyhyi]
select = ["test-weakening"]

[tool.pyhyi.rules.test-weakening]
base = "origin/main"
```

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
