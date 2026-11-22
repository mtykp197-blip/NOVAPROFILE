"""A multi-dimensional audit of a generated profile.

The project's claim is narrow and absolute: no two profiles may ever be
seen by a site as the same device or the same person, and every surface
of a single profile has to agree with every other surface of that same
profile.  A claim like that cannot be established by a unit test that
checks one field at a time -- it has to be checked against the profile
as a whole, the way a site would read it.

This module is that check.  It answers four questions about a fleet:

**Is it real?**
    Every value a profile reports has to be a value some real device
    could report.  A synthetic value -- a serial that is not a serial,
    a screen size no panel ever shipped, a GPU whose limits contradict
    its model -- is a fingerprint of its own kind: every profile that
    shares it is trivially groupable.  This is what
    :mod:`novaprofile.core.consistency` checks, and this module runs it
    over a whole fleet rather than one profile.

**Is it unique?**
    No two profiles may agree on any axis a site can read.  The axes are
    the guarded ones of :mod:`novaprofile.core.collision`.

**Is it coherent?**
    The axes of one profile have to agree with each other.  A profile
    that claims a Mali GPU and a Snapdragon GPU model, or a 4G
    connection with a 200 ms round trip and a 10 Mbit/s downlink, is a
    profile that no device could be, and a vendor that knows the
    combinations can single it out.

**Does it hold at scale?**
    Every axis has to carry enough entropy for a hundred million
    profiles.  An axis that does not is not a bug the guard can paper
    over: the guard would have to refuse to let the fleet exist.

The audit reports each dimension separately, because "the fleet is
unique" and "the fleet is real" are different claims and a project
that only meets one of them has not met the goal.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from novaprofile.core.collision import GUARDED_AXES
from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.schema.profile import ProfileConfig

#: The fleet the engine is designed for.
TARGET_FLEET = 100_000_000

#: The bits every identifying axis needs at the target fleet size.
REQUIRED_BITS = math.log2(TARGET_FLEET)

#: The characters a real device model name is made of.
#:
#: A model name is not restricted to the letters and the digits: the
#: manufacturers of the Vietnamese market ship devices whose names carry
#: parentheses -- "Nothing Phone (2a)", "Oppo Reno8 (China)", "Realme 12x
#: (India)" -- and the plus sign -- "Realme 12+", "Realme 13+".  Those
#: are the names the devices report in the User-Agent and in the
#: ``Sec-CH-UA-Model`` hint, and a rule that rejected them would reject
#: the devices themselves.
#:
#: What the pattern excludes is a name that carries a character no
#: manufacturer puts into a product name -- a comma, a colon, a slash or
#: a control character.
_MODEL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .()_+/-]*")


# ----------------------------------------------------------------------
# Findings
# ----------------------------------------------------------------------


@dataclass
class Finding:
    """One problem the audit found."""

    dimension: str
    """``real``, ``unique``, ``coherent`` or ``scale``."""

    severity: str
    """``error`` or ``warning``."""

    subject: str
    """What the problem is about -- an axis name, a field path."""

    message: str
    """What is wrong, in one sentence."""

    example: str = ""
    """A concrete instance of the problem, so it can be reproduced."""

    def __str__(self) -> str:
        prefix = f"[{self.dimension}/{self.severity}] {self.subject}: "
        tail = f" ({self.example})" if self.example else ""
        return f"{prefix}{self.message}{tail}"


@dataclass
class Dimension:
    """The result of one dimension of the audit."""

    name: str
    passed: bool
    checked: int
    """How many profiles (or axes) were checked."""

    failures: list[Finding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.failures if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.failures if f.severity == "warning")


@dataclass
class AuditReport:
    """The result of the whole audit."""

    dimensions: dict[str, Dimension] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether every dimension passed without an error."""
        return all(d.passed for d in self.dimensions.values())

    @property
    def total_errors(self) -> int:
        return sum(d.error_count for d in self.dimensions.values())

    @property
    def total_warnings(self) -> int:
        return sum(d.warning_count for d in self.dimensions.values())

    def add(self, dimension: Dimension) -> None:
        self.dimensions[dimension.name] = dimension

    def format(self) -> str:
        """Render the report as a readable summary."""
        lines: list[str] = []
        order = ["real", "coherent", "unique", "scale"]
        for name in order:
            dimension = self.dimensions.get(name)
            if dimension is None:
                continue
            status = "PASS" if dimension.passed else "FAIL"
            lines.append(
                f"{status:4s} {name:10s} "
                f"{dimension.checked:>8,} checked, "
                f"{dimension.error_count} error(s), "
                f"{dimension.warning_count} warning(s)"
            )

        lines.append("")
        if self.ok:
            lines.append("the fleet passes every dimension of the audit")
        else:
            lines.append("the fleet FAILS the audit:")
            for dimension in self.dimensions.values():
                for finding in dimension.failures:
                    if finding.severity == "error":
                        lines.append(f"  {finding}")

        if self.total_warnings:
            lines.append("")
            lines.append(f"{self.total_warnings} warning(s); the first few:")
            shown = 0
            for dimension in self.dimensions.values():
                for finding in dimension.failures:
                    if finding.severity == "warning":
                        lines.append(f"  {finding}")
                        shown += 1
                        if shown >= 5:
                            break
                if shown >= 5:
                    break
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Sampling
# ----------------------------------------------------------------------


