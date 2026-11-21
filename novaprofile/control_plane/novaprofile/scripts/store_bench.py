"""Parallel benchmark of the compact seed store.

Measures the throughput and the on-disk size of storing profiles as seed
tuples instead of as configuration JSON, which is what makes a fleet of
100 million rows practical.

Run it directly::

    python -m novaprofile.scripts.store_bench --count 200000
"""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from novaprofile.core.devices import list_presets
from novaprofile.core.generator import ProfileGenerator
from novaprofile.registry.seed_store import SeedStore

_PRESETS: list[str] = []


def _init() -> None:
    global _PRESETS
    _PRESETS = [p.preset_id for p in list_presets()]


def _chunk(args: tuple[int, int, str]) -> tuple[int, str]:
    """Generate and store one slice of the fleet into its own database."""

    start, count, db_path = args
    generator = ProfileGenerator()
    store = SeedStore(db_path)
    written = 0
    for index in range(start, start + count):
        preset = _PRESETS[index % len(_PRESETS)]
        config = generator.generate(preset, master_seed=f"{index:064x}")
        store.save(config)
        written += 1
    store.close()
    return written, db_path


def _chunks(total: int, workers: int, tmp: str) -> list[tuple[int, int, str]]:
    """Split the fleet into one slice and one database file per worker.

    SQLite serialises writers, so a single file cannot take the write load of
    a whole fleet.  Each worker therefore writes its own shard, and the
    shards are merged afterwards.
    """

    per = total // workers
    remainder = total % workers
    out: list[tuple[int, int, str]] = []
    start = 0
    for worker in range(workers):
        size = per + (1 if worker < remainder else 0)
        out.append((start, size, str(Path(tmp) / f"shard{worker:03d}.db")))
        start += size
    return out


def run(count: int, workers: int) -> dict[str, float | int]:
    with TemporaryDirectory() as tmp:
        started = time.time()
        with ProcessPoolExecutor(max_workers=workers, initializer=_init) as pool:
            results = list(pool.map(_chunk, _chunks(count, workers, tmp)))
        written = sum(r[0] for r in results)

        # Merge the shards into the fleet database.
        main_db = str(Path(tmp) / "fleet.db")
        store = SeedStore(main_db)
        for _, shard in results:
            store.attach_and_merge(shard)
        store.close()

        elapsed = time.time() - started

        size = sum(p.stat().st_size for p in Path(tmp).glob("*.db*") if p.is_file())

        store = SeedStore(main_db)
        stored = store.count()
        store.close()

        return {
            "count": count,
            "workers": workers,
            "written": written,
            "stored": stored,
            "elapsed_s": round(elapsed, 1),
            "rows_per_s": round(written / elapsed, 1) if elapsed else 0.0,
            "db_bytes": size,
            "bytes_per_row": round(size / written, 1) if written else 0.0,
            "gb_per_100m": round(size / written * 100_000_000 / 1024**3, 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed store benchmark")
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    args = parser.parse_args()

    report = run(args.count, args.workers)
    for key, value in report.items():
        print(f"{key:20s} {value}")


if __name__ == "__main__":
    main()
