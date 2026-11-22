"""Tests for the per-profile Chromium major draw.

The generator used to pin every profile of a given Android release to the
same Chromium major, which meant a fleet of 7,222 device presets carried
only a handful of distinct browser versions.  That is exactly the kind of
field a vendor groups on: the Chrome major is one of the few things a
device actually reveals about how old its browser is.

The draw has to satisfy four properties at once:

* it is **random** -- many distinct majors per preset, not two;
* it is **in band** -- the major must be one that actually shipped with
  the profile's Android release, or the UA contradicts the OS;
* it is **derived from the seed** -- the same seed yields the same major,
  so a profile survives a restart with an identical fingerprint;
* it is **off the edges** -- a phone is usually a few releases into its
  update cycle, not on the very first build.

These tests read the band from the version registry itself rather than
from a table written next to them, so a change to the registry's shipped
table cannot leave the tests asserting a band that no longer exists.
"""

from __future__ import annotations

import re

import pytest

from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.generator import (
    DEFAULT_BROWSER_MAJOR,
    ProfileGenerator,
    _chromium_band_for_android_release,
    _major_for_android_release,
    _pick_chrome_major,
)
from novaprofile.core.seed import SeedDeriver
from novaprofile.core.versions import VERSION_TABLE, _android_for_major
from novaprofile.schema.version import BrowserVersion

#: The presets the tests draw from, one per Android release in the
#: catalogue.  The release of a preset is fixed by the device catalogue.
ANDROID_14_PRESET = "pixel-8-pro-android14"

#: How many profiles are drawn per preset when the distribution is
#: measured.
SAMPLES = 300

#: The lowest number of distinct majors that counts as "not degenerate".
#:
#: The Android 14 band has 14 majors and 12 of them are drawable once the
#: edges are trimmed.  A uniform draw over 12 buckets that produced fewer
#: than 8 distinct values in 300 samples would mean the draw is badly
#: skewed, so 8 is the floor this suite defends.
MIN_DISTINCT_MAJORS = 8

_CHROME_IN_UA = re.compile(r"Chrome/(\d+)\.0\.0\.0")


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _majors(gen: ProfileGenerator, preset_id: str, count: int = SAMPLES) -> list[int]:
    """The Chromium major of ``count`` profiles of one preset."""

    majors: list[int] = []
    for index in range(count):
        profile = gen.generate(preset_id, master_seed=f"{index:064x}")
        majors.append(profile.browser_version.major)
    return majors


# --------------------------------------------------------------------------
# The band itself
# --------------------------------------------------------------------------


def test_the_band_is_derived_from_the_registry() -> None:
    """The band must be the registry's, not a second copy of it.

    Every supported major is asked which Android release it belongs to;
    the majors of one release are exactly the band of that release.
    """

    for major in sorted(VERSION_TABLE):
        release, _sdk = _android_for_major(major)
        low, high = _chromium_band_for_android_release(release)
        assert low <= major <= high, f"Chromium {major} is outside the band of Android {release}"


def test_the_band_matches_the_shipped_table() -> None:
    """The derived bands are the ones the registry's table describes."""

    assert _chromium_band_for_android_release("13") == (100, 113)
    assert _chromium_band_for_android_release("14") == (114, 127)
    assert _chromium_band_for_android_release("15") == (128, 155)


def test_every_major_of_the_band_is_in_the_registry() -> None:
    """A major outside ``VERSION_TABLE`` would have no capability entry."""

    for release in ("13", "14", "15"):
        low, high = _chromium_band_for_android_release(release)
        for major in range(low, high + 1):
            assert major in VERSION_TABLE


def test_the_representative_major_sits_inside_the_band() -> None:
    """The build's default version is a real member of its band."""

    for release in ("13", "14", "15"):
        low, high = _chromium_band_for_android_release(release)
        assert low <= _major_for_android_release(release) <= high


