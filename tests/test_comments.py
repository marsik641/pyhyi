import pytest

from pyhyi.comments import comment_text, iter_comments


@pytest.mark.parametrize(
    ("line", "path", "expected"),
    [
        ("x = 1  # TODO: y", "a.py", " TODO: y"),
        ("# TODO", "a.py", " TODO"),
        ("// FIXME later", "a.ts", " FIXME later"),
        ("/* HACK */", "a.c", " HACK */"),
        (" * TODO in a docblock", "a.js", " TODO in a docblock"),
        ("-- XXX", "a.sql", " XXX"),
        ("<!-- TODO -->", "README.md", " TODO -->"),
        ("{# TODO #}", "page.jinja", " TODO #}"),
        ("echo hi  # TODO later", "run.sh", " TODO later"),
        ("echo it's here  # TODO", "run.sh", " TODO"),  # unclosed apostrophe
        ("build:  # TODO", "Makefile", " TODO"),
        ("key: 1  # TODO", "config.yaml", " TODO"),
        ("# TODO", ".env.example", " TODO"),
        ("% TODO", "paper.tex", " TODO"),
        ("<!-- TODO -->", "App.vue", " TODO -->"),
        ("const a = 'x'  // TODO", "App.vue", " TODO"),
    ],
)
def test_comment_found(line: str, path: str, expected: str) -> None:
    assert comment_text(line, path) == expected


@pytest.mark.parametrize(
    ("line", "path"),
    [
        ("# TODO list", "README.md"),  # Markdown heading, not a comment
        ("* TODO item", "notes.md"),  # Markdown bullet
        ('s = "#TODO"', "a.py"),  # opener glued to a quote
        ("url = 'http://host/a'", "a.py"),
        ("plain text", "a.txt"),
        ("*/", "a.c"),
        ('echo "progress: # TODO items"', "run.sh"),  # quoted text
        ('const help = "usage // TODO";', "app.js"),
        ("const help = `usage // TODO`;", "app.ts"),
        ("* TODO: describe", "plan.rst"),  # unknown file types have no comments
        ("* TODO write", "notes.org"),
        ("# TODO", "notes.txt"),
        ('{"note": "x # TODO"}', "data.json"),
        ("x = a % TODO", "a.js"),  # not a JS comment opener
        ("ls -- TODO", "run.sh"),
    ],
)
def test_no_comment(line: str, path: str) -> None:
    assert comment_text(line, path) is None


def test_shebang_selects_hash_comments() -> None:
    source = "#!/bin/sh\n# TODO: one\necho done\n"
    assert [(n, body) for n, _, body in iter_comments(source, "bin/tool")] == [
        (1, "!/bin/sh"),
        (2, " TODO: one"),
    ]
    assert list(iter_comments("# TODO: one\n", "bin/tool")) == []
