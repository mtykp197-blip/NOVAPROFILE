"""The deterministic core of the NOVA profile engine.

* :mod:`novaprofile.core.seed` -- HKDF-SHA256 seed derivation.
* :mod:`novaprofile.core.devices` -- the real Android device catalog.
* :mod:`novaprofile.core.consistency` -- cross-layer consistency resolver.
* :mod:`novaprofile.core.collision` -- the multi-layer collision guard.
"""

from __future__ import annotations

from .seed import Axis, SeedDeriver, SeedError, new_master_seed, new_uuid7

__all__ = ["Axis", "SeedDeriver", "SeedError", "new_master_seed", "new_uuid7"]
