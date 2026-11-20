import json

import pytest

from pyhyi.cli import main

MODULE = "src/billing.py"
CASE = "tests/test_billing.py"
NODE = CASE + "::test_boundary"
FIXED = "def charge(n):\n    return 10 if n >= 5 else 0\n"
TEST = "from billing import charge\n\ndef test_boundary():\n    assert charge(5) == 10\n"


@pytest.fixture
def history(repo):
    repo.commit("feat: charge", {MODULE: FIXED.replace(">=", ">")})
    fix = repo.commit("fix: charge at threshold", {MODULE: FIXED, CASE: TEST})
    return repo, fix


def replay(history, capsys, *extra):
    repo, fix = history
    code = main(
        [
            "replay",
            str(repo.root),
            "--fix",
            fix,
            "--source",
            MODULE,
            "--test",
            NODE,
            "--format",
            "json",
            *extra,
        ]
    )
    return code, json.loads(capsys.readouterr().out)


def test_caught_and_isolated(history, capsys):
    repo, _ = history
    repo.write(CASE, "raise RuntimeError('local draft')\n")
    repo.write("local.txt", "draft")
    before = repo.git("status", "--porcelain")
    code, result = replay(history, capsys)
    assert (code, result["status"]) == (0, "caught")
    assert [t["outcome"] for t in result["trials"]] == [
        "passed",
        "assertion",
        "passed",
        "assertion",
    ]
    assert repo.git("status", "--porcelain") == before
    assert (repo.root / CASE).read_text().startswith("raise RuntimeError")


def test_loss_with_renamed_current_test_and_unrelated_source_edit(history, capsys):
    repo, _ = history
    repo.git("mv", CASE, "tests/test_current.py")
    repo.commit(
        "refactor: update charge tests",
        {
            MODULE: "def unrelated():\n    return 3\n\n\n" + FIXED,
            "tests/test_current.py": TEST.replace("charge(5)", "charge(6)"),
        },
    )
    code, result = replay(history, capsys, "--current-test", "tests")
    assert (code, result["status"]) == (1, "survived")
    assert [t["outcome"] for t in result["trials"]] == [
        "passed",
        "assertion",
        "passed",
        "passed",
        "passed",
        "assertion",
    ]
    assert result["trials"][-1]["assertions"] == result["trials"][1]["assertions"]


def test_witness_can_restore_historical_fixture(repo, capsys):
    repo.commit("feat: charge", {MODULE: FIXED.replace(">=", ">")})
    fixture = "import pytest\n\n@pytest.fixture\ndef quantity():\n    return 5\n"
    test = TEST.replace("test_boundary()", "test_boundary(quantity)").replace(
        "charge(5)", "charge(quantity)"
    )
    fix = repo.commit("fix: threshold", {MODULE: FIXED, CASE: test, "tests/conftest.py": fixture})
    repo.commit(
        "test: change fixture", {"tests/conftest.py": fixture.replace("return 5", "return 6")}
    )
    code, report = replay((repo, fix), capsys)
    assert (code, report["status"]) == (2, "inconclusive")
    code, report = replay((repo, fix), capsys, "--fixture", "tests/conftest.py")
    assert (code, report["status"]) == (1, "survived")


@pytest.mark.parametrize(
    ("source", "test", "phase"),
    [
        (FIXED, TEST.replace("assert charge(5) == 10", "import pytest\n    pytest.skip()"), "head"),
        (FIXED, TEST.replace("charge(5) == 10", "charge(6) == charge(5)"), "head-reversed"),
        (FIXED.replace("10 if", "9 + 1 if"), TEST, "head-reversed"),
        (FIXED, TEST.replace("charge(5)", "charge(0)"), "head"),
    ],
)
def test_skip_other_failure_conflict_or_red_control_is_inconclusive(
    history, capsys, source, test, phase
):
    repo, _ = history
    repo.commit("refactor: change billing", {MODULE: source, CASE: test})
    code, result = replay(history, capsys)
    assert (code, result["status"], result["trials"][-1]["phase"]) == (2, "inconclusive", phase)


def test_no_historical_failure_is_inconclusive(repo, capsys):
    repo.commit("feat: charge", {MODULE: FIXED.replace(">=", ">")})
    fix = repo.commit(
        "fix: boundary", {MODULE: FIXED, CASE: TEST.replace("charge(5)", "charge(6)")}
    )
    code, result = replay((repo, fix), capsys)
    assert (code, result["status"], result["trials"][-1]["phase"]) == (
        2,
        "inconclusive",
        "fix-reversed",
    )


def test_list_fix_candidates_and_reject_nonproduction_path(history, capsys):
    repo, fix = history
    assert main(["replay", str(repo.root), "--list", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"fix": fix, "subject": "fix: charge at threshold", "sources": [MODULE], "tests": [CASE]}
    ]
    assert main(["replay", str(repo.root), "--fix", fix, "--test", NODE, "--source", CASE]) == 2
    assert "production Python file" in capsys.readouterr().err


def test_one_hunk_restores_only_its_own_behavior(repo, capsys):
    gap = "\n" * 9
    old = FIXED.replace(">=", ">") + gap + "flag = False\n"
    new = FIXED + gap + "flag = True\n"
    test = TEST.replace("from billing import charge", "from billing import charge, flag").replace(
        "    assert charge", "    assert flag\n    assert charge"
    )
    repo.commit("feat: initial billing", {MODULE: old})
    fix = repo.commit("Handle the boundary", {MODULE: new, CASE: test})
    assert main(["replay", str(repo.root), "--list", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["fix"] == fix
    code, result = replay((repo, fix), capsys, "--hunk", "1")
    assert (code, result["status"]) == (0, "caught")
    assert result["trials"][1]["assertions"][0]["statement"] == "assert charge(5) == 10"


def test_unittest_subtests_count_as_one_historical_item(repo, capsys):
    repo.commit("feat: charge", {MODULE: FIXED.replace(">=", ">")})
    test = (
        "from unittest import TestCase\nfrom billing import charge\n\n"
        "class BillingTests(TestCase):\n"
        "    def test_boundary(self):\n"
        "        for n in (5, 6):\n"
        "            with self.subTest(n=n):\n"
        "                self.assertEqual(charge(n), 10)\n"
    )
    fix = repo.commit("fix: charge at threshold", {MODULE: FIXED, CASE: test})
    code, report = replay((repo, fix), capsys, "--test", CASE + "::BillingTests::test_boundary")
    assert (code, report["status"]) == (0, "caught")
    assert [t["count"] for t in report["trials"]] == [1, 1, 1, 1]
