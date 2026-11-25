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
import heapq
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..schema.profile import ProfileConfig
from .collision import CollisionGuard, InMemorySignatureRegistry, SignatureRegistry
from .devices import DevicePreset, all_presets, cores_from_cluster_layout
from .generator import ProfileGenerator, _geo_target
from .hardware_approximation import approximate_device_memory
from .machine import count_machines, flush_count_cache
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

#: How many times the fleet may go round the catalogue looking for
#: another unit of a machine it has already used.
#:
#: The catalogue holds a few thousand handsets, and a fleet of a
#: hundred million profiles needs a hundred million units of them.
#: Each round adds one more unit of every handset, so the number of
#: rounds is what decides how many phones of one model the fleet
#: owns.  The cap keeps the plan from walking forever: a fleet that
#: asks for more units than the cap allows is stopped with the
#: catalogue exhausted, and the caller is told how far it got.
_MAX_UNIT_ROUNDS = 4096

#: How many machines the spreading search may pick.
#:
#: The search is worth its cost while the fleet is smaller than the
#: catalogue of machines: it chooses the handsets whose readings are
#: the hardest for a site to group on, and that choice is what keeps
#: the fleet ungroupable.  Its cost grows with the square of the
#: number of machines it picks, so the number it picks is capped.
#:
#: The cap is a few times the size of the catalogue: the search still
#: gets to walk the whole catalogue several times over, which is
#: where the handsets that spread best get their chance, and the units
#: beyond the cap are additional phones of the models the fleet
#: already holds.
_SPREAD_LIMIT = 120_000

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


