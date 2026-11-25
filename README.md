# NOVA PROFILE

Browser profile engine for Windows. Builds Android-flavoured Chromium
profiles that are **provably distinct from each other** — every field a
site can read is derived so that two profiles never collide.

Two documents carry the story:

| File | What it holds |
|---|---|
| [`PROGRESS.md`](PROGRESS.md) | The work log, turn by turn, with the measurements that decided each change |
| [`NEXT_STEPS.md`](NEXT_STEPS.md) | The handover: what is left, in what order, and the traps worth not stepping in twice |

---

## What it is

A control plane that generates browser profiles and drives a Chromium
build that carries the fingerprint **natively**.

The fingerprint does not travel as a JavaScript injection. It travels
as a JSON configuration the browser process reads before it forks the
renderer:

```
--nova-config=/path/to/nova_core.json
```

That matters because a JavaScript hook is trivially detectable —
`Function.prototype.toString`, `Object.getOwnPropertyDescriptor` and
Proxy traps all expose it. The native core puts the values below Blink,
inside the C++ process, where there is nothing to detect.

The Chromium side lives in `third_party/blink/renderer/core/nova/`:
44 configuration structures, 3.048 lines across 24 files.

---

## The parts

| Path | What it is |
|---|---|
| `novaprofile/control_plane/novaprofile/core/` | The profile engine: seeds, devices, identity, consistency |
| `novaprofile/control_plane/novaprofile/js_runtime/` | The JavaScript fallback for a stock Chromium |
| `novaprofile/control_plane/novaprofile/launcher/` | Launches the browser with the right flags and config |
| `novaprofile/control_plane/novaprofile/probe/` | Verifies a profile against a real browser |
| `novaprofile/control_plane/novaprofile/installer/` | Builds and installs the browser as a core |
| `novaprofile/core_patch/` | The build tooling and the patch series for the Chromium core |
| `source tham khảo/` | A reference ecosystem, read to compare — **not** part of this product |

---

## Starting on a new machine

Everything the product is made of is in this repository, except the
built core, which is too large for Git and is published on the
releases page.

### 1. The source

```bash
git clone https://github.com/mtykp197-blip/NOVAPROFILE.git
cd NOVAPROFILE
```

Or, in one step, the script that does the rest as well:

```bash
git clone https://github.com/mtykp197-blip/NOVAPROFILE.git
cd NOVAPROFILE
python setup_new_machine.py
```

### 2. The control plane

```powershell
cd novaprofile\control_plane
python -m pip install -e .
```

The dependencies are declared in `pyproject.toml`.  One of them is not
optional: Windows has no system copy of the IANA time zone database,
and without `tzdata` every profile reports UTC with a zero offset.

### 3. The core

The core is the Chromium fork with the patch series applied.  It is
five hundred megabytes, so it is not in the repository -- it is on the
releases page, and it is fetched the way any other file is fetched:

```powershell
curl.exe -L -o core.zip `
  https://github.com/mtykp197-blip/NOVAPROFILE/releases/download/core-win-x64/160.0.8177.0.zip

Expand-Archive core.zip -DestinationPath core\build\cores\160.0.8177.0
```

The shell reads the core from `core/build/cores/<version>`, and the
version is the one the manifest names.

The release is at
<https://github.com/mtykp197-blip/NOVAPROFILE/releases/tag/core-win-x64>.

The `setup_new_machine.py` script below does this step and the ones
after it.

To build the core from source instead, a Linux host with the Android
SDK, the Android NDK and `depot_tools` is needed -- Chromium asserts
that an Android build needs a Linux host, and the check fires before a
single file is compiled.  The notes are in
[`novaprofile/core_patch/BUILDING.md`](novaprofile/core_patch/BUILDING.md):

```bash
python novaprofile/core_patch/build.py build --major 159
python novaprofile/core_patch/build.py verify --major 159
```

### 4. The shell

```powershell
cd novaprofile\desktop
npm install
```

### 5. The application

```powershell
# the frozen control plane
cd ..\control_plane
python -m PyInstaller nova-backend.spec --noconfirm

# the shell, packaged
cd ..\desktop
npm run dist
```

The installer is written to `novaprofile/desktop/dist/`.

---

## Getting started

```bash
cd novaprofile/control_plane
export PYTHONPATH="$PWD"

# the whole suite
python -m pytest tests -q

# a profile, on the console
python -m novaprofile.cli --help
```

The suite is the first thing to run, and it is the
specification of the engine. A change that breaks one of them broke a
property the fleet depends on.

---

## The catalogue

19.381 Android presets, drawn from the market dataset in
`novaprofile/core_patch/data/vn_devices.jsonl`.

```bash
python tools/generate_device_presets.py
```

Regenerate after changing the generator. The generator is the part that
decides the fleet's shape, and two of its rules are worth knowing:

* **the graphics part is derived from the part's own name**, not from a
  table — the catalogue names 194 renderers, and a table of 38 would
  have made 163 of them report the same architecture
* **the limits agree with each other** — a card that samples a 32768
  texture renders into a 32784 renderbuffer, never the other way round

---

## Building the browser

The product drives a Chromium that was patched for it. The build takes
hours; the scripts are in `C:\chromium\src` and in
`novaprofile/core_patch/`.

```bash
# the two environment variables the build needs
set DEPOT_TOOLS_WIN_TOOLCHAIN=0
set vs2022_install=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools

# generate, build, link
nova_gen.bat
nova_build.bat
nova_link_chrome.bat
```

The target is `win64`, not `android`: Chromium 159 asserts that Android
builds need a Linux host, and the win64 configuration compiles the same
Blink translation units.

When the build finishes:

```bash
# bundle it and register it as a core
python -m novaprofile.installer.install_built_core \
    --chromium C:/chromium/src/out/nova-win \
    --version 160.0.8177.0 --major 160

# verify a profile against it
python -m novaprofile.probe.verify_built_core \
    --browser C:/chromium/src/out/nova-win/chrome.exe \
    --preset pixel-8-pro-android14

# walk the third-party detection sites with a profile
nova probe web <profile-id>
nova probe web <profile-id> --site whatsmybrowser --headful
```

---

## The rules the fleet keeps

These are the properties the tests enforce, and the reason they exist
is that a site reads them:

* **no two profiles collide** — every profile in a fleet is distinct in
  every field, not merely in most of them
* **a chipset carries one core count** — two handsets that report the
  same `soc_model` and different `hardwareConcurrency` are caught
  immediately
* **the renderer names a real part** — the architecture, the vendor and
  the texture limits have to agree with each other
* **the Android release and the SDK level agree** — Android 16 is 36
* **the panel and the density agree** — a declared density outside the
  panel's neighbourhood is a contradiction

---

## The collision problem that started it

Every profile was derived from a master seed. The plain draws —
`derive_bytes`, `derive_int` — never saw the **stratum**, the index of
the profile within the fleet. So every profile of a fleet shared its
serial number, its incremental build id and its canvas, audio and WebGL
salts. One hundred profiles, one fingerprint.

The fix was to fold the stratum into the HKDF info, so that every draw
is a function of both the seed and the stratum. One hundred profiles,
one hundred fingerprints, on every axis.

---

## Licensing

The Chromium core is built from the Chromium source tree under its own
licence; the patch series in `novaprofile/core_patch/patches/` is
original work.
