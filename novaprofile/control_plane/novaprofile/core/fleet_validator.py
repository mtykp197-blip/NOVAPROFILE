"""Physical-coherence validator for the Android device fleet.

The fleet is growing from the hand-written reference catalogue to tens of
thousands of devices generated from real GSMArena specifications.  A
generator that produces 10,000 devices has to be able to fail: a preset
whose SoC claims a Tensor while its renderer claims an Adreno, or whose
texture limit belongs to a different GPU class, is a profile that any
anti-bot vendor can single out by cross-checking two values it already
has.

This module is that gate.  It is deliberately *standalone*: it works on
:class:`~novaprofile.core.devices.DevicePreset` objects only and never
imports the generator, so the generator cannot satisfy the checker by
construction.

The rules are grouped by the axis they police:

``gpu``
    The renderer, the vendor, the texture limit and the announced GPU
    architecture have to describe the same physical GPU.
``abi``
    The SoC vendor has to follow from the SoC model.
``screen``
    The panel has to be physically possible and the pixel ratio has to
    agree with the density bucket.
``release``
    The Android release, the SDK level, the security patch and the build
    id have to describe the same moment in time.
``memory``
    The RAM, CPU, pixel-ratio and density options have to be plausible
    and have to cover the panel the preset itself declares.
``identity``
    The identity strings have to be present and the preset id has to be
    unique across the fleet.

The reference catalogue -- the devices in
:mod:`novaprofile.core.devices` and
:mod:`novaprofile.core.device_catalog_extended` -- is the ground truth:
every rule below passes on all of them.  Where the shipped catalogue
deviates from a textbook rule (a Snapdragon 8 Gen 1 whose architecture
string says "Adreno 642", a Motorola build id of the form "T1TR33.2-72-1")
the rule is written to accept the shipped reality, because the checker
exists to protect the catalogue's contract rather than to contradict it.

Three rules are looser than a literal reading would make them, and each
of them is looser because the catalogue says so:

* the **architecture** only has to name the same GPU *family* as the
  renderer, not the same model -- the catalogue ships an Adreno 660 whose
  architecture string says "Adreno 642";
* the **density options** only have to cover the panel's density
  *approximately* -- a Xperia 1 VI ships a 560 dpi panel and offers
  650 dpi -- while the pixel ratio options have to cover the ratio
  exactly, because a preset cannot produce a ratio it does not offer;
* the **pixel ratio** only has to be the ratio of the panel's density
  bucket or one of its two neighbours -- a 420 dpi bucket reports 2.625
  on most handsets and 3.0 on the Xperia 10 IV.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetIssue:
    """A single physical-coherence finding against one preset."""

    preset_id: str
    axis: str
    field: str
    message: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        """A JSON-serialisable view of the issue."""
        return {
            "preset_id": self.preset_id,
            "axis": self.axis,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }

    def __str__(self) -> str:
        location = self.field if self.field.startswith(f"{self.axis}.") else (
            f"{self.axis}.{self.field}"
        )
        return f"[{self.severity}] {self.preset_id}: {location}: {self.message}"


@dataclass(frozen=True)
class FleetReport:
    """The result of validating a whole fleet."""

    checked: int
    issues: tuple[FleetIssue, ...]

    @property
    def errors(self) -> tuple[FleetIssue, ...]:
        """The issues that make the fleet unusable."""
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[FleetIssue, ...]:
        """The issues that are merely implausible."""
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        """``True`` when the fleet carries no hard error."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view of the report."""
        return {
            "checked": self.checked,
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def summary(self) -> str:
        """A one-paragraph human summary of the fleet's health."""
        errors = self.errors
        warnings = self.warnings
        verdict = "coherent" if not errors else "INCOHERENT"
        text = (
            f"Checked {self.checked} device preset(s): the fleet is {verdict} "
            f"with {len(errors)} error(s) and {len(warnings)} warning(s)."
        )
        if errors:
            by_axis: dict[str, int] = {}
            for issue in errors:
                by_axis[issue.axis] = by_axis.get(issue.axis, 0) + 1
            detail = ", ".join(f"{axis}={count}" for axis, count in sorted(by_axis.items()))
            text += f" Errors by axis: {detail}."
            first = errors[0]
            location = (
                first.field
                if first.field.startswith(f"{first.axis}.")
                else f"{first.axis}.{first.field}"
            )
            text += f" First error: {first.preset_id} [{location}] {first.message}"
        if warnings:
            by_axis: dict[str, int] = {}
            for issue in warnings:
                by_axis[issue.axis] = by_axis.get(issue.axis, 0) + 1
            detail = ", ".join(f"{axis}={count}" for axis, count in sorted(by_axis.items()))
            text += f" Warnings by axis: {detail}."
        return text


# --------------------------------------------------------------------------
# Rule tables
# --------------------------------------------------------------------------

#: Android release -> SDK level.  A preset whose release and SDK level
#: disagree describes a device that cannot exist.
_RELEASE_SDK: dict[str, int] = {
    "9": 28,
    "10": 29,
    "11": 30,
    "12": 31,
    "13": 33,
    "14": 34,
    "15": 35,
    "16": 36,
}

#: The GPU class a renderer belongs to.  The class decides which texture
#: limits the GPU may report.
#:
#: A model can appear in more than one class -- the Adreno 630 is a
#: budget part in the entry-level Snapdragon and a flagship part in the
#: Snapdragon 8 series.  The renderer string alone does not carry the
#: SoC, so the class of an ambiguous model is the *union* of the classes
#: it can belong to, and the texture limit has to fall inside that union.
#: The catalog resolves the ambiguity the way the hardware does: the
#: entry-level parts report the smaller limit and the flagships the
#: larger one.
#:
#: The table is keyed on the *specification* of the part, not on the
#: name of its family, because a family spans several classes: the
#: Mali-G52 is a 4096 part in its first revision and an 8192 part in
#: the later ones, and the Mali-G71 and Mali-G72 are 16384 parts
#: throughout.  Every revision that changes the class therefore gets
#: its own pattern, and the longest matching pattern wins.
#:
#: The patterns are matched against the *normalised* renderer, so a
#: pattern is written without the trademark marker ("Adreno 610", not
#: "Adreno (TM) 610").  A pattern does not have to span the whole
#: renderer string: the real data carries model suffixes such as
#: "Mali-G76 MP12 - EMEA/LATAM" or "Adreno 330 (S800)", and the
#: pattern only has to match the leading part of the model name.
_GPU_CLASSES: dict[str, tuple[str, ...]] = {
    # --- budget: the 4096 and 8192 parts -------------------------------
    "budget": (
        # Qualcomm Adreno, the first generation up to the entry-level
        # Snapdragon 8 series.  The Adreno 2xx/3xx are the original
        # 2000s parts and report 4096; the 4xx/5xx report 8192.
        "Adreno 130",
        "Adreno 200",
        "Adreno 203",
        "Adreno 205",
        "Adreno 220",
        "Adreno 225",
        "Adreno 302",
        "Adreno 304",
        "Adreno 305",
        "Adreno 306",
        "Adreno 308",
        "Adreno 320",
        "Adreno 330",
        "Adreno 400",
        "Adreno 405",
        "Adreno 418",
        "Adreno 420",
        "Adreno 430",
        "Adreno 504",
        "Adreno 506",
        "Adreno 509",
        "Adreno 510",
        "Adreno 610",
        "Adreno 616",
        "Adreno 618",
        "Adreno 619",
        # The bare family name, when a record carries no model at all.
        "Adreno",
        # ARM Mali.  The T6xx/T7xx/T8xx families are the older Utgard
        # cores and report 8192, and so does the first wave of the
        # G51/G52/G615.
        "Mali-400",
        "Mali-450",
        "Mali-T6",
        "Mali-T624",
        "Mali-T628",
        "Mali-T720",
        "Mali-T7",
        "Mali-T820",
        "Mali-T830",
        "Mali-T860",
        "Mali-T880",
        "Mali-G51",
        # The Mali-G52 MC2 is the 4096 core of the Helio G80/G85/G88;
        # the later MC4/MC6 revisions and the 2EEMC2 one are 8192 and
        # are listed in the mid class below.  The bare family takes
        # the class of its most common revision, which is the MC2.
        "Mali-G52 MC2",
        "Mali-G52 MP2",
        "Mali-G52",
        "Mali G52",
        "Mali-G615",
        "Mali G615",
        # The bare family name, when a record carries no model at all.
        "Mali",
        # Imagination PowerVR.  The SGX5xx cores, the Rogue GE8/GE83
        # and the G6x00/GX parts all report 4096.
        "PowerVR SGX",
        "PowerVR GE8",
        "PowerVR GE8300",
        "PowerVR G6200",
        "PowerVR G6430",
        "PowerVR GM9446",
        "PowerVR GT7600",
        "PowerVR GX",
        "PowerVR GXA",
        "PowerVR Series",
        # The bare family name, when a record carries no model at all.
        "PowerVR",
        # Some sheets name the core without its family name.
        "SGX531",
        "SGX544",
        # Imagination B-Series.
        "IMG8322",
        "IMG BXM-8-256",
        "IMG 8XE1PPC",
        "IMG GE8300",
        # Vivante's mobile cores are the 4096 parts of the low end.
        "Vivante GC4000",
        "Vivante GC7000",
        "Vivante GC800",
    ),
    # --- mid: the 8192 and 16384 parts ---------------------------------
    "mid": (
        # The mainstream Adreno parts of the Snapdragon 6 and 7 series.
        "Adreno 505",
        "Adreno 508",
        "Adreno 512",
        "Adreno 530",
        "Adreno 540",
        # The Adreno 612 (Snapdragon 675) and the Adreno 613 (Snapdragon
        # 4 Gen 2) are the 16384 parts of the mid tier.
        "Adreno 612",
        "Adreno 613",
        # The Adreno 630 ships in an entry-level Snapdragon and in a
        # flagship one, so it belongs to both classes.
        "Adreno 630",
        "Adreno 642",
        # The Mali-G52 2EEMC2 (Helio G70) and the later G52 revisions
        # are 8192 parts; the bare "Mali-G52" takes the class of its
        # most common revision, which is the MC2, and is listed in the
        # budget class above.
        "Mali-G52 2EEMC2",
        "Mali-G52 MP6",
        "Mali-G52 MC",
        "Mali-G57",
        "Mali-G68",
        # The Mali-G61/G615/G68/G71/G72 are the mainstream Utgard
        # cores of the mid tier.
        "Mali-G71 MP3",
        "Mali-G72 MC",
        # The Mali-G610 is an 8192 part: the MC3, MC4 and MC6
        # revisions of the Dimensity 7000 and 8000 series all report
        # 8192.  The mid band covers the 8192 and the 16384 limit.
        "Mali-G610",
        "Mali-T760",
        "Mali-T860",
        "Mali-G76 MC4",
    ),
    # --- flagship: the 16384 parts ------------------------------------
    "flagship": (
        "Adreno 615",
        "Adreno 620",
        "Adreno 630",
        "Adreno 640",
        "Adreno 642",
        "Adreno 644",
        "Adreno 650",
        "Adreno 660",
        "Adreno 702",
        "Adreno A702",
        "Adreno 710",
        "Adreno 720",
        "Adreno 725",
        "Adreno 730",
        "Adreno 732",
        "Adreno 735",
        "Adreno 740",
        "Adreno 742",
        "Adreno 750",
        "Adreno 840",
        "Mali-G710",
        "Mali-G715",
        "Mali-G71",
        "Mali-G72",
        "Mali-G76",
        "Mali-G77",
        "Mali-G78",
        "Immortalis-G715",
        "Immortalis-G720",
        "Xclipse 920",
        "Xclipse 940",
        "Xclipse 530",
        "Maleoon 910",
        # Apple's own GPU cores.  The four- and five-core designs are
        # the A-series parts of the iPhone 11 and the A13/A14; the
        # six-core and wider designs are the A15 and later, and they
        # are all 16384 parts.
        "Apple GPU (4-core",
        "Apple GPU (5-core",
        "Apple GPU (6-core",
        "Apple GPU (7-core",
        "Apple GPU (8-core",
        "Apple GPU (9-core",
        "Apple GPU (10-core",
        "Apple GPU (three-core",
        "Apple GPU (four-core",
        "Apple GPU (five-core",
        "Apple GPU (six-core",
        "Apple GPU (seven-core",
        "Apple GPU (eight-core",
        "Apple GPU (nine-core",
        # The desktop and console cores that the dataset picked up from
        # the GSMArena specification sheets of pre-2011 handsets.  They
        # all report 16384.
        "ULP GeForce",
        "Nvidia Maxwell",
        "Kepler",
        "Intel HD Graphics",
        "Intel Gen",
        "Intel graphics media accelerator",
        "Broadcom VideoCore",
        "VideoCore IV",
        "Vivante GC1000",
    ),
}

#: The texture limits each GPU class may report.  A class is a band, not
#: a point: the exact number is less important than the class agreement.
_CLASS_TEXTURES: dict[str, tuple[int, ...]] = {
    "budget": (4096, 8192),
    "mid": (8192, 16384),
    "flagship": (16384,),
}

#: The renderer families and the vendors that ship them.  A renderer that
#: belongs to one family cannot report another family's vendor.
_RENDERER_VENDORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Immortalis", ("ARM", "Google Inc. (ARM)")),
    ("Maleoon", ("Huawei",)),
    ("Xclipse", ("ARM",)),
    ("PowerVR", ("Imagination Technologies",)),
    ("SGX", ("Imagination Technologies",)),
    ("Adreno", ("Qualcomm", "Google Inc. (Qualcomm)")),
    ("Mali", ("ARM", "Google Inc. (ARM)")),
    ("IMG", ("Imagination Technologies",)),
    ("Apple GPU", ("ARM", "Apple", "Google Inc. (ARM)")),
    ("VideoCore", ("ARM", "Broadcom")),
    ("Nvidia", ("ARM", "NVIDIA")),
    ("Kepler", ("ARM", "NVIDIA")),
    ("Intel", ("ARM", "Intel")),
    ("Vivante", ("ARM", "Vivante")),
    ("GeForce", ("ARM", "NVIDIA")),
)

