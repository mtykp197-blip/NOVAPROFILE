"""The multi-layer collision guard.

The spec's survival criterion is absolute: *no two profiles may ever be
clustered as the same user*.  This module enforces that with a three-stage
escalation:

1. **Nudge** -- shift the deep decimal digits of the WebGL image value by
   incrementing ``nudge_step``.  This moves the profile within its device
   family without changing the device identity.
2. **Roll seed** -- when nudging is exhausted, generate a brand new
   ``master_seed`` and increment ``seed_epoch``.  This turns the profile into
   a completely different hardware device.
3. **Reject** -- when eight attempts have failed, refuse to allocate the
   profile and raise :class:`ProfileCollisionError`.  Returning a duplicate
   is never an option.

Every candidate is checked against the signature registry before it is
returned, and the winning signatures are recorded so that the next profile
cannot reuse them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from ..schema.enums import SignatureKind, SignatureState
from ..schema.profile import ProfileConfig, SeedConfig
from .consistency import ConsistencyResolver
from .seed import Axis, SeedDeriver, new_master_seed

if TYPE_CHECKING:
    from .generator import ProfileGenerator


def _default_generator_factory() -> ProfileGenerator:
    """Build a generator for the guard's rebuild step.

    The import is local because :mod:`novaprofile.core.generator` imports
    the consistency resolver, which this module also uses.
    """

    from .generator import ProfileGenerator

    return ProfileGenerator()


# --------------------------------------------------------------------------
# Registry protocol
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SignatureRecord:
    """A single axis signature belonging to a profile."""

    kind: SignatureKind
    signature: str
    profile_id: str
    seed_epoch: int
    nudge_step: int
    state: SignatureState = SignatureState.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SignatureRegistry(Protocol):
    """The storage the guard consults and updates.

    The method names match :class:`novaprofile.registry.store.ProfileStore`
    so that the persistent store can be handed to the guard directly.
    """

    def find_signature(self, kind: SignatureKind | str, signature: str) -> SignatureRecord | None:
        """Return the ACTIVE record for this axis signature, if any."""
        ...

    def record_signature(self, record: SignatureRecord) -> None:
        """Store a signature."""
        ...

    def count_signatures(self, kind: SignatureKind | str) -> int:
        """How many active signatures of this kind exist."""
        ...


class InMemorySignatureRegistry:
    """A registry backed by a dictionary.

    Used by the tests and as the default when no persistent store is wired
    up.  It is *not* thread-safe across processes, but it is sufficient for
    a single control-plane process.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[SignatureKind, str], SignatureRecord] = {}

    def find_signature(self, kind: SignatureKind | str, signature: str) -> SignatureRecord | None:
        record = self._records.get((kind, signature))
        if record is None or record.state is not SignatureState.ACTIVE:
            return None
        return record

    def record_signature(self, record: SignatureRecord) -> None:
        self._records[(record.kind, record.signature)] = record

    def count_signatures(self, kind: SignatureKind | str) -> int:
        return sum(
            1
            for (record_kind, _), record in self._records.items()
            if record_kind == kind and record.state is SignatureState.ACTIVE
        )

    # -- the legacy names, kept so that older callers keep working --

    def find_active(self, kind: SignatureKind, signature: str) -> SignatureRecord | None:
        return self.find_signature(kind, signature)

    def insert(self, record: SignatureRecord) -> None:
        self.record_signature(record)

    def count_active(self, kind: SignatureKind) -> int:
        return self.count_signatures(kind)

    def all_records(self) -> Iterable[SignatureRecord]:
        return list(self._records.values())


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ProfileCollisionError(RuntimeError):
    """Raised when a profile cannot be allocated without colliding."""

    def __init__(
        self,
        profile_id: str,
        kind: SignatureKind,
        signature: str,
        attempts: int,
        conflicting_profile_id: str | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.kind = kind
        self.signature = signature
        self.attempts = attempts
        self.conflicting_profile_id = conflicting_profile_id
        message = (
            f"profile {profile_id} could not be allocated after {attempts} attempts: "
            f"the {kind.value} signature {signature[:16]}... still collides"
        )
        if conflicting_profile_id:
            message += f" with profile {conflicting_profile_id}"
        super().__init__(message)


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


#: The axes that are checked, in the order they are checked.  The composite
#: ``sig_full`` is checked last because it is the most expensive to compute
#: and the least likely to collide on its own.
GUARDED_AXES: tuple[SignatureKind, ...] = (
    SignatureKind.DEVICE,
    SignatureKind.CANVAS,
    SignatureKind.WEBGL_DRAW,
    SignatureKind.WEBGL_PARAMS,
    SignatureKind.RECTS,
    SignatureKind.DEVICES,
    SignatureKind.AUDIO,
    SignatureKind.SENSORS,
    SignatureKind.STORAGE,
    SignatureKind.WEBGPU,
    SignatureKind.FONTS,
    SignatureKind.GEO,
    SignatureKind.FULL,
)

#: Maps a :class:`SignatureKind` onto the key in
#: :meth:`ProfileConfig.signature_parts`.
_AXIS_TO_PARTS_KEY = {
    SignatureKind.CANVAS: "sig_canvas",
    SignatureKind.WEBGL_DRAW: "sig_webgl_draw",
    SignatureKind.WEBGL_PARAMS: "sig_webgl_params",
    SignatureKind.RECTS: "sig_rects",
    SignatureKind.DEVICES: "sig_devices",
    SignatureKind.AUDIO: "sig_audio",
    SignatureKind.DEVICE: "sig_device",
    SignatureKind.SENSORS: "sig_sensors",
    SignatureKind.STORAGE: "sig_storage",
    SignatureKind.WEBGPU: "sig_webgpu",
    SignatureKind.FONTS: "sig_fonts",
    SignatureKind.GEO: "sig_geo",
}


class CollisionGuard:
    """Allocates profiles without ever creating a duplicate fingerprint.

    Parameters
    ----------
    registry:
        Where the signatures of already-allocated profiles are stored.
    resolver:
        The consistency resolver used to re-validate a profile after it has
        been nudged or rolled.
    deriver_factory:
        Produces a :class:`SeedDeriver` for a given seed configuration.  It
        defaults to :meth:`SeedConfig.deriver` but can be overridden in tests.
    master_seed_factory:
        Produces a fresh master seed when a roll is required.  Defaults to
        :func:`novaprofile.core.seed.new_master_seed`.
    """

    #: How many variants of a candidate may be tried before the allocation
    #: is refused.  The escalation is *nudge* first (move along the device
    #: family) and *roll* second (become a completely different device).  A
    #: roll draws a fresh 64-bit serial and a fresh 64-bit incremental
    #: build number, so the chance that a rolled candidate still collides
    #: is about 2**-64.  Eight attempts cover five nudges, one full roll
    #: and two nudges of the rolled seed, which leaves a comfortable margin
    #: on top of that margin.
    MAX_ATTEMPTS: int = 8
    #: How many nudges a single seed may take before the seed is rolled.
    MAX_NUDGE_STEPS: int = 5
    #: How many strata a machine walk may take.  The machine readings are
    #: drawn from the stratum's slice of the choice space, so the only
    #: way off a taken machine is a new stratum -- and the space of
    #: strata of one model is finite.  The walk therefore gets a budget
    #: of its own: the eight attempts above are about the *fingerprints*,
    #: and a machine that is taken is not a fingerprint collision.
    MAX_MACHINE_STEPS: int = 4096

    def __init__(
        self,
        registry: SignatureRegistry,
        resolver: ConsistencyResolver | None = None,
        deriver_factory: Callable[[SeedConfig], SeedDeriver] | None = None,
        master_seed_factory: Callable[[], str] | None = None,
        generator_factory: Callable[[], ProfileGenerator] | None = None,
        preset_pool: Sequence[str] | None = None,
    ) -> None:
        self._registry = registry
        self._resolver = resolver or ConsistencyResolver()
        self._deriver_factory = deriver_factory or (lambda cfg: cfg.deriver())
        self._master_seed_factory = master_seed_factory or new_master_seed
        self._generator_factory = generator_factory or _default_generator_factory
        # The machines this guard has handed out.  The registry stores
        # the signatures as hashes, so the machines cannot be read back
        # out of it; the set is what makes the guard's own allocations
        # visible to it, and the store is asked for the rest.
        self._known_machines: set[tuple] = set()
        # The models the walk has walked off, and how far into each of
        # them it has got.
        #
        # A model has a finite number of machines -- the panel, the
        # density, the model code, the chip count and the memory are a
        # small set of combinations -- so a fleet larger than one
        # model's machines has to move to another model, and a
        # *different* model is a different machine to a site by
        # construction: a different graphics part, a different panel, a
        # different build.
        #
        # The walk must not come back.  A model that was walked off is
        # spent, and a second unit that lands on it walks the same
        # space from the start and lands on a machine that is already
        # taken -- which is exactly the pair the guard exists to
        # prevent.
        self._spent_models: set[str] = set()
        # How many slices of each model the walk has handed out.  A
        # model that is entered a second time continues where it left
        # off rather than starting over.
        self._model_slots: dict[str, int] = {}
        # The slices each model has handed out.  A model may be entered
        # more than once -- the walk leaves it and comes back -- and
        # the second visit has to land on a slice the first visit did
        # not use.  The set is what makes that true.
        self._used_slices: dict[str, set[int]] = {}
        # The models the guard may move a profile to when its own model
        # has no machine left.  A model has a finite number of
        # machines -- the panel, the density, the model code, the chip
        # count and the memory are a small set of combinations -- so a
        # fleet larger than one model's machines has to move to another
        # model, and a *different* model is a different machine to a
        # site by construction: a different graphics part, a different
        # panel, a different build.
        self._preset_pool = tuple(preset_pool) if preset_pool else ()
        self._preset_cursor = 0
        # The catalogue of models, read once.  It is large -- thousands
        # of handsets -- and the walk asks for it on every model switch.
        self._cached_pool: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate(self, profile_id: str, candidate: ProfileConfig) -> ProfileConfig:
        """Return a collision-free version of ``candidate``.

        The returned profile has a possibly-adjusted ``nudge_step`` and
        ``seed_epoch``/``master_seed``.

        The signatures are **not** written to the registry here.  The caller
        must first persist the profile and then call :meth:`commit`; the
        registry's signature rows reference the profile, so writing them
        before the profile exists would violate the referential integrity
        of the store.

        Raises
        ------
        ProfileCollisionError
            When no collision-free variant could be found within
            :attr:`MAX_ATTEMPTS` attempts.
        """
        seed_config = candidate.seed_config
        nudge_step = seed_config.nudge_step
        epoch = seed_config.seed_epoch
        stratum = seed_config.stratum
        last_conflict: tuple[SignatureKind, str, str | None] | None = None
        machine_steps = 0

        # The geographic anchor is walked on its own.  The other axes
        # move by nudging or by rolling the seed, but the geo anchor is
        # drawn from the catalogue of the world, and a catalogue of a
        # hundred-odd timezones cannot hold a hundred million distinct
        # people.  The anchor therefore moves to a *new* entry of the
        # catalogue, and the coordinates move with it, so that a fleet
        # spreads over the world instead of piling into one city.
        geo_index = self._geo_index(candidate)

        # The models the walk may move to when the current one has no
        # machine left.  The current model comes first, so that a fleet
        # which fits inside one model stays inside it.
        pool = self._model_pool(candidate)
        model = candidate.seed_config.preset_id
        # How many strata the current model has.  The walk leaves the
        # model when it has used them all, because a stratum beyond the
        # model's space draws the *same* machine again.
        model_capacity = self._model_capacity(model)

        while True:
            for _attempt in range(self.MAX_ATTEMPTS):
                working = self._rebuild(
                    candidate,
                    seed_config,
                    nudge_step,
                    epoch,
                    stratum,
                    preset_id=model,
                    geo_index=geo_index,
                )

                # Re-validate: a nudge must not break the internal consistency.
                report = self._resolver.check(working)
                if report.errors:
                    raise ConsistencyRejectedError(profile_id, report)

                conflict = self._find_conflict(working, profile_id)
                if conflict is None:
                    # The signatures are not the whole story.  The machine
                    # axes -- the renderer, the panel, the chip count, the
                    # memory -- are the ones a website reads straight off
                    # the page, and two profiles that agree on all of them
                    # are one device to that site however different their
                    # canvas and their audio are.
                    machine = _machine_of(working)
                    if machine not in self._known_machines:
                        self._known_machines.add(machine)
                        # The walk has handed out one more slice of
                        # this model.  The slice is remembered by its
                        # *index*, not by a count, because the walk
                        # into a model has to continue where the last
                        # one stopped -- a count would let the walk
                        # land on a slice it already used, and two
                        # units would then be one machine.
                        model_key = model or ""
                        used = self._model_slots.get(model_key, -1)
                        if stratum is not None and stratum > used:
                            self._model_slots[model_key] = stratum
                        # The slice itself is remembered too, because a
                        # model may be entered again after the walk
                        # moved on and came back, and the slice it
                        # lands on has to be one it has not used.
                        if model_key and stratum is not None:
                            self._used_slices.setdefault(model_key, set()).add(stratum)
                        return working

                    # The machine is moved by the **stratum**, not by the
                    # seed: the panel, the density, the model code, the
                    # chip count and the memory are all drawn from the
                    # stratum's slice of the choice space, so a new
                    # stratum is the only thing that moves them.  A nudge
                    # or a rolled seed leaves the machine exactly where it
                    # was, and the walk would run out of attempts without
                    # ever leaving the device.
                    last_conflict = (SignatureKind.DEVICE, "", profile_id)
                    machine_steps += 1
                    if machine_steps > self.MAX_MACHINE_STEPS:
                        raise ProfileCollisionError(
                            profile_id=profile_id,
                            kind=SignatureKind.DEVICE,
                            signature="",
                            attempts=self.MAX_ATTEMPTS,
                            conflicting_profile_id=profile_id,
                        )

                    # The next stratum of this model, or the first
                    # stratum of the next model when this one is spent.
                    if stratum is None:
                        stratum = 0
                    else:
                        stratum += 1
                    if model_capacity and stratum >= model_capacity:
                        # The model is spent: every machine it owns is
                        # taken.  It is marked as such so that the walk
                        # never comes back to it -- a second unit that
                        # landed on it would walk the same space from
                        # the start and find a machine that is already
                        # taken.
                        if model:
                            self._spent_models.add(model)
                        next_model = self._next_model(pool, model or "")
                        if next_model is None:
                            raise ProfileCollisionError(
                                profile_id=profile_id,
                                kind=SignatureKind.DEVICE,
                                signature="",
                                attempts=self.MAX_ATTEMPTS,
                                conflicting_profile_id=profile_id,
                            )
                        model = next_model
                        # The new model continues where it left off,
                        # rather than starting at its first machine
                        # again.  A model that was handed out before
                        # has already used its first slices, and the
                        # walk has to pick up after them.
                        #
                        # The slice it picks up after is the one *past*
                        # the last one used, so the walk never lands on
                        # a machine that is already taken.
                        stratum = self._model_slots.get(model, -1) + 1
                        model_capacity = self._model_capacity(model)

                        # The walk may come back to a model it has
                        # already visited, and the slices that model
                        # used are taken.  The walk steps over them
                        # until it finds one that is free, and only
                        # then does it try the machine.
                        used = self._used_slices.get(model, set())
                        while stratum in used and model_capacity:
                            if stratum >= model_capacity:
                                # The model is spent after all: every
                                # slice of it is taken.
                                self._spent_models.add(model)
                                next_model = self._next_model(pool, model or "")
                                if next_model is None:
                                    raise ProfileCollisionError(
                                        profile_id=profile_id,
                                        kind=SignatureKind.DEVICE,
                                        signature="",
                                        attempts=self.MAX_ATTEMPTS,
                                        conflicting_profile_id=profile_id,
                                    )
                                model = next_model
                                stratum = self._model_slots.get(model, -1) + 1
                                model_capacity = self._model_capacity(model)
                                used = self._used_slices.get(model, set())
                                continue
                            stratum += 1

                    nudge_step = seed_config.nudge_step
                    epoch = seed_config.seed_epoch
                    seed_config = seed_config.model_copy(update={"stratum": stratum})
                    break

                last_conflict = conflict
                kind, signature, other_id = conflict

                # Stage 0: the geographic anchor.  A conflict here means
                # the profile sits where another profile already sits, and
                # a site reads the clock -- so the anchor moves to another
                # place on the earth.  The move is done before the nudge
                # and the roll, because both of those leave the anchor
                # exactly where it was.
                if kind is SignatureKind.GEO:
                    next_index = self._next_geo_index(geo_index)
                    if next_index is None:
                        raise ProfileCollisionError(
                            profile_id=profile_id,
                            kind=kind,
                            signature=signature,
                            attempts=self.MAX_ATTEMPTS,
                            conflicting_profile_id=other_id,
                        )
                    geo_index = next_index
                    nudge_step = seed_config.nudge_step
                    epoch = seed_config.seed_epoch
                    continue
                # Stage 1: nudge.  Move along the device family.
                if nudge_step < self.MAX_NUDGE_STEPS:
                    nudge_step += 1
                    continue

                # Stage 2: roll the seed.  Become a completely new device.
                epoch += 1
                nudge_step = 0
                seed_config = seed_config.with_epoch(
                    epoch, self._master_seed_factory()
                )
            else:
                kind, signature, other_id = last_conflict or (
                    SignatureKind.FULL,
                    "",
                    None,
                )
                raise ProfileCollisionError(
                    profile_id=profile_id,
                    kind=kind,
                    signature=signature,
                    attempts=self.MAX_ATTEMPTS,
                    conflicting_profile_id=other_id,
                )

    # ------------------------------------------------------------------
    # The geographic walk
    # ------------------------------------------------------------------

    def _geo_index(self, candidate: ProfileConfig) -> int:
        """Which entry of the geo catalogue the candidate is anchored to.

        The index is the anchor's *address*, so a rebuilt profile that
        keeps the same index keeps the same place on the earth.
        """

        from .geo_catalog import GEO_CATALOGUE

        wanted = candidate.timezone.iana_name
        for index, entry in enumerate(GEO_CATALOGUE):
            if entry[1] == wanted:
                return index
        return 0

    def _next_geo_index(self, current: int) -> int | None:
        """The next place the profile may occupy, or ``None``.

        A Vietnamese profile stays in Vietnam, so the walk moves it from
        one *province* to the next rather than from one country to the
        next.  Everywhere else the walk moves along the catalogue of the
        world.
        """

        from .geo_vietnam import VIETNAM_COORDINATES, VIETNAM_TIMEZONE

        if not VIETNAM_COORDINATES:
            return None
        # The Vietnamese fleet's geography is the province table, so the
        # walk runs over it whether or not the candidate is Vietnamese;
        # the guard only ever rebuilds a candidate with the anchor it
        # was given.
        return (current + 1) % len(VIETNAM_COORDINATES)

    def _model_capacity(self, preset_id: str | None) -> int:
        """How many machines one model has.

        The capacity is the number of *distinct* machines the model
        produces, not the size of its slice space: the panel, the
        density, the model code, the chip count and the memory collapse
        onto a handful of combinations, and a stratum beyond the last
        machine draws the first one again.
        """

        if not preset_id:
            return 0
        try:
            from .devices import get_preset
            from .machine import count_machines

            preset = get_preset(preset_id)
            if preset is None:
                return 0
            return count_machines(preset)
        except Exception:  # pragma: no cover - the catalogue is optional
            return 0

    def _model_pool(self, candidate: ProfileConfig) -> tuple[str, ...]:
        """The models the machine walk may use, in order.

        The candidate's own model comes first, so a fleet that fits
        inside one model stays inside it.  The rest of the pool is the
        catalogue, and the walk reaches it only when the current model
        has no machine left -- which happens once the fleet is larger
        than the model's machine space.

        The pool is built once per allocation and cached on the guard,
        because the catalogue is large and the walk may ask for it
        again after every model switch.
        """

        if self._preset_pool:
            own = candidate.seed_config.preset_id
            if own and own in self._preset_pool:
                return (own, *(p for p in self._preset_pool if p != own))
            return self._preset_pool

        if not self._cached_pool:
            from .devices import all_presets

            self._cached_pool = tuple(preset.preset_id for preset in all_presets())
        own = candidate.seed_config.preset_id
        if own and own in self._cached_pool:
            return (own, *(p for p in self._cached_pool if p != own))
        return self._cached_pool

    def _next_model(self, pool: tuple[str, ...], current: str) -> str | None:
        """The next model of the pool, or ``None`` when the pool is done.

        A model that the walk has already spent is skipped.  The walk
        has to be a walk: a model whose machines are all taken cannot
        hand out another machine, and coming back to it would only land
        on one that is already taken.
        """

        if current not in pool:
            for preset_id in pool:
                if preset_id not in self._spent_models:
                    return preset_id
            return None

        index = pool.index(current) + 1
        while index < len(pool):
            candidate = pool[index]
            if candidate not in self._spent_models:
                return candidate
            index += 1
        return None

    def check_collision(
        self, config: ProfileConfig, profile_id: str | None = None
    ) -> tuple[SignatureKind, str, str | None] | None:
        """Report the first collision of ``config`` without allocating."""
        return self._find_conflict(config, profile_id)

    def commit(self, config: ProfileConfig, profile_id: str | None = None) -> None:
        """Record the signatures of an allocated profile.

        Call this **after** the profile itself has been persisted: the
        signature rows reference the profile row, so they cannot be written
        first.

        Parameters
        ----------
        config:
            The profile returned by :meth:`allocate`.
        profile_id:
            The id to record the signatures under.  Defaults to the profile's
            own id.
        """
        self._record(config, profile_id or config.identity.id)

    def allocate_and_commit(self, profile_id: str, candidate: ProfileConfig) -> ProfileConfig:
        """Allocate and immediately commit.

        Only usable with a registry that does not enforce a foreign key on
        the profile row -- for example the in-memory registry used by the
        tests.  With the persistent store, use :meth:`allocate` followed by
        :meth:`commit` instead.
        """
        working = self.allocate(profile_id, candidate)
        self.commit(working, profile_id)
        return working

    def signatures_for(self, config: ProfileConfig) -> dict[SignatureKind, str]:
        """All guarded signatures of a profile."""
        return self._compute_signatures(config)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rebuild(
        self,
        candidate: ProfileConfig,
        seed_config: SeedConfig,
        nudge_step: int,
        epoch: int,
        stratum: int | None = None,
        preset_id: str | None = None,
        geo_index: int | None = None,
    ) -> ProfileConfig:
        """Rebuild a profile with a new seed configuration.

        The whole profile is regenerated, not just the axes that the guard
        checks.  Regenerating only the guarded axes would leave the device
        identity in place: a rolled seed would still report the *old* serial,
        build fingerprint, screen panel and locale, so the new profile would
        be a different fingerprint attached to the same physical device --
        exactly the link the guard is supposed to prevent.

        The parts that belong to the operator rather than to the seed (the
        profile's name, its proxy and its browser version) are carried over.
        """
        updated_seed = seed_config.model_copy(
            update={"nudge_step": nudge_step, "seed_epoch": epoch, "stratum": stratum}
        )
        target_preset = preset_id or seed_config.preset_id or candidate.seed_config.preset_id
        if target_preset:
            updated_seed = updated_seed.model_copy(update={"preset_id": target_preset})

        # The geographic anchor.  When the walk has moved the anchor, the
        # rebuild is given the new place on the earth, so that the profile
        # does not merely carry a different fingerprint for the same
        # person but is a different person.
        geo = None
        geo_province = None
        if geo_index is not None:
            geo = self._geo_target(geo_index)
            # The Vietnamese fleet places its profiles in the provinces,
            # so the walk moves a profile from one province to the next
            # rather than from one country to the next.
            geo_province = geo_index

        # Nothing to do when the requested configuration is the one the
        # candidate already carries.  The comparison is against the
        # *candidate's* own seed configuration, because ``seed_config`` is
        # the configuration the guard is moving towards -- after a seed roll
        # it already differs from the candidate.
        if (
            candidate.seed_config.seed_epoch == epoch
            and candidate.seed_config.nudge_step == nudge_step
            and candidate.seed_config.master_seed == updated_seed.master_seed
            and candidate.seed_config.stratum == stratum
            and candidate.seed_config.preset_id == updated_seed.preset_id
            and geo is None
        ):
            return candidate

        if not target_preset:
            # Without a preset the profile cannot be regenerated; fall back
            # to re-deriving only the axes that the guard checks.
            return self._rebuild_axes_only(candidate, updated_seed)

        generator = self._generator_factory()
        try:
            rebuilt = generator.generate(
                target_preset,
                name=candidate.identity.name,
                master_seed=updated_seed.master_seed,
                seed_epoch=updated_seed.seed_epoch,
                nudge_step=updated_seed.nudge_step,
                stratum=updated_seed.stratum,
                proxy=candidate.proxy if candidate.proxy.enabled else None,
                profile_id=candidate.identity.id,
                browser_version=candidate.browser_version,
                geo=geo,
                geo_province=geo_province,
            )
        except KeyError:
            # The preset disappeared from the catalogue between the original
            # generation and the rebuild; keep the old behaviour rather than
            # failing the allocation.
            return self._rebuild_axes_only(candidate, updated_seed)

        # The geo anchor is part of the operator's intent when it was set
        # explicitly; the generator picks one from the seed otherwise.
        return rebuilt.model_copy(update={"identity": candidate.identity})

    def _geo_target(self, index: int):
        """The geo target of an entry of the catalogue.

        The fleet is Vietnamese, so the anchor is Vietnam and the index
        selects the *province* the profile sits in.
        """

        from .generator import _geo_target as make_target
        from .geo_vietnam import (
            VIETNAM_COORDINATES,
            VIETNAM_CURRENCY,
            VIETNAM_LOCALE,
            VIETNAM_TIMEZONE,
        )

        if not VIETNAM_COORDINATES:
            return None
        return make_target(
            VIETNAM_LOCALE,
            VIETNAM_TIMEZONE,
            "VN",
            VIETNAM_CURRENCY,
            "h23",
            ("vi", "en"),
        )

    def _rebuild_axes_only(
        self, candidate: ProfileConfig, updated_seed: SeedConfig
    ) -> ProfileConfig:
        """Re-derive only the seed-derived axes of a profile.

        This is the fallback for a profile whose preset is no longer
        available.  It cannot change the device identity, so it is only
        used when regeneration is impossible.
        """
        deriver = self._deriver_factory(updated_seed)

        renderer = candidate.webgl.unmasked_renderer
        image_value = deriver.derive_webgl_image_value(renderer)

        webgl = candidate.webgl.model_copy(
            update={
                "image_noise": candidate.webgl.image_noise.model_copy(update={"value": image_value})
            }
        )

        canvas = candidate.canvas_2d.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.CANVAS_2D, 16, context="salt").hex(),
                "noise_strength": round(
                    deriver.derive_float(Axis.CANVAS_2D, 0.0, 1.0, context="strength"),
                    6,
                ),
            }
        )

        # The perturbation has to actually perturb something: a profile
        # whose geometry is declared as perturbed but reports all-zero
        # deltas disagrees with itself, and the guard has to be able to
        # move it.
        rect_deltas: list[int] = []
        for context in ("dx", "dy", "dw", "dh"):
            rect_deltas.append(deriver.derive_int(Axis.RECTS, -1, 1, context=context))
        if all(delta == 0 for delta in rect_deltas):
            index = 0
            while all(delta == 0 for delta in rect_deltas):
                index += 1
                rect_deltas[index % 4] = deriver.derive_int(
                    Axis.RECTS, -1, 1, context=f"retry-{index}"
                )

        rects = candidate.rects.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.RECTS, 16, context="salt").hex(),
                "delta_x": rect_deltas[0],
                "delta_y": rect_deltas[1],
                "delta_width": rect_deltas[2],
                "delta_height": rect_deltas[3],
            }
        )

        audio = candidate.audio.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.AUDIO, 16, context="salt").hex(),
                "sample_rate": deriver.derive_choice(
                    Axis.AUDIO, (48000, 44100), context="sample-rate"
                ),
                "base_latency": deriver.derive_choice(
                    Axis.AUDIO,
                    (0.005333333333333333, 0.01, 0.010666666666666666),
                    context="base-latency",
                ),
                "output_latency": deriver.derive_choice(
                    Axis.AUDIO,
                    (0.0, 0.02, 0.04, 0.2),
                    context="output-latency",
                ),
                "channel_count": deriver.derive_choice(Axis.AUDIO, (1, 2), context="channels"),
            }
        )

        media_devices = candidate.media_devices.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.MEDIA_DEVICES, 16, context="salt").hex(),
            }
        )

        # The sensors, the storage estimate, the WebGPU adapter and the font
        # list are guarded axes in their own right, so they have to move
        # together with the rest.  Leaving them in place would make a
        # collision on any of them unresolvable.
        #
        # The salt of each surface is the part that carries the
        # uniqueness -- the gravity reading is about 9.81 on every
        # device on Earth and the quota is one of a handful of round
        # numbers -- so the salt has to be re-derived too, or the
        # rebuilt profile collides with the one it is escaping.
        gravity_z = round(
            9.80665 + deriver.derive_float(Axis.SENSORS, -0.04, 0.04, "gravity"),
            6,
        )
        motion_profile = deriver.derive_choice(
            Axis.SENSORS,
            ("stationary_micro_jitter", "walking_subtle"),
            context="motion",
        )
        jitter = 0.01 if motion_profile == "stationary_micro_jitter" else 0.08
        linear_x = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "lx"), 6)
        linear_y = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "ly"), 6)
        linear_z = round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "lz"), 6)
        sensors = candidate.sensors.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.SENSORS, 16, context="salt").hex(),
                "motion_profile": motion_profile,
                "sampling_hz": deriver.derive_choice(Axis.SENSORS, (60, 100, 200), context="hz"),
                "accelerometer": candidate.sensors.accelerometer.model_copy(
                    update={
                        "x": linear_x,
                        "y": linear_y,
                        "z": round(gravity_z + linear_z, 6),
                    }
                ),
                "gyroscope": candidate.sensors.gyroscope.model_copy(
                    update={
                        "x": round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gx"), 6),
                        "y": round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gy"), 6),
                        "z": round(deriver.derive_float(Axis.SENSORS, -jitter, jitter, "gz"), 6),
                    }
                ),
                "linear_acceleration": candidate.sensors.linear_acceleration.model_copy(
                    update={"x": linear_x, "y": linear_y, "z": linear_z}
                ),
                "gravity": candidate.sensors.gravity.model_copy(update={"z": gravity_z}),
            }
        )

        quota = deriver.derive_choice(
            Axis.STORAGE_QUOTA,
            (
                3_221_225_472,
                6_442_450_944,
                12_884_901_888,
                25_769_803_776,
            ),
            context="quota",
        )
        storage_network = candidate.storage_network.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.STORAGE_QUOTA, 16, context="salt").hex(),
                "quota_bytes": quota,
                "usage_bytes": int(
                    quota * deriver.derive_float(Axis.STORAGE_QUOTA, 0.01, 0.45, "usage")
                ),
            }
        )

        webgpu = candidate.webgpu.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.WEBGPU, 16, context="salt").hex(),
            }
        )

        fonts = candidate.fonts.model_copy(
            update={
                "salt": deriver.derive_bytes(Axis.FONTS, 16, context="salt").hex(),
            }
        )

        # The device identity cannot be regenerated without the preset, so
        # the serial and the incremental build number are re-derived
        # directly from the seed.  That keeps the device axis resolvable: a
        # rolled seed yields a different serial *and* a different
        # fingerprint, which is what the guard needs to escape a collision.
        incremental = str(
            deriver.derive_int(Axis.ANDROID_BUILD, 0, 2**64 - 1, context="incremental")
        )
        build = candidate.android_build.model_copy(
            update={
                "serial": _derive_serial(deriver),
                "version_incremental": incremental,
                "display": f"{candidate.android_build.build_id}.{incremental}",
            }
        )
        build = build.model_copy(
            update={
                "fingerprint": _rewrite_fingerprint(build),
            }
        )

        return candidate.model_copy(
            update={
                "seed_config": updated_seed,
                "android_build": build,
                "webgl": webgl,
                "canvas_2d": canvas,
                "rects": rects,
                "audio": audio,
                "media_devices": media_devices,
                "sensors": sensors,
                "storage_network": storage_network,
                "webgpu": webgpu,
                "fonts": fonts,
            }
        )

    def _compute_signatures(self, config: ProfileConfig) -> dict[SignatureKind, str]:
        """Hash the per-axis signature inputs."""
        parts = config.signature_parts()
        signatures: dict[SignatureKind, str] = {}

        for kind, parts_key in _AXIS_TO_PARTS_KEY.items():
            values = parts.get(parts_key, [])
            signatures[kind] = _hash_signature(kind.value, values)

        signatures[SignatureKind.FULL] = _hash_signature(
            SignatureKind.FULL.value, config.full_signature_parts()
        )
        return signatures

    def _find_conflict(
        self, config: ProfileConfig, profile_id: str | None
    ) -> tuple[SignatureKind, str, str | None] | None:
        """Return the first colliding signature, or ``None``."""
        signatures = self._compute_signatures(config)
        for kind in GUARDED_AXES:
            signature = signatures[kind]
            existing = self._registry.find_signature(kind, signature)
            if existing is None:
                continue
            # A profile does not collide with itself.
            if profile_id is not None and existing.profile_id == profile_id:
                continue
            return kind, signature, existing.profile_id
        return None

    def _record(self, config: ProfileConfig, profile_id: str) -> None:
        """Write the winning signatures into the registry."""
        signatures = self._compute_signatures(config)
        seed = config.seed_config
        for kind, signature in signatures.items():
            self._registry.record_signature(
                SignatureRecord(
                    kind=kind,
                    signature=signature,
                    profile_id=profile_id,
                    seed_epoch=seed.seed_epoch,
                    nudge_step=seed.nudge_step,
                    state=SignatureState.ACTIVE,
                )
            )


