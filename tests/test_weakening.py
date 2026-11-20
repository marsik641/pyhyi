"""End-to-end checks against small synthetic repositories."""

from __future__ import annotations

import json
import os
import subprocess
from textwrap import dedent, indent

import pytest

from pyhyi import git
from pyhyi.cli import main
from pyhyi.context import MAX_TEXT_BYTES, Context

RULE = "test-weakening"


def check(repo, capsys, *args):
    assert main(["check", str(repo.root), "--select", RULE, "--format", "json", *args]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == []
    assert report["summary"]["issues"] == 0
    assert all(f["severity"] == "advice" for f in report["findings"])
    return report


def test_snapshot(repo, capsys):
    source = dedent("""\
        import pytest as pt
        @pt.mark.skip(reason="manual")
        async def test_demo(): pass
        class TestGroup:
            def test_value(self):
                assert True, "pending"
            @pt.mark.skipif(True, reason="conditional")
            def test_conditional(self): assert 1 == 1
        @other.mark.skip
        def test_lookalike(): pass
        @pt.mark.xfail
        def test_raises():
            with pt.raises(ValueError):
                parse("")
        def helper(): assert True
        def test_scope():
            if active: assert True
            def helper(): assert True
        """)
    repo.write("sample_test.py", source)
    repo.write("helper.py", source)
    repo.write("test_invalid.py", "def test_invalid(:")
    findings = check(repo, capsys)["findings"]
    assert [(f["path"], f["line"]) for f in findings] == [
        ("sample_test.py", 2),
        ("sample_test.py", 6),
    ]
    assert "test_demo has" in findings[0]["message"]
    assert "TestGroup.test_value contains" in findings[1]["message"]
    assert main(["check", str(repo.root), "--format", "json"]) == 0
    assert RULE not in json.loads(capsys.readouterr().out)["summary"]["rules"]


def test_merge_base_rename_and_local_edits(repo, capsys):
    path = "test_previous.py"
    dest = "-example folder/sample_test.py"
    source = dedent("""\
        from pytest import mark as marker
        def test_first():
            value = read_value()
            assert value == 12
        class TestOutput:
            async def test_message(self):
                output = await render()
                assert output.endswith("example")
        """)
    repo.commit("test: add examples", {path: source})
    repo.git("switch", "-qc", "topic")
    (repo.root / dest).parent.mkdir()
    repo.git("mv", "--", path, dest)
    repo.commit("test: relocate examples")
    repo.git("switch", "-q", "main")
    repo.commit("test: update main", {path: source.replace("assert value == 12", "assert True")})
    repo.git("switch", "-q", "topic")
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []
    current = source.replace("assert value == 12", "assert True").replace(
        "    async def", "    @marker.skip\n    async def"
    )
    repo.write(dest, current)
    found = check(repo, capsys, "--base", "main")["findings"]
    assert [(f["path"], f["line"]) for f in found] == [(dest, 4), (dest, 6)]
    assert "test_first replaced" in found[0]["message"]
    assert "TestOutput.test_message gained" in found[1]["message"]


def test_no_historical_claim_for_unmatched_or_ambiguous_changes(repo, capsys):
    base = dedent("""\
        import pytest
        @pytest.mark.skip()
        def test_old(): assert True
        def test_keep(): assert value
        def test_rewrite():
            value = 3
            assert value
        def test_rename(): assert value
        def test_duplicate(): assert value
        def test_duplicate(): pass
        def test_constant(): assert 3 > 1
        def test_drop(): assert value
        """)
    repo.commit("test: base", {"test_sample.py": base, "test_deleted.py": base})
    changed = (
        base.replace(
            "def test_keep(): assert value", "def test_keep():\n    assert value\n    assert True"
        )
        .replace("value = 3\n    assert value", "value = 4\n    assert True")
        .replace("test_rename(): assert value", "test_other(): assert True")
        .replace("test_duplicate(): assert value", "test_duplicate(): assert True")
        .replace("assert 3 > 1", "assert True")
        .replace("def test_drop(): assert value", "")
    )
    repo.write("test_sample.py", changed)
    (repo.root / "test_deleted.py").unlink()
    repo.write("test_new.py", changed)
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []


@pytest.mark.parametrize("revision", ["", "missing-branch", "--help", "--all"])
def test_invalid_ref_fails_only_when_selected(repo, capsys, revision):
    repo.commit("test: init")
    assert main(["check", str(repo.root), "--select", RULE, f"--base={revision}"]) == 2
    assert "base" in capsys.readouterr().err
    assert main(["check", str(repo.root), f"--base={revision}"]) == 0


def test_missing_merge_base(repo, capsys):
    repo.commit("test: init")
    repo.git("switch", "--orphan", "other")
    repo.commit("test: unrelated")
    assert main(["check", str(repo.root), "--select", RULE, "--base", "main"]) == 2
    assert "no merge base" in capsys.readouterr().err


def test_configuration_and_baseline(repo, capsys):
    source = dedent("""\
        def test_sample():
            assert True
            assert True  # pyhyi: ignore[test-weakening] intentional
            assert True
        """)
    same = ["test_ok.py", "vendor/test_one.py", "samples/test_two.py"]
    repo.commit("test: examples", {p: source for p in same})
    repo.write(
        "pyhyi.toml",
        dedent("""\
        select = ["test-weakening"]
        exclude = ["vendor"]
        [per-path-ignores]
        "samples/**" = ["test-weakening"]
        """),
    )
    report = check(repo, capsys)
    assert [(f["path"], f["line"]) for f in report["findings"]] == [
        ("test_ok.py", 2),
        ("test_ok.py", 4),
    ]
    assert report["summary"]["suppressed"] == 1
    assert main(["baseline", str(repo.root)]) == 0
    capsys.readouterr()
    entries = json.loads((repo.root / ".pyhyi-baseline.json").read_text())["findings"]
    assert len({f["key"] for f in entries}) == 2
    repo.write(same[0], "\n" + source)
    report = check(repo, capsys)
    assert report["findings"] == []
    assert report["summary"]["baselined"] == 2
    repo.write("pyproject.toml", '[tool.pyhyi.rules.test-weakening]\nbase = "missing"')
    (repo.root / "pyhyi.toml").unlink()
    assert main(["check", str(repo.root), "--select", RULE]) == 2
    assert "cannot resolve base" in capsys.readouterr().err
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []


@pytest.mark.parametrize(
    ("imports", "base", "definition"),
    [
        ("import unittest as unit", "unit.TestCase", "def"),
        ("from unittest import IsolatedAsyncioTestCase as Case", "Case", "async def"),
    ],
)
def test_direct_unittest_methods(repo, capsys, imports, base, definition):
    original = dedent(f"""\
        {imports}
        import pytest
        class SampleChecks({base}):
            {definition} test_result(self):
                self.assertEqual(self.actual, 2)
        """)
    repo.commit("test: add case", {"test_sample.py": original})
    src = original.replace("self.assertEqual(self.actual, 2)", "assert True")
    repo.write("test_sample.py", src)
    findings = check(repo, capsys, "--base", "HEAD")["findings"]
    assert [(f["line"], f["message"]) for f in findings] == [
        (5, "SampleChecks.test_result replaced a self.assertEqual call with 'assert True'.")
    ]
    repo.write(
        "test_sample.py",
        src.replace("    " + definition, "    @pytest.mark.skip\n    " + definition),
    )
    assert [f["line"] for f in check(repo, capsys)["findings"]] == [4, 6]


@pytest.mark.parametrize(
    ("imports", "base", "extra", "decorator", "body"),
    [
        ("from local import TestCase", "TestCase", "", "", ""),
        ("from unittest import TestCase\nTestCase = Other", "TestCase", "", "", ""),
        ("from unittest import TestCase\nclass Child(TestCase): pass", "Child", "", "", ""),
        ("from unittest import TestCase", "TestCase, Mixin", "", "", ""),
        ("from unittest import TestCase", "TestCase", "def assertEqual(self, a, b): pass", "", ""),
        (
            "from unittest import TestCase",
            "TestCase",
            "def setUp(self): self.assertEqual = dummy",
            "",
            "",
        ),
        ("from unittest import TestCase", "TestCase", "", "@staticmethod", ""),
        ("from unittest import TestCase", "TestCase", "", "@classmethod", ""),
        ("from unittest import TestCase", "TestCase", "", "", "self = fake"),
    ],
)
def test_unittest_ambiguous_receiver(repo, capsys, imports, base, extra, decorator, body):
    src = (
        f"{imports}\nclass SampleChecks({base}):\n"
        + indent(extra + "\n" + decorator, "    ")
        + "\n    def test_result(self):\n"
        + indent(body, "        ")
        + "\n        self.assertEqual(actual, 2)\n"
    )
    repo.commit("test: base", {"test_sample.py": src})
    repo.write("test_sample.py", src.replace("self.assertEqual(actual, 2)", "assert True"))
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []


def test_only_direct_two_operand_equality(repo, capsys):
    calls = [
        "self.assertEqual(1, 2)",
        "self.assertEqual(actual, 1, msg='example')",
        "self.assertTrue(actual)",
        "other.assertEqual(actual, 1)",
        "self.assertEqual(*actual, 1)",
    ]
    base = "import unittest\nclass ValueChecks(unittest.TestCase):\n" + "".join(
        f"    def test_case{i}(self):\n        {call}\n" for i, call in enumerate(calls)
    )
    repo.commit("test: calls", {"test_cases.py": base})
    for call in calls:
        base = base.replace(call, "assert True")
    repo.write("test_cases.py", base)
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []


def test_no_ambiguous_skip_alias(repo, capsys):
    for i, imports in enumerate(
        [
            "from other import mark",
            "from .pytest import mark",
            "from pytest import mark\nmark = other",
            "if enabled:\n    from pytest import mark as mark",
            "from pytest import mark\nfrom extras import *",
        ]
    ):
        repo.write(f"test_alias{i}.py", f"{imports}\n@mark.skip\ndef test_case(): pass")
    repo.write(
        "test_class.py",
        "import pytest as p\nclass TestA:\n    p = other\n"
        "    @p.mark.skip\n    def test_a(self): pass",
    )
    assert check(repo, capsys)["findings"] == []


def test_changed_sources_only_and_blob_limit(repo, capsys, monkeypatch):
    source = "def test_example(): assert value"
    repo.commit(
        "test: base",
        {
            "test_active.py": source,
            "test_unchanged.py": source,
            "test_large.py": source + "\n#" + "x" * MAX_TEXT_BYTES,
            "helper.py": source,
        },
    )
    for path in ["test_active.py", "test_large.py", "helper.py", "test_new.py"]:
        repo.write(path, source.replace("value", "True"))
    read_current = []
    read_blobs = []
    read_text, run_git_bytes = Context.read_text, git.run_git_bytes

    def record_read(self, path):
        read_current.append(path)
        return read_text(self, path)

    def record_git(args, *a, **kw):
        if args[:2] == ["cat-file", "blob"]:
            read_blobs.append(args[2])
        return run_git_bytes(args, *a, **kw)

    monkeypatch.setattr(Context, "read_text", record_read)
    monkeypatch.setattr(git, "run_git_bytes", record_git)
    findings = check(repo, capsys, "--base", "HEAD")["findings"]
    assert [f["path"] for f in findings] == ["test_active.py"]
    assert set(read_current) == {"test_active.py", "test_large.py"}
    assert read_blobs == [repo.git("rev-parse", "HEAD:test_active.py").strip()]


def test_symlink_parent_cannot_escape(repo, capsys, tmp_path, monkeypatch):
    name = "test_example.py"
    src = "def test_example(): assert value"
    repo.commit("test: base", {f"cases/{name}": src})
    outside = tmp_path / "outside"
    (repo.root / "cases").rename(outside)
    (outside / name).write_text(src.replace("value", "True"), encoding="utf-8")
    try:
        (repo.root / "cases").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt" or exc.winerror != 1314:
            raise
        # A directory junction exercises parent traversal without symlink privileges.
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", "cases", r"..\outside"], cwd=repo.root, check=True
        )

    def no_read(self, path):
        pytest.fail(f"attempted to read {path}")

    monkeypatch.setattr(Context, "read_text", no_read)
    assert check(repo, capsys)["findings"] == []
    assert check(repo, capsys, "--base", "HEAD")["findings"] == []
