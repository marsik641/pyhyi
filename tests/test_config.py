from pathlib import Path

import pytest

from pyhyi.config import DEFAULT_BASELINE, Config, load_config, parse_config
from pyhyi.errors import ConfigError


def write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_defaults_without_config(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()
    assert Config().baseline == DEFAULT_BASELINE


def test_pyproject_tool_table(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        """
[project]
name = "demo"

[tool.pyhyi]
select = ["a"]
ignore = ["b"]
exclude = ["vendor"]
baseline = "ci/baseline.json"

[tool.pyhyi.per-path-ignores]
"tests/**" = ["c"]

[tool.pyhyi.rules.a]
limit = 3
""",
    )
    config = load_config(tmp_path)
    assert config.source == tmp_path / "pyproject.toml"
    assert config.select == ("a",)
    assert config.ignore == ("b",)
    assert config.exclude == ("vendor",)
    assert config.baseline == "ci/baseline.json"
    assert config.per_path_ignores == {"tests/**": ("c",)}
    assert config.rules == {"a": {"limit": 3}}


def test_pyproject_without_tool_table_is_skipped(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "demo"\n')
    assert load_config(tmp_path) == Config()


def test_standalone_file_wins_over_pyproject(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[tool.pyhyi]\nselect = ["from-pyproject"]\n')
    write(tmp_path, ".pyhyi.toml", 'select = ["from-dotfile"]\n')
    assert load_config(tmp_path).select == ("from-dotfile",)
    write(tmp_path, "pyhyi.toml", 'select = ["from-pyhyi-toml"]\n')
    assert load_config(tmp_path).select == ("from-pyhyi-toml",)


def test_select_absent_means_default_selection() -> None:
    assert parse_config({}).select is None
    assert parse_config({"select": []}).select == ()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"selekt": ["a"]}, "unknown key"),
        ({"select": "a"}, "select must be a list of strings"),
        ({"exclude": [1]}, "exclude must be a list of strings"),
        ({"per-path-ignores": ["x"]}, "per-path-ignores must be a table"),
        ({"per-path-ignores": {"x": "a"}}, "per-path-ignores.x must be a list"),
        ({"baseline": ""}, "baseline must be a non-empty"),
        ({"rules": {"a": 1}}, "rules.a must be a table"),
    ],
)
def test_invalid_config(data: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_invalid_toml(tmp_path: Path) -> None:
    write(tmp_path, "pyhyi.toml", "select = [\n")
    with pytest.raises(ConfigError, match=r"pyhyi\.toml: invalid TOML"):
        load_config(tmp_path)


def test_error_mentions_source_file(tmp_path: Path) -> None:
    write(tmp_path, "pyhyi.toml", "bogus = 1\n")
    with pytest.raises(ConfigError, match=r"pyhyi\.toml: unknown key\(s\): bogus"):
        load_config(tmp_path)
