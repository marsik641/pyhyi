# Historical defect replay

Use `pyhyi replay` to check whether today's pytest selection detects a
specific, previously fixed defect. It executes code from the repository's
history. Use trusted revisions and the project's disposable test environment,
including any database the tests use. Temporary Git clones isolate tracked
files; tests retain the interpreter's ordinary access to external services.

## Choose a case

1. Run `pyhyi replay --list`. A single Git history scan lists single-parent
   changes to existing production Python files that also touch Python tests or
   fixtures, or use a conventional `fix:` subject. These are leads for review.
   Any reachable single-parent fix can also be supplied directly.
2. Select the changed production file with `--source` and a single historical
   pytest case with `--test`. For parametrized tests include the parameter ID.
   Repeat `--source` when the fix spans several files. For a mixed change in one
   file, select a one-based `--hunk` from
   `git diff --diff-algorithm=myers -U3 --inter-hunk-context=0 FIX^ FIX -- FILE`.
   Added, deleted and renamed production files are outside this version's scope.
3. By default the same selector runs on committed `HEAD`. Pass
   `--current-test tests` for a directory, `--current-test .` for the project,
   or a new node ID if the test moved. Uncommitted edits are excluded.
   Optionally repeat `--fixture` for historical test support files, e.g.
   `tests/conftest.py`, needed to reproduce the old inputs.

## Evidence

Each trial uses a fresh local clone and the invoking Python environment. The
clone's `src/` and root are on the import path. Install pytest and the test
dependencies beforehand. Pyhyi removes inherited `PYTEST_ADDOPTS`, honors the
checkout's pytest configuration and gives each invocation its own temporary
directory. The default timeout is 120 seconds per pytest invocation.

| Trial | Code | Tests | Required observation |
| --- | --- | --- | --- |
| 1 | Fix | Historical case | Pass |
| 2 | Fix with selected patch reversed | Same case | One or more failures in the test body |
| 3 | HEAD | Current selection | All pass |
| 4 | HEAD with selected patch reversed | Same selection | Reproduce every historical failure, or all pass |
| 5 and 6, if trial 4 passes | HEAD, then HEAD with the patch reversed | Historical test module and optional historical fixtures on HEAD | Pass, then reproduce the original failure |

The report includes the commit IDs, paths, selectors, counts and failure
evidence. Use `--format json` to save it. The selection fingerprint identifies
the cases that actually ran. Matching a failure requires the same pytest node
ID, test source statement and assertion message. Changed diagnostics can
therefore require manual review.

A `unittest` method with subtests counts as one case. Replay records every
reported assertion failure. It also records exceptions raised in a selected
production file when the traceback leads back to the test. For these exceptions
it also compares the exception type, message, source path and raising statement.
The comparison counts repeated failures and allows subtests to run in a different
order. A partial match, fixture failure or exception outside the selected
production files leaves the result inconclusive.

| Outcome | Exit | Interpretation |
| --- | --- | --- |
| `caught` | 0 | The current selection reproduces all recorded historical failures when the defect is restored. |
| `survived` | 1 | The current selection passes with the defect present, while the historical test still rejects it. Review whether the old contract still applies and whether the selection covers the relevant suite. |
| `inconclusive` | 2 | A comparison could not establish either result. Inspect the first unexpected trial, for example a conflicting patch, missing dependency, skip, xfail, different assertion or failing fixture. |

Invalid input and Git errors also exit 2. A replay measures the observed runs;
nondeterministic cases need repeated confirmation. Historical dependencies are
not reconstructed, and submodules are not initialized.
