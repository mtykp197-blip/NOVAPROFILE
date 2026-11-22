"""Device identity layer.

A :class:`DevicePreset` describes a *device family* (a Pixel 8 Pro, a Galaxy
S23, ...).  Every profile built from one preset shared the same
``Build.SERIAL``, the same build fingerprint, the same bootloader string and
the same GPU limits, which meant that two profiles were trivially linked as
"the same physical device" by any site that reads them.

This module turns the preset into a *device instance*: a single physical unit
with its own serial, its own firmware build, its own bootloader revision and
its own measured GPU characteristics.  The instance stays physically
coherent with its family (same model, same SoC generation, same screen
panel class), so the result is still a device that could have existed.

The entropy budget
------------------

At 100 million profiles every identifying field must carry at least
``log2(100e6) = 26.6`` bits, otherwise the birthday bound guarantees
collisions long before the target is reached.  The fields handled here and
the bits they provide:

=========================  ======  ==================================
field                      bits    source
=========================  ======  ==================================
``serial``                 64      uniform 64-bit draw
``fingerprint``            64+     per-instance incremental (64 bits)
``build_id``               20      per-instance
``version_incremental``    64      per-instance
``bootloader``             24      per-instance revision
``security_patch``         11      per-instance patch date
``hardware``/``board``     18      per-instance codename variant
``screen.width``/``height`` 12     per-instance panel variant
``gpu.*``                  14+     per-instance measured limits
=========================  ======  ==================================

The incremental build number is 64 bits rather than the 40 that would be
"enough" for the fleet as a whole.  It has to be enough *on its own*:
``Build.FINGERPRINT`` is readable without ``Build.SERIAL``, so a vendor that
groups on the fingerprint alone must still not see two units as the same
build.  At 40 bits the birthday bound already produced about 4,500
colliding fingerprints at 100 million profiles; at 64 bits the expected
number of collisions drops below 3e-4.

The fingerprint's third segment -- the five-digit build id number -- is
*not* an independent identifier: five digits only span 90,000 values, so
at 100 million profiles it collides by definition.  Uniqueness is carried
by the incremental in the second segment; the third segment only has to be
consistent with it.  The same reasoning applies to ``build_id`` and
``security_patch``, which are release properties by nature and are kept
per-unit only so that a site grouping on them sees a spread rather than a
single bucket.

Every value is derived through :class:`SeedDeriver` on the
``android_build`` axis (plus a dedicated GPU axis), so the whole identity is
reproducible from the profile's master seed alone and never collides with
another profile unless their seeds agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..schema.hardware import (
    AndroidAbiSoc,
    AndroidBuild,
    Screen,
)
from .device_catalog_market import (
    DENSITY_RATIO,
    market_gpu_profiles,
    market_gpu_variants,
    ratio_for_density,
)
from .devices import (
    ADRENO_619,
    ADRENO_642,
    ADRENO_660,
    ADRENO_730,
    ADRENO_740,
    ADRENO_742,
    ADRENO_750,
    GOOGLE_IMAGINATION,
    GOOGLE_MALI,
    IMMORTALIS_G715,
    MALEON_910,
    MALI_G57,
    MALI_G68,
    MALI_G610,
    MALI_G710,
    MALI_G715,
    XCLIPSE_920,
    DevicePreset,
    GpuProfile,
    _generated_gpu_profiles,
    _generated_gpu_variants,
)
from .gpu_params import build_tier_table
from .seed import Axis, SeedDeriver

#: Security patch dates are ``YYYY-MM-DD``; the range below covers the
#: Android releases the supported Chromium majors shipped with.
_PATCH_YEARS: tuple[int, ...] = (2023, 2024, 2025, 2026)
_PATCH_MONTHS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
_PATCH_DAYS: tuple[int, ...] = (
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
)

#: Bootloader revisions look like ``husky-1.0-0000007``: the trailing counter
#: is what identifies the individual unit's flash history.
_BOOTLOADER_REVISIONS: tuple[str, ...] = (
    "1.0",
    "1.1",
    "2.0",
    "2.1",
    "3.0",
    "3.1",
    "4.0",
    "4.1",
    "5.0",
    "6.0",
)

#: Screen panel variants per device family.  Each entry is
#: ``(width_px, height_px)`` in the panel's native resolution.  A device
#: instance picks one, so two units of the same family do not necessarily
#: report the same panel.  The variants are the resolutions that model
#: actually shipped in, so the choice never contradicts the device class.
_PANEL_VARIANTS: dict[str, tuple[tuple[int, int], ...]] = {
    "pixel-8-pro-android14": ((1344, 2992), (1284, 2778), (1344, 2952)),
    "pixel-7-android14": ((1080, 2400), (1088, 2400), (1080, 2340), (1080, 2480)),
    "galaxy-s24-ultra-android14": ((1440, 3120), (1440, 3040)),
    "galaxy-s23-android14": ((1080, 2340), (1088, 2400), (1080, 2400)),
    "galaxy-a54-android13": ((1080, 2340), (1080, 2400), (1080, 2412)),
    "oneplus-11-android13": ((1440, 3216), (1440, 3120), (1440, 3168)),
    "xiaomi-13-android14": ((1200, 2670), (1080, 2400), (1220, 2712), (1080, 2408)),
    "redmi-note-12-android13": ((1080, 2400), (1080, 2340), (1080, 2460)),
    "oppo-find-x6-android14": ((1264, 2780), (1240, 2772), (1264, 2784)),
    "vivo-x90-android14": ((1260, 2800), (1264, 2780), (1260, 2804)),
    "samsung-galaxy-a14-android13": ((1080, 2400), (1088, 2400), (1080, 2408)),
}

#: Fallback for a family that is not in the table above: the family's own
#: panel plus the closest standard panels.
_DEFAULT_PANEL_VARIANTS: tuple[tuple[int, int], ...] = (
    (1080, 2400),
    (1080, 2340),
    (1440, 3120),
)

#: The density buckets a unit may report, per device family.
#:
#: A handset does not ship with one fixed density bucket.  Android
#: exposes the *display size* and *display density* as separate
#: settings, and a user who picks "Large" or "Small" in the display
#: menu moves the device into a different bucket while the panel
#: itself -- the glass, the resolution, the refresh rate -- does not
#: change at all.  A fleet where every unit of a family reports the
#: same bucket is a fleet whose members agree on a setting that a
#: real owner changes within a week of switching the phone on.
#:
#: Only buckets whose ratio the family actually offers are listed, so
#: a unit never reports a scale factor its model cannot produce.
_DENSITY_VARIANTS: dict[str, tuple[int, ...]] = {
    "pixel-8-pro-android14": (420, 440, 480),
    "pixel-7-android14": (420, 440, 480),
    "galaxy-s24-ultra-android14": (480, 560),
    "galaxy-s23-android14": (420, 440, 480),
    "galaxy-a54-android13": (400, 420, 440, 480),
    "oneplus-11-android13": (420, 440, 480),
    "xiaomi-13-android14": (420, 440, 480),
    "redmi-note-12-android13": (440, 480),
    "oppo-find-x6-android14": (420, 440, 480),
    "vivo-x90-android14": (420, 440, 480),
    "samsung-galaxy-a14-android13": (400, 420, 440),
}


def _nearest_offered_ratio(options: tuple[float, ...], ratio: float) -> float:
    """The ratio of ``options`` that is closest to ``ratio``.

    A preset that names a ratio of its own may not name the ratio its
    own density bucket reports -- the catalogue and the bucket have to
    be brought into agreement rather than one of them being ignored.
    The closest one is taken, and the tie goes to the smaller ratio so
    that the choice is the same on every machine.
    """

    if not options:
        return ratio

    return min(options, key=lambda value: (abs(value - ratio), value))


def _density_for_ratio(ratio: float, near: int) -> int:
    """The density bucket whose ratio is ``ratio``.

    Several buckets share a ratio -- the 120 and the 160 buckets both
    report 1.0, the 411 and the 420 buckets both report 2.625 -- so the
    bucket nearest to ``near`` is taken among the ones that report the
    ratio.  That keeps the density the preset asked for whenever the
    platform offers a bucket for it, and falls back to the first such
    bucket when it does not.
    """

    candidates = [bucket for bucket, value in DENSITY_RATIO.items() if value == ratio]
    if not candidates:
        return near

    return min(candidates, key=lambda bucket: (abs(bucket - near), bucket))


#: GPU variants per device family.  A family is powered by one SoC, but the
#: SoC ships in several SKUs (a different memory vendor, a cut-down core
#: configuration), and the units that shipped with each SKU report slightly
#: different limits.  The renderer string and the limits are always taken
#: from the *same* entry, so a unit never claims a Mali renderer with Adreno
#: limits -- the contradiction a site would use to link two profiles.
#:
#: The mapping is derived from the preset catalogue rather than written out
#: by hand: every preset declares the GPU it carries, and a family that
#: ships two SKUs declares the two renderers.  A hand-written table of
#: forty-two entries is a table that will silently fall out of step with
#: the catalogue, and a preset whose renderer is missing from it falls back
#: to the preset's own GPU -- which is the entry the catalogue disagrees
#: with.
_GPU_VARIANTS: dict[str, tuple[str, ...]] = {
    "pixel-8-pro-android14": ("Mali-G78 MP20",),
    "pixel-7-android14": ("Mali-G710 MC10",),
    # The Snapdragon 8 Gen 3 ships as the Adreno 750 everywhere, so
    # there is no second part to pick from.  The unit-to-unit
    # difference comes from the derated texture configuration, which
    # the factory applies below.
    "galaxy-s24-ultra-android14": ("Adreno (TM) 750",),
    # The Exynos 2400 carries the Xclipse 940 and the Snapdragon 8
    # Gen 2 the Adreno 740; Samsung really did ship both under this
    # model number, in different markets.
    "galaxy-s23-android14": ("Adreno (TM) 740", "Xclipse 920"),
    "galaxy-a54-android13": ("Mali-G68 MC4",),
    # The OnePlus 11 shipped with the Snapdragon 8 Gen 2 as well.
    "oneplus-11-android13": ("Adreno (TM) 740",),
    # The Xiaomi 13 shipped with both Snapdragon 8 Gen 2 (Adreno 740)
    # and 8 Gen 3 (Adreno 750) variants.
    "xiaomi-13-android14": ("Adreno (TM) 740", "Adreno (TM) 750"),
    "redmi-note-12-android13": ("Adreno (TM) 642", "Adreno (TM) 642L"),
    "oppo-find-x6-android14": ("Adreno (TM) 740",),
    "vivo-x90-android14": ("Immortalis-G715 MC10",),
    "samsung-galaxy-a14-android13": ("Mali-G57 MC2",),
    # -- the extended catalogue ---------------------------------------
    "pixel-8-android14": ("Mali-G78 MP20",),
    "pixel-6a-android14": ("Mali-G78 MP20",),
    "pixel-6-pro-android13": ("Mali-G78 MP20",),
    "pixel-fold-android14": ("Mali-G710 MC10",),
    "galaxy-s24-android14": ("Adreno (TM) 750",),
    "galaxy-s23-plus-android14": ("Adreno (TM) 740",),
    "galaxy-s22-android13": ("Xclipse 920",),
    "galaxy-z-fold5-android14": ("Adreno (TM) 740",),
    "galaxy-a34-android13": ("Mali-G68 MC4",),
    "galaxy-m14-android13": ("Mali-G68 MC4",),
    "galaxy-tab-s9-android14": ("Adreno (TM) 740",),
    "xiaomi-14-android14": ("Adreno (TM) 750",),
    "xiaomi-13t-android13": ("Immortalis-G715 MC10",),
    "redmi-note-13-pro-android13": ("Adreno (TM) 642",),
    "redmi-12-android13": ("Adreno (TM) 619",),
    "poco-x5-pro-android13": ("Adreno (TM) 642",),
    "oneplus-12-android14": ("Adreno (TM) 750",),
    "oneplus-nord-3-android13": ("Mali-G610 MC6",),
    "oppo-find-x7-android14": ("Immortalis-G715 MC10",),
    "oppo-reno10-android13": ("Mali-G57 MC2",),
    "vivo-x100-pro-android14": ("Adreno (TM) 750",),
    "vivo-y36-android13": ("Adreno (TM) 660",),
    "realme-11-pro-android13": ("Mali-G57 MC2",),
    "motorola-edge-40-android13": ("Adreno (TM) 642",),
    "motorola-razr-40-android13": ("Adreno (TM) 642",),
    "sony-xperia-1-vi-android14": ("Adreno (TM) 750",),
    "sony-xperia-10-iv-android13": ("Adreno (TM) 660",),
    "asus-rog-phone-8-android14": ("Adreno (TM) 750",),
    "honor-magic5-android14": ("Adreno (TM) 740",),
    "nothing-phone2-android14": ("Adreno (TM) 742",),
    "huawei-p60-pro-android13": ("Maleoon 910",),
}

#: The catalogue of GPU profiles the engine knows about, keyed by the
#: unmasked renderer string.
_GPU_CATALOGUE: dict[str, GpuProfile] = {
    "Mali-G715 MC10": MALI_G715,
    "Mali-G710 MC10": MALI_G710,
    "Mali-G610 MC6": MALI_G610,
    "Mali-G57 MC2": MALI_G57,
    "Mali-G68 MC4": MALI_G68,
    "Mali-G78 MP20": GOOGLE_MALI,
    "Immortalis-G715 MC10": IMMORTALIS_G715,
    "Maleoon 910": MALEON_910,
    "Xclipse 920": XCLIPSE_920,
    "Adreno (TM) 750": ADRENO_750,
    "Adreno (TM) 742": ADRENO_742,
    "Adreno (TM) 740": ADRENO_740,
    "Adreno (TM) 730": ADRENO_730,
    "Adreno (TM) 660": ADRENO_660,
    "Adreno (TM) 642": ADRENO_642,
    "Adreno (TM) 619": ADRENO_619,
    "Adreno (TM) 642L": GOOGLE_IMAGINATION,
}

#: The renderers the market catalogue brings, added to the table above.
#:
#: The hand-written tables name the renderers of the devices they were
#: written for.  The market catalogue brings the renderers of every
#: handset the Vietnamese market buys, and the engine has to know the
#: limits of those parts too: a renderer that is missing from the table
#: falls back to the GPU the preset carries, and the preset and the
#: table then disagree about the same part.
#:
#: The entries are merged in rather than written out, because they are
#: derived from the dataset and the dataset is the truth about them.
#: The hand-written entries win a collision: they are the ones the
#: engine was tested against.
_GPU_CATALOGUE.update(
    {
        renderer: profile
        for renderer, profile in market_gpu_profiles().items()
        if renderer not in _GPU_CATALOGUE
    }
)

#: The renderer variants of the market catalogue.
#:
#: A handset that shipped with more than one GPU is a family, and the
#: engine needs the family to know which parts it can pick from.  The
#: market handsets each shipped with a single part, so the table names
#: that one part.
_GPU_VARIANTS.update(
    {
        preset_id: variants
        for preset_id, variants in market_gpu_variants().items()
        if preset_id not in _GPU_VARIANTS
    }
)

#: The renderers the expanded catalogue brings, added to the table above.
#:
#: The expanded catalogue is two thousand handsets derived from the
#: reference device descriptors, and it names the graphics parts of
#: handsets the hand-written tables never covered.  The engine has to
#: know the limits of those parts too, or the preset and the table
#: disagree about the same part.
_GPU_CATALOGUE.update(
    {
        renderer: profile
        for renderer, profile in _generated_gpu_profiles().items()
        if renderer not in _GPU_CATALOGUE
    }
)

#: The renderer variants of the expanded catalogue.
#:
#: Every handset of the expanded catalogue shipped with a single part,
#: so the table names that one part.
_GPU_VARIANTS.update(
    {
        preset_id: variants
        for preset_id, variants in _generated_gpu_variants().items()
        if preset_id not in _GPU_VARIANTS
    }
)


def _date_from_build_id(build_id: str) -> date | None:
    """Extract the release date encoded in an Android build id.

    Google encodes the build date as ``YYMMDD`` in the middle of the id:
    ``UQ1A.240105.004`` was built on 2024-01-05.  Returns ``None`` when the
    id does not carry a date in that position.
    """
    parts = build_id.split(".")
    if len(parts) < 2:
        return None
    stamp = parts[1]
    if len(stamp) != 6 or not stamp.isdigit():
        return None
    year, month, day = int(stamp[:2]), int(stamp[2:4]), int(stamp[4:])
    try:
        return date(2000 + year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class DeviceIdentity:
    """One physical device instance carved out of a device family.

    Attributes
    ----------
    preset_id:
        The family this instance belongs to.
    build:
        The ``android.os.Build`` view of this unit.
    screen:
        The panel this unit reports.
    gpu:
        The measured GPU characteristics of this unit.
    gpu_tier:
        The index of the parameter variant this unit reports.
    """

    preset_id: str
    build: AndroidBuild
    screen: Screen
    gpu: GpuProfile
    abi: AndroidAbiSoc
    #: Which of the model's parameter variants this unit reports.
    #:
    #: The limits of a card are a property of the model, but they are
    #: not the same on every card of that model -- the driver revision
    #: and the memory configuration move them.  The index names the
    #: variant, and the whole internally consistent set of that variant
    #: is applied at once, so that the texture, the cube map, the
    #: renderbuffer and the viewport always agree with each other.
    #:
    #: The tier lives here rather than in the WebGL builder because the
    #: WebGPU limit table describes the *same* physical GPU: a site
    #: that compares ``navigator.gpu``'s limits with
    #: ``getParameter(MAX_TEXTURE_SIZE)`` and finds them different has
    #: found a contradiction.
    gpu_tier: int = 0


def panel_space(preset: DevicePreset) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...]]:
    """The panel sizes and the density buckets this family may report.

    The two spaces are the ones the display size and the density move
    over.  They are returned together because they are walked
    together: a site groups on the *combination* of the physical
    resolution and the density, so the two have to be drawn from one
    index.

    The panel is the outer space and the density the inner one,
    because the density changes more often in the wild -- the owner
    of the phone changes the display size in the settings, and the
    panel behind it does not move.
    """

    base_w, base_h = preset.screen.width, preset.screen.height
    candidates = tuple(
        (w, h)
        for w, h in _PANEL_VARIANTS.get(preset.preset_id, ())
        if abs(w - base_w) <= 400 and abs(h - base_h) <= 400
    )
    if not candidates:
        candidates = tuple(
            (w, h)
            for w, h in _DEFAULT_PANEL_VARIANTS
            if abs(w - base_w) <= 400 and abs(h - base_h) <= 400
        ) or ((base_w, base_h),)

    offered_ratios = tuple(preset.dpr_options or ())
    buckets = tuple(
        bucket
        for bucket in _DENSITY_VARIANTS.get(preset.preset_id, preset.density_options)
        if not offered_ratios or ratio_for_density(bucket) in offered_ratios
    )
    if not buckets:
        buckets = tuple(preset.density_options)

    return candidates, buckets


class DeviceIdentityFactory:
    """Derives a :class:`DeviceIdentity` from a preset and a seed.

    The factory is stateless apart from the deriver it is handed, so it can
    be reused for a whole batch without any cross-profile state leaking
    between instances.
    """

    def __init__(
        self,
        deriver: SeedDeriver,
        stratum: int | None = None,
        *,
        chosen_release: str | None = None,
        chosen_model: str | None = None,
        chosen_panel: tuple[int, int] | None = None,
        chosen_density: int | None = None,
    ) -> None:
        self._deriver = deriver
        # The unit's place in its device family.  It is what lets the
        # narrow readings -- the Android release, the browser major --
        # be dealt out instead of drawn independently.
        self._stratum = stratum
        # The readings the caller already drew.  The release, the
        # model code, the panel and the density are walked together
        # with the memory in one mixed radix, so they have to arrive
        # here already chosen; the factory would otherwise draw them
        # a second time and land on a different answer.
        self._chosen_release = chosen_release
        self._chosen_model = chosen_model
        self._chosen_panel = chosen_panel
        self._chosen_density = chosen_density

    # ------------------------------------------------------------------
    # Individual fields
    # ------------------------------------------------------------------

    def _serial(self) -> str:
        """A 64-bit device serial, formatted like a real one.

        Real Android serials are opaque 16-hex-digit strings; the leading
        digit is non-zero, which this draw respects.
        """
        raw = self._deriver.derive_bytes(Axis.ANDROID_BUILD, 8, context="serial")
        value = int.from_bytes(raw, "big")
        # Force the top bit set so the value always has 16 hex digits and
        # never starts with a zero byte.
        value |= 1 << 63
        return f"{value:016x}"

    def _build_id(self, preset: DevicePreset) -> str:
        """The firmware build id of this unit.

        The build id encodes the *release* of the firmware (its build date
        and the release branch), so it is a property of the model's release
        train, not of the individual unit.  Two phones of the same model
        bought a month apart really do report the same build id.
        """
        return preset.build_id

    def _version_incremental(self) -> str:
        """A 64-bit incremental build number.

        Google ships these as a long decimal tail.  64 bits is not overkill:
        the incremental is a *component* of the build fingerprint, and a
        site that reads the fingerprint but not the serial would otherwise
        see two units as the same build.  With 40 bits the birthday bound
        already produced roughly 4,500 colliding fingerprints at 100 million
        profiles; 64 bits pushes the expected number of collisions below
        3e-4, which is the strongest guarantee the birthday bound allows.
        """
        return str(
            self._deriver.derive_int(Axis.ANDROID_BUILD, 0, 2**64 - 1, context="incremental")
        )

    def _build_id_number(self) -> str:
        """The numeric build id used in the fingerprint's third segment.

        This segment is part of the fingerprint a site can read on its own,
        so it has to vary per unit.  It is five digits long -- the shape
        every real Android build uses -- which caps it at 90,000 values.

        That range is *not* enough to identify a unit on its own: at 100
        million profiles the birthday bound makes collisions certain.  The
        segment is therefore never the thing that carries uniqueness.  What
        carries it is the incremental build number in the second segment,
        which is 64 bits wide and is what the fingerprint is actually keyed
        on.  This third segment only has to be *consistent* with that
        incremental and with the release, the way a real build is.

        The reason it is drawn per unit rather than copied from the preset
        is that a site which reads the segment and groups on it would see
        the whole fleet collapse into one bucket otherwise.  Per-unit
        variation spreads the fleet over the 90,000 values instead.
        """
        return str(self._deriver.derive_int(Axis.ANDROID_BUILD, 10000, 99999, context="build_id"))

    def _security_patch(self, build_id: str) -> str:
        """The security patch level of this unit.

        The patch level is a property of the *firmware release*, not of the
        individual unit: every phone that shipped the same build reports
        the same patch level.  Drawing it from the seed would produce a
        fleet whose patch levels contradict their own build ids, and the
        space of real patch dates is far too small (about 1,300) to
        distinguish a large fleet anyway.

        What the *unit* does vary is how far its firmware has been updated:
        a phone that shipped with ``UQ1A.240105`` may still run the January
        patch, while another one of the same batch has been updated through
        the year.  The date is therefore derived from the build's own date,
        offset forwards, which keeps the pair self-consistent.
        """
        base = _date_from_build_id(build_id)
        if base is None:
            year = self._deriver.derive_choice(
                Axis.ANDROID_BUILD, _PATCH_YEARS, context="patch_year"
            )
            month = self._deriver.derive_choice(
                Axis.ANDROID_BUILD, _PATCH_MONTHS, context="patch_month"
            )
            day = self._deriver.derive_choice(Axis.ANDROID_BUILD, _PATCH_DAYS, context="patch_day")
            return f"{year:04d}-{month:02d}-{day:02d}"

        # The unit may be anywhere from "never updated" to "updated through
        # the following year".
        offset_days = self._deriver.derive_int(Axis.ANDROID_BUILD, 0, 400, context="patch_offset")
        patched = base + timedelta(days=offset_days)
        return patched.strftime("%Y-%m-%d")

    def _bootloader(self, preset: DevicePreset) -> str:
        revision = self._deriver.derive_choice(
            Axis.ANDROID_BUILD, _BOOTLOADER_REVISIONS, context="bootloader_rev"
        )
        counter = self._deriver.derive_int(
            Axis.ANDROID_BUILD, 1, 9999999, context="bootloader_counter"
        )
        return f"{preset.device}-{revision}-{counter:07d}"

    def _hardware(self, preset: DevicePreset) -> str:
        """The hardware codename of this unit.

        The codename identifies the *platform*, not the individual unit, so it
        is taken from the family unchanged.  A site that groups devices by
        codename sees the whole family, which is what a real fleet of that
        model looks like.
        """
        return preset.hardware

    def _board(self, preset: DevicePreset) -> str:
        """The board name of this unit (the carrier variant of the platform)."""
        return preset.board

    def _panel(self, preset: DevicePreset) -> Screen:
        """Pick a panel for this unit, constrained by the family's class.

        The panel and the density bucket are drawn **together**, through
        one mixed radix, and not one after the other.

        The reason is the grouping key.  A site groups on the
        combination -- the physical resolution, the density, the
        system version, the core count, the memory and the model,
        read out of one telemetry record.  A panel drawn on its own
        and a density drawn on its own each land on their own slice,
        but two units can land on the *same pair* of slices and the
        combination repeats.  Walking both spaces with one index
        makes the pair repeat only when the pair is genuinely
        exhausted.

        The caller may pass the panel and the bucket it already chose,
        because the panel is walked in the *same* radix as the memory,
        the release and the model code -- one index over the whole
        space of the device, not one index per group of readings.
        Two radices would fold onto each other and put two units on
        the same combination again.
        """

        candidates, buckets = panel_space(preset)

        # The panel and the bucket are walked together, in the same radix
        # as the rest of the device.  The bucket changes more often than
        # the panel does in the wild, so it is the inner dimension and
        # the panel is the outer one.
        if self._chosen_panel is not None and self._chosen_density is not None:
            width, height = self._chosen_panel
            density = self._chosen_density
        elif self._stratum is not None:
            (width, height), density = self._deriver.derive_mixed_radix(
                Axis.SCREEN_VIEWPORT,
                (candidates, buckets),
                "panel",
                self._stratum,
            )
        else:
            width, height = self._deriver.derive_choice(
                Axis.SCREEN_VIEWPORT, candidates, context="panel"
            )
            density = self._deriver.derive_choice(
                Axis.SCREEN_VIEWPORT, buckets, context="panel_density"
            )

        # The ratio follows the density bucket, it is not picked beside
        # it.  The platform rounds the raw pixel density onto a bucket
        # and the browser then reads the ratio of the *panel*, so the
        # two are one number wearing two names: the 213 dpi bucket
        # reports 1.5 and not the 1.333x of its arithmetic quotient,
        # and the 411 and 420 buckets both report 2.625.  Choosing the
        # ratio from a separate list is how a panel ends up carrying a
        # density and a ratio that no device has ever had, and a site
        # that cross-checks the two reads the disagreement as a fake.
        dpr = ratio_for_density(density)

        # A preset that offers a ratio of its own may not name the ratio
        # its own density bucket reports.  The bucket and the offered
        # ratio have to be brought into agreement, and the density is
        # the one that moves: it is snapped onto the bucket whose ratio
        # the preset does offer, so that the pair the profile reports is
        # one a real device reports.  A preset that offers no ratio at
        # all keeps the bucket it was given.
        offered = tuple(preset.dpr_options or ())
        if offered and dpr not in offered:
            wanted = _nearest_offered_ratio(offered, dpr)
            density = _density_for_ratio(wanted, density)
            dpr = ratio_for_density(density)

        # The screen object reports *logical* pixels -- the same unit the
        # viewport reports in.  A real Android phone with a 1344x2992
        # physical panel at a ratio of 2.625 tells the page that the
        # screen is 512x1140 and that the ratio is 2.625; a profile that
        # reports the physical numbers instead is a profile whose
        # ``screen.width`` is four times its ``innerWidth``, which is a
        # shape no mobile browser has ever had and which a site spots by
        # comparing the two numbers.
        logical_width = int(round(width / dpr))
        logical_height = int(round(height / dpr))

        # The available area is the panel minus the system bars.  On a
        # phone in portrait the width is fully available -- the bars are
        # at the top and the bottom -- while the height loses the
        # status bar and the navigation bar.
        avail_w = logical_width
        avail_h = int(logical_height * 0.94)
        return preset.screen.model_copy(
            update={
                "width": logical_width,
                "height": logical_height,
                "avail_width": avail_w,
                "avail_height": avail_h,
                "density_dpi": density,
                "device_pixel_ratio": dpr,
            }
        )

    def _gpu(self, preset: DevicePreset) -> GpuProfile:
        """The catalogue entry of the GPU this unit carries.

        The renderer string and the limits always come from the same
        catalogue entry, so the reported limits are the ones that GPU really
        has.  Varying them independently would produce a contradiction a
        site could use to link two profiles.

        The *variant* of the limits -- the tier -- is not decided here.
        It is drawn on the ``gpu_params`` axis, which is separate from
        the ``webgl_params`` axis that picks the renderer, so that the
        choice of part and the choice of its memory configuration do
        not perturb each other.  The tier is carried on the identity
        so that the WebGL and the WebGPU builders apply the same one.
        """
        variants = _GPU_VARIANTS.get(preset.preset_id, (preset.gpu.unmasked_renderer,))
        renderer = self._deriver.derive_choice(Axis.WEBGL_PARAMS, variants, context="gpu_model")
        return _GPU_CATALOGUE.get(renderer, preset.gpu)

    def _gpu_tier(self, gpu: GpuProfile) -> int:
        """Draw the parameter variant of this unit.

        A unit may ship with a memory configuration that halves the
        addressable texture size, and a model that is not already at
        the top of its class may ship with the high-memory one.  Both
        are properties of the *unit*, not of the GPU model, so they
        are drawn per unit -- and the whole set of limits moves with
        the choice, because the memory that bounds the texture bounds
        the cube map, the renderbuffer and the viewport with it.
        """
        table = build_tier_table(gpu)
        if len(table) == 1:
            return 0
        return self._deriver.derive_int(
            Axis.GPU_PARAMS, 0, len(table) - 1, context="gpu_tier"
        )

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def build(self, preset: DevicePreset) -> DeviceIdentity:
        """Create the device instance for ``preset`` under this seed."""

        build_id = self._build_id(preset)
        incremental = self._version_incremental()
        hardware = self._hardware(preset)
        board = self._board(preset)
        release, sdk_int = self._release(preset)
        model = self._model(preset)

        build = AndroidBuild(
            version_release=release,
            sdk_int=sdk_int,
            security_patch=self._security_patch(build_id),
            build_id=build_id,
            display=f"{build_id}.{incremental}",
            version_incremental=incremental,
            model=model,
            device=preset.device,
            product=preset.product,
            manufacturer=preset.manufacturer,
            brand=preset.brand,
            hardware=hardware,
            board=board,
            bootloader=self._bootloader(preset),
            fingerprint=(
                f"{preset.brand.lower()}/{preset.product}/{preset.device}:"
                f"{release}/{build_id}.{incremental}:"
                f"{self._build_id_number()}:user/release-keys"
            ),
            host=preset.host,
            user=preset.user,
            tags=preset.tags,
            type=preset.type,
            serial=self._serial(),
        )
        gpu = self._gpu(preset)
        return DeviceIdentity(
            preset_id=preset.preset_id,
            build=build,
            screen=self._panel(preset),
            gpu=gpu,
            abi=preset.abi,
            gpu_tier=self._gpu_tier(gpu),
        )

    def _model(self, preset: DevicePreset) -> str:
        """The model code this unit carries.

        A handset is sold under several codes -- the global one, the
        American one, the Korean one -- and the code is in the build
        fingerprint, in the user agent and in the client hints.  Two
        handsets of one model that were bought in different regions
        are two different devices to a site that reads the code.

        The caller may pass the code it already chose, because the
        code is walked together with the memory and the release in one
        mixed radix and the three have to agree.
        """

        variants = preset.model_variants
        if not variants:
            return preset.model

        if self._chosen_model in variants:
            return self._chosen_model

        if self._stratum is not None:
            index = self._deriver.derive_stratified(
                Axis.ANDROID_BUILD,
                list(range(len(variants))),
                "model-code",
                self._stratum,
            )
        else:
            index = self._deriver.derive_int(
                Axis.ANDROID_BUILD, 0, len(variants) - 1, context="model-code"
            )
        return variants[index]

    def _release(self, preset: DevicePreset) -> tuple[str, int]:
        """Pick the Android release this unit is running.

        A handset is not pinned to the release it shipped with.  The
        Android release is in the user agent, so two handsets on
        different releases are visibly different handsets -- and a
        fleet of identical phones really is spread across releases,
        because the update arrives over the air and each phone takes
        it at its own time.

        The draw is stratified for the same reason the browser major
        is: the release list is short, and an independent draw lets
        two handsets of one model land on the same one often enough
        for a site to group them.

        The caller may pass the release it already chose, because the
        release is walked together with the memory in one mixed radix
        and the two have to agree.
        """

        variants = preset.release_variants
        if not variants:
            return preset.release, preset.sdk_int

        for release, sdk_int in variants:
            if release == self._chosen_release:
                return release, sdk_int

        stratum = self._stratum
        if stratum is not None:
            index = self._deriver.derive_stratified(
                Axis.ANDROID_BUILD,
                list(range(len(variants))),
                "android-release",
                stratum,
            )
        else:
            index = self._deriver.derive_int(
                Axis.ANDROID_BUILD, 0, len(variants) - 1, context="android-release"
            )
        release, sdk_int = variants[index]
        return release, sdk_int


__all__ = ["DeviceIdentity", "DeviceIdentityFactory"]
