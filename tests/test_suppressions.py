import pytest

from pyhyi.suppressions import Suppression, parse_suppression, scan


@pytest.mark.parametrize(
    ("line", "rules", "reason"),
    [
        ("x = 1  # pyhyi: ignore[rule-a] generated file", ("rule-a",), "generated file"),
        ("// pyhyi: ignore[a, b]: kept for API compat", ("a", "b"), "kept for API compat"),
        (
            "<!-- pyhyi: ignore[broken-link] external mirror -->",
            ("broken-link",),
            "external mirror",
        ),
        ("/* pyhyi: ignore[a] - legacy */", ("a",), "legacy"),
        ("-- pyhyi: ignore[a] — sql comment", ("a",), "sql comment"),
        ("{# pyhyi: ignore[a] template #}", ("a",), "template"),
        ("# pyhyi:ignore[a]", ("a",), ""),
        ("# pyhyi: ignore[] why", (), "why"),
    ],
)
def test_parse(line: str, rules: tuple[str, ...], reason: str) -> None:
    assert parse_suppression(line, 7) == Suppression(line=7, rules=rules, reason=reason)


@pytest.mark.parametrize(
    "line",
    [
        "plain text",
        'hint = "`pyhyi: ignore[rule-id] reason`"',  # inside a string, no comment opener
        "Use pyhyi: ignore[x] in prose",
        "Write `# pyhyi: ignore[x] reason` to suppress.",  # quoted in backticks
        "url = 'http://host/#pyhyi: ignore[x] y'",  # opener glued to text
        "# pyhyi: noqa[x] wrong verb",
    ],
)
def test_not_a_suppression(line: str) -> None:
    assert parse_suppression(line, 1) is None


def test_scan_numbers_lines() -> None:
    text = "a\n# pyhyi: ignore[x] one\nb\n// pyhyi: ignore[y] two\n"
    assert sorted(scan(text)) == [2, 4]
    assert scan("nothing here") == {}