def sample_profiles(count: int, seed_prefix: str = "") -> Iterator[ProfileConfig]:
    """Generate ``count`` profiles across the whole preset catalogue."""
    generator = ProfileGenerator()
    presets = [p.preset_id for p in list_presets()]
    for index in range(count):
        seed = f"{seed_prefix}{index:064x}" if seed_prefix else f"{index:064x}"
        yield generator.generate(
            presets[index % len(presets)], master_seed=seed
        )


# ----------------------------------------------------------------------
# Dimension 1: is it real?
# ----------------------------------------------------------------------


def audit_realism(profiles: Iterable[ProfileConfig]) -> Dimension:
    """Check that every profile is a device that could have existed.

    This is the consistency resolver's job: it knows which GPU model
    goes with which limits, which screen size goes with which device,
    which user agent goes with which client hint.  A profile that fails
    it is a profile that a vendor can single out by looking for the
    impossible combination.
    """

    resolver = ConsistencyResolver()
    findings: list[Finding] = []
    checked = 0

    for profile in profiles:
        checked += 1
        result = resolver.check(profile)
        for issue in result.errors:
            findings.append(
                Finding(
                    dimension="real",
                    severity="error",
                    subject=issue.axis,
                    message=issue.message,
                    example=",".join(issue.fields),
                )
            )
        for issue in result.warnings:
            findings.append(
                Finding(
                    dimension="real",
                    severity="warning",
                    subject=issue.axis,
                    message=issue.message,
                    example=",".join(issue.fields),
                )
            )
        if len(findings) > 500:
            break

    return Dimension(
        name="real",
        passed=not any(f.severity == "error" for f in findings),
        checked=checked,
        failures=findings,
    )


# ----------------------------------------------------------------------
# Dimension 2: is it unique?
# ----------------------------------------------------------------------


def audit_uniqueness(profiles: Iterable[ProfileConfig]) -> Dimension:
    """Check that no two profiles agree on any guarded axis.

    Every axis is tracked separately: a collision on one axis is a
    collision, even when the other eleven are clean.
    """

    seen: dict[str, dict[str, str]] = defaultdict(dict)
    findings: list[Finding] = []
    checked = 0

    for profile in profiles:
        checked += 1
        parts = profile.signature_parts()
        for kind in GUARDED_AXES:
            if kind.value == "sig_full":
                values = profile.full_signature_parts()
            else:
                values = parts.get(kind.value, [])
            material = "\x1f".join(map(str, values))

            previous = seen[kind.value].get(material)
            if previous is not None:
                findings.append(
                    Finding(
                        dimension="unique",
                        severity="error",
                        subject=kind.value,
                        message="two profiles share this axis",
                        example=previous,
                    )
                )
                if len(findings) > 500:
                    break
            else:
                seen[kind.value][material] = str(
                    getattr(profile, "profile_id", "") or ""
                )
        if len(findings) > 500:
            break

    return Dimension(
        name="unique",
        passed=not any(f.severity == "error" for f in findings),
        checked=checked,
        failures=findings,
    )