def test_the_default_constant_is_not_the_version_a_profile_picks() -> None:
    """``DEFAULT_BROWSER_MAJOR`` and the drawn major are separate things.

    The constant keeps its own meaning -- "the version this build
    defaults to" -- while a new profile draws its own major from the
    seed.  Overwriting one with the other is what made the fleet
    degenerate in the first place.
    """

    assert DEFAULT_BROWSER_MAJOR == 120

    deriver = SeedDeriver("a" * 64)
    picked = {_pick_chrome_major(deriver, "14") for _ in range(200)}
    assert DEFAULT_BROWSER_MAJOR not in picked or len(picked) > 1


# --------------------------------------------------------------------------
# 1. Many distinct majors
# --------------------------------------------------------------------------


def test_many_profiles_yield_many_distinct_majors(gen) -> None:
    """A fleet must not collapse onto a couple of browser versions.

    Before the draw was introduced, 200 profiles of one preset produced
    exactly two distinct majors.  The bar here is deliberately well above
    that: at least 8 distinct majors over 300 samples.
    """

    majors = _majors(gen, ANDROID_14_PRESET)
    distinct = set(majors)

    assert len(distinct) >= MIN_DISTINCT_MAJORS, (
        f"only {len(distinct)} distinct Chromium majors over {SAMPLES} profiles: "
        f"{sorted(distinct)}"
    )


def test_the_major_is_the_one_the_user_agent_reports(gen) -> None:
    """The drawn major is the one that reaches the wire.

    The User-Agent is built from the capability entry, so this also
    proves the major was not hand-built into the UA string.
    """

    for index in range(50):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        match = _CHROME_IN_UA.search(profile.navigator.user_agent)
        assert match is not None
        assert int(match.group(1)) == profile.browser_version.major


def test_the_brands_follow_the_drawn_major(gen) -> None:
    """The ``Sec-CH-UA`` brand list comes from the registry, not from the
    generator, so it must agree with the major that was drawn."""

    for index in range(50):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        brands = {b.brand: b.version for b in profile.user_agent_data.brands}
        assert brands["Chromium"] == str(profile.browser_version.major)
        assert brands["Google Chrome"] == str(profile.browser_version.major)
        assert f'"Chromium";v="{profile.browser_version.major}"' in profile.http_headers.sec_ch_ua


# --------------------------------------------------------------------------
# 1b. A drawn major below 119 must not claim the high-entropy hints
# --------------------------------------------------------------------------


def test_a_pre_119_major_claims_no_high_entropy_hint(gen) -> None:
    """A core that cannot send a hint must not report it.

    ``Sec-CH-UA-Model``, ``Sec-CH-UA-Arch`` and ``Sec-CH-UA-Bitness``
    arrived in Chromium 119.  A profile that declares Chromium 116 and
    still names a model is claiming a capability the running core does
    not have -- and it is exactly the sort of contradiction a vendor
    checks for.

    The *data* has to be empty as well as the header: the model is what
    the JS runtime hands to ``navigator.userAgentData.model``, and the
    consistency resolver renders the header from the data, so a value
    left in the data would find its way back onto the wire.
    """

    found_pre_119 = False
    for index in range(200):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        major = profile.browser_version.major
        if major >= 119:
            continue
        found_pre_119 = True

        assert profile.user_agent_data.model == "", f"Chromium {major} must not name a model"
        assert profile.user_agent_data.architecture == ""
        assert profile.user_agent_data.bitness == ""
        assert profile.http_headers.sec_ch_ua_model == ""
        assert profile.http_headers.sec_ch_ua_arch == ""
        assert profile.http_headers.sec_ch_ua_bitness == ""

    assert found_pre_119, "no pre-119 major was drawn; the test did not exercise the case"


def test_a_post_119_major_still_reports_the_high_entropy_hints(gen) -> None:
    """The gating must not strip the hints a modern core does send."""

    found = False
    for index in range(200):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        if profile.browser_version.major < 119:
            continue
        found = True

        assert profile.user_agent_data.model == profile.android_build.model
        assert profile.user_agent_data.architecture
        assert profile.user_agent_data.bitness
        assert profile.http_headers.sec_ch_ua_model == profile.android_build.model
        assert profile.http_headers.sec_ch_ua_arch
        assert profile.http_headers.sec_ch_ua_bitness

    assert found, "no post-119 major was drawn; the test did not exercise the case"