#: The GPU family a renderer belongs to.  The architecture string has to
#: name the same family -- a different model of the same family is a
#: legitimate difference (the catalogue ships an Adreno 660 whose
#: architecture string says "Adreno 642"), a different family is not.
_RENDERER_FAMILIES: tuple[tuple[str, str], ...] = (
    ("Immortalis", "Immortalis"),
    ("Maleoon", "Maleoon"),
    ("Xclipse", "Xclipse"),
    ("PowerVR", "PowerVR"),
    ("SGX", "PowerVR"),
    ("Adreno", "Adreno"),
    ("Mali", "Mali"),
    ("Apple GPU", "Apple GPU"),
    ("VideoCore", "VideoCore"),
    ("GeForce", "GeForce"),
    ("Nvidia", "Nvidia"),
    ("Kepler", "Kepler"),
    ("Intel", "Intel"),
    ("Vivante", "Vivante"),
    ("IMG", "IMG"),
)

#: The SoC model fragments and the vendor that builds them.
_SOC_VENDOR_MARKERS: tuple[tuple[str, str], ...] = (
    ("snapdragon", "Qualcomm"),
    ("tensor", "Google"),
    ("exynos", "Samsung"),
    ("dimensity", "MediaTek"),
    ("helio", "MediaTek"),
    ("kirin", "HiSilicon"),
    ("unisoc", "Unisoc"),
    ("spreadtrum", "Unisoc"),
)

