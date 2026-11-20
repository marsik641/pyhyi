"""Statement-level evidence from synthetic exception expectations."""

from __future__ import annotations

import ast
import builtins
import re
import unittest

import pytest

from pyhyi.rules.exception_checks import exception_check


def parse(source, **overrides):
    names = dict(
        pytest_names={"pt"},
        raises_names={"expect"},
        builtin_names=set(vars(builtins)),
        unittest_methods={"assertRaises", "assertRaisesRegex"},
    )
    return exception_check(ast.parse(source).body[0], **(names | overrides))


def block(call):
    return f"with {call}: operation()"


@pytest.mark.parametrize(
    "call",
    ["pt.raises(Exception)", "expect(BaseException)", "self.assertRaises(Exception, msg='detail')"],
)
def test_snapshot_reports_both_broad_builtin_classes(call):
    check = parse(block(call))
    assert check.expected.__name__ in check.broad_warning()
    assert check.weaker_than(check) is None


@pytest.mark.parametrize(
    ("source", "overrides"),
    [
        (block("pt.raises(Exception)"), {"pytest_names": set()}),
        (block("expect(ValueError)"), {"raises_names": set()}),
        (block("self.assertRaises(Exception)"), {"unittest_methods": {"assertRaisesRegex"}}),
        (
            block("self.assertRaisesRegex(Exception, 'text')"),
            {"unittest_methods": {"assertRaises"}},
        ),
        (block("pt.raises(Exception)"), {"builtin_names": {"ValueError"}}),
        (block("other.assertRaises(Exception)"), {}),
        (block("pt.raises(str)"), {}),
        (block("pt.raises((ValueError, TypeError))"), {}),
        (block("pt.raises(Exception, **options)"), {}),
        (block("pt.raises(Exception, check=validate)"), {}),
        (block("pt.raises(Exception, match=pattern)"), {}),
        (block("pt.raises(Exception, match=b'bytes')"), {}),
        (block("self.assertRaisesRegex(Exception, pattern)"), {}),
        (block("pt.raises(Exception), self.assertRaises(ValueError)"), {}),
        (block("expect(Exception, run)"), {}),
        ("pt.raises(Exception)", {}),
        ("caught = pt.raises(Exception, run)", {}),
    ],
)
def test_unresolved_or_unsupported_contract(source, overrides):
    assert parse(source, **overrides) is None


@pytest.mark.parametrize(
    "source",
    [
        block("pt.raises(TypeError)"),
        block("pt.raises(Exception, match='exact')"),
        block("self.assertRaisesRegex(BaseException, 'reason')"),
        block("expect(Exception) as caught"),
        "with expect(Exception): assert inspect_error()",
        "with expect(Exception): self.assertRegex(error.message, 'reason')",
        "with expect(Exception):\n    with pt.raises(TypeError): operation()",
        "with expect(Exception):\n    try: operation()\n    except TypeError: raise",
    ],
)
def test_no_snapshot_warning_with_constraints(source):
    assert parse(source).broad_warning() is None


@pytest.mark.parametrize(
    ("before", "after", "example"),
    [
        (block("expect(KeyError)"), block("pt.raises(LookupError)"), "LookupError"),
        (
            block("self.assertRaises(Exception)"),
            block("self.assertRaises(BaseException)"),
            "BaseException",
        ),
        (
            "self.assertRaises(IndexError, fetch, 0)",
            "self.assertRaises(LookupError, fetch, 0)",
            "LookupError",
        ),
        (
            "pt.raises(ValueError, run, match='arg')",
            "pt.raises(Exception, run, match='arg')",
            "Exception",
        ),
        (
            block("pt.raises(ValueError, match='^bad$')"),
            block("pt.raises(Exception, match='^bad$')"),
            "'bad'",
        ),
    ],
)
def test_superclass_witness(before, after, example):
    old, new = parse(before), parse(after)
    report = new.weaker_than(old)
    assert old.expected.__name__ in report
    assert example in report
    assert old.weaker_than(new) is None