def test_a_drawn_profile_survives_the_consistency_resolver(gen) -> None:
    """Resolving a profile must not turn it into an inconsistent one.

    The resolver repairs a profile whose User-Agent was tampered with;
    a profile the generator produced is already consistent, so the
    repair must leave it alone.
    """

    resolver = ConsistencyResolver()
    for index in range(60):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        resolved = resolver.resolve(profile)
        report = resolver.check(resolved)
        assert report.errors == [], (
            f"Chromium {profile.browser_version.major}: {report.errors}"
        )


# --------------------------------------------------------------------------
# 2. Every major is inside the band of its Android release
# --------------------------------------------------------------------------


def test_every_major_is_inside_the_band_of_its_release(gen) -> None:
    """The OS and the browser must agree, or the UA is a contradiction."""

    for index in range(SAMPLES):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        release = profile.android_build.version_release
        low, high = _chromium_band_for_android_release(release)
        assert low <= profile.browser_version.major <= high, (
            f"Chromium {profile.browser_version.major} is outside the band of "
            f"Android {release} ({low}..{high})"
        )


def test_the_profile_passes_its_own_consistency_check(gen) -> None:
    """A drawn major must not introduce a consistency finding."""

    resolver = ConsistencyResolver()
    for index in range(60):
        profile = gen.generate(ANDROID_14_PRESET, master_seed=f"{index:064x}")
        report = resolver.check(profile)
        assert report.errors == [], f"Chromium {profile.browser_version.major}: {report.errors}"


# --------------------------------------------------------------------------
# 3. The draw is derived from the seed
# --------------------------------------------------------------------------


def test_the_same_seed_yields_the_same_major(gen) -> None:
    """A profile survives a restart with an identical fingerprint."""

    for index in range(40):
        seed = f"{index:064x}"
        first = gen.generate(ANDROID_14_PRESET, master_seed=seed)
        second = gen.generate(ANDROID_14_PRESET, master_seed=seed)
        assert first.browser_version.major == second.browser_version.major


def test_the_same_seed_and_preset_yield_the_same_major_across_calls(gen) -> None:
    """Two separate generator instances agree on the major."""

    other = ProfileGenerator()
    for index in range(40):
        seed = f"{index:064x}"
        first = gen.generate(ANDROID_14_PRESET, master_seed=seed)
        second = other.generate(ANDROID_14_PRESET, master_seed=seed)
        assert first.browser_version.major == second.browser_version.major


def test_the_whole_profile_is_reproducible(gen) -> None:
    """The major is part of the fingerprint, so it must be stable too."""

    seed = "9" * 64
    first = gen.generate(ANDROID_14_PRESET, master_seed=seed)
    second = gen.generate(ANDROID_14_PRESET, master_seed=seed)
    assert first.browser_version.major == second.browser_version.major
    assert first.navigator.user_agent == second.navigator.user_agent


# --------------------------------------------------------------------------
# 4. The distribution is not degenerate
# --------------------------------------------------------------------------


def test_the_distribution_covers_a_reasonable_share_of_the_band(gen) -> None:
    """The distinct majors must be a real fraction of the band.

    The band is trimmed at the edges, so the drawable majors are the
    interior ones; the distinct majors have to be a decent share of
    *those*, not of the raw band.
    """

    low, high = _chromium_band_for_android_release("14")
    drawable = max(1, (high - low + 1) - 2)
    majors = _majors(gen, ANDROID_14_PRESET)
    distinct = set(majors)

    assert len(distinct) >= MIN_DISTINCT_MAJORS
    assert len(distinct) >= drawable // 2, (
        f"only {len(distinct)} distinct majors out of {drawable} drawable ones"
    )


def test_the_distribution_is_not_one_hot(gen) -> None:
    """No single major may take the whole fleet."""

    majors = _majors(gen, ANDROID_14_PRESET)
    counts: dict[int, int] = {}
    for major in majors:
        counts[major] = counts.get(major, 0) + 1
    assert max(counts.values()) < len(majors) // 2, f"one major dominates: {counts}"


