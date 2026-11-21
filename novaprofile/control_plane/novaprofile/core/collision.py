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

    def __init__(
        self,
        registry: SignatureRegistry,
        resolver: ConsistencyResolver | None = None,
        deriver_factory: Callable[[SeedConfig], SeedDeriver] | None = None,
        master_seed_factory: Callable[[], str] | None = None,
        generator_factory: Callable[[], ProfileGenerator] | None = None,
    ) -> None:
        self._registry = registry
        self._resolver = resolver or ConsistencyResolver()
        self._deriver_factory = deriver_factory or (lambda cfg: cfg.deriver())
        self._master_seed_factory = master_seed_factory or new_master_seed
        self._generator_factory = generator_factory or _default_generator_factory

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
        last_conflict: tuple[SignatureKind, str, str | None] | None = None

        for _attempt in range(self.MAX_ATTEMPTS):
            working = self._rebuild(candidate, seed_config, nudge_step, epoch)

            # Re-validate: a nudge must not break the internal consistency.
            report = self._resolver.check(working)
            if report.errors:
                raise ConsistencyRejectedError(profile_id, report)

            conflict = self._find_conflict(working, profile_id)
            if conflict is None:
                return working

            last_conflict = conflict
            kind, signature, other_id = conflict

            # Stage 1: nudge.  Move along the device family.
            if nudge_step < self.MAX_NUDGE_STEPS:
                nudge_step += 1
                continue

            # Stage 2: roll the seed.  Become a completely new device.
            epoch += 1
            nudge_step = 0
            seed_config = seed_config.with_epoch(epoch, self._master_seed_factory())

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
            update={"nudge_step": nudge_step, "seed_epoch": epoch}
        )

        # Nothing to do when the requested configuration is the one the
        # candidate already carries.  The comparison is against the
        # *candidate's* own seed configuration, because ``seed_config`` is
        # the configuration the guard is moving towards -- after a seed roll
        # it already differs from the candidate.
        if (
            candidate.seed_config.seed_epoch == epoch
            and candidate.seed_config.nudge_step == nudge_step
            and candidate.seed_config.master_seed == updated_seed.master_seed
        ):
            return candidate

        preset_id = seed_config.preset_id or candidate.seed_config.preset_id
        if not preset_id:
            # Without a preset the profile cannot be regenerated; fall back
            # to re-deriving only the axes that the guard checks.
            return self._rebuild_axes_only(candidate, updated_seed)

        generator = self._generator_factory()
        try:
            rebuilt = generator.generate(
                preset_id,
                name=candidate.identity.name,
                master_seed=updated_seed.master_seed,
                seed_epoch=updated_seed.seed_epoch,
                nudge_step=updated_seed.nudge_step,
                proxy=candidate.proxy if candidate.proxy.enabled else None,
                profile_id=candidate.identity.id,
                browser_version=candidate.browser_version,
            )
        except KeyError:
            # The preset disappeared from the catalogue between the original
            # generation and the rebuild; keep the old behaviour rather than
            # failing the allocation.
            return self._rebuild_axes_only(candidate, updated_seed)

        # The geo anchor is part of the operator's intent when it was set
        # explicitly; the generator picks one from the seed otherwise.
        return rebuilt.model_copy(update={"identity": candidate.identity})

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
