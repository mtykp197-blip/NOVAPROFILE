# NOVA Profile — the Chromium core patch

This directory holds the native half of NOVAPROFILE: the C++ that makes the
fingerprint live **inside the browser process** instead of being patched
into the page from the outside — and the tooling that turns that C++ into
a browser core.

---

## Why a native patch

A JavaScript injection layer has to be injected into every frame, every
worker and every realm, and it has to be injected *before* the page's own
script runs.  It also leaves traces: the property descriptor of an
overridden accessor is not the same as the stock one, the stack of the
overriding function is visible to `Function.prototype.toString`, and a
patched accessor can be detected by reading it and comparing the source.

A native patch has none of that.  The value lives in the renderer, so
every frame, worker and realm sees the same one, the accessor keeps the
stock descriptor, and the page cannot tell the difference.

This is why the `js_runtime` layer is a *fallback* for the surfaces the
core patch does not cover, and not the primary mechanism.

---

## Layout

| Path | Role |
|---|---|
| `src/nova_core_config.h` | the configuration structs and the holder |
| `src/nova_core_config.cc` | the parser that reads the profile configuration |
| `src/nova_chromium_hooks.h` | the public hooks the Blink patches call |
| `src/nova_blink_patch.cc` | the Blink-side substitutions |
| `src/nova_pixel_noise.h` | the deterministic pixel-noise generator |
| `src/nova_pixel_noise.cc` | its implementation |
| `patches/0001-nova-profile-core.patch` | the identity surfaces (UA, client hints, screen, WebGL, canvas) |
| `patches/0002-nova-profile-hardware-surfaces.patch` | the hardware surfaces (audio, rects, fonts, sensors, storage, WebGPU) |
| `build_config.json` | the version matrix: branches, tags, GN arguments, patch series |
| `build.py` | the build tool: `list`, `plan`, `apply`, `verify`, `check` |

The three patches are a **series** and must be applied in order: the second
patch reaches the profile's configuration through the same `nova::` hooks
the first patch introduced.

---

## The build tool

`build.py` is the only piece of the project that talks to `git`, `gn` and
`ninja`; everything else in NOVAPROFILE speaks to a *built* core.  It is
plain Python 3.12 with no third-party dependencies and runs from anywhere:

```bash
python novaprofile/core_patch/build.py --help
```

### `list` — the supported versions

```bash
python novaprofile/core_patch/build.py list
```

Prints every supported Chromium version with its release branch, its tag
and the patch series it accepts.  The data comes from `build_config.json`,
never from the network.

### `plan` — what a build would do

```bash
python novaprofile/core_patch/build.py plan --version 120 --checkout C:\chromium\src
```

Prints the exact commands that fetch the checkout, check out the release
branch, apply the series, run `gn gen` and run `ninja`.  **Nothing is
executed.**  A Chromium build costs a machine a day, and a plan that has
been reviewed is worth more than one that has been guessed at, so the
command deliberately has no side effects.

The printed commands are written for the shell of the machine they are
printed on: `Set-Location` and `Copy-Item` on Windows, `cd` and `cp`
everywhere else.

### `apply` — patch a checkout

```bash
python novaprofile/core_patch/build.py apply --version 120 --checkout C:\chromium\src
```

Applies the series with `git apply`, in order, against an existing
checkout.  The command is **idempotent**: a patch that is already in the
tree is detected with `git apply --check --reverse` and reported as
`already-applied` instead of being applied twice, so an interrupted build
can simply be re-run.

Each patch is reported on its own line, and a patch that fails is
reported with **git's own stderr**, verbatim:

```
[applied] 0001-nova-profile-core.patch
[failed] 0002-nova-profile-hardware-surfaces.patch
error: patch failed: third_party/blink/renderer/core/storage/storage_manager.cc:97
```

The series stops at the first failure — applying the second patch on top
of a broken first one produces a tree that matches neither the source nor
the intent — and the command exits non-zero.

### `verify` — check a built core

```bash
python novaprofile/core_patch/build.py verify --checkout C:\chromium\src --version 120
```

Three things are decided: the patched binary exists, it is **newer than
the patch files** that produced it, and the version string it reports
matches the one that was asked for.  A binary that predates the patches
is a binary built from a different series, and it is rejected.

### `check` — validate the series

```bash
python novaprofile/core_patch/build.py check
```

Validates the patch series **without a Chromium checkout**, which is the
point: a broken patch is discovered before the machine that builds the
cores is even switched on.  Five things are decided:

- every patch parses — the hunks have headers and the files have paths;
- every hunk header's line counts match its body, because `git apply`
  rejects a patch whose counts do not with *"corrupt patch"*;
- every hunk carries context lines, because a hunk without context has
  nothing for `git apply` to match against;
