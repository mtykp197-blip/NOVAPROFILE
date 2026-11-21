# NOVAPROFILE — README

A commercial-grade Custom Android Browser Core (Chromium/Blink/ANGLE/Skia)
and an Android Profile Engine & Control Plane.

NOVAPROFILE generates **deterministic, collision-free** browser fingerprints
that are internally consistent at the hardware, OS, browser and network
layers, and native — the values live inside the browser process, so they are
identical across the main frame, iframes, Web Workers, SharedWorkers and
ServiceWorkers, and leave no `navigator.webdriver` or `Proxy` trace.

---

## Quick start

```powershell
# from the repository root
pip install -e ./novaprofile/control_plane

nova doctor                                  # verify the installation
nova presets list                           # the 42 real device presets
nova presets show pixel-8-pro-android14     # one preset in full
nova profile create --preset pixel-8-pro-android14 --label "team-a"
nova profile list
nova batch create --count 50 --preset galaxy-s24-ultra-android14
nova probe run --profile <id>
```

The control plane can also be run as a service:

```powershell
novaprofile-api --host 127.0.0.1 --port 8848
```

> **Preset ids are exact.** `nova presets list` prints the id to use; the
> ids carry the Android release (`pixel-8-pro-android14`), because the
> release decides the Chromium major that ships with it.

---

## The two hard requirements

| Requirement | Enforced by |
|---|---|
| **100% distinct** — no two profiles share a fingerprint | `core/collision.py` |
| **Stable** — a profile's fingerprint never changes | `core/seed.py` |

A fingerprint is a pure function of `(master_seed, seed_epoch, nudge_step)`.
The seed is stored; regenerating from it reproduces the profile exactly,
which is what makes a profile stable across restarts, machines and
reinstalls.

```python
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import new_master_seed

seed = new_master_seed()
profile = ProfileGenerator().generate("pixel-8-pro-android14", seed=seed)

# regenerate -> byte-identical
again = ProfileGenerator().generate("pixel-8-pro-android14", seed=seed)
assert again.model_dump() == profile.model_dump()
```

---

## Verification

Every claim this project makes is measured, and every measurement is filed
in a ledger so that no test has to be repeated to find out whether it was
already done.

```powershell
cd novaprofile/control_plane

python -m pytest -q                                        # 559 tests
python -m ruff check .                                     # linter
python -m novaprofile.scripts.audit --count 2000           # 4 dimensions
python -m novaprofile.scripts.scale_check --count 2000000 --workers 18
python -m novaprofile.scripts.entropy_budget               # entropy per axis
python -m novaprofile.scripts.measurements_cli list        # the ledger
python -m novaprofile.scripts.measurements_cli verify      # checksums
```

The four audit dimensions are:

| Dimension | Question |
|---|---|
| `real` | does every value match what the hardware really reports? |
| `coherent` | do the axes agree with each other (GPU ↔ SoC, locale ↔ timezone, network ↔ downlink)? |
| `unique` | does no two profiles collide on any of the 12 guarded axes? |
| `scale` | does every axis carry enough entropy to hold 100 million profiles? |

---

## Repository layout

```
novaprofile/
  control_plane/          the Python control plane
    novaprofile/          the package
    tests/                the test suite (29 files)
    pyproject.toml
  core_patch/             the Chromium/Blink/ANGLE/Skia patches
    src/                 the C++ core (config holder, seed, pixel noise)
    patches/             the unified diffs
    README.md            how to build the patched Chromium
  js_runtime/             the page-context JS layer (not yet written)
  docs/
    ARCHITECTURE.md      the full architectural breakdown
    AGENT_CONTRACT.md    the interface contract
    measurements/         the measurement ledger
      INDEX.md           what has been measured, and what it concluded
```

---

## Documentation

| Document | Covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | the determinism chain, the collision guard, the native core, the network layer |
| [`docs/AGENT_CONTRACT.md`](docs/AGENT_CONTRACT.md) | the module interfaces the parallel agents work against |
| [`docs/measurements/INDEX.md`](docs/measurements/INDEX.md) | every measurement that has been taken and its conclusion |
| [`core_patch/README.md`](core_patch/README.md) | building the patched Chromium |
| [`../RULES.md`](../RULES.md) | the architectural rules and the error ledger |
| [`../PROGRESS.md`](../PROGRESS.md) | what is done and what is left |
