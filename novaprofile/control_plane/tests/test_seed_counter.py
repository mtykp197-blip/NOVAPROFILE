"""Tests for the seed allocation counter.

The ``increment`` seed strategy must hand out a different seed to every
caller, *including callers in different processes*.  A counter kept in the
process memory restarts at zero in every worker, so two workers would
produce the same profile -- the exact duplicate the collision guard exists
to prevent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from novaprofile.api.routes_profiles import _seed_for_strategy
from novaprofile.registry.store import ProfileStore


def test_the_counter_advances(tmp_path):
    """Two draws give two different values."""

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()

    first = store.next_counter("seed:pixel")
    second = store.next_counter("seed:pixel")
    third = store.next_counter("seed:pixel")

    assert first == 1
    assert second == 2
    assert third == 3


def test_counters_are_independent(tmp_path):
    """One preset's counter does not move another's."""

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()

    store.next_counter("seed:a")
    store.next_counter("seed:a")
    value = store.next_counter("seed:b")

    assert value == 1


def test_the_increment_strategy_uses_the_store_counter(tmp_path):
    """The strategy draws its counter from the store, not from memory."""

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()

    first, _ = _seed_for_strategy("pixel", "increment", store)
    second, _ = _seed_for_strategy("pixel", "increment", store)

    assert first != second
    assert store.next_counter("seed:pixel") == 3


def test_the_increment_strategy_returns_the_unit_number(tmp_path):
    """The counter is also the profile's place in its device family.

    The narrow readings are drawn stratified, so the profile has to
    know which slice of the choice space it owns.  Without the unit
    number, two handsets of one model would report the same browser
    major and the same core count.
    """

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()

    _, first_unit = _seed_for_strategy("pixel", "increment", store)
    _, second_unit = _seed_for_strategy("pixel", "increment", store)

    assert first_unit == 1
    assert second_unit == 2


def test_the_fixed_strategy_is_stable(tmp_path):
    """The fixed strategy always returns the same seed."""

    store = ProfileStore(tmp_path / "state.db")
    store.init_db()

    first, _ = _seed_for_strategy("pixel", "fixed", store)
    second, _ = _seed_for_strategy("pixel", "fixed", store)

    assert first == second


def test_two_processes_never_receive_the_same_seed(tmp_path):
    """The counter is shared across processes, not per process."""

    db_path = str(tmp_path / "state.db")
    store = ProfileStore(db_path)
    store.init_db()

    package_root = str(Path(__file__).resolve().parents[1])
    program = (
        "import sys;"
        f"sys.path.insert(0, {package_root!r});"
        "from novaprofile.registry.store import ProfileStore;"
        f"s = ProfileStore({db_path!r});"
        "print(s.next_counter('seed:pixel'))"
    )

    values = set()
    for _ in range(4):
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
        )
        values.add(int(completed.stdout.strip()))

    # Four processes, four different counters.
    assert len(values) == 4
