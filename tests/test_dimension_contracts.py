import pytest

from pyhyi.config import Config
from pyhyi.context import Context
from pyhyi.findings import Severity
from pyhyi.rules import Rule
from pyhyi.rules.dimension_contracts import observations


class Advice(Rule):
    id = "calculation-drift"
    severity = Severity.ADVICE


SERVER = """\
def require_image(metadata):
    x, y = metadata["crop_width_px"], metadata["crop_height_px"]
    limit_x, limit_y = metadata.get("image_width_px"), metadata.get("image_height_px")
    def exceeds(a, b, c, d):
        return a > c or b > d
    if exceeds(x, y, limit_x, limit_y):
        raise ValueError("Crop falls outside image")

def measure(metadata):
    metadata.update(pixel_count=42, aspect_ratio=1.25)
    require_image(metadata)
    return metadata
"""

CLIENT = """\
export function cropPreview(metadata) {
    const problems = []
    const x = metadata.crop_width_px, y = metadata.crop_height_px
    if (x <= 0 || y <= 0) problems.push("empty")
    return { valid: problems.length === 0, pixel_count: x * y, aspect_ratio: x / y }
}
"""


def scan(repo, client=CLIENT, server=SERVER):
    repo.write("camera.py", server)
    repo.write("editor.js", client)
    return list(observations(Context(repo.root, Config()), Advice()))


def test_paired_dimension_advice_is_grounded_but_does_not_claim_a_runtime_failure(repo):
    (finding,) = scan(repo)
    assert finding.severity == Severity.ADVICE
    assert finding.path == "editor.js"
    assert finding.line == 1
    assert "Review candidate" in finding.message
    assert "cropPreview and measure return fields" in finding.message
    assert "require_image compares" in finding.message
    assert "image_width_px" in finding.message
    assert "crop_height_px" in finding.message
    assert "camera.py:6" in finding.hint
    assert "whether both layers" in finding.hint


@pytest.mark.parametrize(
    "extra",
    [
        "if (x > metadata.image_width_px || y > metadata.image_height_px) problems.push('size')",
        "remoteValidate(metadata)",
        "metadata[computedKey]()",
    ],
)
def test_unknown_or_explicit_validation_is_not_flagged(repo, extra):
    assert not scan(repo, CLIENT.replace("return {", extra + "\n    return {"))


def test_two_shared_outputs_are_required(repo):
    assert not scan(repo, CLIENT.replace("pixel_count", "megapixels"))


def test_unused_guard_is_not_joined_to_output_fields(repo):
    assert not scan(repo, server=SERVER.replace("    require_image(metadata)\n", ""))


def test_independent_positive_checks_are_not_a_size_relation(repo):
    assert not scan(
        repo, server=SERVER.replace("a > c or b > d", "a > 0 or c > 0 or b > 0 or d > 0")
    )


@pytest.mark.parametrize(
    "extra",
    [
        "    exceeds = lambda *args: False\n",
        "    if feature_enabled:\n        exceeds = skip_this_rule\n",
    ],
)
def test_shadowed_comparison_helper_is_not_used(repo, extra):
    assert not scan(repo, server=SERVER.replace("    if exceeds(", extra + "    if exceeds("))


def test_local_shadow_of_guard_in_result_builder(repo):
    server = SERVER.replace("def measure(metadata):", "def measure(metadata, require_image):")
    assert not scan(repo, server=server)


def test_two_backend_calculations_for_same_contract_are_ambiguous(repo):
    repo.write("alternate.py", SERVER.replace("measure(metadata)", "estimate(metadata)"))
    assert not scan(repo)


def test_fields_from_separate_return_values_are_not_combined(repo):
    client = CLIENT.replace(
        "return { valid:", "if (x === y) return { pixel_count: x*y }; return { valid:"
    )
    assert not scan(repo, client.replace("pixel_count: x * y, ", ""))


@pytest.mark.parametrize("published", [True, False])
def test_mapped_records_must_be_returned_by_the_validating_function(repo, published):
    client = """
export function cropPreview(album) {
    const checks = [];
    const crops = album.items.map(item => {
        const x = item.crop_width_px, y = item.crop_height_px;
        return { pixel_count: x*y, aspect_ratio: x/y };
    })
    return { valid: checks.length === 0, crops }
}
"""
    if not published:
        client = client.replace("=== 0, crops", "=== 0, summary: 1")
    assert len(scan(repo, client)) == int(published)