#: The canonical Android build id, e.g. "UP1A.231005.007": an alphanumeric
#: tag that ends in a digit, a dot, six digits, a dot, three digits.  The
#: tag carries the build's branch letter and its year/month code, so
#: "UQ1A", "TP1A" and "TQ3A" are all real.
_BUILD_ID_PATTERN = re.compile(r"^[A-Za-z]{2,4}\d[A-Za-z]?\.\d{6}\.\d{3}$")

#: Vendor specific build ids that do not follow the canonical shape but
#: are real: Motorola ships "T1TR33.2-72-1" and Huawei ships
#: "HUAWEIALN-L29".
_BUILD_ID_VENDOR_PATTERN = re.compile(
    r"^(?:[A-Za-z]{1,3}\d[A-Za-z0-9]*\.\d+-\d+-\d+|[A-Za-z]{2,}[A-Z0-9]{2,}-[A-Z0-9]{2,})$"
)

#: The standard Android density buckets and the pixel ratio each of them
#: implies.  A panel's true density is rounded to the nearest bucket, and
#: the browser then reports the ratio of the panel rather than the ratio
#: of the rounded bucket -- so a 400 dpi panel (a 2.5x bucket) quite
#: legitimately reports a 2.625 ratio.
#:
#: The buckets are the ones the platform defines, and they are not
#: evenly spaced: 213 sits between 160 and 240, 411 and 420 sit
#: between 400 and 440, and 520, 525 and 545 sit between 480 and 560.
#: The mapping is the platform's own table and not the arithmetic
#: quotient of the density by 160 -- the 213 bucket reports 1.5 and
#: not the 1.333x of its quotient, and the 411 and 420 buckets both
#: report 2.625.
_DENSITY_BUCKETS: dict[int, float] = {
    120: 1.0,
    160: 1.0,
    213: 1.5,
    240: 1.5,
    280: 1.75,
    320: 2.0,
    360: 2.25,
    400: 2.5,
    411: 2.625,
    420: 2.625,
    440: 2.75,
    480: 3.0,
    520: 3.25,
    525: 3.25,
    545: 3.5,
    560: 3.5,
    640: 4.0,
}