def _scaled_weight(weight: float) -> int:
    """A weight as a whole number.

    The weights are quarters and halves, so multiplying by four turns
    every one of them into a whole number.  The score is then a whole
    number as well, and a whole number can be compared for equality
    without the rounding that a float would bring.
    """

    return int(round(weight * 4))


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

    # The keys every read produces, and the rarity of each read, worked
    # out once per machine.
    #
    # The round below is run once per profile in the fleet, and it
    # walks every machine in the catalogue, so a fleet of a hundred
    # thousand profiles walks the catalogue a hundred thousand times.
    # The keys and the rarity of a machine do not depend on the round,
    # so they are worked out here, once, and the round only adds them
    # up.
    scored: list[tuple[tuple[tuple[int, ...], float, tuple, int], ...]] = []
    for row in values:
        entries = []
        for read, weight in _WEIGHTS.items():
            key = tuple(row[i] for i in read)
            tie = sum(rarity.get(row[i], 0) for i in read)
            entries.append((read, weight, key, tie))
        scored.append(tuple(entries))

    chosen: list[int] = []
    buckets: dict[tuple[int, ...], collections.Counter] = {
        read: collections.Counter() for read in _READS
    }
    # The growth of each bucket that has not been paid for yet.
    #
    # The score of a candidate is the sum of the squares of the sizes
    # of the buckets it would land in, and the score of a candidate
    # that is not chosen still has to grow when one of its buckets
    # grows.  Paying that growth on the bucket, and settling it on the
    # candidate when the candidate reaches the top of the heap, keeps
    # the work of a round proportional to the number of candidates
    # that actually reach the top -- and not to the size of the
    # buckets, which for the cheap readings is the whole catalogue.
    pending: dict[tuple[int, ...], collections.Counter] = {
        read: collections.Counter() for read in _READS
    }
    # How many times each machine has been taken, so a machine that
    # has run out of strata is not taken again.
    taken: collections.Counter[int] = collections.Counter()

    # How many units each machine can supply.
    #
    # The count is measured by walking the machine's space, and the
    # walk is expensive.  The measurement is remembered inside the
    # machine module, but asking for it once per candidate per round
    # turns the round into a walk of the whole catalogue, and the
    # fleet becomes quadratic in the size of the fleet.  The answer
    # does not depend on the round, so it is asked for once here.
    #
    # It is asked for lazily, though: a fleet of a hundred profiles
    # only ever takes a hundred machines, and the walk of a machine
    # that is never taken is wasted.  The measurement is made the
    # first time a machine is a serious contender, and remembered
    # from then on.
    capacity: list[int | None] = [None] * len(candidates)

    def _capacity_of(index: int) -> int:
        """How many units a machine can supply, measured on first use."""

        known = capacity[index]
        if known is None:
            known = count_machines(candidates[index])
            capacity[index] = known
        return known

    # The score of every candidate, and the candidates that share a
    # key on a read.
    #
    # The score is the weighted sum of the square of the size of every
    # bucket the candidate would land in.  Picking the machine that
    # leaves the largest group smallest means picking the candidate
    # with the smallest score, and the score of a candidate only ever
    # *grows* as the fleet is filled: a bucket it shares with an
    # already-chosen machine gets bigger, and bigger buckets score
    # higher.  That monotonicity is what lets the search be done with
    # a heap instead of a full sweep of the catalogue per round.
    #
    # The weights are scaled by four so that the score is a whole
    # number.  A score that is compared as a float is a score that can
    # be compared wrongly, and the tie-break that follows it decides
    # which of two identical machines is taken.
    scores: list[int] = [0] * len(candidates)
    for index, entries in enumerate(scored):
        total = 0
        for _read, weight, key, _tie in entries:
            total += _scaled_weight(weight)
        scores[index] = total

    # The candidates that share a key on a read.  When a bucket grows,
    # the score of every candidate that lands in it grows too, and
    # this is the index that says which ones.
    sharing: dict[tuple[int, ...], dict[tuple, list[int]]] = {
        read: collections.defaultdict(list) for read in _READS
    }
    for index, entries in enumerate(scored):
        for read, _weight, key, _tie in entries:
            sharing[read][key].append(index)

    # The candidates that are still on offer, ordered by score.
    #
    # A candidate that runs out of strata leaves the heap, and a
    # candidate whose score grows is pushed again with its new score.
    # The entry left behind by that push is recognised as stale when
    # it surfaces, because the score it carries is not the score the
    # candidate has any more.  Since a score only grows, the fresh
    # entry always surfaces before the stale one would have been
    # useful, so nothing is chosen on a stale score.
    heap: list[tuple[int, int, int]] = []

    # The tie-break value of every candidate: how rare its own values
    # are in the catalogue.  It is worked out once per candidate,
    # because it is asked for on every push into the heap and the heap
    # is pushed into a great many times.
    ties: list[int] = [sum(entry[3] for entry in entries) for entries in scored]

    for index in range(len(candidates)):
        heapq.heappush(heap, (scores[index], ties[index], index))

    while len(chosen) < count:
        best_index = -1
        best_tie = None

        while heap:
            score, tie, index = heapq.heappop(heap)
            if score != scores[index]:
                # The candidate's score grew after this entry was
                # pushed, so the entry is stale and a fresher one is
                # on its way up.
                continue

            # The score of a candidate is settled when it reaches the
            # top of the heap.  The growth of every bucket it shares
            # has been recorded on the bucket while the fleet was
            # filled, and the candidate pays for all of it now.
            #
            # The growth is taken off the bucket as it is paid, so the
            # bucket only carries what has grown since the last time a
            # candidate of it was settled.  A candidate that is settled
            # twice therefore pays for the growth of the second round
            # only, and the total each candidate pays is the whole
            # growth of its buckets.
            row = values[index]
            grown = 0
            for read, _weight, key, _tie in scored[index]:
                pending_growth = pending[read][key]
                if pending_growth:
                    pending[read][key] = 0
                    grown += pending_growth
            if grown:
                scores[index] += grown
                # The candidate may no longer be the best one, so it
                # goes back onto the heap with its settled score.
                heapq.heappush(heap, (scores[index], tie, index))
                continue

            if taken[index] >= _capacity_of(index):
                # The machine is out of strata and stays out.
                continue
            best_index = index
            best_tie = tie
            break

        if best_index < 0:
            break

        # The machine is taken, and the buckets it lands in grow.
        #
        # The growth of a bucket raises the score of every candidate
        # that shares it, and each of them has to be pushed back onto
        # the heap so the heap knows about the new score.  Doing that
        # here, one candidate at a time, makes the cost of a round
        # grow with the size of the *buckets* -- and the buckets of
        # the cheap readings hold most of the catalogue, so a round
        # over a large fleet costs billions of pushes.
        #
        # The growth is therefore recorded on the *bucket* instead, and
        # the score of a candidate is settled when the candidate
        # reaches the top of the heap: the pending growth of every
        # bucket the candidate shares is added to its score then, and
        # the candidate is put back if the settled score says it is
        # no longer the best.  Nothing is pushed that does not have to
        # be, and the work of a round is proportional to the number of
        # buckets that are still small enough to tell two candidates
        # apart.
        row = values[best_index]
        for read in _READS:
            key = tuple(row[i] for i in read)
            counter = buckets[read]
            size = counter[key]
            counter[key] = size + 1

            # The bucket grew from ``size`` to ``size + 1``, so the
            # bucket a candidate lands in holds ``size + 1`` machines
            # already and will hold ``size + 2`` once the candidate
            # joins it.  The score of the candidate therefore grows by
            # ``weight * ((size + 2)^2 - (size + 1)^2)``.
            delta = (size + 2) * (size + 2) - (size + 1) * (size + 1)
            weight = _scaled_weight(_WEIGHTS[read])
            pending[read][key] += weight * delta

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
        # The spreading search is the expensive part of the plan: it
        # scores every machine of the catalogue against every reading a
        # site might group on, and it does that once per unit of the
        # fleet.  The cost therefore grows with the *square* of the
        # fleet, and a fleet of a hundred million units would take
        # longer than the century it is meant to span.
        #
        # The search is worth what it costs while the fleet is still
        # smaller than the catalogue of machines: it picks the
        # handsets whose readings are the hardest to group on, and
        # that choice is what keeps the *first* part of the fleet
        # ungroupable.  Past that point the catalogue is exhausted
        # and every machine is in the fleet anyway, so the search has
        # nothing left to choose between -- the units that follow are
        # additional phones of the models the fleet already holds, and
        # what separates them is their serial, not their panel.
        #
        # So the search runs over the catalogue and no further, and the
        # rest of the fleet is filled by the fast path below.
        spread_limit = _SPREAD_LIMIT
        wanted = max(count - len(chosen), 0)
        if wanted > spread_limit:
            wanted = spread_limit
        if wanted:
            chosen.extend(_spread_machines(rest, wanted))

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

    # How many machines each of the chosen models has, measured once.
    #
    # The measurement walks the slice space of a model, and the answer
    # is remembered between runs, but asking for it inside the loop
    # would ask for it once per model per round.  The rounds are
    # counted in thousands for a fleet of a hundred million, so the
    # answer is looked up once here and then reused.
    capacities: list[int] = [count_machines(preset) for preset in chosen]

    # The units are dealt round-robin across the chosen models, so that
    # two neighbours of the fleet are never the same handset.
    #
    # The order matters.  Dealing model by model -- every unit of one
    # model, then every unit of the next -- puts a run of alike
    # readings at the head of the fleet: ten handsets of one model in
    # a row, all of them reporting the same graphics part, the same
    # panel and the same chip count, and a site that reads any two of
    # those readings puts the whole run in one bucket.  Dealing
    # round-robin puts a different handset between every two units,
    # so the largest group of alike readings stays as small as the
    # catalogue allows.
    #
    # The stratum still moves on when the same model comes round
    # again: the second unit of one model is the second slice of that
    # model's space, and the per-unit readings -- the serial, the
    # incremental build number -- keep moving with it.
    remaining = list(capacities)
    while len(preset_ids) < count and any(remaining):
        added = False
        for index, preset in enumerate(chosen):
            if len(preset_ids) >= count:
                break
            if remaining[index] <= 0:
                continue
            remaining[index] -= 1
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
            #
            # The key is the *whole* list, not its first entry.  The
            # Pixel 8 Pro is sold under four codes -- GC3VE, G1MN,
            # GE9DP and G1FQ -- and the generator draws one of them per
            # unit.  A key built from the first entry alone gave every
            # unit of that handset the same counter, and the units
            # walked the same space twice: fifty handsets all reported
            # "GC3VE" and a site reading the model put them in one
            # bucket.  The key is the tuple of codes, so the walk
            # moves on for each of them.
            model_key = (
                tuple(preset.model_variants) if preset.model_variants else (preset.model,)
            ) or (preset.preset_id,)
            slot = model_slots.get(model_key, 0)
            model_slots[model_key] = slot + 1
            strata.append(slot)
            added = True
        if not added:
            break

    # A fleet larger than the catalogue of *machines* is still a fleet.
    #
    # The catalogue holds a few thousand distinct handsets, and a real
    # shop of a hundred thousand phones owns a few of every model: the
    # handsets of one model differ from each other by their serial and
    # their incremental build number, not by their panel.  Two units of
    # one model that share a panel are two phones of the same line, and
    # a site cannot tell them apart -- which is exactly the point.
    #
    # The walk is therefore allowed to leave the space of the machine
    # and come back to it: the hardware readings repeat, the way they
    # do on two phones of one model, while the per-unit readings -- the
    # serial, the incremental build number, the bootloader and the
    # salts of the canvas, the audio and the fonts -- keep moving.
    # The stratum is what keys those per-unit readings, so a second
    # unit of one machine is a second stratum.
    #
    # The units are dealt round-robin across the catalogue, so the
    # fleet keeps spreading over the handsets instead of exhausting one
    # model before it moves on to the next.
    if len(preset_ids) < count and chosen:
        rounds = 0
        while len(preset_ids) < count and rounds <= _MAX_UNIT_ROUNDS:
            added = False
            for preset in chosen:
                if len(preset_ids) >= count:
                    break
                preset_ids.append(preset.preset_id)
                model_key = (
                    preset.model_variants[0]
                    if preset.model_variants
                    else preset.model
                ) or preset.preset_id
                slot = model_slots.get(model_key, 0)
                model_slots[model_key] = slot + 1
                strata.append(slot)
                added = True
            if not added:
                break
            rounds += 1

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

    # The planner measured how many machines every model in the
    # catalogue produces, and that measurement is kept between runs so
    # the next fleet does not have to pay for it again.
    flush_count_cache()

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
