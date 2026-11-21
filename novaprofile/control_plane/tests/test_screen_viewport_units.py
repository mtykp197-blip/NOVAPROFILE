"""The screen, the viewport and the units they are measured in.

Every browser reports the screen in *logical* pixels -- the unit the
CSS layout works in -- and reports the ratio between the physical
panel and that unit separately.  A phone with a 1344x2992 panel at a
ratio of 2.625 tells the page that the screen is 512x1140 and that the
ratio is 2.625.

Getting this wrong is not a cosmetic mistake.  A profile that reports
the physical numbers has a screen four times the width of its own
viewport, and a page that measures the width of a rendered element
against ``window.innerWidth`` learns the answer immediately: no mobile
browser has ever had a viewport narrower than its own screen by that
factor.

These tests pin the relationship down so that a later change cannot
quietly put the two surfaces into different units.
"""

from __future__ import annotations

import pytest

from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.schema.profile import ProfileConfig


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


@pytest.fixture(scope="module")
def resolver() -> ConsistencyResolver:
    return ConsistencyResolver()


def _fleet(gen: ProfileGenerator, count: int = 120) -> list[ProfileConfig]:
    presets = [p.preset_id for p in list_presets()]
    return [
        gen.generate(presets[index % len(presets)], master_seed=f"{index:064x}")
        for index in range(count)
    ]


def test_the_screen_and_the_viewport_share_one_unit(gen: ProfileGenerator) -> None:
    """The viewport has to fit inside the area the browser claims."""

    for profile in _fleet(gen):
        assert profile.viewport.inner_width <= profile.screen.avail_width
        assert profile.viewport.outer_width <= profile.screen.width
        assert profile.viewport.inner_height <= profile.screen.avail_height


def test_the_screen_reports_logical_pixels(gen: ProfileGenerator) -> None:
    """The screen is the logical panel, not the physical one.

    The physical panel is the logical width times the ratio.  A profile
    that reported the physical width would report a screen that is
    several times wider than the viewport it renders into.

    The bound is generous because the fleet carries foldables and
    tablets: a Pixel Fold opened is 841 logical pixels across, and a
    tablet is wider still.  What matters is that the width is nowhere
    near the physical width of the same panel.
    """

    for profile in _fleet(gen):
        ratio = profile.screen.device_pixel_ratio
        assert ratio >= 1.75
        # A logical width is a few hundred pixels -- a few hundred more
        # on a foldable or a tablet.  A physical width would be above a
        # thousand for every panel in the catalogue.
        assert profile.screen.width <= 1200
        assert profile.screen.height <= 1600


def test_the_viewport_may_not_exceed_the_available_area(
    resolver: ConsistencyResolver,
) -> None:
    """The consistency resolver has to catch a viewport that is too tall."""

    profile = _fleet(ProfileGenerator(), 1)[0]
    broken = profile.model_copy(
        update={
            "viewport": profile.viewport.model_copy(
                update={
                    "inner_height": profile.screen.avail_height + 40,
                    "outer_height": profile.screen.avail_height + 80,
                }
            )
        }
    )
    report = resolver.check(broken)
    assert "screen_viewport" in {issue.axis for issue in report.errors}


def test_the_safe_area_insets_vary_between_the_units(gen: ProfileGenerator) -> None:
    """The safe area is not a constant.

    A phone with a punch hole reports a taller inset than a flat
    panel, and a fleet where every profile reports the same inset is a
    fleet a vendor can group on.
    """

    tops = {profile.viewport.safe_area_insets.top for profile in _fleet(gen, 200)}
    assert len(tops) > 1
