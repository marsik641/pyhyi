# Changelog

User-visible changes of each release. Versions follow the policy in
[docs/releasing.md](docs/releasing.md). Notes for releases before 0.4.0 are on
[GitHub Releases](https://github.com/marsik641/pyhyi/releases).

## 0.4.0 - 2026-11-26

### Breaking changes

- The `calculation-drift` rule is removed. A configuration that selects,
  ignores or configures it now fails with an unknown rule error, and
  `bad-suppression` reports inline suppressions that name it. Remove the rule
  from `select`, `ignore`, `per-path-ignores`, `[rules.calculation-drift]` and
  suppressions, or pin `pyhyi==0.3.1` to keep it.
- The `pyhyi replay` command is removed. Pin `pyhyi==0.3.1` to keep it.

### Changed

- `test-weakening` without a base no longer reports broad expectations such
  as `pytest.raises(Exception)` or `assertRaises(Exception)`. Ruff reports them
  as B017 and PT011. With a base, a narrower expectation that became broader is
  still reported.
- `stale-todo` takes the comment syntax from the file type: its suffix, a
  well-known name such as `Makefile`, or a `#!` first line. Files of other
  types, such as `.txt`, `.rst`, `.org` or `.json`, are no longer scanned.
- The README is shorter. Rule options and limits moved to
  [docs/rules.md](https://github.com/marsik641/pyhyi/blob/main/docs/rules.md),
  hook, pre-commit, GitHub Actions and pytest setup to
  [docs/integrations.md](https://github.com/marsik641/pyhyi/blob/main/docs/integrations.md).

### Fixed

- `git commit --fixup` works with the hooks again. The commit message hook
  accepts `fixup!`, `squash!` and `amend!` subjects, and `commit-convention`
  reports such commits only once a remote-tracking branch contains them. Before,
  the hook rejected the fixup, and the pre-commit check blocked every later
  commit until it was squashed.
- `stale-todo` no longer reports markers inside quoted text, such as
  `echo "# TODO"` in a shell script or `"// TODO"` in JavaScript, or `* TODO`
  list items in `.rst` and `.org` files.
- The pre-push hook no longer rechecks a branch's whole history when it is
  pushed to a URL as a new branch. Commits that any remote already has are
  skipped.

### Upgrading

Findings that the changed rules no longer produce show up as stale baseline
entries. Run `pyhyi baseline` to shrink the baseline. For sites built with
MkDocs, VitePress or Docusaurus, see the `broken-doc-link` notes in
docs/rules.md: links without `.md` and site routes are reported as broken.

**Full changelog**: https://github.com/marsik641/pyhyi/compare/v0.3.1...v0.4.0
