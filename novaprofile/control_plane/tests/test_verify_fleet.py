"""Every profile has to survive the browser, not just the one.

A verification run proves that a single profile agrees with a live
browser.  It does not prove that the *other* profiles do -- and the
fleet is where the interesting profiles live, because the fleet is
where the per-unit variation lives: the panel variants, the density
buckets, the pruned font lists, the second GPU SKU.

A change that makes one profile pass can easily make another one
fail.  A panel variant whose logical size rounds badly, a density
bucket the runtime does not translate, a font set the runtime
filters differently than the generator built it -- each of those
shows up only in the units that carry the odd value.

So this runs the whole harness over a spread of profiles and demands
that every one of them agrees with the browser on every axis.
"""

from __future__ import annotations

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.probe.verify import HOST_DEPENDENT_AXES, VerifyRunner


def _profile(preset_id: str, seed: str):
    return ProfileGenerator().generate(preset_id, name=f"Verify {preset_id}", master_seed=seed)


#: A spread that covers the variation the generator produces: the
#: panel variants, the density buckets, the font tiers, the GPU SKUs
#: and the locale scripts.
SPREAD: tuple[tuple[str, str], ...] = (
    ("pixel-8-pro-android14", "1" * 64),
    ("galaxy-s23-android14", "2" * 64),
    ("galaxy-a54-android13", "3" * 64),
    ("xiaomi-13-android14", "4" * 64),
    ("oppo-find-x6-android14", "5" * 64),
    ("vivo-x90-android14", "6" * 64),
    ("redmi-note-12-android13", "7" * 64),
    ("samsung-galaxy-a14-android13", "8" * 64),
    ("oneplus-11-android13", "9" * 64),
    ("galaxy-s24-ultra-android14", "a" * 64),
    ("pixel-7-android14", "b" * 64),
)


@pytest.mark.parametrize(("preset_id", "seed"), SPREAD, ids=[p for p, _ in SPREAD])
def test_the_profile_agrees_with_the_browser_on_every_axis(preset_id, seed) -> None:
    """The whole harness, over one profile of the spread."""

    runner = VerifyRunner(_profile(preset_id, seed), headless=True, timeout=180.0)
    report = runner.run_sync()

    assert report.error == "", report.error
    assert report.has_runtime is True
    assert len(report.axes) == 21

    failed = [item.axis for item in report.axes if item.verdict == "fail"]
    assert failed == [], f"these axes disagree with the profile: {failed}"

    unavailable = [item.axis for item in report.axes if item.verdict == "unavailable"]
    assert unavailable == [], f"these axes were not compared: {unavailable}"

    # An axis whose values come from the machine the verification ran
    # on cannot agree with the profile -- the profile describes a
    # handset, the verification runs on a desktop.  Such an axis is
    # reported rather than counted, and it is not a failure.  What
    # matters is that it is one of the axes the design knows it cannot
    # settle, and that every *comparable* axis matched.
    host = [item.axis for item in report.axes if item.verdict == "host-dependent"]
    assert set(host) <= set(HOST_DEPENDENT_AXES), f"these axes should have been comparable: {host}"

    comparable = [item for item in report.axes if item.verdict in {"pass", "fail"}]
    assert comparable, "no axis was comparable"
    for item in comparable:
        assert item.verdict == "pass", f"{item.axis} disagrees: " + ", ".join(
            f"{f.field}: {f.expected} != {f.actual}" for f in item.mismatches
        )

    # The axes that moved when the panel variants and the density
    # buckets were widened are the ones that have to be watched: a
    # profile whose panel rounds badly reaches the browser as a
    # different screen, and a font set the runtime filters differently
    # than the generator built it reaches the browser as a different
    # font list.
    screen = next(item for item in report.axes if item.axis == "screen")
    for field in ("width", "height", "devicePixelRatio"):
        item = next(f for f in screen.fields if f.field == field)
        assert item.verdict == "match", f"screen.{field}: {item.expected} != {item.actual}"

    fonts = next(item for item in report.axes if item.axis == "fonts")
    installed = next(f for f in fonts.fields if f.field == "declaredInstalled")
    assert installed.verdict == "match", f"fonts: {installed.expected} != {installed.actual}"

    gl = next(item for item in report.axes if item.axis == "webgl")
    renderer = next(f for f in gl.fields if f.field == "unmaskedRenderer")
    assert renderer.verdict == "match", f"renderer: {renderer.expected} != {renderer.actual}"


def test_the_spread_covers_every_preset() -> None:
    """The spread has to reach every family the product ships."""

    covered = {preset_id for preset_id, _ in SPREAD}
    shipped = {preset.preset_id for preset in DEVICE_PRESETS}
    assert shipped - covered == set(), (
        f"these families are not covered by the live verification: {sorted(shipped - covered)}"
    )
