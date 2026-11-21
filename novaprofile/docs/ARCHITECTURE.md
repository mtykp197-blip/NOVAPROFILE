# NOVA Profile Engine — Architecture

> A browser-fingerprint profile manager with a deterministic, collision-free
> identity engine and a native Chromium core.

---

## 1. The two hard requirements

Everything in this system exists to satisfy two properties that an
anti-bot vendor can measure:

| Requirement | Where it is enforced | How it is measured |
|---|---|---|
| **No two profiles collide** | `core/collision.py` | `tests/test_collision_and_persistence.py::TestDistinctRatio` |
| **A profile never changes** | `core/seed.py` | `tests/test_collision_and_persistence.py::TestPersistence` |

Everything else is a means to those two ends.

---

## 2. The determinism chain

A profile's fingerprint is a **pure function** of three values:

```
(master_seed, seed_epoch, nudge_step)
```

```
                        master_seed (64 hex chars, from the OS CSPRNG)
                                 │
                    ┌────────────┴────────────┐
                    │   HKDF-Extract           │   one PRK per axis
                    │   salt = DOMAIN_SALTS[axis]│   (16 independent axes)
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │   HKDF-Expand            │   info = axis|epoch|nudge|context
                    │   → the axis' values     │
                    └────────────┬────────────┘
                                 │
        ┌────────────┬───────────┼───────────┬────────────┬──────────┐
        ▼            ▼           ▼           ▼            ▼          ▼
   navigator   client_hints  screen   canvas_2d   webgl_pixel   audio
   webgpu      geo_timezone  rects    fonts       media_devices sensors
   storage_quota  battery_network                          webgl_params
```

### Why the domain salts matter

Each axis is derived through its **own** HKDF salt. If two axes shared a
salt, then changing one field would perturb an unrelated axis, and two
profiles with different seeds could still correlate on the shared axis.
The salts are protocol constants in `core/seed.py::DOMAIN_SALTS` and must
never change once released — changing one invalidates every profile ever
generated with the old version.

The rule "every axis gets its own salt" is enforced by a test:

```python
def test_every_axis_has_a_unique_salt() -> None:
    salts = list(DOMAIN_SALTS.values())
    assert len(salts) == len(set(salts))
```

### The `hash()` trap

A profile's fingerprint must not change when the browser restarts, the
control plane restarts, or the machine reboots. Seeded derivation gives
that for free — but only for values that actually come from the seed.

Any value derived from Python's built-in `hash()` breaks it. CPython
randomises the string hash per process (`PYTHONHASHSEED`), so:

```python
@property
def serial(self) -> str:
    return f"0x{abs(hash(self.preset_id)) & 0xFFFFFFFF:08x}"   # wrong
```

returns a different serial on every interpreter start. The device then
presents itself as a different physical unit each time it launches — a
single profile that a vendor can link across sessions on a signal that has
nothing to do with the seed.

The fix is a fixed digest:

```python
@property
def serial(self) -> str:
    digest = 0xCBF29CE484222325            # FNV-1a offset basis
    for byte in self.preset_id.encode("utf-8"):
        digest ^= byte
        digest = (digest * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"0x{digest & 0xFFFFFFFF:08x}"
```

`core/devices.py` is the only place this pattern appeared, and
`tests/test_seed.py::TestFingerprintStability` now runs the generator in
three subprocesses with different `PYTHONHASHSEED` values to prove the
result is identical.

### What is *not* part of the fingerprint

The identity metadata — the UUID, the display name, the creation and update
timestamps, the last-started marker — is management data. It is expected to
differ between two generations of the same seed, and it is excluded from
`signature_parts()`. The fingerprint is the seed-derived payload:
`full_signature_parts()` plus the OS, navigator, screen, canvas, WebGL,
audio, rects and font blocks.

---

## 3. The WebGL image value — the anti cross-GPU-collision axis

This value deserves its own section because it is the subtlest part of the
system.

