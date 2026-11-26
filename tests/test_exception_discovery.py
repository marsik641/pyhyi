"""Discovery of exception checks in synthetic Python test classes and functions."""

from textwrap import dedent

import pytest

from pyhyi.rules.weakening import TestWeakening as WeakeningRule
from pyhyi.rules.weakening import _tests


@pytest.mark.parametrize(
    ("header", "changed_header", "args"),
    [
        (
            "@pt.mark.parametrize('value', [1])\nclass TestCase:",
            "@pt.mark.parametrize('value', [2])\nclass TestCase:",
            "self, value",
        ),
        ("class TestCase(unit.TestCase):", "class TestCase(unit.IsolatedAsyncioTestCase):", "self"),
    ],
)
def test_changed_class_header_excludes_method_comparison(header, changed_header, args):
    source = (
        "import pytest as pt\nimport unittest as unit\n"
        + header
        + f"\n    def test_value({args}):\n        with pt.raises(ValueError): run()\n"
    )
    name = "TestCase.test_value"
    before = _tests(source)[name]
    edited = source.replace("ValueError", "Exception")
    rule = WeakeningRule({})
    assert len(list(rule.observations("test_sample.py", name, _tests(edited)[name], before))) == 1
    after = _tests(edited.replace(header, changed_header))[name]
    assert list(rule.observations("test_sample.py", name, after, before)) == []


def test_method_body_and_decorators_resolve_names_in_their_own_scopes():
    source = dedent("""\
        import pytest as pt
        from pytest import raises
        class TestChecks:
            pt = other
            raises = other.raises
            @pt.mark.skip
            def test_errors(self):
                with raises(Exception): run()
                with pt.raises(Exception): run()
            def test_parameter(self, raises):
                with raises(Exception): run()
        """)
    tests = _tests(source)
    method = tests["TestChecks.test_errors"]
    assert method.skips == []
    assert sorted(c.lineno for c in method.exceptions.values()) == [8, 9]
    assert not tests["TestChecks.test_parameter"].exceptions


def test_class_with_metaclass_keyword_is_excluded_from_comparison():
    source = "class TestChecks:\n    def test_value(self): pass\n"
    assert "TestChecks.test_value" in _tests(source)
    assert not _tests(source.replace("class TestChecks:", "class TestChecks(metaclass=Meta):"))


def test_class_level_mutation_of_pytest_module_invalidates_method_lookup():
    source = dedent("""\
        import pytest as pt
        class TestChecks:
            pt.raises = other
            def test_errors(self):
                with pt.raises(Exception): run()
        """)
    assert not _tests(source)["TestChecks.test_errors"].exceptions


def test_deep_context_and_signatures_leave_other_checks_discoverable():
    deep = "config" + ".child" * 1400
    source = dedent(f"""\
        from pytest import raises
        @{deep}
        class TestDeep:
            def test_hidden(self): pass
        class TestOrdinary:
            def test_signature(self, value={deep}): pass
            def test_body(self):
                with raises(Exception): {deep}
                with raises(Exception): run()
        def test_sibling():
            with raises(Exception): run()
        """)
    tests = _tests(source)
    assert set(tests) == {"TestOrdinary.test_body", "test_sibling"}
    assert [c.lineno for c in tests["TestOrdinary.test_body"].exceptions.values()] == [9]
    assert [c.lineno for c in tests["test_sibling"].exceptions.values()] == [11]
