"""The ratios a model is able to report.

A panel reports the ratio of its density bucket, and a model is offered
a handful of buckets, so the set is short and closed.  Two things follow
from that, and both are checked here.

The first is that the ratio travels with the profile: the core draws
from the model's own list, so a model whose panels are all 2.625 never
reports 3.5.  The second is that the list survives the trip to the
core whole: a ratio of 2.625 that is truncated to an integer on the way
is a different ratio, and the core would draw from a list of ratios no
device has.
"""

from __future__ import annotations

from novaprofile.core.devices import DevicePreset
from novaprofile.launcher.core_document import coerce_document


def _preset_with_ratios(ratios: tuple[float, ...]) -> DevicePreset:
    return DevicePreset.model_construct(
        preset_id="test-ratio",
        screen=None,
        dpr_options=ratios,
    )


def test_a_list_of_ratios_reaches_the_core_untruncated() -> None:
    document = coerce_document(
        {"screen": {"device_pixel_ratio_options": [2.625, 3.25, 3.5]}}
    )
    assert document["screen"]["device_pixel_ratio_options"] == [2.625, 3.25, 3.5]


def test_a_ratio_of_the_profile_itself_is_kept() -> None:
    document = coerce_document({"screen": {"device_pixel_ratio": 2.625}})
    assert document["screen"]["device_pixel_ratio"] == 2.625


def test_a_ratio_that_has_to_be_whole_is_still_truncated() -> None:
    # The core reads the panel's density as an integer, and a density
    # of 411.5 does not exist.
    document = coerce_document({"screen": {"density_dpi": 411.5}})
    assert document["screen"]["density_dpi"] == 411