class ConsistencyRejectedError(RuntimeError):
    """Raised when a candidate profile is internally inconsistent."""

    def __init__(self, profile_id: str, report) -> None:
        self.profile_id = profile_id
        self.report = report
        detail = "; ".join(f"[{i.axis}] {i.message}" for i in report.errors)
        super().__init__(
            f"profile {profile_id} is internally inconsistent and was rejected: {detail}"
        )


def _machine_of(profile: ProfileConfig) -> tuple:
    """The hardware identity of a profile.

    The same values the machine signature is built from, read straight
    off the profile.  The guard uses this to tell whether two attempts
    of an allocation produced the *same* machine -- and a machine that
    has been seen before is a machine that has been refused, so the
    walk has to move on.
    """

    return (
        profile.webgl.unmasked_renderer,
        profile.android_build.model,
        profile.screen.width,
        profile.screen.height,
        profile.screen.device_pixel_ratio,
        profile.navigator.hardware_concurrency,
        profile.navigator.device_memory,
        profile.navigator.max_touch_points,
        profile.screen.color_depth,
    )


def _hash_signature(kind: str, values: Iterable[str]) -> str:
    """A stable SHA-256 signature over an axis' values."""
    material = "\x1f".join([kind, *[str(v) for v in values]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _derive_serial(deriver: SeedDeriver) -> str:
    """A 64-bit device serial derived from the seed.

    This mirrors :meth:`novaprofile.core.identity.DeviceIdentityFactory._serial`
    exactly: the same seed must produce the same serial whether the profile
    was regenerated from its preset or re-derived without one.
    """

    raw = deriver.derive_bytes(Axis.ANDROID_BUILD, 8, context="serial")
    value = int.from_bytes(raw, "big") | (1 << 63)
    return f"{value:016x}"


def _rewrite_fingerprint(build: Any) -> str:
    """Rebuild the build fingerprint around a new incremental number.

    The fingerprint's second segment carries the build id and the
    incremental number, so the segment is regenerated from the seed as
    well.  The third segment -- the numeric build id -- is kept as it is:
    it belongs to the device family and has to stay stable for the preset
    to remain coherent.
    """

    head, _build_segment, build_id_number, tail = build.fingerprint.split(":")
    return (
        f"{head}:{build.version_release}/{build.build_id}"
        f".{build.version_incremental}:{build_id_number}:{tail}"
    )


__all__ = [
    "SignatureRecord",
    "SignatureRegistry",
    "InMemorySignatureRegistry",
    "CollisionGuard",
    "ProfileCollisionError",
    "ConsistencyRejectedError",
    "GUARDED_AXES",
]
