"""Find the constants in a fleet.

A profile that reports the same value on a field every other profile
also reports is a fingerprint of its own kind.  A vendor does not need
to break the uniqueness of a profile to group it: it only has to notice
that every profile of this fleet carries the same string, and it has
found a bucket with the whole fleet in it.

This module measures that.  It flattens a fleet of profiles and reports
every field, how many distinct values it took, and -- the part that
matters -- how many *bits* the field actually carries.

The distinction matters.  A field that is genuinely constant on the
hardware is not a bug: every Android phone reports
``navigator.vendor`` as ``Google Inc.``, and bending that to manufacture
differences would make the profile detectable.  A field that is constant
*only because the generator drew it from a single value* is a bug.

The module therefore separates three kinds of field:

``constant``
    One value across the whole fleet.  Either the hardware really does
    have one value -- in which case the field must be left alone -- or
    the generator is not drawing from it at all.
``shared``
    A handful of values, shared by every profile of a family.  This is
    how real hardware behaves: a whole model shares its GPU.
``unique``
    A value per profile.  This is what an identifying field has to be.

The ``constant`` and ``shared`` fields are the ones to look at: every
one of them is a bucket a vendor can group on.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator

#: The fleet the engine is designed for.
TARGET_FLEET = 100_000_000


@dataclass
class FieldReport:
    """How one field behaved across the fleet."""

    path: str
    distinct: int
    sample: int
    examples: list[str] = field(default_factory=list)

    @property
    def is_constant(self) -> bool:
        return self.distinct == 1

    @property
    def is_shared(self) -> bool:
        """True when the field takes few enough values to be a family trait."""
        return 1 < self.distinct <= max(2, self.sample // 50)

    @property
    def bits(self) -> float:
        """The entropy the field carries, in bits."""
        if self.distinct <= 1:
            return 0.0
        return math.log2(self.distinct)

    @property
    def birthday_p_at_target(self) -> float:
        """The collision probability at the target fleet size."""
        if self.distinct <= 1:
            return 1.0
        if self.bits > 128:
            return 0.0
        space = float(self.distinct)
        return 1.0 - math.exp(-(TARGET_FLEET * (TARGET_FLEET - 1)) / (2 * space))

    @property
    def verdict(self) -> str:
        if self.is_constant:
            return "CONSTANT"
        if self.is_shared:
            return "shared"
        return "unique"


def flatten(profile: Any) -> dict[str, str]:
    """Flatten a profile into ``path -> value``."""
    out: dict[str, str] = {}

    def walk(node: Any, path: str) -> None:
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


def survey(count: int = 400, seed_prefix: str = "") -> dict[str, FieldReport]:
    """Survey every field of a fleet of ``count`` profiles."""
    generator = ProfileGenerator()
    presets = [p.preset_id for p in list_presets()]

    values: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[str]] = defaultdict(list)

    for index in range(count):
        seed = f"{seed_prefix}{index:064x}" if seed_prefix else f"{index:064x}"
        profile = generator.generate(
            presets[index % len(presets)], master_seed=seed
        )
        for path, value in flatten(profile).items():
            bucket = values[path]
            if value not in bucket:
                bucket.add(value)
                if len(examples[path]) < 4:
                    examples[path].append(value)

    return {
        path: FieldReport(
            path=path,
            distinct=len(bucket),
            sample=count,
            examples=examples[path],
        )
        for path, bucket in values.items()
    }


def _row(f: FieldReport) -> str:
    bits = f.bits
    p = f.birthday_p_at_target
    p_text = f"{p:.1e}" if 0 < p < 1 else ("1" if p >= 1 else "0")
    examples = "; ".join(e[:28] for e in f.examples[:2])
    return (
        f"{f.verdict:8s} {f.path:52s} {f.distinct:>7d} distinct  "
        f"{bits:6.1f} bits  p={p_text:>8s}  [{examples}]"
    )


def report(count: int = 400, only: str | None = None) -> str:
    """Render the survey as a readable table.

    ``only`` restricts the output to the fields whose path contains it.
    """
    fields = survey(count)

    constants = [f for f in fields.values() if f.is_constant]
    shared = [f for f in fields.values() if f.is_shared]
    unique = [f for f in fields.values() if not f.is_constant and not f.is_shared]

    lines: list[str] = [
        f"fleet of {count} profiles, {len(fields)} fields surveyed",
        "",
        f"constant : {len(constants):4d} field(s)",
        f"shared   : {len(shared):4d} field(s)",
        f"unique   : {len(unique):4d} field(s)",
        "",
    ]

    if only:
        wanted = [f for f in fields.values() if only in f.path]
        lines.append(f"--- fields matching '{only}' ---")
        for f in sorted(wanted, key=lambda x: x.path):
            lines.append(_row(f))
        return "\n".join(lines)

    lines.append("--- CONSTANT fields (a vendor can group on every one) ---")
    for f in sorted(constants, key=lambda x: x.path):
        lines.append(_row(f))

    lines.append("")
    lines.append("--- SHARED fields (a family trait; expected on real hardware) ---")
    for f in sorted(shared, key=lambda x: (x.distinct, x.path))[:60]:
        lines.append(_row(f))

    lines.append("")
    lines.append("--- UNIQUE fields (what an identifying field must be) ---")
    for f in sorted(unique, key=lambda x: x.path)[:40]:
        lines.append(_row(f))
    if len(unique) > 40:
        lines.append(f"... and {len(unique) - 40} more")

    return "\n".join(lines)


def constant_fields(count: int = 400) -> list[str]:
    """The paths of every field that took exactly one value."""
    return sorted(path for path, f in survey(count).items() if f.is_constant)


__all__ = [
    "FieldReport",
    "TARGET_FLEET",
    "constant_fields",
    "flatten",
    "report",
    "survey",
]
