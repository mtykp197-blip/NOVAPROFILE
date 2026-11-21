"""The collision and persistence test suite.

This is the module the specification asks for by name.  It proves the two
survival criteria of the system:

* **Distinct ratio = 100%** -- no two profiles ever share a fingerprint.
* **Persistence = True** -- a profile's fingerprint survives an arbitrary
  number of restarts unchanged.

The tests run against the in-memory signature registry so that they are
deterministic and need neither a browser nor a network.
"""

from __future__ import annotations

import pytest

from novaprofile.core.collision import (
    GUARDED_AXES,
    CollisionGuard,
    InMemorySignatureRegistry,
    ProfileCollisionError,
)
from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import new_master_seed
from novaprofile.schema.enums import SignatureKind

PRESET_IDS = [preset.preset_id for preset in DEVICE_PRESETS]


@pytest.fixture()
def generator() -> ProfileGenerator:
    return ProfileGenerator()


@pytest.fixture()
def registry() -> InMemorySignatureRegistry:
    return InMemorySignatureRegistry()


@pytest.fixture()
def guard(registry: InMemorySignatureRegistry) -> CollisionGuard:
    return CollisionGuard(registry, ConsistencyResolver())


class TestDistinctRatio:
    """The 100% distinct-ratio criterion."""

    def test_ten_profiles_are_all_distinct(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        allocated = []
        for index in range(10):
            preset = PRESET_IDS[index % len(PRESET_IDS)]
            candidate = generator.generate(preset, name=f"p{index}")
            allocated.append(guard.allocate_and_commit(candidate.identity.id, candidate))

        signatures = []
        for profile in allocated:
            computed = guard.signatures_for(profile)
            full = next(k for k in computed if k is SignatureKind.FULL)
            signatures.append(computed[full])

        assert len(set(signatures)) == len(signatures)
        assert len(set(signatures)) / len(signatures) == 1.0

    def test_large_batch_has_zero_collisions(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        """A batch of 500 profiles must be allocated without a single clash."""
        count = 500
        allocated = []
        for index in range(count):
            preset = PRESET_IDS[index % len(PRESET_IDS)]
            candidate = generator.generate(preset, name=f"p{index}")
            allocated.append(guard.allocate_and_commit(candidate.identity.id, candidate))

        assert len(allocated) == count
        # Only the guarded axes are checked: the navigator surface is
        # deliberately excluded because it is shared by every unit of a
        # device family and by every device running the same browser
        # build, exactly as on real hardware.
        for kind in GUARDED_AXES:
            values = {guard.signatures_for(profile)[kind] for profile in allocated}
            assert len(values) == count, f"{kind.value} collided in a batch of {count}"

    def test_same_seed_yields_the_same_fingerprint(self, generator: ProfileGenerator) -> None:
        """Determinism: the same seed always produces the same device."""
        seed = new_master_seed()
        first = generator.generate(PRESET_IDS[0], name="a", master_seed=seed)
        second = generator.generate(PRESET_IDS[0], name="b", master_seed=seed)
        assert first.webgl.image_noise.value == second.webgl.image_noise.value
        assert first.canvas_2d.salt == second.canvas_2d.salt
        assert first.navigator.user_agent == second.navigator.user_agent

    def test_different_seeds_yield_different_fingerprints(
        self, generator: ProfileGenerator
    ) -> None:
        first = generator.generate(PRESET_IDS[0], name="a", master_seed=new_master_seed())
        second = generator.generate(PRESET_IDS[0], name="b", master_seed=new_master_seed())
        assert first.webgl.image_noise.value != second.webgl.image_noise.value
        assert first.canvas_2d.salt != second.canvas_2d.salt


class TestPersistence:
    """The persistence criterion: the fingerprint never changes."""

    def test_five_restarts_keep_the_same_signature(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        candidate = generator.generate(PRESET_IDS[0], name="persist")
        profile = guard.allocate(candidate.identity.id, candidate)

        baseline = guard.signatures_for(profile)
        for _ in range(5):
            # A restart re-reads the profile from storage: the same seed and
            # the same epoch, so the same signature must come back.
            reloaded = generator.generate(
                PRESET_IDS[0],
                name="persist",
                master_seed=profile.seed_config.master_seed,
                seed_epoch=profile.seed_config.seed_epoch,
                nudge_step=profile.seed_config.nudge_step,
            )
            assert guard.signatures_for(reloaded) == baseline

    def test_the_webgl_value_survives_a_restart(self, generator: ProfileGenerator) -> None:
        seed = new_master_seed()
        first = generator.generate(PRESET_IDS[0], name="p", master_seed=seed)
        second = generator.generate(PRESET_IDS[0], name="p", master_seed=seed)
        assert first.webgl.image_noise.value == second.webgl.image_noise.value

    def test_rolling_the_seed_changes_the_fingerprint(self, generator: ProfileGenerator) -> None:
        seed = new_master_seed()
        first = generator.generate(PRESET_IDS[0], name="p", master_seed=seed)
        second = generator.generate(
            PRESET_IDS[0], name="p", master_seed=new_master_seed(), seed_epoch=1
        )
        assert first.webgl.image_noise.value != second.webgl.image_noise.value


class TestCollisionGuardEscalation:
    """The nudge -> roll seed -> reject escalation."""

    def test_a_duplicate_seed_is_resolved_by_nudging(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        """A second profile with the same seed must not be handed out as-is."""
        seed = new_master_seed()
        first = generator.generate(PRESET_IDS[0], name="first", master_seed=seed)
        allocated_first = guard.allocate_and_commit(first.identity.id, first)

        second = generator.generate(PRESET_IDS[0], name="second", master_seed=seed)
        allocated_second = guard.allocate_and_commit(second.identity.id, second)

        assert allocated_first.seed_config.master_seed == seed
        assert allocated_second.seed_config.master_seed == seed
        # The guard must have moved the profile along one of its axes.
        assert (
            allocated_second.seed_config.nudge_step != allocated_first.seed_config.nudge_step
            or allocated_second.seed_config.seed_epoch != allocated_first.seed_config.seed_epoch
        )
        assert allocated_second.webgl.image_noise.value != allocated_first.webgl.image_noise.value

    def test_the_registry_rejects_the_reject_path(self, generator: ProfileGenerator) -> None:
        """When every attempt fails the guard must refuse, never duplicate."""
        from novaprofile.core.collision import SignatureRecord

        seed = new_master_seed()

        class _AlwaysConflicts(InMemorySignatureRegistry):
            def find_signature(self, kind, signature):  # type: ignore[no-untyped-def]
                return SignatureRecord(
                    kind=kind,
                    signature=signature,
                    profile_id="someone-else",
                    seed_epoch=0,
                    nudge_step=0,
                )

        strict = CollisionGuard(_AlwaysConflicts(), ConsistencyResolver())
        duplicate = generator.generate(PRESET_IDS[0], name="dup2", master_seed=seed)
        with pytest.raises(ProfileCollisionError) as excinfo:
            strict.allocate(duplicate.identity.id, duplicate)
        assert excinfo.value.attempts == CollisionGuard.MAX_ATTEMPTS
        assert excinfo.value.profile_id == duplicate.identity.id

    def test_the_guard_never_returns_a_known_collision(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        for index in range(50):
            candidate = generator.generate(PRESET_IDS[index % len(PRESET_IDS)], name=f"p{index}")
            allocated = guard.allocate_and_commit(candidate.identity.id, candidate)
            assert guard.check_collision(allocated, allocated.identity.id) is None

    def test_a_profile_does_not_collide_with_itself(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        candidate = generator.generate(PRESET_IDS[0], name="self")
        allocated = guard.allocate_and_commit(candidate.identity.id, candidate)
        assert guard.check_collision(allocated, allocated.identity.id) is None


class TestCollisionRate:
    """The measured collision rate must be exactly zero."""

    def test_collision_rate_is_zero_over_a_batch(
        self, generator: ProfileGenerator, guard: CollisionGuard
    ) -> None:
        count = 200
        signatures = []
        for index in range(count):
            candidate = generator.generate(PRESET_IDS[index % len(PRESET_IDS)], name=f"p{index}")
            allocated = guard.allocate_and_commit(candidate.identity.id, candidate)
            computed = guard.signatures_for(allocated)
            full = next(k for k in computed if k is SignatureKind.FULL)
            signatures.append(computed[full])

        distinct = len(set(signatures))
        collision_rate = (count - distinct) / count
        assert collision_rate == 0.0
        assert distinct / count == 1.0