A bot vendor renders an interpolated-coordinate shader into a WebGL
surface and reads the pixels back. On a real GPU the result carries
information about the *actual* GPU's rasterizer. Two profiles that claim
different GPUs but produce identical readback pixels are immediately
linked.

The value is derived as:

```python
material = axis_key || "epoch=N" || "nudge=N" || unmasked_renderer || tag
digest   = SHA256(material)
raw      = HKDF-Expand(digest, "webgl-image-value", 32)
value    = (int(raw) / 2**256) * 1e-4 + 1e-7
```

Two properties follow:

1. **The renderer string is inside the hash.** Two different GPU models can
   never produce the same image value, even with an identical master seed.
   This is what stops a fleet from being clustered by GPU.
2. **The value carries 17 significant digits.** It survives the round trip
   through a C++ `double` and a JSON string without rounding. The Python
   side formats it with `"%.17g"` and the C++ side uses
   `snprintf("%.17g")` — byte for byte identical output.

```python
def test_renderer_binding_holds_across_many_pairs() -> None:
    # Eight real GPU strings, eight distinct values, no exceptions.
```

---

## 4. The three-stage collision guard

`core/collision.py` implements the escalation the specification asks for.

```
   candidate profile
          │
          ▼
   ┌─────────────┐   conflict on an axis signature?
   │   NUDGE     │──────────────────────────────────────┐
   │ nudge_step++│                                       │
   └─────────────┘                                       │
          │ no conflict                                   │
          ▼                                               │
   ┌─────────────┐   still colliding after MAX_NUDGE?    │
   │  ROLL SEED  │──────► seed_epoch++, fresh master_seed│
   └─────────────┘                                       │
          │ no conflict                                   │
          ▼                                               ▼
   ┌─────────────┐                              ┌─────────────┐
   │   REJECT    │  after MAX_ATTEMPTS (8)       │   REJECT    │
   └─────────────┘                              └─────────────┘
        ProfileCollisionError
```

### The nudge must be able to move *every* axis

A nudge increments `nudge_step`, which re-keys every axis through the
HKDF `info` field. That means the nudge can resolve a collision on the
canvas salt, the rects deltas, the audio salt or the media-device ids —
not just on the WebGL image value.

This is why `CollisionGuard._rebuild()` regenerates **the whole profile**
from the preset and the new seed, not only the seed-derived fields. An
earlier revision re-derived only the fields the guard checks, and a rolled
seed then left the *device identity* in place: the new profile reported the
old serial, the old build fingerprint and the old locale. Two profiles with
different fingerprints on the same physical device is exactly the link a
site uses to recognise a returning device, so the rebuild now regenerates
everything and carries over only the parts that belong to the operator
(the name, the proxy, the browser version).

### The device identity is a guarded axis of its own

`SignatureKind.DEVICE` covers the serial, the build fingerprint, the
bootloader and the screen panel. The other axes only describe the
*rendering* and *hardware sampling* surfaces, so two profiles could differ
on every one of them and still report the same serial — which is the pair a
site reads to recognise a device.

The device axis is checked **first**, because the correct answer to a
repeated serial is to become a different device, not a differently-drawn
canvas.

### The guard checks; the caller commits

`allocate()` performs the checks and returns a collision-free profile but
does **not** write the signatures. The caller persists the profile first
and then calls `commit()`.

The reason is referential integrity: the `signatures` table carries a
foreign key onto the `profiles` table, so a signature cannot be written
before its profile exists.

```python
allocated = _allocate(store, guard, config)   # checks only
store.create_profile(allocated)                # the profile row
guard.commit(allocated, profile_id)            # now the signatures
```

---

## 5. Cross-layer consistency

`core/consistency.py` is the gate that stops a profile whose parts
disagree with each other. A vendor that samples the UA string, the client
hints, the WebGL renderer and the screen size in one session can trivially
detect a mismatch.

### Errors vs. warnings

| Severity | Meaning | Example |
|---|---|---|
| **error** | Impossible or a dead giveaway | a desktop GPU string on an Android UA; `navigator.webdriver == true`; the client-hint model disagreeing with the build model |
| **warning** | Implausible but repairable | the Chrome major version outside the window for the claimed OS release; the Accept-Language not starting with the primary locale |