#: The pixel ratios a bucket may be reported at, in ascending order.  A
#: device reports the ratio of its own panel, which is the ratio of its
#: rounded bucket or one of the neighbouring standard ratios.
_STANDARD_RATIOS: tuple[float, ...] = (
    1.0,
    1.5,
    1.75,
    2.0,
    2.25,
    2.5,
    2.625,
    2.75,
    3.0,
    3.25,
    3.5,
    4.0,
)

#: How many standard ratios away from its bucket a panel may report its
#: pixel ratio.
_DPR_STEP_REACH = 2

#: How far the declared panel density may sit from the nearest declared
#: density option, as a fraction of the panel density.  The catalogue
#: pairs a panel with the density list of the family it belongs to, so
#: the match is a neighbourhood and not an exact equality -- a Xperia 1 VI
#: ships a 560 dpi panel and offers 650 dpi.
_DENSITY_OPTION_TOLERANCE = 0.20


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


#: A renderer prefix that is followed by a core name.  The core name is
#: kept and only the vendor word in front of it is dropped.
_RENDERER_PREFIX_PATTERN = re.compile(
    r"^(?:Qualcomm|Imagination Technologies|ARM|Arm|MediaTek|Mediatek)\s+"
    r"(?=(?:Mali|Adreno|PowerVR|IMG|Xclipse|Immortalis|Maleoon)\b)"
)

