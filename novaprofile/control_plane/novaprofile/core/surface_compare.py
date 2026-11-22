"""Compare what a profile claims with what a browser measures.

The generator writes a profile; the browser answers a set of
questions.  This module puts the two side by side and says, for each
surface, whether the profile and the browser agree.

An agreement is not automatically a good thing.  Two profiles that
agree on the canvas hash are two profiles that a vendor can group; the
report therefore says two things about every surface: whether it is
consistent, and whether it is distinct.

The comparison is deliberately honest about what it cannot decide.  A
surface the browser refused to answer is reported as unknown, not as a
pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novaprofile.core.surfaces import SURFACES, Surface

#: The three verdicts a comparison can reach.
PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"


@dataclass
class Comparison:
    """One surface, and what the comparison found."""

    #: The name of the surface.
    name: str
    #: The category the surface falls into.
    category: str
    #: The verdict.
    verdict: str
    #: What the profile claimed.
    claimed: Any = None
    #: What the browser answered.
    measured: Any = None
    #: Why the verdict is what it is.
    reason: str = ""

    @property
    def passed(self) -> bool:
        """Whether the profile and the browser agree."""

        return self.verdict == PASS


@dataclass
class Report:
    """The comparison of a profile against a measurement."""

    #: Every comparison.
    comparisons: list[Comparison] = field(default_factory=list)
    #: The surfaces the browser refused to answer.
    missing: list[str] = field(default_factory=list)

    def add(self, comparison: Comparison) -> None:
        """Record one comparison."""

        self.comparisons.append(comparison)

    @property
    def passed(self) -> list[Comparison]:
        """The comparisons that agree."""

        return [item for item in self.comparisons if item.verdict == PASS]

    @property
    def failed(self) -> list[Comparison]:
        """The comparisons that disagree."""

        return [item for item in self.comparisons if item.verdict == FAIL]

    @property
    def unknown(self) -> list[Comparison]:
        """The comparisons that could not be made."""

        return [item for item in self.comparisons if item.verdict == UNKNOWN]

    @property
    def consistent(self) -> bool:
        """Whether every surface that could be checked agrees."""

        return not self.failed

    def score(self) -> float:
        """The share of the checked surfaces that agree."""

        checked = self.passed + self.failed
        if not checked:
            return 0.0
        return len(self.passed) / len(checked)

    def by_category(self) -> dict[str, list[Comparison]]:
        """The comparisons grouped by their category."""

        grouped: dict[str, list[Comparison]] = {}
        for item in self.comparisons:
            grouped.setdefault(item.category, []).append(item)
        return grouped


def _get(container: Any, path: str) -> Any:
    """The value at a dotted path, or ``None`` when it is not there."""

    current = container
    for part in path.split("."):
        if current is None:
            return None
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return current


def _compare_value(claimed: Any, measured: Any, surface: Surface) -> tuple[str, str]:
    """Compare one value, and say why.

    The comparison is forgiving about the shape of the value and strict
    about its meaning.  A number that is a string in one place and a
    number in the other is the same number; a screen that is 1080 wide
    in one place and 1081 in the other is a contradiction.
    """

    if claimed is None and measured is None:
        return UNKNOWN, "neither side has a value"

    if claimed is None:
        return FAIL, "the profile does not carry this surface"

    if measured is None:
        return UNKNOWN, "the browser did not answer this surface"

    if isinstance(claimed, bool) or isinstance(measured, bool):
        if bool(claimed) == bool(measured):
            return PASS, "the two agree"
        return FAIL, f"the profile says {claimed!r} and the browser says {measured!r}"

    if isinstance(claimed, (int, float)) and isinstance(measured, (int, float)):
        if abs(float(claimed) - float(measured)) < 1e-9:
            return PASS, "the two agree"
        return FAIL, f"the profile says {claimed} and the browser says {measured}"

    # A number that arrives as a string is the same number.  The
    # DevTools protocol carries every value as a string, and a
    # comparison that treated "8" and 8 as a contradiction would
    # report every measurement as a failure.
    if isinstance(claimed, (int, float)) and isinstance(measured, str):
        try:
            if abs(float(claimed) - float(measured)) < 1e-9:
                return PASS, "the two agree"
        except ValueError:
            pass
        return FAIL, f"the profile says {claimed} and the browser says {measured!r}"

    if isinstance(measured, (int, float)) and isinstance(claimed, str):
        try:
            if abs(float(measured) - float(claimed)) < 1e-9:
                return PASS, "the two agree"
        except ValueError:
            pass
        return FAIL, f"the profile says {claimed!r} and the browser says {measured}"

    if isinstance(claimed, str) and isinstance(measured, str):
        if claimed == measured:
            return PASS, "the two agree"
        if claimed in measured or measured in claimed:
            return PASS, "one contains the other"
        return FAIL, f"the profile says {claimed!r} and the browser says {measured!r}"

    if isinstance(claimed, (list, tuple)) and isinstance(measured, (list, tuple)):
        if list(claimed) == list(measured):
            return PASS, "the two agree"
        if sorted(str(item) for item in claimed) == sorted(str(item) for item in measured):
            return PASS, "the two agree, in a different order"
        return FAIL, "the lists differ"

    if isinstance(claimed, dict) and isinstance(measured, dict):
        compared = False
        for key in claimed:
            if key not in measured:
                continue
            compared = True
            verdict, reason = _compare_value(claimed[key], measured[key], surface)
            if verdict == FAIL:
                return FAIL, f"the key {key!r} differs: {reason}"
        if not compared:
            return UNKNOWN, "the two hold different keys"
        return PASS, "every key the profile carries agrees"

    if claimed == measured:
        return PASS, "the two agree"

    return FAIL, f"the profile says {claimed!r} and the browser says {measured!r}"


#: The surfaces that are compared, and where the profile keeps them.
#:
#: The key is the name of the surface; the value is the path in the
#: profile where the claim lives.  A path of ``None`` means the
#: profile does not carry the surface at all, which is worth knowing.
CLAIM_PATHS: dict[str, str | None] = {
    "user_agent": "navigator.user_agent",
    "platform": "navigator.platform",
    "language": "navigator.language",
    "languages": "navigator.languages",
    "hardware_concurrency": "navigator.hardware_concurrency",
    "device_memory": "navigator.device_memory",
    "max_touch_points": "navigator.max_touch_points",
    "screen": "screen",
    "canvas_2d": "canvas_2d",
    "webgl": "webgl",
    "webgpu": "webgpu",
    "audio": "audio",
    "fonts": "fonts",
    "timezone": "timezone",
    "media_devices": "media_devices",
    "permissions": "permissions",
    "storage_estimate": "storage",
    "webrtc": "webrtc",
    "battery": "battery",
    "connection": "connection",
}


def compare(profile: Any, measured: dict[str, Any]) -> Report:
    """Compare a profile against a measurement.

    The profile is the object the generator produced; the measurement
    is the dictionary the probe read from the browser.
    """

    report = Report()

    for surface in SURFACES:
        path = CLAIM_PATHS.get(surface.name)

        if path is None:
            # The surface is not one the profile claims; it is worth
            # reporting that the browser answered it anyway, because
            # that is a surface the vendor can use.
            if surface.name in measured and measured[surface.name] is not None:
                report.add(
                    Comparison(
                        name=surface.name,
                        category=surface.category,
                        verdict=UNKNOWN,
                        measured=measured[surface.name],
                        reason="the browser answers it but the profile does not claim it",
                    )
                )
            continue

        claimed = _get(profile, path)
        value = measured.get(surface.name)

        if value is None:
            report.missing.append(surface.name)

        verdict, reason = _compare_value(claimed, value, surface)
        report.add(
            Comparison(
                name=surface.name,
                category=surface.category,
                verdict=verdict,
                claimed=claimed,
                measured=value,
                reason=reason,
            )
        )

    return report


__all__ = [
    "CLAIM_PATHS",
    "FAIL",
    "PASS",
    "UNKNOWN",
    "Comparison",
    "Report",
    "compare",
]