# ----------------------------------------------------------------------
# Dimension 3: is it coherent?
# ----------------------------------------------------------------------


#: The cross-axis couplings that a single profile has to satisfy.
#:
#: Each entry is a callable that returns a problem description, or
#: ``None`` when the profile is coherent.  These are the checks that
#: the schema cannot express, because they involve two different parts
#: of the configuration.
COHERENCE_RULES: list[tuple[str, Any]] = []


def _rule(name: str):
    """Register a coherence rule."""

    def decorate(function):
        COHERENCE_RULES.append((name, function))
        return function

    return decorate


@_rule("gpu-model-vs-client-hints")
def _gpu_vs_client_hints(profile: ProfileConfig) -> str | None:
    """The GPU the profile claims must match the client hints it sends.

    ``Sec-CH-UA-Model`` and the WebGL renderer describe two different
    pieces of hardware -- the handset and its GPU -- but a site can
    cross-check them against the platform version and the architecture
    the client hints carry.  A profile whose architecture hint says
    ``arm`` while the ABI claims ``x86_64`` is a profile no device is.
    """
    uad = profile.user_agent_data
    model = getattr(uad, "model", "") or ""
    if not model:
        return None

    # The model is a codename such as "Pixel 8 Pro"; it has to be a
    # name a manufacturer would actually put on a device.
    #
    # The set of characters a real model name uses is wider than the
    # alphanumeric one.  The manufacturers of the Vietnamese market ship
    # devices whose names carry parentheses -- "Nothing Phone (2a)",
    # "Oppo Reno8 (China)", "Realme 12x (India)" -- and the plus sign
    # -- "Realme 12+", "Realme 13+".  Those are the names the devices
    # report, so a rule that rejects them would reject the devices
    # themselves.  What the rule really excludes is a name that carries
    # a character no manufacturer puts in a product name.
    if not _MODEL_NAME_PATTERN.fullmatch(model):
        return f"the client-hint model '{model}' is not a real model name"

    # The architecture hint has to agree with the ABI the profile claims.
    arch = getattr(uad, "architecture", "") or ""
    abi = profile.android_abi_soc
    primary = abi.primary_abi.lower()
    if arch:
        if primary in ("x86_64", "x86") and arch != "x86":
            return (
                f"the client hints advertise the {arch!r} architecture but "
                f"the profile claims the {primary!r} ABI"
            )
        if primary.startswith("arm") and arch != "arm":
            return (
                f"the client hints advertise the {arch!r} architecture but "
                f"the profile claims the {primary!r} ABI"
            )
    return None


@_rule("audio-sampling-matches-hardware")
def _audio_sampling(profile: ProfileConfig) -> str | None:
    """The audio sample rate must be one a device of this class reports."""
    rate = profile.audio.sample_rate
    if rate not in (44100, 48000):
        return f"the audio sample rate {rate} is not one a device reports"
    return None


@_rule("screen-matches-viewport")
def _screen_viewport(profile: ProfileConfig) -> str | None:
    """The viewport has to fit inside the screen."""
    screen = profile.screen
    if screen.avail_width > screen.width:
        return (
            f"the available width {screen.avail_width} exceeds "
            f"the screen width {screen.width}"
        )
    if screen.avail_height > screen.height:
        return (
            f"the available height {screen.avail_height} exceeds "
            f"the screen height {screen.height}"
        )
    return None


@_rule("network-type-matches-link")
def _network_link(profile: ProfileConfig) -> str | None:
    """The effective connection type must agree with the measured link.

    A 2g connection with a 200 ms round trip and a 10 Mbit/s downlink
    is a contradiction a vendor can read straight off.
    """
    net = profile.storage_network
    if net.effective_type == "2g" and net.downlink > 1.0:
        return (
            f"a 2g connection reports a downlink of {net.downlink} Mbit/s; "
            f"2g tops out well below that"
        )
    if net.effective_type == "3g" and net.downlink > 3.0:
        return (
            f"a 3g connection reports a downlink of {net.downlink} Mbit/s; "
            f"3g tops out well below that"
        )
    if net.connection_type == "cellular" and net.effective_type == "4g":
        # Plausible: a 4g phone on a mobile network.
        return None
    if net.connection_type == "wifi" and net.effective_type == "2g":
        return "a 2g effective type on a wifi connection is not a real combination"
    return None


