import json

from pyhyi.cli import main


def _function(name, score, *, method=False):
    if score is None:
        return ""
    indent = "    " if method else ""
    checks = "".join(
        f"{indent}    if value == {i}:\n{indent}        return {i}\n" for i in range(score - 1)
    )
    return f"{indent}def {name}(value):  # noqa: C901\n{checks}{indent}    return -1\n"


def _check(repo, capsys, *args):
    assert (
        main(["check", str(repo.root), "--select", "complexity-growth", "--format", "json", *args])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["errors"] == []
    return data["findings"]


def test_only_current_repeated_growth_is_reported(repo, capsys):
    revisions = []
    for index, (growing, jump, reset, readded) in enumerate(
        [(7, 7, 7, 7), (9, 7, 9, 9), (11, 7, 15, None), (14, 14, 14, 14)]
    ):
        code = "class Worker:\n" + _function("calculate", growing, method=True)
        code += _function("stable", 16) + _function("jump", jump)
        code += _function("reset", reset) + _function("readded", readded)
        revisions.append(repo.commit(f"feat: step {index}", {"src/work.py": code}))

    repo.commit("docs: unrelated edit", {"README.md": "# Example\n"})
    repo.write("src/work.py", "def local_only(): pass\n")

    (finding,) = _check(repo, capsys)
    assert finding["path"] == "src/work.py"
    assert finding["line"] == 2
    assert finding["severity"] == "advice"
    assert finding["message"] == "`Worker.calculate` McCabe complexity grew 7 -> 9 -> 11 -> 14."
    assert all(rev[:12] in finding["hint"] for rev in revisions)

    assert main(["check", str(repo.root)]) == 0
    assert "complexity-growth" not in capsys.readouterr().out


def test_follows_file_rename_and_filters_by_merge_base(repo, capsys):
    repo.commit("feat: first version", {"original.py": _function("calculate", 7)})
    repo.commit("feat: more branches", {"original.py": _function("calculate", 9)})
    repo.git("mv", "original.py", "renamed.py")
    repo.commit("refactor: rename module")
    repo.commit("feat: two more branches", {"renamed.py": _function("calculate", 11)})
    repo.git("branch", "base")
    repo.commit("feat: final increases", {"renamed.py": _function("calculate", 14)})

    (finding,) = _check(repo, capsys, "--base", "base")
    assert finding["path"] == "renamed.py"
    assert "7 -> 9 -> 11 -> 14" in finding["message"]

    repo.git("branch", "updated-base")
    repo.commit(
        "feat: update another function",
        {"renamed.py": _function("calculate", 14) + _function("other", 10)},
    )
    assert _check(repo, capsys, "--base", "updated-base") == []

    repo.write(
        "pyhyi.toml",
        'select = ["complexity-growth"]\n[rules.complexity-growth]\nbase = "updated-base"\n',
    )
    assert _check(repo, capsys) == []
    assert len(_check(repo, capsys, "--base", "base")) == 1


def test_ambiguous_definition_and_window_break_the_series(repo, capsys):
    for index, score in enumerate((7, 9, 11, 14)):
        code = _function("calculate", score)
        if index == 1:
            code += _function("calculate", score)
        repo.commit(
            f"feat: step {index}", {"sample.py": code, "other.py": _function("valid", score)}
        )

    (finding,) = _check(repo, capsys)
    assert finding["path"] == "other.py"
    repo.write("pyhyi.toml", "[rules.complexity-growth]\nmax-commits = 2\n")
    assert _check(repo, capsys) == []


def test_unavailable_comparison_base_is_reported(repo, capsys):
    repo.commit("feat: initial")
    assert (
        main(["check", str(repo.root), "--select", "complexity-growth", "--base", "missing"]) == 2
    )
    assert "complexity-growth: cannot resolve base" in capsys.readouterr().err
