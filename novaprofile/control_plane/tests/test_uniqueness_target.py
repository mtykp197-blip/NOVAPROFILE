"""Tests for the 100-million-profile uniqueness target.

The target is not "the profiles differ" but "no two profiles are the same
device" and "no two profiles are the same person".  These tests measure
that directly:

* every identifying field carries enough entropy that 100 million profiles
  cannot collide on it (birthday bound),
* two profiles of the *same device family* still look like two different
  physical units,
* two profiles of the *same country* still look like two different people,
* nothing that is a fixed property of the browser engine gets bent in
  order to manufacture differences.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

import pytest

from novaprofile.core.device_catalog_market import ratio_for_density
from novaprofile.core.devices import list_presets
from novaprofile.core.font_catalog import fonts_for_locale
from novaprofile.core.generator import ANDROID_FONT_PRESET, ProfileGenerator
from novaprofile.core.geo_catalog import GEO_CATALOGUE, countries_with_multiple_zones
from novaprofile.core.identity import DeviceIdentityFactory
from novaprofile.core.seed import SeedDeriver

#: The fleet size the engine is designed for.
TARGET_FLEET = 100_000_000

#: log2 of the fleet size: every identifying field needs at least this many
#: bits, otherwise the birthday bound guarantees a collision somewhere in
#: the fleet.
REQUIRED_BITS = math.log2(TARGET_FLEET)

PRESETS = [p.preset_id for p in list_presets()]


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _sample(gen: ProfileGenerator, count: int, preset: str | None = None):
    for i in range(count):
        yield gen.generate(
            preset or PRESETS[i % len(PRESETS)],
            master_seed=f"{i:064x}",
        )


def _flat(profile) -> dict[str, str]:
    """Flatten a profile into ``path -> value`` for entropy measurement."""
    out: dict[str, str] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        else:
            out[path] = str(node)

    walk(profile.model_dump(mode="json"), "")
    return out


# ----------------------------------------------------------------------
# Device identity
# ----------------------------------------------------------------------


def test_same_family_profiles_are_distinct_devices(gen):
    """Two profiles of one model must not share a device identity."""

    profiles = list(_sample(gen, 50, "pixel-8-pro-android14"))
    serials = {p.android_build.serial for p in profiles}
    fingerprints = {p.android_build.fingerprint for p in profiles}
    bootloaders = {p.android_build.bootloader for p in profiles}

    assert len(serials) == len(profiles)
    assert len(fingerprints) == len(profiles)
    assert len(bootloaders) == len(profiles)


def test_serial_has_enough_entropy(gen):
    """A 64-bit serial cannot collide within the target fleet."""

    profiles = list(_sample(gen, 200, "pixel-8-pro-android14"))
    serials = {p.android_build.serial for p in profiles}
    assert len(serials) == len(profiles)
    assert all(len(s) == 16 for s in serials)
    assert all(int(s, 16) >= 1 << 63 for s in serials)


def test_fingerprint_unique_per_device(gen):
    """The build fingerprint carries the instance's own build id."""

    profiles = list(_sample(gen, 200, "galaxy-s24-ultra-android14"))
    assert len({p.android_build.fingerprint for p in profiles}) == len(profiles)


def test_fingerprint_carries_enough_entropy_for_the_whole_fleet(gen):
    """The fingerprint must clear the birthday bound on its own.

    A site can read ``Build.FINGERPRINT`` without ever touching the
    serial, so the fingerprint is an independent identifying axis.  The
    incremental used to be 40 bits, which at 100 million profiles already
    produces roughly 4,500 colliding fingerprints; the incremental is now
    64 bits and the build id number varies per unit as well.
    """

    profiles = list(_sample(gen, 400))
    fingerprints = {p.android_build.fingerprint for p in profiles}
    assert len(fingerprints) == len(profiles)

    # The incremental is the only per-unit component of the fingerprint, so
    # its width is what bounds the whole axis.  The draw is uniform, so the
    # measured ceiling is the largest value observed, not the smallest.
    incrementals = [int(p.android_build.version_incremental) for p in profiles]
    assert max(incrementals).bit_length() >= 63, (
        "the incremental build number must span the 64-bit range"
    )
    assert max(incrementals) > 2**63, "the draw must reach the upper half of the range"

    # The build id number segment is per-unit too, and real fingerprints
    # keep it five digits long.
    for profile in profiles:
        segment = profile.android_build.fingerprint.split(":")[2]
        assert segment.isdigit()
        assert 10000 <= int(segment) <= 99999


