from pyhyi.cli import main


def test_preview_apply_and_repeat(repo, capsys):
    repo.commit("docs: original", {"Old.md": "text\n", "docs/Guide.md": "guide\n"})
    repo.git("mv", "Old.md", "New name.md")
    repo.commit("docs: move")
    path = repo.write("README.md")
    original = (
        b'[link](Old.md?q=1#part "title") ![img](/docs/guide.md)\r\n'
        b'\r\n[ref]: <Old.md> "title"\r\n'
        b'<a href="Old.md">visit</a>\r\n'
        b"<!-- [example](Old.md) -->\r\n"
        b"`[literal](Old.md)` and [missing](unknown.md)\r\n"
    )
    path.write_bytes(original)
    args = ["fix", str(repo.root)]
    assert main([*args, "--dry-run"]) == 0
    assert "README.md:1: Old.md?q=1#part -> New%20name.md?q=1#part" in capsys.readouterr().out
    assert path.read_bytes() == original
    assert main(args) == 0
    assert path.read_bytes() == original.replace(b"Old.md?", b"New%20name.md?").replace(
        b"/docs/guide.md", b"/docs/Guide.md"
    ).replace(b"<Old.md>", b"<New%20name.md>").replace(b'href="Old.md"', b'href="New%20name.md"')
    assert main(args) == 0
    assert "0 link edit(s)" in capsys.readouterr().out


def test_fix_obeys_baseline_and_selection(repo):
    repo.commit("docs: link", {"Guide.md": "v", "README.md": "[guide](guide.md)\n"})
    args = ["fix", str(repo.root)]
    assert main(["baseline", str(repo.root)]) == 0
    assert main(args) == 0
    (repo.root / ".pyhyi-baseline.json").unlink()
    repo.write("pyhyi.toml", 'ignore = ["broken-doc-link"]\n')
    assert main(args) == 0
    assert (repo.root / "README.md").read_text() == "[guide](guide.md)\n"


def test_multiple_historical_destinations_are_left_alone(repo):
    repo.commit("docs: first", {"old.md": "one", "README.md": "[x](old.md)"})
    repo.git("mv", "old.md", "one.md")
    repo.commit("docs: move first")
    repo.commit("docs: second", {"old.md": "two"})
    repo.git("mv", "old.md", "two.md")
    repo.commit("docs: move second")
    assert main(["fix", str(repo.root)]) == 0
    assert (repo.root / "README.md").read_text() == "[x](old.md)"
