import ast
import json
from textwrap import dedent

import pytest

from pyhyi.cli import main
from pyhyi.rules.predicates import widening


def compare(before, after):
    return widening(ast.parse(before, mode="eval").body, ast.parse(after, mode="eval").body)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("amount == 120", "amount > 0"),
        ("score > -2", "score >= -2"),
        ("code in {200, 201}", "code in {200, 201, 404}"),
        ("status == 'done'", "status"),
        ("response.status == 200", "response.status is not None"),
        ("price() == 120", "price() > 0"),
        ("good and ready", "good"),
        ("ready", "ready or pending"),
        ("0 < amount and amount < 10", "amount < 10"),
        ("120 == amount", "0 < amount"),
    ],
)
def test_strict_widening_has_example(before, after):
    assert compare(before, after) is not None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("amount == 120", "amount == 100"),
        ("amount > 0", "amount == 120"),
        ("amount > 0", "amount >= 1"),
        ("amount > 0", "amount >= 1 or amount == -1"),
        ("amount is None or amount == 10", "amount is not None and amount >= 0"),
        ("len(rows) == 0", "len(rows) <= 0"),
        ("status in {'ok', 'no'}", "status in {'ok', 'maybe'}"),
        ("amount > 1 and amount < 4", "amount > 10"),
        ("amount == 120", "different > 0"),
        ("flag", "flag or flag"),
        ("get() and get()", "get()"),
        ("handler(value)", "other_handler(value)"),
    ],
)
def test_strengthening_equivalence_or_uncertainty_is_quiet(before, after):
    assert compare(before, after) is None


def check(repo, capsys, base=False):
    args = ["check", str(repo.root), "--select", "test-weakening", "--format", "json"]
    if base:
        args.extend(["--base", "HEAD"])
    assert main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["errors"] == []
    return data["findings"]


def test_real_assertions_still_execute(repo, capsys):
    original = dedent("""\
        import pytest
        import unittest

        def test_amount(value):
            assert value == 120

        def test_flags(first, second):
            assert first and second

        def test_two(amount, status):
            assert amount == 99
            assert status == 'done'

        def test_deleted(value):
            assert value > 0
            assert value < 10

        class Checks(unittest.TestCase):
            def test_query(self):
                with self.assertRaises(ValueError):
                    operation()

            def test_code(self):
                self.assertEqual(self.code, 200)

        def test_nested():
            with resource():
                with pytest.raises(ValueError, match='invalid'):
                    operation()
        """)
    repo.commit("test: capture expectations", {"test_sample.py": original})
    modified = (
        original.replace("== 120", "> 0")
        .replace("first and second", "first")
        .replace("== 99", "> 1")
        .replace("status == 'done'", "status")
        .replace("    assert value < 10\n", "")
        .replace("self.assertRaises(ValueError)", "self.assertRaises(Exception)")
        .replace("self.assertEqual(self.code, 200)", "self.assertIsNotNone(self.code)")
        .replace(", match='invalid'", "")
    )
    repo.write("test_sample.py", modified)
    found = check(repo, capsys, base=True)
    assert len(found) == 8
    assert all(f["severity"] == "advice" and f["line"] > 0 for f in found)
    assert sum("new check accepts" in f["message"] for f in found) == 5
    assert sum("removed the check" in f["message"] for f in found) == 1
    # The snapshot leaves the broad assertRaises(Exception) to Ruff B017.
    assert check(repo, capsys) == []


def test_context_changes_are_not_comparison_evidence(repo, capsys):
    original = dedent("""\
        import pytest
        from pytest import raises as expect

        @pytest.mark.parametrize("value", [120])
        def test_parameters(value):
            assert value == 120

        def test_setup():
            value = old()
            assert value == 120

        def test_scenario(fn):
            with expect(ValueError):
                fn(1)

        def test_local(pytest, Exception):
            with pytest.raises(Exception):
                run()
        """)
    repo.commit("test: capture setup", {"test_sample.py": original})
    repo.write(
        "test_sample.py",
        original.replace("[120]", "[20]")
        .replace("old()", "new()")
        .replace("== 120", "> 0")
        .replace("expect(ValueError)", "expect(Exception)")
        .replace("fn(1)", "fn(2)"),
    )
    assert check(repo, capsys, base=True) == []
    assert check(repo, capsys) == []


def test_combined_assertions_and_failure_context(repo, capsys):
    original = dedent("""\
        import pytest
        def test_order(n):
            assert n >= 0
            assert n >= 10

        def test_failure(n):
            with pytest.raises(AssertionError):
                assert n > 0
        """)
    repo.commit("test: capture both checks", {"test_case.py": original})
    repo.write(
        "test_case.py",
        original.replace("n >= 0", "n > 10")
        .replace("n >= 10", "n > 0")
        .replace("        assert n > 0", "        assert n >= 0"),
    )
    assert check(repo, capsys, base=True) == []


def test_baseline_distinguishes_branches(repo, capsys):
    source = dedent("""\
        def test_branches(value, flag):
            if flag:
                assert value == 2
            else:
                assert value == 2
        """)
    repo.commit(
        "test: capture branches",
        {
            "test_case.py": source,
            "pyhyi.toml": 'select=["test-weakening"]\n[rules.test-weakening]\nbase="HEAD"\n',
        },
    )
    repo.write("test_case.py", source.replace("== 2", "> 0", 1))
    assert main(["baseline", str(repo.root)]) == 0
    capsys.readouterr()
    repo.write("test_case.py", source[: source.rindex("== 2")] + "> 0\n")
    assert len(check(repo, capsys, base=True)) == 1