- the accessors the first patch substitutes are implemented in the NOVA
  sources, and every hook the second patch calls is one the first patch
  introduces — the patches are the halves of one change;
- the file paths the patches name exist in a Chromium checkout layout.

### `describe` — hand a built core to the installer

```bash
python novaprofile/core_patch/build.py describe --checkout C:\chromium\src --version 120
```

Describes the core a checkout holds, in the terms the control plane's
installer uses.  The Python API is `describe_built_core(checkout,
version)`, which returns a `BuiltCore` with a `register_kwargs()`
mapping that can be passed straight into `CoreManager.register_core`:

```python
from novaprofile.core_patch.build import describe_built_core

core = describe_built_core(r"C:\chromium\src", "120")
manager.register_core(**core.register_kwargs())
```

The description is raised from what is *on disk* — the binary that was
found and the version it reports — not from what was asked for.  A core
of another milestone is refused rather than registered under the wrong
name, because a profile that asks for Chromium 120 and gets Chromium 119
would have a User-Agent, a set of client hints and a running core that
do not agree.

---

## The version matrix

`build_config.json` describes every supported Chromium release:

```json
{
  "major": 120,
  "branch": 6099,
  "beta_branch": 1200,
  "branch_ref": "refs/branch-heads/6099",
  "branch_head": "branch-heads/6099",
  "version": "120.0.6099.225",
  "tag": "120.0.6099.225",
  "tag_pattern": "120.0.6099.*",
  "channel": "stable",
  "patches": ["patches/0001-nova-profile-core.patch", "patches/0002-nova-profile-hardware-surfaces.patch"],
  "gn_args": { "target_os": "android", "target_cpu": "arm64" }
}
```

| Field | Meaning |
|---|---|
| `major` | the Chromium milestone (100 … 159) |
| `branch` | the **release** branch number — the third component of the version string |
| `beta_branch` | the number of the old, temporary beta-stage branch, recorded separately |
| `branch_ref` | the full ref of the release branch (`refs/branch-heads/<branch>`) |
| `branch_head` | the short form git uses on the command line (`branch-heads/<branch>`) |
| `version` / `tag` | the exact release the matrix pins |
| `tag_pattern` | the wildcard form, for finding a newer patch release of the same milestone |
| `channel` | `stable` or `beta` |
| `patches` | the patch series, in the order it has to be applied |
| `gn_args` | the GN arguments the build is generated with |

### Where the branch and tag facts come from

Chromium's release branches are named by the **branch number**, which is
the third component of the release's version string: milestone 120 ships
as `120.0.6099.*` and its branch is therefore branch **6099**
(`refs/branch-heads/6099`).  The branch numbers of every milestone from
100 to 159 are the third components of the corresponding release versions,
and they are recorded here explicitly rather than derived, because the
beta-stage branches of the older milestones do not follow the same rule.

The `beta_branch` field holds the number of the old, temporary
beta-stage branch of a milestone (1000, 1120, 1200, 1400 …).  Those
branches are long gone; the number is kept because it is the way some
tooling still refers to those milestones, and mixing the two kinds of
number is a mistake worth making visible.

The `latest` entry is an alias for the newest supported milestone; it
resolves to the same version as the highest `major` in the matrix.

---

## The configuration contract

The native core reads one JSON document per profile.  It carries the
values the profile generator produced, and it is delivered to the browser
process before the first navigation.

The document has to agree with the control plane's schema exactly.  The
contract is enforced on both sides:

- the Python side serialises it (`ProfileConfig.model_dump()`),
- the C++ side parses it (`ParseProfileConfig` in `nova_core_config.cc`),
- the test `tests/test_core_config_contract.py` compares the two.

**The contract test is the thing to run first** when a field is added.
A field that exists on one side and not the other is a configuration that
silently keeps its default value in the browser — a profile that claims
one thing and behaves like another.

```bash
cd novaprofile/control_plane
python -m pytest tests/test_core_config_contract.py -v
```

---

## Tests

The build tool's own tests live with the control plane's:

```bash
cd novaprofile/control_plane
python -m pytest tests/test_core_patch_build.py -q
```

They need neither a Chromium checkout nor a network.  The `apply` tests
build a throw-away git repository in a temporary directory and patch it
for real, so the apply logic is proven rather than mocked.

---

## Status

The series has **not been compiled against a real Chromium checkout yet**.

`build.py check` reports that the shipped patches are **not well-formed**:
their hunk headers declare more lines than their bodies carry, so
`git apply` rejects them outright with *"corrupt patch"*.  The check
finds this without a checkout; it is the first thing to fix before the
series is taken to a build machine.

The Python side of the configuration contract is enforced by the test
suite; the C++ side has been written against the documented APIs but has
not been through a compiler.
