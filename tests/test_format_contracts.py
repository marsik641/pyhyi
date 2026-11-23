"""Synthetic sensor readings exercise the static display-precision checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyhyi.config import parse_config
from pyhyi.context import Context
from pyhyi.rules import Rule
from pyhyi.rules.format_contracts import observations

if TYPE_CHECKING:
    from conftest import GitRepo


class FormatRule(Rule):
    id = "calculation-drift"


SENSOR = """\
const compact = new Intl.NumberFormat("en", {
  maximumFractionDigits: 2,
})
export function dial(reading: number, increment: number): string {
  if (increment < 1) return compact.format(reading)
  return String(reading)
}
"""


def results(repo: GitRepo):
    return list(observations(Context(repo.root, parse_config({})), FormatRule()))


def test_formatter_literal_at_resolved_import(repo: GitRepo) -> None:
    repo.write("app/dial.ts", SENSOR)
    repo.write("app/panel.ts", "import { dial as show } from './dial'\nshow(2.375, 0.005)\n")
    (finding,) = results(repo)
    assert finding.path == "app/dial.ts"
    assert finding.line == 4
    assert "2.375" in finding.message and "2.38" in finding.message
    assert "Literal call at app/panel.ts:2" in finding.hint


def test_vue_call_and_sample_are_marked_as_unlinked(repo: GitRepo) -> None:
    repo.write("sensors/dial.ts", SENSOR)
    repo.write(
        "screens/Gauge.vue",
        "<script setup>\nimport { dial } from '../sensors/dial'\n</script>\n"
        "<template>{{ dial(sample.voltage, sample.graduation) }}</template>\n",
    )
    repo.write("calibration.json", '{"examples":[{"voltage":2.375,"graduation":"0.005"}]}')
    (finding,) = results(repo)
    assert "Gauge.vue:4" in finding.hint
    assert "calibration.json" in finding.hint and "unverified" in finding.hint


def test_arrow_optional_step_and_fixed_digits(repo: GitRepo) -> None:
    repo.write(
        "lib/label.ts",
        "const view = (n: number | null, quantum?: number) =>\n"
        "  n == null ? 'absent' : quantum !== undefined && quantum <= 1 ? n.toFixed(0) : n\n"
        "view(-3.5, 0.5)\n",
    )
    (finding,) = results(repo)
    assert "-4" in finding.message


@pytest.mark.parametrize(
    "use",
    [
        "dial(2.375, 0.1)",  # Off the declared grid.
        "dial(2.35, 0.005)",  # Display preserves this reading.
        "dial(2.375, 2)",  # Outside this branch.
        "dial(row.voltage, other.graduation)",  # Different records.
        "dial(row.voltage, row.graduation)",  # JSON has no matching pair of fields.
        "// dial(2.375, 0.005)\n'dial(2.375, 0.005)'",
    ],
)
def test_unconfirmed_example_is_quiet(repo: GitRepo, use: str) -> None:
    repo.write("app/sensor.ts", SENSOR + use)
    repo.write("scale.json", '[{"voltage": 2.375}, {"graduation": 0.005}]')
    assert results(repo) == []


def test_different_formatter_and_unrelated_module_are_quiet(repo: GitRepo) -> None:
    repo.write("app/dial.ts", SENSOR)
    repo.write("app/other.ts", "export const dial = (x, y) => x.toString()")
    repo.write("app/panel.ts", "import { dial } from './other'\ndial(2.375, 0.005)")
    repo.write("app/compact.ts", SENSOR.replace("maximumFractionDigits: 2", "notation: 'compact'"))
    repo.write("app/display.ts", "import { dial } from './compact'\ndial(2.375, 0.005)")
    assert results(repo) == []


def test_to_fixed_and_intl_round_the_same_decimal_tie_differently(repo: GitRepo) -> None:
    repo.write(
        "gauge.js",
        "const asText = (n, resolution) => resolution < 1 ? n.toFixed(1) : n\nasText(2.55, 0.05)\n",
    )
    repo.write(
        "gauge.ts",
        SENSOR.replace("maximumFractionDigits: 2", "maximumFractionDigits: 1")
        + "dial(2.55, 0.05)\n",
    )
    observed = {finding.path: finding.message for finding in results(repo)}
    assert observed["gauge.js"].endswith("would yield 2.5.")
    assert observed["gauge.ts"].endswith("would yield 2.6.")


def test_wrapper_result_is_unknown(repo: GitRepo) -> None:
    repo.write(
        "panel.js",
        "const badge = (n, resolution) => resolution < 1 ? lookup(n.toFixed(1)) : n\n"
        "badge(2.55, 0.05)\n",
    )
    (finding,) = results(repo)
    assert "input to lookup" in finding.hint and "return value is unknown" in finding.hint


@pytest.mark.parametrize(
    "caller",
    [
        "function other(dial) { dial(2.375, 0.005) }",
        "function other() { const dial = f; dial(2.375, 0.005) }",
    ],
)
def test_shadowed_import_is_quiet(repo: GitRepo, caller: str) -> None:
    repo.write("app/dial.ts", SENSOR)
    repo.write("app/panel.ts", "import { dial } from './dial'\n" + caller)
    assert results(repo) == []


def test_locally_shadowed_formatter_and_ambiguous_import_are_quiet(repo: GitRepo) -> None:
    repo.write(
        "app/main.ts", SENSOR + "\nfunction other() { const compact = adapt() }\ndial(2.375, 0.005)"
    )
    repo.write("lib/dial.ts", SENSOR)
    repo.write("lib/dial.js", "export const dial = (x) => x")
    repo.write("app/client.js", "import { dial } from '../lib/dial'\ndial(2.375, 0.005)")
    assert results(repo) == []
