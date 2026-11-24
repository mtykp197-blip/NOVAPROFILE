"""The collision guard: the assertion that the noise axes really carry.

Why this module exists
----------------------

The canvas hash, the WebGL pixel hash and the audio hash are the digests
of what the browser *actually rendered*.  The rasteriser, the glyph
cache, the driver's shader compiler and the machine's colour
management all have a say in those pixels, so no model living outside
the browser can predict them.  A simulator that produced a value for
them would be inventing one, and asserting against it would be a
guarantee of false failures.

So those three hashes are deliberately *not asserted* against an
expected value.  Instead the property that actually matters is checked
directly, on the measured values:

* **stability** -- the same profile must render the same hash every
  time it is measured.  A hash that moves between two runs of the same
  profile means the noise is not deterministic, which means the profile
  is not a fingerprint at all: the site sees a different device on
  every visit and the profile is trivially detectable.

* **collision** -- two different profiles must render different hashes.
  A shared hash between two profiles means the core did not apply the
  per-profile salt, which is precisely the failure the whole product
  exists to prevent.

This module performs both checks and reports them as a verdict of
their own, so that a failure of either is as visible as a failed axis.

How it works
------------

The measurement happens inside a browser session that stays open, so
that the *same* browser, with the *same* loaded profile, is asked
twice.  That isolates the variable that matters -- the noise -- from
every other source of variation, and it does so without paying for a
second launch.

The collision half needs two *different* profiles, so it pays for a
second browser: the second launch is a fresh process with a fresh
configuration file, which is exactly the situation a real farm is in.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .e2e import E2EReport, E2ERunner, ProfileConfig

#: The comparison field of each noise axis that carries its hash.
NOISE_HASH_FIELDS: dict[str, str] = {
    "canvas": "hash",
    "webgl": "pixelHash",
    "audio": "hash",
}


@dataclass(frozen=True)
class NoiseVerdict:
    """The verdict of one noise axis."""

    axis: str
    stable: bool
    distinct: bool
    first: str = ""
    second: str = ""
    other: str = ""

    @property
    def ok(self) -> bool:
        """Whether the axis is both stable and distinct."""

        return self.stable and self.distinct


@dataclass(frozen=True)
class CollisionReport:
    """The outcome of the guard."""

    verdicts: tuple[NoiseVerdict, ...] = field(default=())
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether every axis passed."""

        return not self.error and all(v.ok for v in self.verdicts)

    @property
    def failures(self) -> tuple[NoiseVerdict, ...]:
        """The axes that did not pass."""

        return tuple(v for v in self.verdicts if not v.ok)


def _hashes(report: E2EReport) -> dict[str, str]:
    """Return the measured noise hashes of a report, keyed by axis.

    The report holds the *comparison*, not the raw measurement, so the
    values are taken from the comparison fields.  A field that was not
    measured is missing from the comparison altogether, which is how
    an absent surface is told apart from an empty one.
    """

    found: dict[str, str] = {}
    for item in report.comparisons:
        field_name = NOISE_HASH_FIELDS.get(item.axis)
        if field_name is None:
            continue
        actual = item.actual or {}
        value = actual.get(field_name)
        if value:
            found[item.axis] = str(value)
    return found


async def _measure_twice(
    config: ProfileConfig,
    browser_path: str | None = None,
    timeout: float = 30.0,
) -> tuple[dict[str, str], dict[str, str]]:
    """Measure the noise hashes of one profile twice.

    Both measurements come from the same browser process, so the second
    one sees the same profile, the same GPU and the same machine.  A
    difference between them can therefore only be the noise itself
    moving.
    """

    runner = E2ERunner(config, browser_path=browser_path, timeout=timeout)
    first = await runner.run()
    second = await runner.run()
    return _hashes(first), _hashes(second)


async def _measure_other(
    config: ProfileConfig,
    browser_path: str | None = None,
    timeout: float = 30.0,
) -> dict[str, str]:
    """Measure the noise hashes of a *different* profile."""

    runner = E2ERunner(config, browser_path=browser_path, timeout=timeout)
    report = await runner.run()
    return _hashes(report)


async def run_guard(
    config: ProfileConfig,
    other: ProfileConfig | None = None,
    browser_path: str | None = None,
    timeout: float = 30.0,
) -> CollisionReport:
    """Run the stability and collision checks.

    ``config`` is the profile under test and ``other`` is a *different*
    profile of the same family: the guard needs two identities to
    compare, and two profiles that share every declared value except
    the salt would be a weaker test.
    """

    try:
        first, second = await _measure_twice(config, browser_path, timeout)
    except Exception as exc:
        return CollisionReport(error=f"the measurement failed: {exc}")

    others: dict[str, str] = {}
    if other is not None:
        try:
            others = await _measure_other(other, browser_path, timeout)
        except Exception as exc:
            return CollisionReport(error=f"the second measurement failed: {exc}")

    verdicts: list[NoiseVerdict] = []
    for axis in NOISE_HASH_FIELDS:
        one = first.get(axis, "")
        two = second.get(axis, "")
        other_value = others.get(axis, "")
        if not one:
            continue
        verdicts.append(
            NoiseVerdict(
                axis=axis,
                stable=bool(one) and one == two,
                distinct=not other_value or one != other_value,
                first=one,
                second=two,
                other=other_value,
            )
        )

    return CollisionReport(verdicts=tuple(verdicts))


def run_guard_sync(
    config: ProfileConfig,
    other: ProfileConfig | None = None,
    browser_path: str | None = None,
    timeout: float = 30.0,
) -> CollisionReport:
    """Run the guard from synchronous code."""

    return asyncio.run(run_guard(config, other, browser_path, timeout))
