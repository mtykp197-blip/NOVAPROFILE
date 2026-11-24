"""The fleet must not collide, and the draw must be the same on both sides.

The product exists to make a hundred thousand profiles that a site
cannot group.  Two properties carry that promise, and this module holds
them to it:

1. **No two units of one family agree.**  The narrow readings -- the
   core count, the memory, the touch points -- are drawn *stratified*,
   so two handsets of the same model report different ones.

2. **The core and the control plane agree.**  The core draws the same
   value the control plane draws, from the same seed, with the same
   algorithm.  A core that answered differently would make every probe
   report a mismatch, and the report would say the whole engine is
   broken.

The second property is the one that is easy to break: the algorithm
lives in two languages, and a change in one of them is a silent change
in the other's answers.  The test below therefore re-derives the core's
answer in Python, from the core's own algorithm, and compares.
"""

from __future__ import annotations

import hashlib
import hmac
import unittest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import DOMAIN_SALTS, Axis, SeedDeriver


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def _core_draw_int(
    master_seed: str,
    epoch: int,
    nudge: int,
    stratum: int | None,
    context: str,
    low: int,
    high: int,
) -> int:
    """The core's own draw, reproduced in Python.

    This is a transcription of ``nova_unit_values.cc``: the salt, the
    info string, the rejection sampling.  When the two sides disagree,
    one of the two is wrong, and the comparison below says which.
    """

    salt = DOMAIN_SALTS[Axis.NAVIGATOR]
    prk = _hkdf_extract(salt, bytes.fromhex(master_seed))

    info_parts = [
        b"navigator",
        f"epoch={epoch}".encode(),
        f"nudge={nudge}".encode(),
    ]
    if stratum is not None:
        info_parts.append(f"stratum={stratum}".encode())
    info = b"|".join(info_parts) + b"|" + context.encode()

    span = high - low + 1
    if span == 1:
        return low

    n_bytes = 1
    while (1 << (n_bytes * 8)) < span:
        n_bytes += 1
    ceiling = 1 << (n_bytes * 8)
    limit = ceiling - (ceiling % span)

    attempt = 0
    retry = 0
    while True:
        attempt_context = f"{context}|draw={attempt}|retry={retry}"
        attempt_info = info[: -len(context.encode())] + attempt_context.encode()
        block = _hkdf_expand(prk, attempt_info, n_bytes)
        value = int.from_bytes(block, "big")
        if value < limit:
            return low + (value % span)
        retry += 1
        attempt += 1


class UnitDrawTest(unittest.TestCase):
    """The core and the control plane have to answer alike.

    The two sides only have to agree on the *draw* itself.  When the
    profile carries the value -- which is the normal case, because the
    control plane always draws it -- the core serves it unchanged and
    there is nothing to compare.  The draw is compared in the case the
    profile leaves the value out: there the core draws it itself, and
    it has to draw the control plane's number.
    """

    def test_the_core_draw_matches_the_python_draw(self) -> None:
        generator = ProfileGenerator()
        for _ in range(8):
            # A profile with no stratum: the control plane draws each
            # reading on its own, and so does the core.
            profile = generator.generate(
                "pixel-8-pro-android14",
                master_seed="ab" * 32,
            )
            seed = profile.seed_config
            self.assertIsNone(seed.stratum)

            cores = profile.navigator.hardware_concurrency_options
            memory = profile.navigator.device_memory_options
            touch = profile.navigator.max_touch_points_options
            if not cores or not memory or not touch:
                self.skipTest("the preset carries no hardware range")

            core_cores = _core_draw_int(
                seed.master_seed,
                seed.seed_epoch,
                seed.nudge_step,
                seed.stratum,
                "cores",
                0,
                len(cores) - 1,
            )
            self.assertEqual(
                cores[core_cores],
                profile.navigator.hardware_concurrency,
                "the core would report a different core count",
            )

            core_memory = _core_draw_int(
                seed.master_seed,
                seed.seed_epoch,
                seed.nudge_step,
                seed.stratum,
                "memory",
                0,
                len(memory) - 1,
            )
            self.assertEqual(
                memory[core_memory],
                profile.navigator.device_memory,
                "the core would report a different memory",
            )

            core_touch = _core_draw_int(
                seed.master_seed,
                seed.seed_epoch,
                seed.nudge_step,
                seed.stratum,
                "touch",
                0,
                len(touch) - 1,
            )
            self.assertEqual(
                touch[core_touch],
                profile.navigator.max_touch_points,
                "the core would report a different touch count",
            )


class FleetUniquenessTest(unittest.TestCase):
    """The units of one family must not agree.

    The signature is the set of readings a site groups on: the model,
    the core count, the memory, the touch points and the panel.  Two
    units of the fleet that agree on all of them are one device to that
    site, whatever their canvases and their serials say.

    The panel is in the signature because the resolution is one of the
    readings, and because it is the one that carries the most values:
    a line that was sold with more than one display gives the fleet a
    dimension that costs nothing in truthfulness.
    """

    def test_the_units_of_one_family_report_different_readings(self) -> None:
        from novaprofile.core.fleet import generate_fleet

        profiles = generate_fleet(count=40, vietnam=True)
        self.assertGreaterEqual(len(profiles), 20)

        signatures = [
            (
                p.android_build.model,
                p.android_build.version_release,
                p.webgl.unmasked_renderer,
                p.navigator.hardware_concurrency,
                p.navigator.device_memory,
                p.navigator.max_touch_points,
                p.screen.width,
                p.screen.height,
                p.screen.device_pixel_ratio,
            )
            for p in profiles
        ]
        self.assertEqual(
            len(set(signatures)),
            len(signatures),
            "two units of the fleet report the same narrow readings",
        )


if __name__ == "__main__":
    unittest.main()