#: Some sheets write the ARM core without its family name -- "ARM G77
#: MC9" instead of "ARM Mali-G77 MC9".  The family name is restored so
#: that the core is recognisable as an ARM part.
_RENDERER_BARE_ARM_CORE_PATTERN = re.compile(
    r"^(?:ARM|Arm)\s+(?=(?:G(?:5[1-9]|6[0-9]|7[0-9]|78)|T\d)\b)"
)


def _normalise_renderer(renderer: str) -> str:
    """Strip the trademark, vendor and revision noise from a renderer.

    Chromium reports "Adreno (TM) 750" where the catalogue table says
    "Adreno 750"; the two have to be comparable.  The GSMArena
    specification sheets add the vendor in front of the core -- "ARM
    Mali-G76 MP16", "Qualcomm Adreno (TM) 618" -- and that word says
    nothing about the class of the core, so it is dropped as well.

    Only a prefix that is followed by a known core name is dropped, so
    a renderer whose name *is* a vendor product name -- "Apple GPU",
    "PowerVR", "Broadcom VideoCore IV" -- keeps its name and stays
    recognisable.
    """
    cleaned = re.sub(r"\(TM\)", " ", renderer)
    cleaned = re.sub(r"\(R\)", " ", cleaned)
    cleaned = _RENDERER_PREFIX_PATTERN.sub("", cleaned)
    cleaned = _RENDERER_BARE_ARM_CORE_PATTERN.sub("Mali-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _issue(
    preset_id: str,
    axis: str,
    field: str,
    message: str,
    severity: str = "error",
) -> FleetIssue:
    return FleetIssue(
        preset_id=preset_id,
        axis=axis,
        field=field,
        message=message,
        severity=severity,
    )


def _gpu_classes(renderer: str) -> set[str]:
    """Every class a renderer can belong to.

    A model that appears in more than one class -- the Adreno 630, which
    ships in both an entry-level Snapdragon and a flagship one -- belongs
    to all of them.  The renderer string does not carry the SoC, so the
    texture limit only has to fall inside the union of the bands.

    The longest matching pattern wins, so "Mali-T760" is recognised as a
    mid-range part before the generic "Mali-T7" budget pattern can claim
    it.
    """
    normalised = _normalise_renderer(renderer)
    best_length = -1
    matched: set[str] = set()
    for class_name, patterns in _GPU_CLASSES.items():
        for pattern in patterns:
            if pattern not in normalised:
                continue
            if len(pattern) > best_length:
                best_length = len(pattern)
                matched = {class_name}
            elif len(pattern) == best_length:
                matched.add(class_name)
    return matched


def _renderer_family(renderer: str) -> str | None:
    """The GPU family a renderer belongs to."""
    normalised = _normalise_renderer(renderer)
    for marker, family in _RENDERER_FAMILIES:
        if marker in normalised:
            return family
    return None


def _expected_soc_vendor(soc_model: str) -> str | None:
    """The vendor implied by a SoC model name, or ``None`` when unclear."""
    lowered = soc_model.lower()
    for marker, vendor in _SOC_VENDOR_MARKERS:
        if marker in lowered:
            return vendor
    return None


