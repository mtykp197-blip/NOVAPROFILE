"""The machine of a unit: the values a website reads off a handset.

A *machine* is the combination of the readings that come out of the
handset itself rather than out of the seed's entropy: the graphics
part, the model code, the panel, the density, the digitiser, the chip
count and the memory.  These are the values a script can read
verbatim, which makes them the axis a website groups on.

The module exists for one reason: the collision guard has to know the
machine of a candidate **without** building the candidate.  Rebuilding
a whole profile to see which panel it draws is expensive, and a guard
that walks a choice space by rebuilding one profile per step turns a
one-second allocation into a four-minute one.

So the machine is resolved here, as a pure function of the preset and
the stratum.  The resolver goes through the *same* identity factory the
generator goes through, so the guard and the generator always agree on
which machine a stratum means -- a disagreement here would show up as a
guard that walks forever, looking for a free slice that the generator
never actually produces.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .devices import DevicePreset, cpu_options_for, inferred_touch_points
from .hardware_approximation import (
    approximate_device_memory,
    approximate_hardware_concurrency,
)
from .identity import DeviceIdentityFactory, machine_space_size, panel_space
from .seed import Axis, SeedDeriver


@dataclass(frozen=True)
class Machine:
    """The readings a script gets off one handset.

    The fields are the ones a website can read without asking the
    browser for anything: the renderer string, the model code, the
    panel, the pixel ratio, the core count, the memory, the number of
    touch points and the colour depth.
    """

    release: str
    model: str
    panel: tuple[int, int]
    density: int
    device_pixel_ratio: float
    cores: int
    memory: int
    touch_points: int
    color_depth: int
    renderer: str

    @property
    def key(self) -> tuple:
        """The identity of the machine, for a set of machines.

        Every field that a script can read is in here, so two machines
        with the same key are the same machine as far as a website is
        concerned -- and a website that has this tuple has everything
        it needs to tell them apart.
        """

        return (
            self.renderer,
            self.model,
            self.panel[0],
            self.panel[1],
            self.device_pixel_ratio,
            self.cores,
            self.memory,
            self.touch_points,
            self.color_depth,
        )


def _draw(
    preset: DevicePreset,
    stratum: int,
    deriver: SeedDeriver,
) -> tuple[str, str, tuple[int, int], int, int]:
    """The release, the model code, the panel, the density and the RAM.

    The draw is the generator's draw, in the generator's order, so the
    two never disagree.  The memory is drawn separately, because it is
    a dimension of its own -- Chromium reports a handful of values, so
    it cannot be part of the mixed radix.
    """

    release_options = tuple(
        variant[0]
        for variant in (preset.release_variants or ((preset.release, preset.sdk_int),))
    )
    model_options = tuple(preset.model_variants or (preset.model,))
    panel_options, density_options = panel_space(preset)

    physical_ram_gb = int(
        deriver.derive_stratified(Axis.NAVIGATOR, preset.ram_options, "ram", stratum)
    )
    (
        release_name,
        chosen_panel,
        chosen_density,
        model_code,
    ) = deriver.derive_mixed_radix(
        Axis.NAVIGATOR,
        (
            release_options,
            panel_options,
            density_options,
            model_options,
        ),
        "device",
        stratum,
    )

    return (
        release_name,
        model_code,
        chosen_panel,
        chosen_density,
        physical_ram_gb,
    )


#: How many distinct machines each preset produces, remembered.
_MACHINE_COUNTS: dict[str, int] = {}

#: The version of the measurement the cache holds.  A change in the
#: identity factory, the panel space or the density snapping changes
#: what a machine is, and therefore how many of them a model produces,
#: so the cache is keyed by a fingerprint of everything that feeds the
#: measurement.
_COUNT_CACHE_VERSION = "machine-count-v1"

#: Where the counts are kept between runs.  The measurement costs about
#: a second and a half per model, and the fleet planner asks for
#: every model in the catalogue, so a run that did not remember the
#: answer would spend eleven seconds re-deriving what it already
#: knows.
def _count_cache_path() -> Path:
    base = os.environ.get("NOVAPROFILE_CACHE_DIR")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".novaprofile" / "cache"
    return root / "machine-counts.json"


def _load_count_cache() -> dict[str, int]:
    """The counts that were measured in an earlier run."""

    path = _count_cache_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("version") != _COUNT_CACHE_VERSION:
        return {}
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        return {}
    measured = {}
    for key, value in counts.items():
        if isinstance(key, str) and isinstance(value, int) and value >= 0:
            measured[key] = value
    return measured


def _save_count_cache(counts: dict[str, int]) -> None:
    """Remember the counts that were measured, for the next run."""

    path = _count_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _COUNT_CACHE_VERSION, "counts": counts}
        # The file is written to a temporary name and then moved into
        # place, so a run that is interrupted never leaves a half
        # written cache behind.
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix="machine-counts-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle)
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
    except OSError:
        # The cache is an optimisation.  A machine that cannot write
        # its cache still measures the counts, just more slowly.
        return


#: The counts that were measured in an earlier run.
_COUNT_CACHE: dict[str, int] | None = None


def _deriver_for(
    seed: str | None,
    stratum: int,
    *,
    epoch: int = 0,
    nudge: int = 0,
) -> SeedDeriver:
    """A deriver bound to a stratum, the way a profile binds it.

    The stratum travels with the deriver, because every draw that
    consults the deriver is keyed to the unit the configuration
    describes.  A deriver without the stratum would answer differently
    from the one the generator uses, and the resolver would name a
    machine the generator never builds.
    """

    from .seed import new_master_seed

    return SeedDeriver(
        (seed or new_master_seed()).lower(),
        epoch,
        nudge,
        stratum=stratum,
    )


def count_machines(preset: DevicePreset, seed: str | None = None) -> int:
    """How many *distinct* machines a model actually produces.

    The slice space of a model is an upper bound, and a loose one: the
    memory collapses onto the handful of values Chromium reports, and
    several of the declared model codes of a family resolve to the same
    code in the field.  So a model with a hundred and eight slices may
    only produce thirty-six different machines.

    The fleet planner has to know the *true* count, because a fleet
    that promises more units of a model than the model owns units will
    end up asking the guard for a slice that does not exist.

    The count is measured by walking the space and counting the
    distinct machines.  The walk needs a seed, and the seed only
    decides *which* machine a slice holds -- not how many there are,
    because the collapse happens in the drawing, not in the seed.  The
    count is therefore taken over one fixed seed, and it is remembered,
    because the fleet planner asks for the same models over and over.

    The remembered count is also kept between runs, in a file, because
    the measurement is expensive and the answer does not change from
    one run to the next.
    """

    global _COUNT_CACHE

    cached = _MACHINE_COUNTS.get(preset.preset_id)
    if cached is not None:
        return cached

    if _COUNT_CACHE is None:
        _COUNT_CACHE = _load_count_cache()
    cached = _COUNT_CACHE.get(preset.preset_id)
    if cached is not None:
        _MACHINE_COUNTS[preset.preset_id] = cached
        return cached

    size = machine_space_size(preset)

    seen: set[tuple] = set()
    for stratum in range(size):
        machine = resolve_machine(preset, stratum, _deriver_for(seed, stratum))
        seen.add(machine.key)

    count = len(seen)
    _MACHINE_COUNTS[preset.preset_id] = count
    _COUNT_CACHE[preset.preset_id] = count
    return count


def flush_count_cache() -> None:
    """Write the measured counts to disk, so the next run remembers them."""

    global _COUNT_CACHE

    if _COUNT_CACHE is None:
        _COUNT_CACHE = _load_count_cache()
    _save_count_cache(_COUNT_CACHE)


def resolve_machine(
    preset: DevicePreset,
    stratum: int,
    deriver: SeedDeriver | None = None,
) -> Machine:
    """The machine a unit of ``preset`` draws at ``stratum``.

    The screen is resolved through the identity factory, so the ratio
    the machine reports is the ratio the profile will report -- the
    factory snaps the density onto the bucket whose ratio the preset
    offers, and a resolver that did not snap would name a machine the
    generator never builds.

    The deriver must be bound to the stratum, the way a profile binds
    it.  One that is not would answer differently from the generator's
    and name the wrong graphics part.
    """

    if deriver is None:
        deriver = _deriver_for(None, stratum)

    release, model, panel, density, physical_ram_gb = _draw(preset, stratum, deriver)

    factory = DeviceIdentityFactory(
        deriver,
        stratum,
        chosen_release=release,
        chosen_model=model,
        chosen_panel=panel,
        chosen_density=density,
    )
    # The identity is built, not re-derived.  The factory draws the
    # renderer string from the same counter the generator's factory
    # does, and a resolver that drew it on its own would be one draw
    # ahead or behind and would name the wrong part.
    identity = factory.build(preset)
    screen = identity.screen
    gpu = identity.gpu

    cpu_options = cpu_options_for(preset)
    cores = approximate_hardware_concurrency(
        int(
            deriver.derive_stratified(
                Axis.NAVIGATOR, cpu_options, "cores", stratum
            )
        )
    )
    memory = approximate_device_memory(physical_ram_gb)

    touch_points = inferred_touch_points(preset)
    color_depth = preset.screen.color_depth
    renderer = gpu.unmasked_renderer

    return Machine(
        release=release,
        model=model,
        panel=(screen.width, screen.height),
        density=screen.density_dpi,
        device_pixel_ratio=screen.device_pixel_ratio,
        cores=cores,
        memory=memory,
        touch_points=max(touch_points) if touch_points else 5,
        color_depth=color_depth,
        renderer=renderer,
    )
