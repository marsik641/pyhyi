import pytest

from pyhyi.paths import PathMatcher


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # bare names match at any depth, files or directories
        ("node_modules", "node_modules/a.js", True),
        ("node_modules", "web/node_modules/a/b.js", True),
        ("node_modules", "my_node_modules/a.js", False),
        ("*.log", "x.log", True),
        ("*.log", "a/b/x.log", True),
        ("*.log", "x.log.txt", False),
        # patterns with a slash are anchored at the root
        ("docs/*.md", "docs/a.md", True),
        ("docs/*.md", "docs/sub/a.md", False),
        ("docs/*.md", "x/docs/a.md", False),
        ("/build", "build/out.bin", True),
        ("/build", "src/build/out.bin", False),
        ("build/", "build/out.bin", True),
        # double star
        ("docs/**/*.md", "docs/a.md", True),
        ("docs/**/*.md", "docs/x/y/a.md", True),
        ("tests/**", "tests/a/b.py", True),
        ("**/fixtures", "fixtures/a.json", True),
        ("**/fixtures", "a/b/fixtures/c.json", True),
        # question mark and literal characters
        ("file?.txt", "file1.txt", True),
        ("file?.txt", "file10.txt", False),
        ("a+b.txt", "a+b.txt", True),
        ("a+b.txt", "aab.txt", False),
    ],
)
def test_pattern(pattern: str, path: str, expected: bool) -> None:
    assert PathMatcher([pattern]).match(path) is expected


def test_any_of_several() -> None:
    m = PathMatcher(["*.log", "dist"])
    assert m.match("dist/x.js")
    assert m.match("a.log")
    assert not m.match("src/a.py")


def test_empty_matcher_matches_nothing() -> None:
    assert not PathMatcher([]).match("anything")
