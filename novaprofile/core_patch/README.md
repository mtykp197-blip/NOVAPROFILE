# NOVA Profile — the Chromium core patch

This directory holds the native half of NOVAPROFILE: the C++ that makes the
fingerprint live **inside the browser process** instead of being patched
into the page from the outside.

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

| File | Role |
|---|---|
| `src/nova_core_config.h` | the configuration structs and the holder |
| `src/nova_core_config.cc` | the parser that reads the profile configuration |
| `src/nova_chromium_hooks.h` | the public hooks the Blink patches call |
| `src/nova_blink_patch.cc` | the Blink-side substitutions |
| `src/nova_pixel_noise.h` | the deterministic pixel-noise generator |
| `src/nova_pixel_noise.cc` | its implementation |
| `patches/0001-*.patch` | the identity surfaces (UA, client hints, screen, WebGL, canvas) |
| `patches/0002-*.patch` | the hardware surfaces (audio, rects, fonts, sensors, storage, WebGPU) |

The two patches are a **series** and must be applied in order: the second
one assumes the first has already substituted the identity accessors.

---

## Applying the patches

From a Chromium checkout:

```bash
git checkout -b nova-profile
git apply /path/to/novaprofile/core_patch/patches/*.patch
```

The patches touch these Blink files:

- `third_party/blink/renderer/core/navigator/navigator.idl`
- `third_party/blink/renderer/core/frame/frame.cc` / `frame.h`
- `third_party/blink/renderer/modules/screen/screen.idl`
- `third_party/blink/renderer/modules/webgl/webgl_rendering_context.cc`
- `third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.cc`

Every change is a **substitution** of an existing accessor with a
config-backed accessor.  No new DOM property is introduced, so a page
cannot detect the patch by enumerating the prototype.  Every substituted
accessor keeps the stock behaviour as its fallback: if the profile
configuration has not been loaded, the accessor returns what Chromium
would have returned.

---

## The configuration

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

## Building

The patch targets Chromium from **version 100 upward**; the series is
kept compatible with the API changes in that range.  The build itself is
an ordinary Chromium build:

```bash
cd src
git checkout -b <version>
# apply the patches, then:
git clone https://chromium.googlesource.com/chromium/src.git
cd src
fetch --nohooks android
git checkout <version>
cd third_party/depot_tools
./bootstrap.sh
cd ../..
gclient sync -D --with_branch_heads --with_tags
cd src
git apply /path/to/novaprofile/core_patch/patches/*.patch
gn gen out/nova --args='target_os="android" target_cpu="arm64" is_debug=false'
ninja -C out/nova chrome
```

### Versions

| Range | Status |
|---|---|
| 100 – 116 | the series applies as written |
| 117 – 127 | applies; the client-hint accessors moved, the patches carry the new location |
| 128 and later | applies; the client-hint accessors moved again |

The patches are deliberately written against the *accessor* rather than
against a line number, so a version bump is usually a rebase of the
context lines rather than a rewrite.

---

## Status

The series has **not been compiled against a real Chromium checkout yet**.
The Python side of the contract is enforced by the test suite; the C++
side has been written against the documented APIs but has not been through
a compiler.  This is the first open item in `PROGRESS.md`.
