import json
from collections import Counter
from pathlib import Path

import pytest

from pyhyi import baseline
from pyhyi.errors import PyhyiError
from pyhyi.findings import Finding


def f(rule: str = "r", path: str | None = "a.txt", message: str = "m", **kw) -> Finding:
    return Finding(rule=rule, message=message, path=path, **kw)


def test_fingerprint_ignores_line_and_prefers_key() -> None:
    assert baseline.fingerprint(f(line=1)) == baseline.fingerprint(f(line=99))
    assert baseline.fingerprint(f(key="k")) == ("r", "a.txt", "k")


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "baseline.json"
    findings = [f(), f(line=5), f(rule="other", path=None, message="repo-level")]
    baseline.dump(path, findings)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == baseline.VERSION
    assert data["findings"][0] == {"rule": "other", "path": None, "key": "repo-level", "count": 1}
    assert baseline.load(path) == Counter(
        {("r", "a.txt", "m"): 2, ("other", None, "repo-level"): 1}
    )


def test_dump_is_lf_and_unicode(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    baseline.dump(path, [f(path="данные.txt")])
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert "данные.txt" in raw.decode("utf-8")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert baseline.load(tmp_path / "nope.json") is None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"version": 99, "findings": []}',
        '{"version": 1, "findings": [{"rule": "r"}]}',
        '{"version": 1, "findings": [{"rule": "r", "path": null, "key": "k", "count": 0}]}',
    ],
)
def test_load_rejects_bad_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "b.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PyhyiError):
        baseline.load(path)


def test_apply_counts_and_stale() -> None:
    known = Counter({("r", "a.txt", "m"): 2, ("r", "gone.txt", "m"): 1, ("skipped", None, "x"): 4})
    findings = [f(), f(), f(), f(path="new.txt")]
    new, old, stale = baseline.apply(findings, known, active_rules={"r"})
    assert len(old) == 2
    assert [x.path for x in new] == ["a.txt", "new.txt"]
    # gone.txt was fixed; "skipped" did not run, so it is not reported as stale
    assert stale == 1