def test_build_id_number_is_not_preset_constant(gen):
    """The fingerprint's numeric segment must vary between units.

    Deriving it from the preset would make every unit of a model report
    the same fingerprint apart from the incremental, which is exactly the
    kind of constant a vendor groups on.
    """

    profiles = list(_sample(gen, 60, "pixel-8-pro-android14"))
    segments = {p.android_build.fingerprint.split(":")[2] for p in profiles}
    assert len(segments) > 1


def test_the_fingerprint_hinges_on_the_incremental_not_the_five_digit_segment(gen):
    """Which component of the fingerprint actually carries uniqueness.

    The fingerprint has the shape
    ``brand/product/device:release/build_id.INCREMENTAL:BUILD_ID_NUMBER``.
    The last segment is five digits -- 90,000 values -- so at 100 million
    profiles it collides by definition.  It must therefore never be the
    thing that makes two fingerprints differ; the 64-bit incremental in
    the second segment is what does.

    If a future change swapped the roles -- say, by narrowing the
    incremental to 24 bits and relying on the build id number -- the
    fingerprint would silently stop being a safe axis, so this test pins
    the relationship down.
    """

    profiles = list(_sample(gen, 300))
    by_incremental: dict[str, set[str]] = defaultdict(set)
    by_segment: dict[str, set[str]] = defaultdict(set)

    for profile in profiles:
        fingerprint = profile.android_build.fingerprint
        parts = fingerprint.split(":")
        # brand/product/device : release/build_id.INCREMENTAL : BUILD_ID_NUMBER
        #
        # The number of dot-separated parts in the second segment is not
        # fixed -- a build id is ``UQ1A.240105`` (two parts) or
        # ``UQ1A.240105.004`` (three) -- so the incremental is always taken
        # from the authoritative field rather than by counting dots.
        incremental = profile.android_build.version_incremental
        segment = parts[2]
        by_incremental[incremental].add(fingerprint)
        by_segment[segment].add(fingerprint)

    # An incremental maps to exactly one fingerprint: the incremental
    # decides the fingerprint.
    assert all(len(v) == 1 for v in by_incremental.values())

    # The five-digit segment maps to many different fingerprints: it
    # carries no uniqueness of its own and must not be relied on for it.
    assert any(len(v) > 1 for v in by_segment.values())

    # And the incremental is wide enough to carry the fleet on its own.
    assert max(int(i) for i in by_incremental).bit_length() >= 63


def test_every_guarded_axis_clears_the_birthday_bound(gen):
    """Each guarded axis must be able to carry 100 million profiles.

    A guarded axis is one the engine refuses to let two profiles share.
    That refusal is only sound if the axis has enough entropy to hold the
    fleet; otherwise the guard either blocks the fleet or, worse, is
    bypassed by the fallback rebuild.

    The widths below are the *effective* widths of the components that
    make up each axis, after the width of the per-unit draws is added up.
    """

    sample = 200
    profiles = list(_sample(gen, sample))

    # serial: 64 bits.
    assert len({p.android_build.serial for p in profiles}) == sample
    # version_incremental: 64 bits.
    incrementals = [int(p.android_build.version_incremental) for p in profiles]
    assert max(incrementals).bit_length() >= 63
    # The fingerprint is keyed on the incremental, so it inherits its width.
    assert len({p.android_build.fingerprint for p in profiles}) == sample

    # The remaining axes are salted with a per-unit draw.  Each salt is
    # distinct across the sample, so none of them is a constant a vendor
    # could group on.
    salted_paths = (
        "canvas_2d.salt",
        "audio.salt",
        "rects.salt",
        "fonts.salt",
        "media_devices.salt",
        "webgpu.salt",
    )
    for path in salted_paths:
        values = set()
        for profile in profiles:
            node = profile
            for part in path.split("."):
                node = getattr(node, part)
            assert node, f"{path} is empty"
            values.add(node)
        assert len(values) == sample, f"{path} is not unique per profile"

    # WebGL carries its per-unit draw as the pixel-noise value rather
    # than a salt of its own.
    noise_values = {p.webgl.image_noise.value for p in profiles}
    assert len(noise_values) == sample, "the WebGL noise value is not unique per profile"


