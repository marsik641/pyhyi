# Rules

Run `pyhyi rules` for the list of available rules. Options go under
`[rules.<id>]` in `pyhyi.toml` or `[tool.pyhyi.rules.<id>]` in
`pyproject.toml`.

## Default rules

These five rules run unless the configuration or `--select` chooses others.
Their findings fail the check.

### commit-convention

Reports commit subjects that do not follow
[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`,
optionally with `!` before the colon.

- Merge commits and git's own `Revert "..."` subjects are skipped.
- `fixup!`, `squash!` and `amend!` commits are reported once a remote-tracking
  branch contains them. Local ones are left for `git rebase --autosquash`, and
  the commit message hook accepts them.

| Option | Default | Meaning |
| --- | --- | --- |
| `types` | feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert | Allowed types |
| `since` | none | A date such as `2026-01-31`; older commits are skipped |

### root-clutter

Reports files in the repository root other than well-known project files and
source code. Allowed are documentation and community files (README, LICENSE,
CHANGELOG, CONTRIBUTING, AGENTS and similar), manifests and lockfiles, build
and tool configuration (`*.toml`, `*.yml`, `.*rc`, `Dockerfile`, `Makefile`,
...), environment templates such as `.env.example`, and source files. A stray
report, dump, log, screenshot or committed `.env` is reported.

| Option | Default | Meaning |
| --- | --- | --- |
| `allow` | `[]` | Extra glob patterns of allowed root files |

### broken-doc-link

Reports relative links in Markdown files that point to paths git does not
track. External URLs, pure anchors and links in code are ignored. The hint
names the new path when git history shows a rename or when only the letter
case differs; `pyhyi fix` can apply it (see [integrations](integrations.md)).
A link to a file that exists only locally is reported as untracked.

Links are resolved as repository paths, the way GitHub renders Markdown. Static
site generators such as MkDocs, VitePress and Docusaurus also accept links
without `.md` and links to site routes such as `/guide/api`. pyhyi reports
those as broken. For a site's source directory, turn the rule off there:

```toml
[tool.pyhyi.per-path-ignores]
"docs/**" = ["broken-doc-link"]
```

### stale-todo

Reports `TODO`, `FIXME`, `HACK` and `XXX` at the start of a comment when
`git blame` dates the line more than `max-age-days` ago. Uncommitted lines are
ignored.

Comments are recognized without parsing each language, so the rule stays on
the safe side:

- Python files are tokenized.
- Other files use the comment syntax of their type, found by suffix, by a
  well-known name such as `Makefile` or `.gitignore`, or by a `#!` first line.
  Files of other types, such as `.txt`, `.rst` or `.json`, have no comments.
- In Markdown and HTML only `<!-- -->` comments count, so a heading
  `# TODO list` is not a marker.
- Text in quotes closed on the same line is skipped, so `echo "# TODO"` is
  not a marker either.

| Option | Default | Meaning |
| --- | --- | --- |
| `markers` | TODO, FIXME, HACK, XXX | Words that mark debt |
| `max-age-days` | 14 | Allowed age in days |

### bad-suppression

Reports inline suppressions that give no reason, name no rules or name
unknown rules. A suppression without a reason is not applied.

## Opt-in review rules

These rules report advice: findings for a human to review. Advice is listed in
the report but on its own leaves the exit status at 0. Enable a rule with
`--select` or `select` in the configuration.

### dump-folder and orphan-file

```sh
pyhyi check --select dump-folder,orphan-file
```

| Rule | Why a candidate appears | Options |
| --- | --- | --- |
| `dump-folder` | A directory accumulates numbered dump, export, report or snapshot files, each changed once, across several commits. | `min-files` (5), `min-commits` (3) |
| `orphan-file` | An old Python or JS/TS module has a scratch, backup, obsolete or unused name and no detected local consumer. | `min-days` (30) |

A detected reference clears a candidate. References come from Python imports
and string literals, literal JS/TS paths, Markdown links, manifests, and
Markdown code spans that name a single repository file. Dynamic imports and
external callers are not visible, so review a candidate before deleting it.
With these rules enabled in configuration, `pytest --pyhyi` also clears
candidates that the test run opened. Both rules use committed history and
produce nothing in shallow clones.

### stale-doc

```sh
pyhyi check --select stale-doc
```

Reports a Python declaration quoted in Markdown, such as `PAGE_LIMIT = 20` or
`def search(query: str, limit: int = 20)`, that matched the linked source when
the document was last committed and differs at `HEAD`. The quote must be in a
paragraph that links to exactly one local Python file. Only unambiguous
module-level declarations are compared, and documents or sources with local
edits are skipped. The finding shows both versions.

With `file-history = true` the rule instead reports linked source files with
at least `min-commits` commits after the document's last edit, the latest of
them at least `min-days` days after it. This mode is for a manual review of
whole files.

| Option | Default |
| --- | --- |
| `file-history` | false |
| `min-days` | 30 |
| `min-commits` | 3 |

### complexity-growth

```sh
pyhyi check --select complexity-growth
pyhyi check --select complexity-growth --base origin/main
```

Needs Ruff on `PATH`. The rule scores each Python function with McCabe
complexity (Ruff C901) along the last `max-commits` first-parent commits up to
`HEAD`. It reports a function whose score rose in at least `min-increases`
separate commits, by at least `min-growth` in total, to at least
`min-complexity`. The finding lists the scores and commits.

Edits that keep the score continue the series; a lower score, a missing
function or an ambiguous definition ends it. Functions are matched by
qualified name within a file, across renames Git detects. Uncommitted edits
and sources that cannot be parsed are skipped; a shallow clone uses the
history it has. With `--base`, only functions
that grew relative to the merge base of the ref and `HEAD` are reported.

| Option | Default |
| --- | --- |
| `base` | none |
| `max-commits` | 50 |
| `min-increases` | 3 |
| `min-growth` | 5 |
| `min-complexity` | 12 |

### test-weakening

```sh
pyhyi check --select test-weakening
pyhyi check --select test-weakening --base origin/main
```

Without a base, the rule looks at the current test files, including untracked
ones, and reports unconditional `pytest.mark.skip` decorators and direct
`assert True` statements. Broad expectations such as
`pytest.raises(Exception)` are Ruff's B017 and PT011.

With a base, it compares each changed test file with its version at the merge
base of the ref and `HEAD`, which on a clean PR checkout is the whole PR. Git
pairs renamed files; both paths must pass `exclude`. Tests are paired by
unique name, `Class.method` for methods. A finding needs a concrete value that
the new assertion accepts and the old one rejected:

| Before | After | What the change admits |
| --- | --- | --- |
| `assert total == 120` | `assert total > 0` | An integer such as `1` |
| `assert status in {200, 201}` | `assert status in {200, 201, 404}` | `404` |
| `assert ready and valid` | `assert ready` | A ready result that fails validation |
| `with pytest.raises(ValueError)` | `with pytest.raises(Exception)` | Exceptions of other types from the same operation |

The value is searched for among integers, literals, membership and truth
values combined with `and`, `or` and `not`. Changing `== 120` to `== 100`
changes the contract in both directions and is not reported. Recognized
`unittest` assertions such as `self.assertEqual` and `self.assertIsNotNone`
take part in the same comparison.

The comparison also reports:

- a removed direct check;
- a broader built-in exception type or a removed message pattern;
- a newly added unconditional skip;
- a single assertion replaced in place by `assert True` while the rest of the
  test stays the same. The old assertion must use a name, or be a direct
  `self.assertEqual(actual, expected)` in a `unittest` method.

Only statements under unchanged control flow are compared, and unmatched or
ambiguous edits produce no finding. The example is about the assertion alone:
fixtures, other tests and custom operators can change what it means.
Snapshot and comparison findings have separate baseline keys, so a new lost
check in a test with a baselined one is still reported.

Test discovery follows pytest's defaults without running collection:
`test_*.py` and `*_test.py` files, top-level `test*` functions, methods of
plain `Test*` classes and of classes with a single directly imported
`unittest.TestCase` or `unittest.IsolatedAsyncioTestCase` base. Pytest
fixtures are skipped and `__test__ = False` is honored. Project-specific base
classes, class markers and conditional skips are not followed.

To run the comparison in `pytest --pyhyi`, select the rule and set its base in
the configuration. The CLI's `--base` overrides it. A missing ref or common
ancestor fails the check, so in shallow CI checkouts fetch enough history.

```toml
[tool.pyhyi]
select = ["test-weakening"]

[tool.pyhyi.rules.test-weakening]
base = "origin/main"
```