`resolve()` repairs every warning deterministically and re-derives the
dependent fields. It never silently repairs an error — it raises
`ConsistencyError`.

### The error that matters most

```python
_DESKTOP_GPU_MARKERS = (
    "ANGLE (", "NVIDIA", "GeForce", "Radeon",
    "Intel(R)", "Intel ", "SwiftShader", "llvmpipe",
    "Microsoft Basic Render",
)
```

Any of these in `unmasked_renderer` on an Android profile is a hard error.
`ANGLE (…)` in particular is the signature of a real desktop Chrome, and
an Android profile that reports it is identified instantly.

---

## 6. The native core

The browser-side implementation lives in `core_patch/`. It patches Blink
rather than injecting JavaScript, because anything running in the JS world
is observable:

- `Function.prototype.toString()` returns the shim's source
- `Object.getOwnPropertyDescriptor()` reveals an accessor
- a `Proxy` traps every property access
- `navigator.webdriver` is set to `true`

### The patched surfaces

| File | What it serves |
|---|---|
| `third_party/blink/renderer/core/html/navigator_id.cc` | UA, platform, cores, memory, touch points, languages |
| `third_party/blink/renderer/core/screen/screen.cc` | screen metrics and the device pixel ratio |
| `third_party/blink/renderer/modules/webgl/webgl_rendering_context.cc` | the unmasked renderer and `readPixels` |
| `third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.cc` | the 2D canvas readback |
| `content/browser/browser_main_loop.cc` | loads the config before any renderer is forked |
| `content/gpu/gpu_main.cc` | the same config in the GPU process |
| `content/browser/net/browser_context.cc` | the same config in the network service |

Every change is a **substitution** of an existing accessor. No new DOM
properties are introduced, so a page cannot detect the patch by
enumerating the prototype.

### One config, many processes

The configuration is loaded in the browser process *before* any child
process is created, and replicated into the renderer, the GPU process and
the network service. Every process therefore reports identical values.

This is what makes the cross-context check succeed: the main frame, a
cross-origin iframe, a sandboxed iframe, a `srcdoc` iframe, a Web Worker,
a SharedWorker and a ServiceWorker are separate JS worlds but the same
native process, so they all read the same `NovaConfigHolder`.

### The noise is applied at the readback

The pixel noise is applied to the buffer that is handed back to the
script, not to the rendering surface. The script therefore cannot recover
the original pixels by any means.

The perturbation is deliberately tiny — at most one or two least
significant bits of a channel — which is below the threshold the vendors
use to flag a modified canvas. The alpha channel is never touched: a
non-255 alpha is a strong "this canvas was modified" signal.

---

## 7. The network layer

`network/` owns everything that must agree with the proxy's physical
location.

| Module | Responsibility |
|---|---|
| `proxy.py` | endpoint resolution and the `--proxy-server` switch |
| `geo.py` | exit-IP → country/region/city/ASN, datacenter detection |
| `rotation.py` | the rotation modes and the response formats |
| `dns.py` | the per-mode Chromium DNS flags and the leak predicate |
| `webrtc.py` | the WebRTC flags and the deterministic local-IP spoofing |
| `leakcheck.py` | the post-launch verification of the four leak channels |

### What is deliberately *not* faked

**TLS and HTTP/2 are left native.** The JA3/JA4 fingerprint and the
HTTP/2 SETTINGS frame are properties of the network stack. Spoofing them
in the browser process means reimplementing the TLS handshake, which
either breaks the connection or leaves a fingerprint that is *more*
distinctive than the honest one — a stock Chrome JA3 with a mismatched
HTTP/2 frame is a louder signal than a plain JA3.

The same reasoning applies to the TLS cipher order, the ALPN list and the
ALPN session ticket behaviour. The proxy provides the real network
identity; NOVA makes the *browser* profile consistent with it.

---

## 8. Storage layout

