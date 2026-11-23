"""Exercise calculation observations through the public check command."""

import json

import pytest

from pyhyi.cli import main


def scan(repo, capsys, *args):
    exit_code = main(["check", str(repo.root), "--format", "json", *args])
    report = json.loads(capsys.readouterr().out)
    assert not report["errors"]
    return exit_code, [f for f in report["findings"] if f["rule"] == "calculation-drift"]


SELECT = ("--select", "calculation-drift")


def test_boundary_returns_two_locations_and_input(repo, capsys):
    repo.write(
        "desktop/reader.ts", "function withinRange(sample, limit) { return sample < limit }\n"
    )
    repo.write("worker/reader.py", "def within_range(sample, limit):\n    return sample <= limit\n")
    assert scan(repo, capsys)[1] == []
    code, findings = scan(repo, capsys, *SELECT)
    (finding,) = findings
    assert code == 0 and finding["severity"] == "advice"
    assert finding["path"] == "desktop/reader.ts" and finding["line"] == 1
    assert "worker/reader.py:2" in finding["hint"]
    assert "sample=limit=1" in finding["message"]
    assert "False" in finding["message"] and "True" in finding["message"]
    repo.write("pyhyi.toml", 'exclude=["worker"]')
    assert scan(repo, capsys, *SELECT)[1] == []


def test_different_function_names_agree_on_output_field(repo, capsys):
    repo.write("a.py", 'def classify(rank):\n    return {"bonus_eligible": rank < 15}\n')
    repo.write("b.js", "function preview(rank) { return { bonusEligible: rank <= 15 }; }\n")
    _, (finding,) = scan(repo, capsys, *SELECT)
    assert "bonus_eligible" in finding["message"] and "rank=15" in finding["message"]


@pytest.mark.parametrize("operator", ["<", ">=", "=="])
def test_equal_or_unrelated_predicates_are_quiet(repo, capsys, operator):
    repo.write("a.py", "def low_pressure(p, warning): return p < warning")
    repo.write("b.py", f"def low_pressure(p, warning): return p {operator} warning")
    assert scan(repo, capsys, *SELECT)[1] == []


def test_strings_and_test_helpers_do_not_pair_with_a_source_function(repo, capsys):
    repo.write("wave.py", "def is_wet(level, floor): return level < floor")
    js = "function isWet(level, floor) { return level <= floor }"
    repo.write("doc.js", f"const example = '{js}';\n/* {js} */")
    repo.write("tests/editor.js", js)
    assert scan(repo, capsys, *SELECT)[1] == []


JS_ANALOG = (
    "function gain(bias, gain, level) { return Math.round((bias + gain * level) * 100) / 100 }"
)
PY_ANALOG = """\
def sample(bias: float, gain: float, level: int):
    term = gain * level
    value = bias + term
    return round(value, 2)
"""


def test_scaled_arithmetic_models_a_real_tie(repo, capsys):
    repo.write("console/gainControl.ts", JS_ANALOG)
    repo.write("pipeline/gain_control.py", PY_ANALOG)
    _, (finding,) = scan(repo, capsys, *SELECT)
    assert finding["path"] == "console/gainControl.ts" and "gain_control.py:4" in finding["hint"]
    assert "0.125" in finding["message"] and "0.13" in finding["message"]
    assert "Python round gives 0.12" in finding["message"]


@pytest.mark.parametrize("change", ["other_module", "other_formula", "other_scale", "ambiguous"])
def test_rounding_should_require_both_anchors(repo, capsys, change):
    repo.write("gainControl.js", JS_ANALOG)
    name = "unrelated" if change == "other_module" else "gain_control"
    source = PY_ANALOG
    if change == "other_formula":
        source = source.replace("bias + term", "bias - term")
    if change == "other_scale":
        source = source.replace(", 2)", ", 3)")
    if change == "ambiguous":
        repo.write("legacy/gain_control.py", source)
    repo.write(name + ".py", source)
    assert scan(repo, capsys, *SELECT)[1] == []


def test_decimal_step_and_control(repo, capsys):
    repo.write(
        "browser.ts",
        "function snapValue(signal, spacing) { return Math.round(signal / spacing) * spacing }",
    )
    src = """\
from decimal import Decimal, ROUND_HALF_UP

def snap_value(signal: Decimal, spacing: Decimal):
    idx = (signal / spacing).to_integral_value(rounding=ROUND_HALF_UP)
    return idx * spacing
"""
    repo.write("signal.py", src)
    _, (finding,) = scan(repo, capsys, *SELECT)
    assert "Modeled product" in finding["message"] and "decimal half-up" in finding["message"]
    assert "signal.py:4" in finding["hint"]
    repo.write("signal.py", src.replace("idx * spacing", "signal"))
    assert scan(repo, capsys, *SELECT)[1] == []


def test_rounding_order_compares_results_on_the_same_sequence(repo, capsys):
    end = "def spectrum_total(bins): return round(sum(bins), 2)"
    start = "def spectrum_total(bins): return sum(round(b, 2) for b in bins)"
    repo.write("summary.py", end)
    repo.write("detail.py", start)
    _, (finding,) = scan(repo, capsys, *SELECT)
    assert "[0.125, 0.125]" in finding["message"]
    assert "0.25" in finding["message"] and "0.24" in finding["message"]
    repo.write("detail.py", start.replace("in bins)", "in bins if b > 0)"))
    assert scan(repo, capsys, *SELECT)[1] == []
    repo.write("detail.py", "from custom import round\n" + start)
    assert scan(repo, capsys, *SELECT)[1] == []
