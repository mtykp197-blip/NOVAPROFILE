"""The fleet: many profiles, each one a different machine.

A single preset describes one handset model, so a fleet built from one
preset is a fleet of masks on one device.  This module builds a fleet
the other way round: it draws each profile from a *different* machine
in the catalogue, so that the values a site groups on -- the GPU, the
panel, the build, the chip count, the memory -- separate the fleet
instead of uniting it.

The generator still does the per-profile work.  This module decides
*which* preset each profile gets, and it does it so that two profiles
of the same model still differ on the axes a model can vary.
"""

from __future__ import annotations

import collections
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..schema.profile import ProfileConfig
from .collision import CollisionGuard, InMemorySignatureRegistry, SignatureRegistry
from .devices import DevicePreset, all_presets, cores_from_cluster_layout
from .generator import ProfileGenerator, _geo_target
from .hardware_approximation import approximate_device_memory
from .machine import count_machines
from .seed import new_master_seed, new_profile_id


@dataclass(frozen=True)
class FleetPlan:
    """Which machine each profile of the fleet gets.

    The plan is separate from the profiles themselves so that the
    operator can look at it before the profiles are built: "these are
    the hundred phones this fleet is made of" is a thing worth seeing.
    """

    #: The preset each profile is built from, in order.
    preset_ids: tuple[str, ...]

    #: The stratum each profile gets within its preset.
    strata: tuple[int, ...] = field(default=())

    def __len__(self) -> int:
        return len(self.preset_ids)


@dataclass(frozen=True)
class FleetReport:
    """What a site would make of the fleet.

    This is the measurement that matters, and it is deliberately the
    pessimistic one: a pair counts as *linked* when the two profiles
    agree on the values that describe the machine, because a site that
    finds one shared value does not need to find the rest.
    """

    #: The number of profiles in the fleet.
    count: int

    #: The number of distinct machines the fleet presents itself as.
    distinct_machines: int

    #: How many pairs of profiles share the whole machine identity.
    identical_pairs: int

    #: How many pairs of profiles share at least one machine value.
    linked_pairs: int

    #: The number of distinct values on each axis.
    axis_buckets: dict[str, int] = field(default_factory=dict)

    #: The number of pairs on each axis, counted separately.
    axis_pairs: dict[str, int] = field(default_factory=dict)


#: The readings that describe a machine, and where to read them from.
MACHINE_AXES: tuple[tuple[str, str], ...] = (
    ("gpu", "webgl.unmasked_renderer"),
    ("device_model", "android_build.model"),
    ("build_id", "android_build.build_id"),
    ("hardware_concurrency", "navigator.hardware_concurrency"),
    ("device_memory", "navigator.device_memory"),
    ("screen", "screen"),
    ("color_depth", "screen.color_depth"),
    ("max_touch_points", "navigator.max_touch_points"),
    ("device_pixel_ratio", "screen.device_pixel_ratio"),
)


def _machine_value(profile: ProfileConfig, axis: str) -> object:
    """The value of one machine axis."""

    if axis == "gpu":
        return profile.webgl.unmasked_renderer
    if axis == "device_model":
        return profile.android_build.model
    if axis == "build_id":
        return profile.android_build.build_id
    if axis == "hardware_concurrency":
        return profile.navigator.hardware_concurrency
    if axis == "device_memory":
        return profile.navigator.device_memory
    if axis == "screen":
        return (profile.screen.width, profile.screen.height)
    if axis == "color_depth":
        return profile.screen.color_depth
    if axis == "max_touch_points":
        return profile.navigator.max_touch_points
    if axis == "device_pixel_ratio":
        return profile.screen.device_pixel_ratio
    raise ValueError(axis)


def machine_identity(profile: ProfileConfig) -> tuple:
    """Everything a site can read that says *which phone this is*.

    The identity is the tuple of the machine readings, and two
    profiles that carry the same one are the same device as far as a
    website is concerned -- whatever their canvas, their audio and
    their seed say.
    """

    return tuple(_machine_value(profile, axis) for axis, _ in MACHINE_AXES)