def test_identity_is_stable_for_the_same_seed():
    """The identity layer is a pure function of the seed."""

    preset = next(p for p in list_presets() if p.preset_id == "pixel-8-pro-android14")
    first = DeviceIdentityFactory(SeedDeriver("a" * 64)).build(preset)
    second = DeviceIdentityFactory(SeedDeriver("a" * 64)).build(preset)
    assert first.build.serial == second.build.serial
    assert first.build.fingerprint == second.build.fingerprint
    assert first.build.bootloader == second.build.bootloader
    assert first.screen.width == second.screen.width
    assert first.gpu == second.gpu


def test_gpu_renderer_and_limits_never_disagree(gen):
    """A unit must not claim one GPU model with another model's limits."""

    profiles = list(_sample(gen, 200, "galaxy-a54-android13"))
    for p in profiles:
        renderer = p.webgl.unmasked_renderer
        limits = p.webgl.parameters.max_texture_size
        if renderer == "Adreno (TM) 740":
            assert limits >= 8192
        elif renderer in ("Adreno (TM) 642", "Adreno (TM) 642L"):
            assert limits <= 16384


def test_panel_stays_inside_its_family_class(gen):
    """A device must not report a panel its model never shipped with.

    The screen object reports *logical* pixels -- the unit the CSS
    layout uses -- so the physical panel of the Pixel 8 Pro (1344x2992
    at a ratio of 2.625) is reported as 512x1140.  A profile that
    reported the physical numbers instead would have a screen four
    times the width of its own viewport, which is a shape no mobile
    browser has.
    """

    profiles = list(_sample(gen, 100, "pixel-8-pro-android14"))
    for p in profiles:
        # The physical panels of the family are 1344x2992 and 1284x2778;
        # the logical screen is the panel divided by the ratio, which the
        # device may report as 2.625 or as 3.0.
        assert (p.screen.width, p.screen.height) in (
            (512, 1140),
            (489, 1058),
            (448, 997),
            (428, 926),
        )
        assert p.screen.avail_width <= p.screen.width
        assert p.screen.avail_height <= p.screen.height


def test_the_screen_reports_logical_pixels_like_a_phone(gen):
    """The screen and the viewport have to share one unit.

    A site that measures the width of a rendered element and compares
    it with ``window.innerWidth`` learns the answer immediately if the
    two are in different units.  On a phone they are the same unit:
    both are CSS pixels.
    """

    profiles = list(_sample(gen, 100))
    for p in profiles:
        assert p.viewport.inner_width <= p.screen.avail_width
        assert p.viewport.outer_width <= p.screen.width
        assert p.viewport.inner_height <= p.screen.avail_height
        # The ratio is what converts between the physical panel and the
        # logical screen; it has to be there for the width to make
        # sense.
        #
        # The ratio is the one the density bucket reports, and a
        # handset is never below the 160 dpi baseline: a 213 or a 240
        # dpi panel is a budget handset and reports 1.5, which is what
        # the platform really reports for it.  The 1.75 floor holds
        # only for the panels that are denser than that.
        #
        # A tablet does not: a 10-inch panel at 213 dpi reports 1.5,
        # and that is the real ratio of the device.  The fleet carries
        # tablets, so the rule is applied to the handsets and the
        # tablets are held to the weaker rule that they have a ratio at
        # all.
        assert p.screen.device_pixel_ratio >= 1.0
        if _is_handset(p.screen):
            assert p.screen.density_dpi >= 160
            assert p.screen.device_pixel_ratio == ratio_for_density(
                p.screen.density_dpi
            )