def test_different_seeds_reach_different_majors(gen) -> None:
    """Two seeds must not be locked onto the same major."""

    first = gen.generate(ANDROID_14_PRESET, master_seed="1" * 64)
    second = gen.generate(ANDROID_14_PRESET, master_seed="2" * 64)
    assert first.browser_version.major != second.browser_version.major


# --------------------------------------------------------------------------
# 5. The pick stays off the edges of the band
# --------------------------------------------------------------------------


def test_the_pick_is_not_the_first_or_the_last_major_of_the_band(gen) -> None:
    """A phone is a few releases into its cycle, not on its first build."""

    low, high = _chromium_band_for_android_release("14")
    assert high - low + 1 > 3, "the Android 14 band is expected to be wide"

    majors = _majors(gen, ANDROID_14_PRESET)
    assert low not in majors, "the very first major of the band must not be picked"
    assert high not in majors, "the very last major of the band must not be picked"


def test_the_interior_of_the_band_is_reachable(gen) -> None:
    """Trimming the edges must not empty the band."""

    low, high = _chromium_band_for_android_release("14")
    majors = set(_majors(gen, ANDROID_14_PRESET))
    assert majors
    assert majors <= set(range(low + 1, high))
    assert len(majors & set(range(low + 1, high))) == len(majors)


def test_a_tiny_band_is_used_whole(gen) -> None:
    """A band of three or fewer has no interior, so it is used whole."""

    from novaprofile.core import generator as generator_module

    original = generator_module._chromium_band_for_android_release
    try:
        generator_module._chromium_band_for_android_release = lambda release: (100, 102)
        picks = {
            generator_module._pick_chrome_major(SeedDeriver(f"{i:064x}"), "13")
            for i in range(50)
        }
    finally:
        generator_module._chromium_band_for_android_release = original

    assert picks <= {100, 101, 102}
    assert picks


def test_a_two_major_band_is_used_whole(gen) -> None:
    """A band of two trims to nothing, so it must fall back to the band."""

    from novaprofile.core import generator as generator_module

    original = generator_module._chromium_band_for_android_release
    try:
        generator_module._chromium_band_for_android_release = lambda release: (120, 121)
        picks = {
            generator_module._pick_chrome_major(SeedDeriver(f"{i:064x}"), "14")
            for i in range(50)
        }
    finally:
        generator_module._chromium_band_for_android_release = original

    assert picks <= {120, 121}
    assert picks


# --------------------------------------------------------------------------
# 6. An explicit browser version still wins
# --------------------------------------------------------------------------


def test_an_explicit_browser_version_overrides_the_draw(gen) -> None:
    """The caller still decides when the caller asks."""

    for index in range(30):
        seed = f"{index:064x}"
        profile = gen.generate(
            ANDROID_14_PRESET,
            master_seed=seed,
            browser_version=BrowserVersion(major=125),
        )
        assert profile.browser_version.major == 125
        assert "Chrome/125.0.0.0" in profile.navigator.user_agent


def test_an_explicit_browser_version_is_never_redrawn(gen) -> None:
    """The random choice does not happen at all when a version is passed.

    The same explicit version has to produce the same profile whatever
    the seed, which is only true if the draw was skipped entirely.
    """

    first = gen.generate(
        ANDROID_14_PRESET,
        master_seed="1" * 64,
        browser_version=BrowserVersion(major=118),
    )
    second = gen.generate(
        ANDROID_14_PRESET,
        master_seed="2" * 64,
        browser_version=BrowserVersion(major=118),
    )
    assert first.browser_version.major == second.browser_version.major == 118
    assert first.navigator.user_agent == second.navigator.user_agent


def test_an_explicit_full_version_is_kept(gen) -> None:
    """A full version that belongs to the major is used verbatim."""

    profile = gen.generate(
        ANDROID_14_PRESET,
        master_seed="3" * 64,
        browser_version=BrowserVersion(major=120, full_version="120.0.6099.225"),
    )
    assert profile.browser_version.full_version == "120.0.6099.225"
    assert profile.browser_version.major == 120