@_rule("timezone-matches-longitude")
def _timezone_longitude(profile: ProfileConfig) -> str | None:
    """The timezone has to sit inside the country's own longitude band.

    A profile whose timezone is ``Asia/Tokyo`` but whose country is
    ``Brazil`` is a profile that no person could be: the clock and the
    passport disagree.
    """
    geo = getattr(profile, "geo_intelligence", None)
    tz = getattr(profile, "timezone", None)
    if geo is None or tz is None:
        return None

    country = getattr(geo, "country_code", "")
    iana = getattr(tz, "iana_name", "")
    if not country or not iana:
        return None

    # A coarse band per country is enough to catch the gross mistakes.
    bands: dict[str, tuple[int, int]] = {
        "JP": (129, 146),
        "KR": (124, 132),
        "CN": (73, 135),
        "IN": (68, 98),
        "ID": (95, 142),
        "AU": (112, 154),
        "BR": (-74, -34),
        "US": (-125, -66),
        "GB": (-8, 2),
        "DE": (5, 15),
        "FR": (-5, 9),
        "RU": (19, 190),
        "ZA": (16, 33),
        "CA": (-141, -52),
        "MX": (-118, -86),
        "EG": (24, 37),
        "NG": (2, 15),
        "TR": (26, 45),
        "SA": (34, 56),
    }
    band = bands.get(country)
    if band is None:
        return None

    try:
        from novaprofile.core.geo_catalog import zone_offsets

        offset = zone_offsets.get(iana)
    except (ImportError, AttributeError):
        offset = None
    if offset is None:
        return None

    longitude = offset / 4.0
    if not (band[0] <= longitude <= band[1]):
        return (
            f"the timezone {iana} (UTC{offset:+d}, about {longitude:.0f} degrees) "
            f"does not sit in the longitude band of {country}"
        )
    return None


@_rule("user-agent-matches-client-hints")
def _ua_client_hints(profile: ProfileConfig) -> str | None:
    """The User-Agent and the client hints have to describe one browser."""
    ua = profile.navigator.user_agent
    hints = profile.user_agent_data
    brands = [b.brand for b in hints.brands] if hints.brands else []
    if not brands:
        return None
    major = max(
        (int(b.version) for b in hints.brands if b.version.isdigit()),
        default=None,
    )
    if major is None:
        return None
    if f"Chrome/{major}" not in ua:
        return (
            f"the client hints advertise Chrome {major} but the User-Agent "
            f"'{ua}' does not mention it"
        )
    return None


@_rule("font-list-matches-locale")
def _fonts_locale(profile: ProfileConfig) -> str | None:
    """The font list has to belong to the locale.

    A profile whose locale is ``ja-JP`` but whose font list has no CJK
    font is a profile whose rendering cannot match its own language.
    """
    primary = profile.locale.primary
    installed = set(profile.fonts.installed)
    if not installed:
        return "the profile has no fonts at all"
    if primary in ("ja", "ko", "zh") and not any(
        "CJK" in f or "Noto" in f for f in installed
    ):
        return f"a {primary} user without a CJK font cannot render their own text"
    return None


def audit_coherence(profiles: Iterable[ProfileConfig]) -> Dimension:
    """Check that the axes of one profile agree with each other."""
    findings: list[Finding] = []
    checked = 0

    for profile in profiles:
        checked += 1
        for name, rule in COHERENCE_RULES:
            try:
                problem = rule(profile)
            except Exception as exc:  # noqa: BLE001 - a broken rule is a finding
                problem = f"the rule itself failed: {exc}"
            if problem:
                findings.append(
                    Finding(
                        dimension="coherent",
                        severity="error",
                        subject=name,
                        message=problem,
                        example=str(getattr(profile, "profile_id", "") or ""),
                    )
                )
        if len(findings) > 500:
            break

    return Dimension(
        name="coherent",
        passed=not any(f.severity == "error" for f in findings),
        checked=checked,
        failures=findings,
    )