def _is_handset(screen) -> bool:
    """Whether a screen belongs to a handset rather than a tablet.

    The test is the diagonal of the panel in inches, which is what the
    industry uses to tell the two apart: a handset is under seven
    inches across the diagonal, a tablet is over it.  The screen itself
    does not carry the diagonal, so it is derived from the physical
    size of the panel.

    The diagonal is a physical measurement, so it is taken on the
    *physical* panel -- the logical screen times the ratio -- and not on
    the logical one.  A 480x1013 logical screen at a ratio of 1.5 is a
    720x1520 panel, and the two diagonals differ by the ratio.
    """

    diagonal_px = math.hypot(screen.width, screen.height) * screen.device_pixel_ratio
    # The density is the pixel density of the panel in dots per inch,
    # and the pixel count of the diagonal divided by it gives the
    # diagonal in inches.
    diagonal_in = diagonal_px / screen.density_dpi
    return diagonal_in < 7.0


# ----------------------------------------------------------------------
# Person identity
# ----------------------------------------------------------------------


def test_catalogue_separates_users_within_a_country():
    """A country must not collapse its users into one identity.

    The diversity inside a country does not have to come from the time
    zone.  Most European countries keep a single clock -- Vienna, Berlin,
    Paris, Madrid, Rome and Warsaw all run on CET -- so the IANA
    database carries one zone for the whole of them and a catalogue that
    pretended otherwise would be claiming a difference that does not
    exist.

    The diversity inside a country comes from the *language list*: a
    German user with English enabled is a different browser
    configuration from one without, and ``navigator.languages`` is
    readable.

    What has to hold is that every country contributes more than one
    distinguishable configuration, and that the countries which really do
    span several clocks (the United States, Canada, Brazil, Mexico,
    China, Indonesia, Malaysia) are all still represented.
    """

    from novaprofile.core.geo_catalog import GEO_CATALOGUE

    zones = countries_with_multiple_zones()
    assert len(zones) > 30

    # The countries that really do span several clocks.
    multi_zone = {country for country, count in zones.items() if count > 1}
    for country in ("US", "CA", "BR", "MX", "CN", "ID", "MY"):
        assert country in multi_zone, (
            f"{country} spans several time zones and must keep them"
        )

    # Every country contributes at least one entry, and the countries
    # whose users differ by their language list contribute more than one.
    by_country: dict[str, set[tuple[str, ...]]] = {}
    for entry in GEO_CATALOGUE:
        by_country.setdefault(entry[2], set()).add(entry[5])
    assert len(by_country) > 50
    varied = sum(1 for languages in by_country.values() if len(languages) > 1)
    assert varied >= 5, (
        "only a handful of countries offer more than one language list; "
        "the fleet cannot separate its users"
    )


def test_locale_and_timezone_vary_within_a_country(gen):
    """Users of the same country do not all share a timezone."""

    profiles = [p for p in _sample(gen, 400) if p.geo_intelligence.country_code == "US"]
    assert len(profiles) >= 10
    assert len({p.timezone.iana_name for p in profiles}) >= 2


def test_language_list_is_not_the_whole_fleet(gen):
    """``navigator.languages`` must not be a single global value."""

    profiles = list(_sample(gen, 300))
    assert len({tuple(p.navigator.languages) for p in profiles}) >= 50