```
%LOCALAPPDATA%\NovaProfile\
  profiles\<profile_id>\        the browser's user_data_dir
  state.db                      SQLite: profiles, signatures, groups, folders
  keys\master.key               the DPAPI-protected AES-256 key
  logs\<profile_id>\<session>.log
```

`$NOVA_PROFILE_HOME` overrides the root, which is how the tests stay out
of the real application data.

The master key is protected with **DPAPI** on Windows, so it is bound to
the logged-in user and cannot be lifted off the machine by copying the
file. On other platforms the key falls back to a `0600` file, which is
documented in the module docstring as the weaker option.

---

## 9. Module map

```
novaprofile/
  schema/       the Pydantic models — one file per specification group
    enums.py    the shared literals
    hardware.py group 1 (build/ABI), 4 (screen/viewport), 6 (canvas/webgl/webgpu)
    identity.py group 3 (navigator/UA/chints/permissions), 5 (tz/locale/geo)
    signals.py  group 7 (audio/rects/fonts/media/storage/battery/sensors)
    network.py  group 2 (proxy/webrtc/dns/geo-intel/http headers)
    profile.py  the aggregate ProfileConfig + SeedConfig
  core/
    seed.py         HKDF-SHA256 derivation
    devices.py      the real Android device catalog (11 presets)
    generator.py    preset + seed → a complete profile
    consistency.py  the cross-layer validator and repairer
    collision.py    the three-stage guard
  registry/     the SQLite store
  security/     AES-256-GCM + DPAPI
  network/      proxy, geo, rotation, DNS, WebRTC, leak checks
  launcher/     process management, isolation, the concurrency limiter
  probe/        the in-page measuring harness and the CDP client
  api/          the FastAPI control plane
  cli/          the Typer CLI
```

---

## 10. The specification groups and where each one lives

| Group | Axes | Module |
|---|---|---|
| 1 | Android build, ABI, SoC | `schema/hardware.py` |
| 2 | proxy, rotation, geo intelligence, geo sync, WebRTC, DNS, HTTP headers | `schema/network.py` |
| 3 | navigator, user-agent data, plugins/MIME, permissions | `schema/identity.py` |
| 4 | screen, orientation, viewport, media queries | `schema/hardware.py` |
| 5 | timezone, locale, geolocation | `schema/identity.py` |
| 6 | canvas 2D, WebGL, WebGPU | `schema/hardware.py` |
| 7 | audio, rects, fonts, media devices, storage/network, battery/speech/gamepad, sensors | `schema/signals.py` |

---

## 11. The test map

| File | What it proves |
|---|---|
| `test_seed.py` | the HKDF is deterministic, the axes are independent, the WebGL value has 17 digits and binds the renderer |
| `test_collision_and_persistence.py` | **the two hard requirements** — 100% distinct ratio and fingerprint persistence across restarts |
| `test_registry.py` | the store round-trips every axis, the signature uniqueness is atomic |
| `test_security.py` | the AES-GCM round trip, the tamper detection, the key derivation |
| `test_network.py` | the per-mode flags, the leak predicates, the rotation formats |
| `test_launcher.py` | the isolation directory handling, the command line, the concurrency limit |
| `test_probe.py` | the harness is self-contained, the simulator is deterministic |
| `test_api.py` | the endpoint contracts |
| `test_cli.py` | the command contracts and the exit codes |
| `test_version_matrix.py` | the UA, brands and client hints of every milestone version |
| `test_consistency_version.py` | a profile never claims a hint its core cannot send |
| `test_cores.py` | the multi-core directories, the integrity seal and the fallback policy |
| `test_launcher_cores.py` | the launcher runs the core that matches the declared major |
| `test_collision_device_identity.py` | a repeated serial is resolved by becoming another device |
| `test_consistency_webrtc.py` | a proxied profile cannot leak the host's address |
| `test_api_auth.py` | the API refuses a request without a credential |
| `test_api_redaction.py` | the master seed and the password never leave the control plane |
| `test_persist_race.py` | a profile never survives a failed signature commit |
| `test_seed_counter.py` | two processes never receive the same seed |
| `test_seed_store.py` | the seed tuple reproduces the profile exactly |