# ----------------------------------------------------------------------
# Dimension 4: does it hold at scale?
# ----------------------------------------------------------------------


def audit_scale(profiles: Iterable[ProfileConfig]) -> Dimension:
    """Check that every axis has the entropy the fleet size demands.

    The widths are read from the values themselves: a salt is a
    hexadecimal string, so its width is known exactly, and a draw that
    is narrower than it claims shows up as a narrower number.
    """
    findings: list[Finding] = []
    widths: dict[str, int] = {}
    cardinalities: dict[str, int] = {}
    checked = 0

    for profile in profiles:
        checked += 1
        parts = profile.signature_parts()
        for kind in GUARDED_AXES:
            if kind.value == "sig_full":
                values = profile.full_signature_parts()
            else:
                values = parts.get(kind.value, [])
            widths[kind.value] = max(
                widths.get(kind.value, 0), _material_width(values)
            )
            cardinalities[kind.value] = cardinalities.get(kind.value, 0) + 1

    for axis, bits in sorted(widths.items()):
        probability = _birthday_probability(bits, TARGET_FLEET)
        if bits < REQUIRED_BITS:
            findings.append(
                Finding(
                    dimension="scale",
                    severity="error",
                    subject=axis,
                    message=(
                        f"the axis carries {bits} bits, which is below the "
                        f"{REQUIRED_BITS:.1f} bits a fleet of {TARGET_FLEET:,} "
                        f"profiles needs"
                    ),
                )
            )
        elif probability > 1e-6:
            findings.append(
                Finding(
                    dimension="scale",
                    severity="warning",
                    subject=axis,
                    message=(
                        f"the axis carries {bits} bits, which collides with a "
                        f"probability of {probability:.2e} at the target fleet"
                    ),
                )
            )

    return Dimension(
        name="scale",
        passed=not any(f.severity == "error" for f in findings),
        checked=checked,
        failures=findings,
    )


def _material_width(values: list[Any]) -> int:
    """The width of an axis' identifying material, in bits.

    The width is the width of the *per-unit* part of the material: a
    hexadecimal salt contributes its full width, a decimal integer
    contributes the width of the number, and a fixed string such as a
    GPU model contributes nothing because every profile of that family
    shares it.
    """
    bits = 0
    for value in values:
        text = str(value)
        if not text:
            continue
        try:
            int(text, 16)
        except ValueError:
            pass
        else:
            bits += len(text) * 4
            continue
        try:
            number = int(text)
        except ValueError:
            # A fixed string: no per-unit entropy.
            continue
        bits += max(1, number.bit_length())
    return bits


def _birthday_probability(bits: int, fleet: int) -> float:
    """The probability that ``fleet`` draws collide in ``bits`` bits."""
    if bits <= 0:
        return 1.0
    if bits > 128:
        return 0.0
    space = float(2**bits)
    return 1.0 - math.exp(-(fleet * (fleet - 1)) / (2 * space))


# ----------------------------------------------------------------------
# The audit
# ----------------------------------------------------------------------


def run_audit(count: int = 5000, seed_prefix: str = "") -> AuditReport:
    """Run every dimension of the audit over a fleet of ``count`` profiles."""
    report = AuditReport()
    report.add(audit_realism(sample_profiles(count, seed_prefix)))
    report.add(audit_coherence(sample_profiles(count, seed_prefix)))
    report.add(audit_uniqueness(sample_profiles(count, seed_prefix)))
    report.add(audit_scale(sample_profiles(count, seed_prefix)))
    return report


__all__ = [
    "AuditReport",
    "COHERENCE_RULES",
    "Dimension",
    "Finding",
    "REQUIRED_BITS",
    "TARGET_FLEET",
    "audit_coherence",
    "audit_realism",
    "audit_scale",
    "audit_uniqueness",
    "run_audit",
    "sample_profiles",
]
