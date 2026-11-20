# Test weakening

Status: implemented on main

## Purpose

Expose a lost check when reviewing a Python test change. On first adoption, a team can also inspect unconditional skips and literal placeholder assertions in its current suite. A finding describes the observed test construct. A developer decides whether it is intentional.

## Current behavior

At 221cc43 the registry contains five default rules and three opt-in rules. None inspects Python test assertions. The checker reads the worktree, including eligible untracked files, and supports selection, ignores, suppressions, baselines, advice and JSON output. The pytest plugin uses the same engine. This specification adds one opt-in rule, `test-weakening`.

## Inputs and interface

- `pyhyi check --select test-weakening` inspects the current files once.
- `pyhyi check --select test-weakening --base REF` compares the worktree to the merge base of REF and HEAD. A clean checkout at the PR head gives the net PR change. Local tracked edits are included. The rule reads the old blob and current file only for changed test paths. Discover paths with native Git, with rename detection and NUL delimiting. No history replay, checkout, project import, pytest execution or additional dependencies.
- Support the same base as a string rule option `[tool.pyhyi.rules.test-weakening]` / `base`, including when invoked from pytest. CLI takes precedence. Invalid or unavailable bases must fail clearly when the rule is selected, including shallow history without a common ancestor. Never fall back silently to a snapshot.
- Follow pytest's default Python module naming (`test_*.py`, `*_test.py`), module-level test functions and test methods in conventional test classes. Support sync and async definitions. Document deliberate limits of static discovery and any narrower supported class patterns. Parse source with the standard library and never follow a symlink outside the repo. Ignore non-parseable or unsupported source conservatively.

## Skips and placeholders

Include test methods on classes directly inheriting an unambiguously imported `unittest.TestCase` or `unittest.IsolatedAsyncioTestCase`, independently of the class name. This is a standard pytest collection path. Indirect project-specific base classes may be omitted. In these standard-library classes also accept a direct `self.assertEqual(actual, expected)` call replaced by `assert True` as a narrowly named instance of observation 2. Require an ordinary instance method, a nonliteral operand, a single changed statement and an unambiguous assertion method. Describe the call that disappeared. Include synthetic positive and look-alike negative cases.

1. A test function or method carries an unconditional `pytest.mark.skip` decorator, bare or called (a reason is allowed), with an unambiguous pytest import. In comparison mode require the same test to have lacked that unconditional decorator in the base. Exclude conditional skips, xfail and look-alike decorators. Avoid a claim that a test previously executed, since class markers, fixtures and CI selectors can also skip it.
2. A test contains a direct statement `assert True` with optional message. In comparison mode require evidence that a prior nonconstant assertion in the same test and statement position was replaced by that placeholder. A conservative approach accepts one changed direct assertion with otherwise equivalent surrounding statements, ignoring formatting and line numbers. Skip ambiguous rewrites. Adding a placeholder beside an intact assertion does not constitute replacement.

In snapshot mode describe the construct in present tense. In comparison mode name the precise change. Use the current path and 1-based decorator/assertion line, a stable key and advice severity. Distinct findings must remain distinguishable. Never claim an entire test is useless or that a defect has been demonstrated. Match tests by unambiguous qualified name and Git rename pairing. Added/deleted/renamed test definitions without a match supply no evidence of prior weakening. Several matches or ambiguous identities must not generate historical claims.

## Partial loss of a check

The snapshot reports an unrestricted expectation of `Exception` or
`BaseException` as a review candidate. In a comparison, the old and new tests
provide evidence of change. Match the same test signature and protected
operation before reporting a broadened exception type or removed message
constraint. Give an exception type or message that distinguishes the two.

For assertions, compare supported predicates over the same observed values.
Require the old accepted region to be nonempty, to lie within the new region,
and to exclude at least one concrete example from the new region. Compare
literal equality, integer bounds, membership and boolean combinations on a
bounded number of symbols. Treat repeated calls conservatively. Reject
incomparable changes and changed setup. Report a removed assertion as a
removed statement at its surviving location without claiming a suite-wide loss.

## Implementation order

1. Capture the current registry and example command outcome before implementation.
2. Add the Python rule and minimal comparison support. Reuse existing filtering, baseline logic and output. Keep default rule selection.
3. Add synthetic tests and document both commands, their scope, and advice behavior in the README.
4. Run lint, format and the full pyhyi suite. Have a second reviewer inspect the tested implementation. Exercise both modes on two external repositories, recording the revision, comparison scope, findings and exit status privately.

## Acceptance

| Input or command | Expected |
| --- | --- |
| Snapshot with one direct placeholder and one unconditional skip on separate tests | Two advice findings at their current lines, exit 0 |
| Same repo with a genuine assertion, conditional skip, unrelated decorator, expected-exception test or helper containing a placeholder | Zero findings for those controls |
| Explicit base, same test gains a skip or replaces a substantive assert with the placeholder | One new finding per change, including a matched file rename |
| Unchanged existing placeholders/skips, new test, moved/renamed unmatched test or placeholder added beside intact assertion | Zero historical findings |
| Identical base and clean tip | Zero historical findings |
| Missing base or absent merge base | Actionable diagnostic and unsuccessful check |
| Default selection, excludes, per-path ignores, inline suppression, baseline and configured pytest entry | Follow established engine semantics |
| Fixture with a test-like name, or a test explicitly disabled using a literal `__test__ = False` | No finding for the excluded definition |
| Snapshot baseline contains an existing placeholder and a different check in the same function is subsequently replaced | The new comparison finding remains visible |
| A comparison baseline is written on Python 3.10 and read on Python 3.14 for the same source | The finding matches the existing entry |
| A comparison uses the value of a short-circuit expression, such as `(value or "fallback") == "ok"` | Replacing that assertion with `assert True` produces one finding |
| Exact amount 120 changes to any positive amount, or an allowed set gains a status code | Report a concrete newly accepted value |
| The expected amount changes from 120 to 100, a bound tightens, or reordered checks become jointly stricter | Zero widening findings |
| The protected operation stays the same and its expected exception changes from ValueError to Exception | Identify the widened type and a newly admitted exception |
| An existing context accepts any Exception without checking its cause | Snapshot advice, with no historical claim |
| An existing skip decorator changes spelling, or the module rebinds the imported marker | Avoid reporting a newly added unconditional skip when the previous state or marker identity is ambiguous |
| Git rename across an excluded path | Skip the pair before accessing the old blob |
| An unrelated plugin raises a configuration error inside its check | Preserve the engine's existing JSON error and continue running other selected rules |
| Independent review plus two read-only repository trials | Record observed findings and limitations before acceptance |

## Boundaries and rollback

The broad-exception snapshot overlaps with Ruff B017. Comparisons add evidence
from the base revision. General equivalence of arbitrary Python programs,
mutation testing, class-wide skip inference, custom collection, and documentation
rules are outside this rule. Disable the rule through selection or ignore.