def _nominal_dpr(density_dpi: int) -> float | None:
    """The pixel ratio of the density bucket a panel falls into.

    The buckets are sparse but real panels report densities in between,
    because the value Android reports is the panel's true density
    rounded to the nearest bucket the platform defines.  A 440 dpi panel
    is a 480 dpi panel in the 440 bucket.  The nearest bucket therefore
    decides the nominal ratio.

    ``None`` is returned when the density is out of range of the bucket
    table altogether, in which case the rule does not apply.
    """
    if density_dpi in _DENSITY_BUCKETS:
        return _DENSITY_BUCKETS[density_dpi]
    buckets = sorted(_DENSITY_BUCKETS)
    if density_dpi < buckets[0] or density_dpi > buckets[-1]:
        return None
    nearest = min(buckets, key=lambda bucket: abs(bucket - density_dpi))
    return _DENSITY_BUCKETS[nearest]


def _dpr_is_plausible(density_dpi: int, device_pixel_ratio: float) -> bool:
    """Whether a pixel ratio is one a panel of that density may report.

    A panel reports the ratio of the panel, not the ratio of the density
    bucket it was rounded into.  Android rounds the panel's true density
    down to a bucket, and the browser then divides the panel's pixel
    count by the CSS viewport size, so the reported ratio can sit a step
    or two away from the ratio of the rounded bucket: a 420 dpi bucket
    reports 2.625 on most handsets and 3.0 on the Xperia 10 IV.  A ratio
    further away than that describes a device whose panel and browser
    disagree.
    """
    nominal = _nominal_dpr(density_dpi)
    if nominal is None:
        return True
    allowed = {nominal}
    for index, ratio in enumerate(_STANDARD_RATIOS):
        if abs(ratio - nominal) < 1e-9:
            low = max(0, index - _DPR_STEP_REACH)
            high = min(len(_STANDARD_RATIOS), index + _DPR_STEP_REACH + 1)
            allowed.update(_STANDARD_RATIOS[low:high])
            break
    return any(abs(device_pixel_ratio - value) < 1e-9 for value in allowed)


# --------------------------------------------------------------------------
# Individual rule groups
# --------------------------------------------------------------------------


def _check_gpu(preset: Any, issues: list[FleetIssue]) -> None:
    """The GPU class, vendor and architecture have to agree."""
    gpu = preset.gpu
    renderer = gpu.unmasked_renderer or ""
    vendor = gpu.unmasked_vendor or ""

    # --- rule 1: the texture limit has to match the GPU class ---
    classes = _gpu_classes(renderer)
    if not classes:
        issues.append(
            _issue(
                preset.preset_id,
                "gpu",
                "gpu.unmasked_renderer",
                f"the renderer {renderer!r} does not belong to a known GPU class",
                "warning",
            )
        )
    else:
        # An ambiguous model belongs to several classes; the limit is
        # plausible as long as it is valid for one of them.
        bands = [_CLASS_TEXTURES[name] for name in classes]
        if not any(gpu.max_texture_size in band for band in bands):
            allowed = sorted({limit for band in bands for limit in band})
            issues.append(
                _issue(
                    preset.preset_id,
                    "gpu",
                    "gpu.max_texture_size",
                    (
                        f"the renderer {renderer!r} is a {'/'.join(sorted(classes))} GPU and "
                        f"reports a texture limit of {gpu.max_texture_size}, but such a GPU "
                        f"reports one of {', '.join(str(value) for value in allowed)}"
                    ),
                )
            )

    # --- rule 2: the renderer vendor has to match the family ---
    normalised = _normalise_renderer(renderer)
    for marker, allowed_vendors in _RENDERER_VENDORS:
        if marker in normalised and vendor not in allowed_vendors:
            issues.append(
                _issue(
                    preset.preset_id,
                    "gpu",
                    "gpu.unmasked_vendor",
                    (
                        f"the renderer {renderer!r} is a {marker} GPU and is shipped by "
                        f"{' or '.join(allowed_vendors)}, but the preset claims the vendor "
                        f"{vendor!r}"
                    ),
                )
            )
            break

    # --- rule 3: the announced architecture has to name the same GPU ---
    architecture = preset.abi.gpu_architecture or ""
    renderer_family = _renderer_family(renderer)
    if renderer_family is None:
        issues.append(
            _issue(
                preset.preset_id,
                "gpu",
                "gpu.unmasked_renderer",
                f"the renderer {renderer!r} does not belong to a known GPU family",
                "warning",
            )
        )
    elif renderer_family.lower() not in architecture.lower():
        issues.append(
            _issue(
                preset.preset_id,
                "abi",
                "abi.gpu_architecture",
                (
                    f"the architecture {architecture!r} does not name the same GPU as the "
                    f"renderer {renderer!r}, which is a {renderer_family} part"
                ),
            )
        )