def test_regex_removal_witnesses_are_rejected_by_the_prior_assertions():
    def run():
        raise ValueError("")

    old = parse(block("expect(ValueError, match='code [0-9]+')"))
    new = parse(block("expect(ValueError)"))
    assert "message ''" in new.weaker_than(old)
    with pytest.raises(AssertionError), pytest.raises(ValueError, match=r"code [0-9]+"):
        run()
    with pytest.raises(ValueError):
        run()

    old = parse("self.assertRaisesRegex(ValueError, 'code [0-9]+', run)")
    new = parse("self.assertRaises(ValueError, run)")
    assert "message ''" in new.weaker_than(old)
    case = unittest.TestCase()
    with pytest.raises(AssertionError):
        case.assertRaisesRegex(ValueError, "code [0-9]+", run)
    case.assertRaises(ValueError, run)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (block("expect(TypeError)"), block("expect(ValueError)")),
        (block("expect(IOError)"), block("expect(OSError)")),
        (block("expect(ValueError)"), block("expect(Exception, match='new')")),
        (block("expect(ValueError, match='old')"), block("expect(ValueError, match='new')")),
        (block("expect(ValueError, match='.*')"), block("expect(ValueError)")),
        (block("expect(ValueError, match='[')"), block("expect(ValueError)")),
        (block("expect(ValueError, match='')"), block("expect(ValueError)")),
        (block("self.assertRaises(ValueError)"), block("pt.raises(Exception)")),
        ("expect(ValueError, run)", "expect(Exception, other)"),
        ("expect(ValueError, run, 1)", "expect(Exception, run, 2)"),
        ("expect(ValueError, run, match='arg')", "expect(Exception, run)"),
        (
            block("self.assertRaisesRegex(Exception, 'reason', msg='old')"),
            block("self.assertRaises(Exception, msg='new')"),
        ),
    ],
)
def test_incomparable_changes_or_no_witness(before, after):
    assert parse(after).weaker_than(parse(before)) is None


def test_multi_item_context_and_location():
    old = """with (
        acquire() as handle,
        expect(ValueError, match='missing') as info,
        log(handle),
    ):
        with handle: read()
    """
    new = old.replace(", match='missing'", "")
    check = parse(new)
    assert check.lineno == 3
    assert check.broad_warning() is None
    assert "message ''" in check.weaker_than(parse(old))
    for edited in (
        new.replace(" as info", ""),
        new.replace("as handle", "as resource"),
        new.replace("acquire()", "acquire(flag)"),
        new.replace("log(handle)", "other(handle)"),
        new.replace("read()", "write()"),
        new.replace("        log(handle),\n", "").replace(
            "        expect(", "        log(handle),\n        expect("
        ),
    ):
        assert parse(edited).weaker_than(parse(old)) is None


def test_resource_binding_and_nested_with_allow_a_broad_snapshot():
    source = "with acquire() as handle, self.assertRaises(Exception):\n    with handle: operation()"
    assert parse(source).broad_warning()


@pytest.mark.parametrize(
    ("pattern", "example"),
    [(r"^\[code\]\s\d+[A-Z]?$", "[code] 0"), (r"^a.z$", "axz"), (r"\$\w*", "$")],
)
def test_preserved_pattern_has_a_constructed_matching_example(pattern, example):
    old = parse(block(f"expect(ValueError, match={pattern!r})"))
    new = parse(block(f"expect(Exception, match={pattern!r})"))
    assert repr(example) in new.weaker_than(old)
    with pytest.raises(Exception, match=pattern):
        raise Exception(example)


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(?i)bad", r"a|b", r".*", r"a{1000000000}"])
def test_regex_removal_without_a_proven_counterexample(pattern):
    old = parse(block(f"expect(ValueError, match={pattern!r})"))
    assert parse(block("expect(ValueError)")).weaker_than(old) is None


def test_removal_of_empty_message_requirement():
    pattern = "^$"
    old = parse(block(f"self.assertRaisesRegex(ValueError, {pattern!r})"))
    new = parse(block("self.assertRaises(ValueError)"))
    assert "message 'x'" in new.weaker_than(old)
    assert re.search(pattern, "x") is None


def test_superclass_and_pattern_removal_use_the_type_as_witness():
    old = parse(block("pt.raises(ValueError, match='a|b')"))
    new = parse(block("pt.raises(Exception)"))
    report = new.weaker_than(old)
    assert "removed the message pattern" in report
    assert "exact type is Exception" in report


def test_witness_accounts_for_builtin_message_formatting():
    old = parse(block("expect(KeyError, match='missing')"))
    new = parse(block("expect(KeyError)"))
    assert "message ''" in new.weaker_than(old)
    assert str(KeyError()) == ""
    old = parse(block("expect(UnicodeDecodeError, match='missing')"))
    new = parse(block("expect(UnicodeDecodeError)"))
    assert new.weaker_than(old) is None
