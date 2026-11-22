"""Per-unit variants of the WebGL parameter table.

The problem
-----------
``gl.getParameter`` answers a dozen limits that identify the graphics
card almost as loudly as the renderer string does.  The engine reads
them off the *host* machine and hands them to every profile it
builds.  A fleet of a hundred thousand profiles built on one build
server therefore reports one identical limit table, and a site that
reads ``MAX_TEXTURE_SIZE`` together with ``MAX_VIEWPORT_DIMS``,
``MAX_RENDERBUFFER_SIZE``, ``ALIASED_LINE_WIDTH_RANGE`` and the rest
has a grouping key that is free of charge.

The temptation is to draw the limits at random.  That is wrong, and
wrong in a way that is worse than the leak it fixes: a random limit is
a limit no part of that family ever reported.  ``MAX_TEXTURE_SIZE``
of 12288 on an Adreno 740 is not a *variation* of the Adreno 740, it
is a contradiction, and a site that keeps a table of the real limits
of every mobile part finds it in one comparison.  The same goes for the
other limits: a card reports a coherent set, and the coherence is
what the site checks.

The way out
-----------
A real card of a given model does not report one single table.  The
memory configuration of the handset moves the addressable-memory
limits *within* the band the model allows, and it moves them **as a
set**: a card that is limited to 8192 textures is limited to 8192 in
the cube map and in the renderbuffer and in the viewport, because all
four are the same addressable memory.

So the variant of a unit is a *tier*: a whole, internally consistent
limit set that the model is known to produce.  The tier is drawn from
the seed, so it is fixed for the profile, and it is checked against
the catalogue by the consistency resolver.

What a tier may and may not do
-------------------------------
The rule the tier table follows is narrow on purpose:

* **The catalogue entry is always tier 0.**  The default profile of a
  model stays byte-identical to what the engine produced before the
  tiers existed.
* **The only other variant is the half-memory one.**  A handset that
  ships with a smaller memory configuration reports half the
  addressable texture size, and the whole group follows.  This is
  what the engine already modelled, and it is what a real phone does.
* **Nothing is ever *widened*.**  The class band of a flagship part
  reaches 32768, but no mobile part in the catalogue reports it, and
  the WebGPU limit table of the same physical card cannot report it
  either -- the WebGPU texture dimension is capped far below that.  A
  tier that produced it would be a configuration no phone has ever
  had, and the site that keeps a table of the real limits would find
  it in one read.
* **A limit is never moved on its own.**  The texture size, the cube
  map size, the renderbuffer size and the viewport bounds are one
  number wearing four names.

The class band of the fleet validator is deliberately *not* used here.
That band is a rule about the *preset catalogue* -- it asks whether a
declared device belongs to the class its limits imply -- while a tier
is the unit-level variation of a card that already *is* the catalogue
model.  Applying the catalogue rule to a unit would forbid the very
variation the guard exists to produce.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .devices import GpuProfile

#: The GPU classes the engine knows, with the texture limits each class
#: is allowed to report.
#:
#: These are the bands the fleet validator uses.  They are re-exported
#: here so that the two modules cannot drift apart; see the module
#: docstring for why they are *not* the constraint on a tier.
CLASS_TEXTURE_BANDS: dict[str, tuple[int, ...]] = {
    "budget": (4096, 8192),
    "mid": (8192, 16384),
    "flagship": (16384, 32768),
}


@dataclass(frozen=True)
class GpuParameterTier:
    """One internally consistent limit set of a GPU model."""

    #: The index of the tier inside the model's tier table.
    index: int
    #: A short name of the variant, for logs and for the collision guard.
    name: str
    #: The limits themselves.
    limits: GpuProfile

    @property
    def max_texture_size(self) -> int:
        return self.limits.max_texture_size


def _derated(gpu: GpuProfile, texture: int) -> GpuProfile:
    """A card whose addressable memory is capped at ``texture``.

    The cap applies to *every* limit that is bounded by the same
    memory: the cube map, the renderbuffer and the viewport all
    follow.  A profile that capped the texture size alone would be a
    card that can sample a 16384 texture but cannot render into a
    16384 renderbuffer -- which is not a configuration any driver
    produces.
    """

    return replace(
        gpu,
        max_texture_size=texture,
        max_cube_map_texture_size=texture,
        max_renderbuffer_size=texture,
        max_viewport_dims=(texture, texture),
    )


def build_tier_table(gpu: GpuProfile) -> tuple[GpuParameterTier, ...]:
    """Build the tier table of one GPU model.

    The table holds the catalogue entry as tier 0 and the half-memory
    configuration as tier 1.  The half-memory tier is the one variant
    a mobile card really produces, and it is only offered when the
    halved limit is still a limit the WebGPU table of the same card
    can carry -- a card that reported a 4096 texture could not back a
    WebGPU adapter that claims 8192.
    """

    tiers: list[GpuParameterTier] = [
        GpuParameterTier(index=0, name="stock", limits=gpu),
    ]

    # The half-memory configuration.  The floor is 1024, because a
    # texture smaller than that is not a limit any WebGL
    # implementation reports.
    half = max(1024, gpu.max_texture_size // 2)
    if half != gpu.max_texture_size and half >= 2048:
        tiers.append(
            GpuParameterTier(
                index=len(tiers),
                name="half_memory",
                limits=_derated(gpu, half),
            )
        )

    return tuple(tiers)


def tier_for(gpu: GpuProfile, tier_index: int) -> GpuParameterTier:
    """The tier of a GPU model at a given index.

    An index outside the table falls back to tier 0, which is the
    catalogue entry itself -- the one configuration that is always
    correct.
    """

    table = build_tier_table(gpu)
    if 0 <= tier_index < len(table):
        return table[tier_index]
    return table[0]


def allowed_tiers(gpu: GpuProfile) -> tuple[GpuParameterTier, ...]:
    """Every tier a unit of this GPU model may report."""

    return build_tier_table(gpu)


def tier_texture_values(gpu: GpuProfile) -> tuple[int, ...]:
    """Every texture limit a unit of this GPU model may report.

    The consistency resolver uses this to check that a profile's
    ``max_texture_size`` is one the model really produces, rather than
    the two values the old check knew about.
    """

    return tuple(tier.max_texture_size for tier in build_tier_table(gpu))


__all__ = [
    "CLASS_TEXTURE_BANDS",
    "GpuParameterTier",
    "allowed_tiers",
    "build_tier_table",
    "tier_for",
    "tier_texture_values",
]