---

## 12. The browser version matrix

`core/versions.py` holds the capability table for every Chromium major from
100 to the latest. Each entry records what that version actually does:

* whether the User-Agent is reduced and whether it still carries the
  `Build/` token (removed in 110),
* the GREASE brand and the brand order,
* **which `Sec-CH-UA-*` hints the version sends** — the full version list
  arrived in 116, the architecture, bitness and model in 119,
* the observable API surface, and the Android release the version shipped
  with.

The generator consults the table, so a profile never claims a capability
its declared core does not have. The consistency resolver checks the same
table in the other direction: a hint that the core *would* send but the
profile omits is a warning, and a hint that the core *cannot* send but the
profile declares is an error.

`core/cores.py` manages several installed cores side by side. Each core
owns its directory, its manifest and its integrity seal; the seal hashes
every file except the volatile ones a running browser rewrites (`.log`,
`.tmp`, `SingletonLock`, the crash dumps). `CoreSelector` resolves a
requested major onto an installed core with an explicit fallback policy
(`nearest`, `error`, `oldest`, `newest`).

---

## 13. Containment of the host

A profile that claims to be a Pixel 8 Pro but still reports the host's
Windows GPU, its real address or its real file system is worse than no
profile: the contradiction is a stronger signal than the fingerprint it
was meant to hide.

`launcher/leak_guard.py` closes the paths that the flags can reach: the
GPU process runs in software mode with the SwiftShader backend, QUIC is
disabled (a QUIC handshake bypasses an HTTP proxy and exposes the real
address), and the media, cast, WebUSB, extension and sign-in surfaces are
turned off.

The resolver checks the network side of the same problem. A profile that
uses a proxy must not carry an open WebRTC configuration: an `all` ICE
policy, unblocked non-proxied UDP or disabled mDNS obfuscation would each
let the page learn the host's address, and each of them is an error rather
than a warning.

The control plane's own API is contained in the same spirit: it binds to
the loopback interface by default, refuses to start on a routable
interface without an API key, allows no browser origin unless the operator
names one, redacts the master seed and the proxy password from its
responses, and masks the credentials in the command line it reports.

On Windows the POSIX permission bits are ignored, so `security/hardening.py`
rewrites the ACL of the master key to grant the current user and nobody
else.

---

## 14. Scale

A profile is fully determined by its seed tuple — the master seed, the
preset, the epoch and the nudge step. Regenerating from the tuple
reproduces every fingerprint axis byte for byte; only the identity is new.

`registry/seed_store.py` therefore stores the tuple instead of the
configuration. A configuration is about 11 KB, so a fleet of 100 million
profiles would need roughly a terabyte of JSON; the tuple needs about
54 GB. The `ProfileStore` keeps the full configuration, because the
workbench edits profiles by hand.

A bulk load writes each slice of the fleet into its own shard and merges
the shards afterwards, because SQLite serialises the writers and a single
file cannot take the load of a whole fleet.

Measured on this machine with 18 workers:

| Operation | Rate |
|---|---|
| Profile generation | ~1 400 / s per process, ~9 000 / s with 18 workers |
| Seed-tuple storage | ~5 900 rows / s |
| Row size | ~584 bytes (against ~11 088 for the JSON) |

A collision check over 2 000 000 generated profiles reports zero
collisions on the serial, the signature, the fingerprint and the device
identity.

---

## 15. Known limits

- **The Chromium patch must be applied and rebuilt.** The control plane
  alone cannot spoof the browser; the C++ core is what makes the values
  native. Without the patch the profile configuration is still written and
  served, but the browser reports its real values.
- **The device catalog has 11 presets.** It covers the common SoC/GPU
  combinations, not every shipping Android device.
- **The time-zone database must be present.** `zoneinfo` falls back to a
  warning rather than an error when the tzdata is missing.
- **The probe needs a Chromium build with the patch** to report a
  meaningful distinct ratio; the simulator exists for the case where it
  does not.
