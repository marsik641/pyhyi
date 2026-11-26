# Integrations

## Repair Markdown links

```sh
pyhyi fix --dry-run   # show each file, line and new destination
pyhyi fix             # apply the edits
```

The command repairs links reported by `broken-doc-link` when the target only
differs in letter case or was renamed without ambiguity in Git history.
Titles, queries and fragments are kept. It respects the configured selection,
ignores, suppressions and baseline.

## Git hooks

Run `pyhyi install-hooks` from the virtual environment where pyhyi is
installed. It adds `commit-msg` and `pre-push` hooks that keep using that
environment. Existing hooks and a custom `core.hooksPath` are left alone; to
add pyhyi to an existing shell hook, call `pyhyi commit-msg "$1"` or
`pyhyi pre-push "$1"` and pass on its exit status. The pre-push command reads
Git's ref updates from stdin.

The commit message hook checks the subject with the configured
`commit-convention` rule. `fixup!`, `squash!` and `amend!` subjects pass.

### Documentation map

To ask for a documentation update when source paths change, map them to exact
repository-relative documents in `pyhyi.toml` (`[tool.pyhyi.doc-map]` in
`pyproject.toml`). Patterns use the same syntax as `exclude`.

```toml
[doc-map]
"src/widgets/**" = ["docs/widgets.md", "README.md"]
"src/cli.py" = ["docs/cli.md"]
```

A commit that changes a mapped source path must also change one of its
documents. If the documentation still applies, name the page you checked and
the reason in a trailer; repeat `--trailer` for other pages:

```sh
git commit -m "refactor: share widget lookup" \
  --trailer "Docs-Unchanged: docs/widgets.md: The public widget configuration is unchanged"
```

The commit hook reads the index Git prepared for the commit, so unstaged
documentation does not count.

The push hook checks outgoing non-merge commits of branches whose new tip has
a map, each against the map of its own commit. For an existing branch the
outgoing commits are those after the remote's previous tip; fetch first if that
tip is missing locally. For a new branch, commits reachable from the remote's
tracking refs are skipped, or from any tracking ref when pushing to a URL.
Commits without a map, tags and branch deletions are not checked.

## pre-commit and GitHub Actions

With the pre-commit framework, add both hooks to `.pre-commit-config.yaml` and
install them with `pre-commit install -t pre-commit -t commit-msg`:

```yaml
repos:
  - repo: https://github.com/marsik641/pyhyi
    rev: v0.3.1
    hooks:
      - id: pyhyi
      - id: pyhyi-commit-msg
```

In GitHub Actions, check out the repository with history and run the action
from the pyhyi revision you want:

```yaml
steps:
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0
  - uses: marsik641/pyhyi@v0.3.1
```

## pytest plugin

Install the pytest extra in the project's test environment and run pytest with
`--pyhyi`, or add `--pyhyi` to pytest's `addopts`:

```sh
python -m pip install "pyhyi[pytest]>=0.2.0"
pytest --pyhyi
```

The plugin runs the same check as `pyhyi check` before collection, in the Git
repository that contains pytest's root directory. Tests run even when there
are findings; new blocking findings fail an otherwise successful run. The
terminal summary shows the findings and the suppression and baseline counts.

The summary also lists repository files that Python opened during collection
and the tests, including writes, in the pytest process and pytest-xdist
workers. Other subprocesses and native I/O are not seen, and a cached import
may open only bytecode, so a file missing from the list is not proof that it
is unused.
