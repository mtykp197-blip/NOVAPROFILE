"""Parallel scale check for the 100-million-profile target.

Generates a large fleet across every CPU and verifies that **no two
profiles share anything a site can read**: the device identity, every
rendering axis and the composite signature.

What is checked
---------------

Every guarded axis of :class:`novaprofile.core.collision.CollisionGuard`
is verified independently, so a collision on *any* of them is reported:

``sig_device``
    The serial, the build fingerprint, the bootloader and the panel.  Two
    profiles that share this are the same physical device as far as a site
    is concerned.
``sig_canvas``, ``sig_webgl_draw``, ``sig_webgl_params``, ``sig_rects``,
``sig_devices``, ``sig_audio``
    The rendering and hardware-sampling axes.
``sig_sensors``, ``sig_storage``, ``sig_webgpu``, ``sig_fonts``
    The surfaces a site can read on their own: the motion sensors, the
    storage estimate and network information, the WebGPU adapter and the
    installed font list.
``sig_full``
    The composite signature over every observable value.

Memory
------

A hundred million profiles cannot be held as Python objects: the fleet
would need tens of gigabytes.  Each axis is therefore reduced to a digest
of ``--digest-bits`` bits, kept in a plain list of integers and sorted
before the duplicate run lengths are read off it.  The whole check needs
about 40 bytes per profile per axis -- 32 GB for a fleet of 100 million
across the eight axes -- so the axes are counted one after the other and
the working set is released between them.

A 64-bit digest over a fleet of 10^8 carries a birthday-collision
probability of about 2.7e-4, so a reported collision is a *candidate*
rather than a proof; the operator can confirm it with the full 256-bit
digest through the collision guard itself.

Run it directly::

    python -m novaprofile.scripts.scale_check --count 200000
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import os
import time
from array import array
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor

from novaprofile.core.collision import GUARDED_AXES
from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator

#: The axis keys of :meth:`ProfileConfig.signature_parts`, keyed by the
#: signature kind they feed.
_AXIS_PARTS_KEY: dict[str, str] = {
    "sig_canvas": "sig_canvas",
    "sig_webgl_draw": "sig_webgl_draw",
    "sig_webgl_params": "sig_webgl_params",
    "sig_rects": "sig_rects",
    "sig_devices": "sig_devices",
    "sig_audio": "sig_audio",
    "sig_device": "sig_device",
    "sig_sensors": "sig_sensors",
    "sig_storage": "sig_storage",
    "sig_webgpu": "sig_webgpu",
    "sig_fonts": "sig_fonts",
}

_PRESETS: list[str] = []
_DIGEST_BITS = 64


def _init(digest_bits: int = 64) -> None:
    """Warm the preset catalogue in each worker process."""
    global _PRESETS, _DIGEST_BITS
    _PRESETS = [p.preset_id for p in list_presets()]
    _DIGEST_BITS = digest_bits


def _axis_digest(kind: str, values: list[str]) -> int:
    """Return the truncated digest of one axis' signature material."""
    material = "\x1f".join([kind, *[str(v) for v in values]])
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[: _DIGEST_BITS // 8], "big")


def _chunk(args: tuple[int, int]) -> dict[str, array]:
    """Generate one slice of the fleet.

    Returns one packed digest array per guarded axis.  The arrays are the
    compact representation: eight bytes per profile per axis instead of
    the forty a Python integer would need, which is what makes a fleet of
    a hundred million profiles fit into the machine's memory.
    """
    start, count = args
    generator = ProfileGenerator()

    buckets: dict[str, array] = {kind.value: array("Q") for kind in GUARDED_AXES}
    for index in range(start, start + count):
        preset = _PRESETS[index % len(_PRESETS)]
        profile = generator.generate(preset, master_seed=f"{index:064x}")
        parts = profile.signature_parts()
        for kind in GUARDED_AXES:
            if kind.value == "sig_full":
                values = profile.full_signature_parts()
            else:
                values = parts.get(_AXIS_PARTS_KEY[kind.value], [])
            buckets[kind.value].append(_axis_digest(kind.value, values))
    return buckets


def _chunks(total: int, workers: int) -> Iterable[tuple[int, int]]:
    """Split the fleet into one slice per worker."""
    per = total // workers
    remainder = total % workers
    start = 0
    for worker in range(workers):
        size = per + (1 if worker < remainder else 0)
        yield start, size
        start += size


def _duplicate_count(digests: list[int]) -> int:
    """Return how many entries of a digest list are duplicates."""
    if not digests:
        return 0
    digests.sort()
    duplicates = 0
    previous = digests[0]
    for value in digests[1:]:
        if value == previous:
            duplicates += 1
        else:
            previous = value
    return duplicates


def run(count: int, workers: int, digest_bits: int = 64) -> dict[str, float | int]:
    """Generate ``count`` profiles in parallel and report the collision rate.

    Every guarded axis is checked; the report contains the number of
    collisions per axis.
    """
    started = time.time()
    buckets: dict[str, array] = {}

    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init, initargs=(digest_bits,)
    ) as pool:
        for result in pool.map(_chunk, _chunks(count, workers)):
            for axis, digests in result.items():
                buckets.setdefault(axis, array("Q")).extend(digests)

    elapsed = time.time() - started

    report: dict[str, float | int] = {
        "count": count,
        "workers": workers,
        "digest_bits": digest_bits,
        "elapsed_s": round(elapsed, 1),
        "profiles_per_s": round(count / elapsed, 1) if elapsed else 0.0,
        "axes_checked": len(buckets),
    }

    total_collisions = 0
    for axis in sorted(buckets):
        digests = buckets.pop(axis)
        # The array is unpacked into a list for the sort; that costs about
        # 36 bytes per entry, which is why the axes are counted one after
        # the other rather than all at once.
        ordered = sorted(digests)
        duplicates = _duplicate_count(ordered)
        total_collisions += duplicates
        report[f"distinct_{axis}"] = len(ordered) - duplicates
        report[f"collisions_{axis}"] = duplicates
        del digests, ordered
        gc.collect()

    report["total_collisions"] = total_collisions
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Parallel scale check")
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    parser.add_argument(
        "--digest-bits",
        type=int,
        default=64,
        choices=(64,),
        help="Width of the per-axis digest.",
    )
    args = parser.parse_args()

    report = run(args.count, args.workers, args.digest_bits)
    for key, value in report.items():
        print(f"{key:32s} {value}")


if __name__ == "__main__":
    main()