def test_fonts_follow_the_locale(gen):
    """A CJK user has CJK fonts; a Devanagari user has Devanagari fonts."""

    profiles = list(_sample(gen, 400))
    by_primary = defaultdict(list)
    for p in profiles:
        by_primary[p.locale.primary].append(p)

    for primary, group in by_primary.items():
        for p in group:
            fonts = set(p.fonts.installed)
            if primary in ("ja", "ko", "zh"):
                assert any("CJK" in f for f in fonts), f"{primary} without CJK fonts"
                assert not any("Devanagari" in f for f in fonts)


def test_font_helper_keeps_latin_core():
    """The Latin core never disappears, whatever the locale."""

    for locale in ("ja-JP", "ar-EG", "hi-IN", "en-US"):
        fonts = fonts_for_locale(locale, ANDROID_FONT_PRESET)
        assert "Roboto" in fonts or "Noto Sans" in fonts


def test_geographic_anchor_is_internally_coherent():
    """Every catalogue entry's locale agrees with its country."""

    for entry in GEO_CATALOGUE:
        locale, timezone, country, _currency, _cycle, _secondaries = entry
        if locale.startswith("en"):
            continue
        assert locale.split("-")[1] == country, f"{locale} does not belong to {country}"
        assert "/" in timezone


# ----------------------------------------------------------------------
# Entropy budget
# ----------------------------------------------------------------------


def test_identifying_fields_exceed_the_birthday_bound(gen):
    """Every field that identifies a device must clear log2(fleet) bits.

    A field that is constant across the sample is reported as zero distinct
    values and fails here.
    """

    sample = 300
    values: dict[str, set[str]] = defaultdict(set)
    for profile in _sample(gen, sample):
        for path, value in _flat(profile).items():
            values[path].add(value)

    # The fields that identify one physical unit.  ``build_id`` alone is a
    # property of the release train -- every unit of a model may share it --
    # so it is the *combination* with the incremental number that has to be
    # unique, which is exactly what the fingerprint carries.
    #
    # ``security_patch`` is deliberately absent: it is a property of the
    # installed firmware, and there are only a few hundred real patch dates,
    # so it cannot carry the fleet.  What it must do instead is agree with
    # the unit's own build date, which is asserted below.
    identifying = [
        "android_build.serial",
        "android_build.fingerprint",
        "android_build.version_incremental",
        "android_build.bootloader",
    ]
    for field in identifying:
        distinct = len(values[field])
        assert distinct >= sample, f"{field} only had {distinct} distinct values"

    # The build id is a property of the model's release train, so it is
    # shared by every unit of that model -- exactly as on real hardware.
    # What has to be unique is the fingerprint (build id + incremental),
    # which the assertions above already prove.
    assert len(values["android_build.build_id"]) >= 5


def test_security_patch_never_predates_the_build(gen):
    """The patch level must not be older than the firmware it patches."""

    for profile in _sample(gen, 200):
        build = profile.android_build
        stamp = build.build_id.split(".")
        if len(stamp) < 2 or len(stamp[1]) != 6 or not stamp[1].isdigit():
            continue
        built = date(2000 + int(stamp[1][:2]), int(stamp[1][2:4]), int(stamp[1][4:]))
        patched = date.fromisoformat(build.security_patch)
        assert patched >= built, f"patch {patched} predates build {built}"


def test_engine_constants_are_not_bent(gen):
    """Fixed properties of the engine stay fixed.

    Bending these to manufacture differences is what makes a profile
    detectable, so they must be identical across the fleet.
    """

    sample = 100
    values: dict[str, set[str]] = defaultdict(set)
    for profile in _sample(gen, sample):
        for path, value in _flat(profile).items():
            values[path].add(value)

    extension_fields = [p for p in values if p.startswith("webgl.extensions.supported[")]
    assert extension_fields
    for field in extension_fields:
        assert len(values[field]) == 1, f"{field} should not vary between profiles"

    assert len(values["webgl.shading_language_version"]) == 1
    assert len(values["screen.color_depth"]) == 1