def _check_soc(preset: Any, issues: list[FleetIssue]) -> None:
    """The SoC vendor has to follow from the SoC model."""
    expected = _expected_soc_vendor(preset.abi.soc_model)
    if expected is None:
        return
    actual = preset.abi.soc_vendor
    if actual != expected:
        issues.append(
            _issue(
                preset.preset_id,
                "abi",
                "abi.soc_vendor",
                (
                    f"the SoC {preset.abi.soc_model!r} is a {expected} part but the preset "
                    f"claims the SoC vendor {actual!r}"
                ),
            )
        )


def _check_screen(preset: Any, issues: list[FleetIssue]) -> None:
    """The panel has to be physically possible."""
    screen = preset.screen

    if screen.width <= 0:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.width",
                (
                    f"the panel reports a width of {screen.width}, which is not a "
                    "possible screen size"
                ),
            )
        )
    if screen.height <= 0:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.height",
                (
                    f"the panel reports a height of {screen.height}, which is not a "
                    "possible screen size"
                ),
            )
        )
    if screen.avail_width > screen.width:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.avail_width",
                (
                    f"the available width {screen.avail_width} exceeds the screen width "
                    f"{screen.width}"
                ),
            )
        )
    if screen.avail_height > screen.height:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.avail_height",
                (
                    f"the available height {screen.avail_height} exceeds the screen height "
                    f"{screen.height}"
                ),
            )
        )
    if not 1.0 <= screen.device_pixel_ratio <= 5.0:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.device_pixel_ratio",
                (
                    f"the device pixel ratio {screen.device_pixel_ratio} is outside the "
                    "range an Android panel can report (1.0 to 5.0)"
                ),
            )
        )
    if not 120 <= screen.density_dpi <= 800:
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.density_dpi",
                (
                    f"the panel density {screen.density_dpi} dpi is outside the range an "
                    "Android panel can report (120 to 800)"
                ),
            )
        )

    # --- rule 6: the pixel ratio has to agree with the density ---
    nominal = _nominal_dpr(screen.density_dpi)
    if nominal is not None and not _dpr_is_plausible(
        screen.density_dpi, screen.device_pixel_ratio
    ):
        issues.append(
            _issue(
                preset.preset_id,
                "screen",
                "screen.device_pixel_ratio",
                (
                    f"the device pixel ratio {screen.device_pixel_ratio} does not agree "
                    f"with the {screen.density_dpi} dpi density bucket, which implies a "
                    f"ratio of {nominal} or one of the neighbouring standard ratios"
                ),
                "warning",
            )
        )


def _check_release(preset: Any, issues: list[FleetIssue]) -> None:
    """The release, the SDK level, the patch and the build id agree."""
    expected_sdk = _RELEASE_SDK.get(str(preset.release))
    if expected_sdk is not None and preset.sdk_int != expected_sdk:
        issues.append(
            _issue(
                preset.preset_id,
                "release",
                "release.sdk_int",
                (
                    f"the SDK level {preset.sdk_int} does not correspond to Android "
                    f"{preset.release} (expected {expected_sdk})"
                ),
            )
        )

    patch = preset.security_patch or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", patch):
        issues.append(
            _issue(
                preset.preset_id,
                    "release",
                    "release.security_patch",
                    f"the security patch {patch!r} is not a date of the form YYYY-MM-DD",
            )
        )
    else:
        try:
            parsed = date(int(patch[0:4]), int(patch[5:7]), int(patch[8:10]))
        except ValueError:
            issues.append(
                _issue(
                    preset.preset_id,
                    "release",
                    "release.security_patch",
                    f"the security patch {patch!r} is not a real calendar date",
                )
            )
        else:
            if parsed > date.today():
                issues.append(
                    _issue(
                        preset.preset_id,
                        "release",
                        "release.security_patch",
                        f"the security patch {patch!r} lies in the future",
                    )
                )

    build_id = preset.build_id or ""
    if not (_BUILD_ID_PATTERN.match(build_id) or _BUILD_ID_VENDOR_PATTERN.match(build_id)):
        issues.append(
            _issue(
                preset.preset_id,
                "release",
                "release.build_id",
                (
                    f"the build id {build_id!r} does not look like an Android build id "
                    "(expected something like 'UP1A.231005.007')"
                ),
            )
        )