def measure_fleet(profiles: Sequence[ProfileConfig]) -> FleetReport:
    """Measure how a fleet would look to a site.

    The report is what the operator reads before deciding whether the
    fleet is safe to run, so it counts the pessimistic way: a pair is
    linked when it shares the machine identity in full, and a pair is
    *at risk* when it shares any single machine value.
    """

    count = len(profiles)

    identities = collections.Counter(machine_identity(p) for p in profiles)
    identical_pairs = sum(n * (n - 1) // 2 for n in identities.values())

    axis_buckets: dict[str, int] = {}
    axis_pairs: dict[str, int] = {}
    linked_pairs = 0

    for axis, _path in MACHINE_AXES:
        buckets = collections.Counter(_machine_value(p, axis) for p in profiles)
        axis_buckets[axis] = len(buckets)
        pairs = sum(n * (n - 1) // 2 for n in buckets.values())
        axis_pairs[axis] = pairs
        linked_pairs += pairs

    return FleetReport(
        count=count,
        distinct_machines=len(identities),
        identical_pairs=identical_pairs,
        linked_pairs=linked_pairs,
        axis_buckets=axis_buckets,
        axis_pairs=axis_pairs,
    )


def _distinct_machines(presets: Sequence[DevicePreset]) -> list[DevicePreset]:
    """The presets that are different *machines*.

    Two presets of the same model -- a phone and its landscape
    variant, say -- are one machine as far as a site is concerned when
    they report the same GPU, the same build and the same panel.  The
    fleet needs one of each, not all of them.
    """

    seen: dict[tuple, DevicePreset] = {}
    for preset in presets:
        key = _machine_key(preset)
        if key in seen:
            continue
        seen[key] = preset
    return list(seen.values())


def _machine_key(preset: DevicePreset) -> tuple:
    """The values of a preset that describe the machine it is.

    This is the preset's own answer to "which phone is this", before
    the generator has had a chance to vary the panel, the density or
    the release.  Two presets that agree on all of it are the same
    machine, and the fleet needs one of each.
    """

    return (
        preset.gpu.unmasked_renderer,
        preset.model,
        preset.build_id,
        preset.screen.width,
        preset.screen.height,
    )


def _core_count(preset: DevicePreset) -> int:
    """The number of logical cores the preset's SoC has."""

    return cores_from_cluster_layout(preset.abi.cpu_cluster_layout)


def _memory_value(preset: DevicePreset) -> int:
    """The memory the preset's handsets report, once Chromium rounds it.

    The generator may draw any of the model's options, so the fleet
    treats the whole option list as one bucket: a profile built from
    this preset could report any of them, and the fleet has to be
    safe for the worst of them.
    """

    values = {approximate_device_memory(int(gb)) for gb in preset.ram_options}
    return max(values) if values else 8


def _touch_points(preset: DevicePreset) -> int:
    """The number of touch points the preset's digitiser reports.

    The digitiser is a piece of hardware, and the fleet needs it to
    differ between two handsets: the entry tier shipped five-point
    panels and the upper tier moved to ten, so a fleet that mixes
    them hands a site a grouping key that the screen size does not
    give it.

    The value is the *largest* the preset can report, for the same
    reason the memory is: a profile built from this preset could report
    any of its options, and the fleet has to be safe for the worst of
    them.
    """

    options = preset.touch_points_options
    if isinstance(options, int):
        return options
    if options:
        return max(int(value) for value in options)
    if preset.year > 2018:
        return 10
    return 5


#: How many machines the fleet will not build more of, at the most.
_RUNNER_UP_LIMIT = 16

#: The readings the fleet is scored on, as the indices of the axes a
#: script would plausibly read together.
_READS: tuple[tuple[int, ...], ...] = (
    (0,),
    (3, 4),
    (0, 3),
    (0, 4),
    (0, 5),
    (3,),
    (4,),
    (7,),
    (8,),
    (3, 4, 7),
    (0, 3, 4),
    (0, 5, 3, 4),
    (0, 1, 2, 3, 4, 5, 6, 7, 8),
)

#: How much each read counts.  The whole machine counts most, because
#: a site that reads everything can always read everything.
_WEIGHTS: dict[tuple[int, ...], float] = {
    (0,): 4,
    (3, 4): 3,
    (0, 3): 2,
    (0, 4): 2,
    (0, 5): 1.5,
    (3,): 1.5,
    (4,): 1.5,
    (7,): 1,
    (8,): 1,
    (3, 4, 7): 1,
    (0, 3, 4): 1,
    (0, 5, 3, 4): 0.5,
    (0, 1, 2, 3, 4, 5, 6, 7, 8): 0.25,
}


def _spread_machines(presets: Sequence[DevicePreset], count: int) -> list[DevicePreset]:
    """Pick ``count`` machines that a site cannot group.

    The rule the fleet is built by is not "share as few values as
    possible" -- it is "keep the *largest group* as small as
    possible".  Those are different, and the second one is the one a
    site runs into.

    A site reads a handful of values at once.  Two profiles that
    agree on the GPU are a pair; two that agree on the chip count
    *and* the memory are also a pair, and a script that reads those
    two numbers finds the pair without reading the GPU at all.  The
    chip count has four possible values and the memory has four, so
    those two readings alone put sixteen profiles into one bucket.

    So the fleet is scored on the combinations, and the machine that
    is chosen is the one that makes the largest group smallest.  The
    score of a candidate is the worst group it leaves behind over
    the readings a script would plausibly use:

    * the GPU on its own,
    * the chip count and the memory together -- the cheapest read,
    * the GPU with the chip count,
    * the GPU with the panel,
    * the whole machine.

    Ties are broken towards the machine whose own values are rarest in
    the catalogue, so the fleet leans on the handsets that are
    hardest to find a twin for.
    """

    if count <= 0:
        return []

    candidates = _distinct_machines(presets)
    if not candidates:
        return []

    # The value each machine offers on each axis.  A machine that is
    # chosen more than once in the fleet has to offer a *different*
    # stratum, but the readings a site can group on have to differ
    # too -- and they are the same readings, so they are decided once
    # here and then only the stratum varies.
    values: list[tuple] = []
    for preset in candidates:
        values.append(
            (
                preset.gpu.unmasked_renderer,
                preset.model,
                preset.build_id,
                preset.screen.width,
                preset.screen.height,
                _core_count(preset),
                _memory_value(preset),
                _touch_points(preset),
                preset.screen.device_pixel_ratio,
            )
        )

    # How rare each value is in the catalogue.  A machine whose GPU is
    # carried by four hundred handsets is a worse choice than one
    # whose GPU is carried by three.
    rarity: dict[tuple, int] = {}
    for row in values:
        for value in row:
            rarity[value] = rarity.get(value, 0) + 1

    chosen: list[int] = []
    buckets: dict[tuple[int, ...], collections.Counter] = {
        read: collections.Counter() for read in _READS
    }
    # How many times each machine has been taken, so a machine that
    # has run out of strata is not taken again.
    taken: collections.Counter[int] = collections.Counter()

    while len(chosen) < count:
        best_index = -1
        best_score = None
        best_tie = None

        for index, preset in enumerate(candidates):
            if taken[index] >= count_machines(preset):
                continue
            row = values[index]

            # The score is the largest group the candidate leaves
            # behind, over the reads that matter.
            score = 0.0
            tie = 0
            for read, weight in _WEIGHTS.items():
                key = tuple(row[i] for i in read)
                size = buckets[read][key] + 1
                score += weight * size * size
                tie += sum(rarity.get(row[i], 0) for i in read)

            if best_score is None or score < best_score:
                best_score = score
                best_index = index
                best_tie = tie
            elif score == best_score and best_tie is not None and tie < best_tie:
                best_index = index
                best_tie = tie

        if best_index < 0:
            break

        row = values[best_index]
        for read in _READS:
            key = tuple(row[i] for i in read)
            buckets[read][key] += 1
        taken[best_index] += 1
        chosen.append(best_index)

    return [candidates[index] for index in chosen]


def plan_fleet(
    count: int,
    *,
    presets: Sequence[DevicePreset] | None = None,
    prefer: Sequence[str] | None = None,
) -> FleetPlan:
    """Decide which machine each profile of the fleet gets.

    Parameters
    ----------
    count:
        How many profiles the fleet holds.
    presets:
        The machines to draw from.  Defaults to the whole catalogue.
    prefer:
        Preset ids to use first.  The operator's own choices go to
        the front of the fleet, and the rest is filled from the
        catalogue.

    The machines are dealt out round-robin rather than in blocks, so a
    fleet that is larger than the catalogue still spreads across it
    instead of drawing the first machine twice.
    """

    if count <= 0:
        return FleetPlan(preset_ids=(), strata=())

    catalogue = list(presets) if presets is not None else list(all_presets())

    # The operator's own choices go to the front.
    chosen: list[DevicePreset] = []
    if prefer:
        by_id = {preset.preset_id: preset for preset in catalogue}
        for preset_id in prefer:
            preset = by_id.get(preset_id)
            if preset is not None:
                chosen.append(preset)

    rest = [preset for preset in catalogue if preset not in chosen]
    if rest:
        chosen.extend(_spread_machines(rest, max(count - len(chosen), 0)))

    if not chosen:
        return FleetPlan(preset_ids=(), strata=())

    preset_ids: list[str] = []
    strata: list[int] = []
    # The stratum is the unit's place in the *fleet*, not in the
    # preset.
    #
    # The place in the preset would repeat across the catalogue: the
    # first unit of one model and the first unit of another would both
    # be stratum zero, and they would walk the same first combination
    # of their own dimensions.  Their models differ, but a site that
    # groups on the *combination* of the readings -- and that is how a
    # site groups -- would still find the pair.
    #
    # The place in the fleet never repeats, so no two units of the
    # fleet walk the same combination, whatever models they are built
    # from.
    #
    # **The model is the unit of the walk.**  The catalogue holds
    # several rows for one handset -- the same model code under two
    # different catalogue names, the same handset with two of its
    # memory configurations split into two rows -- and two rows of one
    # model have the same dimensions and the same radix.  If each row
    # started its own walk at stratum zero, the first unit of one row
    # and the first unit of the other would land on the same
    # combination: the same panel, the same density, the same core
    # count, the same memory and the same touch count, under a model
    # code they share.
    #
    # So the walk is continuous over the *model*: the rows of one model
    # take their strata from one counter, and the counter moves on
    # where the previous row left off.  Two units of one model are two
    # slices of one space, which is what they are -- two handsets of
    # one line.
    #
    # The counter is per model rather than per row, and it is *not*
    # reset between models: a handset of one model and a handset of
    # another would then both start at zero, and the site would find
    # the pair on the combination rather than on the model.
    model_slots: dict[str, int] = {}
    for preset in chosen:
        capacity = count_machines(preset)
        for _ in range(capacity):
            if len(preset_ids) >= count:
                break
            preset_ids.append(preset.preset_id)
            # The model is the key the walk is shared under, and it is
            # the *code* the profile reports, not the marketing name.
            # The catalogue holds one handset under both: the
            # hand-written rows name it "Galaxy S23" and the expanded
            # ones name it "SM-S911B", and the build fingerprint, the
            # user agent and the client hints all carry the code.
            # A site reads the code, so the walk is shared under the
            # code -- otherwise the two rows of one handset would walk
            # the same space twice.
            #
            # The variant list is the set of codes the model is sold
            # under, and the first of them is the one the profile
            # carries.
            model_key = (
                preset.model_variants[0] if preset.model_variants else preset.model
            ) or preset.preset_id
            slot = model_slots.get(model_key, 0)
            model_slots[model_key] = slot + 1
            strata.append(slot)
        if len(preset_ids) >= count:
            break

    return FleetPlan(preset_ids=tuple(preset_ids), strata=tuple(strata))


def generate_fleet(
    count: int,
    *,
    name_prefix: str = "",
    presets: Sequence[DevicePreset] | None = None,
    prefer: Sequence[str] | None = None,
    generator: ProfileGenerator | None = None,
    registry: SignatureRegistry | None = None,
    geo=None,
    proxy=None,
    vietnam: bool = True,
) -> list[ProfileConfig]:
    """Build a fleet in which every profile is a different machine.

    The profiles are handed to the generator one at a time, each from
    its own preset, so the machine-describing values separate by
    construction rather than by luck.

    The collision guard *is* consulted, and it is consulted for the
    machine axis in particular.  The guard used to protect the
    fingerprint axes only -- the canvas, the WebGL, the audio -- and
    the machine axes were left to the plan.  That was a gap, because
    the machine axes are the ones a website can read: the graphics
    part, the panel, the chip count and the memory are handed to the
    script verbatim, while the salts that carry the uniqueness of the
    other axes are invisible to it.  The plan spreads the fleet over
    the catalogue; the guard guarantees that no two profiles end up
    claiming the same machine.

    A registry may be supplied so that the fleet is allocated against
    the profiles that already exist -- the duplicate that matters is
    the one against the *whole* set, not the one inside this batch.

    ``vietnam`` puts the whole fleet in Vietnam: one clock, one
    locale, one currency, and the coordinates spread over the country's
    provinces.  The profiles then differ from each other by the place
    they are in and by the machine they are, which is what the
    requirement is: a hundred million *different* Vietnamese people,
    not one person with a hundred million browsers.
    """

    plan = plan_fleet(count, presets=presets, prefer=prefer)
    if not generator:
        generator = ProfileGenerator()

    guard = CollisionGuard(registry=registry or InMemorySignatureRegistry())

    from .geo_vietnam import (
        VIETNAM_COORDINATES,
        VIETNAM_CURRENCY,
        VIETNAM_LOCALE,
        VIETNAM_TIMEZONE,
    )

    profiles: list[ProfileConfig] = []
    for index, (preset_id, stratum) in enumerate(
        zip(plan.preset_ids, plan.strata, strict=True)
    ):
        seed = new_master_seed()
        profile_geo = geo
        if vietnam and profile_geo is None:
            # The anchor is the country, and the *seed* decides which
            # province inside it the profile sits in.  The generator
            # then places the coordinates there and jitters them, so
            # two profiles of the same province are still two people.
            profile_geo = _geo_target(
                VIETNAM_LOCALE,
                VIETNAM_TIMEZONE,
                "VN",
                VIETNAM_CURRENCY,
                "h23",
                ("vi", "en"),
            )
        candidate = generator.generate(
            preset_id=preset_id,
            name=f"{name_prefix or 'Fleet'} {index + 1}",
            master_seed=seed,
            stratum=stratum,
            profile_id=new_profile_id(),
            geo=profile_geo,
            proxy=proxy,
        )
        profile_id = candidate.identity.id or new_profile_id()
        allocated = guard.allocate(profile_id, candidate)
        guard.commit(allocated, profile_id)
        profiles.append(allocated)

    return profiles


__all__ = [
    "FleetPlan",
    "FleetReport",
    "MACHINE_AXES",
    "generate_fleet",
    "machine_identity",
    "measure_fleet",
    "plan_fleet",
]
