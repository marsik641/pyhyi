import pytest

from pyhyi.comments import comment_text


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
    ],
)
def test_no_comment(line: str, path: str) -> None:
    assert comment_text(line, path) is None