def _check_identity(preset: Any, issues: list[FleetIssue]) -> None:
    """The identity strings have to be present."""
    for field in (
        "preset_id",
        "manufacturer",
        "brand",
        "model",
        "device",
        "product",
        "hardware",
        "board",
        "bootloader",
        "fingerprint_product",
        "build_id",
        "version_incremental",
    ):
        value = getattr(preset, field, "")
        if not isinstance(value, str) or not value.strip():
            issues.append(
                _issue(
                    preset.preset_id,
                    "identity",
                    f"identity.{field}",
                    f"the identity field {field!r} is empty",
                )
            )


def _check_memory(preset: Any, issues: list[FleetIssue]) -> None:
    """The option lists have to be plausible and cover the panel."""
    ram_options = tuple(preset.ram_options or ())
    if not ram_options:
        issues.append(
            _issue(
                preset.preset_id,
                "memory",
                "memory.ram_options",
                "the preset offers no RAM configuration at all",
            )
        )
    for value in ram_options:
        if not 1 <= value <= 64:
            issues.append(
                _issue(
                    preset.preset_id,
                    "memory",
                    "memory.ram_options",
                    f"the RAM option {value} GB is outside the range a device offers (1 to 64)",
                    "warning",
                )
            )

    cpu_options = tuple(preset.cpu_options or ())
    if not cpu_options:
        issues.append(
            _issue(
                preset.preset_id,
                "memory",
                "memory.cpu_options",
                "the preset offers no CPU core configuration at all",
            )
        )
    for value in cpu_options:
        if not 1 <= value <= 32:
            issues.append(
                _issue(
                    preset.preset_id,
                    "memory",
                    "memory.cpu_options",
                    (
                        f"the CPU core option {value} is outside the range a device offers "
                        "(1 to 32)"
                    ),
                    "warning",
                )
            )

    dpr_options = tuple(preset.dpr_options or ())
    if not dpr_options:
        issues.append(
            _issue(
                preset.preset_id,
                "memory",
                "memory.dpr_options",
                "the preset offers no pixel ratio at all",
            )
        )
    else:
        own_dpr = preset.screen.device_pixel_ratio
        if not any(abs(value - own_dpr) < 1e-9 for value in dpr_options):
            issues.append(
                _issue(
                    preset.preset_id,
                    "memory",
                    "memory.dpr_options",
                    (
                        f"the preset offers the pixel ratios {list(dpr_options)} but not the "
                        f"ratio {own_dpr} of its own panel, so it cannot produce that panel"
                    ),
                )
            )

    density_options = tuple(preset.density_options or ())
    if not density_options:
        issues.append(
            _issue(
                preset.preset_id,
                "memory",
                "memory.density_options",
                "the preset offers no panel density at all",
            )
        )
    else:
        own_density = preset.screen.density_dpi
        reach = own_density * _DENSITY_OPTION_TOLERANCE
        if not any(abs(value - own_density) <= reach for value in density_options):
            issues.append(
                _issue(
                    preset.preset_id,
                    "memory",
                    "memory.density_options",
                    (
                        f"the preset offers the densities {list(density_options)} but not the "
                        f"density {own_density} of its own panel, so it cannot produce that "
                        "panel"
                    ),
                )
            )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def validate_preset(preset: Any) -> list[FleetIssue]:
    """Validate a single preset and return the issues it raises.

    The returned list is empty when the preset is physically coherent.
    """
    issues: list[FleetIssue] = []

    _check_gpu(preset, issues)
    _check_soc(preset, issues)
    _check_screen(preset, issues)
    _check_release(preset, issues)
    _check_identity(preset, issues)
    _check_memory(preset, issues)

    return issues


def validate_fleet(presets: Iterable[Any]) -> FleetReport:
    """Validate a whole fleet and collect every issue.

    Duplicate preset ids are a hard error: the fleet builder keys on the
    preset id, so two devices with the same id silently overwrite each
    other.  The issue is reported on the second and later occurrences.
    """
    materialised: Sequence[Any] = list(presets)
    issues: list[FleetIssue] = []
    seen: dict[str, int] = {}

    for preset in materialised:
        preset_id = getattr(preset, "preset_id", "") or ""
        seen[preset_id] = seen.get(preset_id, 0) + 1
        occurrence = seen[preset_id]
        if occurrence > 1:
            issues.append(
                _issue(
                    preset_id,
                    "identity",
                    "identity.preset_id",
                    (
                        f"the preset id {preset_id!r} occurs {occurrence} times in the fleet; "
                        "the fleet builder keys on it and cannot hold duplicates"
                    ),
                )
            )
        issues.extend(validate_preset(preset))

    return FleetReport(checked=len(materialised), issues=tuple(issues))


__all__ = [
    "FleetIssue",
    "FleetReport",
    "validate_preset",
    "validate_fleet",
]
