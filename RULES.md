# NOVAPROFILE — the working rules

These are the rules this project is built under.  They exist so that the
work stays correct when it is picked up by someone else, on another
machine, weeks later.  **Every rule below was added because something
went wrong without it.**

Read this file before changing anything.

---

## 1. The rules of the rules

| # | Rule | Why it exists |
|---|---|---|
| R1 | **Zero collisions.** No two profiles may share a value on any of the 12 guarded axes. | The product claim. A collision is a lost customer. |
| R2 | **Maximum diversity.** Every field is varied, not just the User-Agent. | A fleet that only varies the UA clusters immediately. |
| R3 | **Cross-axis coherence.** Every axis must agree with every other one. | A profile whose GPU contradicts its SoC is worse than no profile. |
| R4 | **Stop on the first error.** Never scale up while a logical error remains. | A 100M run with a logic bug costs hours and proves nothing. |
| R5 | **Measure, do not assume.** Every claim is a recorded measurement. | Claims without measurements are opinions. |
| R6 | **Never delete without looking.** Observe carefully before removing a file. | The rule for cleanup; a deleted core file ends the project. |
| R7 | **The account state is not part of the project.** | It must not reach a repository. |

---

## 2. R1 — Zero collisions

### The guarded axes

A profile is a point in a 12-dimensional space.  The axes are:

| Axis | What it covers |
|---|---|
| `identity` | the User-Agent and the brand list |
| `client_hints` | the high-entropy client hints |
| `screen` | the display metrics |
| `webgl` | the WebGL renderer and the noise key |
| `canvas` | the 2D canvas readback |
| `audio` | the audio fingerprint |
| `fonts` | the font list |
| `rects` | the DOM geometry |
| `media_devices` | the media device list |
| `sensors` | the motion readings |
| `storage` | the storage estimate and the network report |
| `webgpu` | the WebGPU adapter |

### The entropy requirement

Each axis must carry at least **128 bits** of entropy.  The reason is
arithmetic, not aesthetic: with 128 bits and 100 million profiles the
birthday collision probability is

$$p \approx \frac{n^2}{2 \cdot 2^{128}} = \frac{(10^8)^2}{2 \cdot 2^{128}} \approx 1.2 \times 10^{-21}$$

which is zero for every practical purpose.  With 56 bits — which is
what the WebGL axis had before the salt was added — the same
calculation gives $p \approx 6.7\%$, which is **millions of
collisions** in a fleet of that size.

So: **an axis below 128 bits is a bug**, no matter how good the
measured sample looks.  A sample of 2,000 profiles cannot distinguish
56 bits from 128 bits; only the arithmetic can.

### How it is checked

```powershell
cd novaprofile/control_plane
python -m novaprofile.scripts.entropy_budget
```

The script must report **≥128 bits on every axis**.  If it does not,
the axis is not finished.

---

## 3. R2 — Maximum diversity

### The three classes

Every field in the profile falls into exactly one class, and the class
decides how the field is allowed to vary:

| Class | Meaning | Rule |
|---|---|---|
| **constant** | an invariant of the engine — the Chromium major, the Android API level of a given release, the Vulkan extension list | must be **derived from** the hardware, never hardcoded |
| **shared** | a family trait — the GPU model of a given SoC, the screen size of a given device | may be shared, but only by devices that really share it |
| **unique** | a per-unit identifier — the WebGL noise key, the sensor salt, the storage salt | must be unique across the fleet |

The distinction matters because a field that *looks* like a constant but
is really a unique value is invisible in the diversity report, and a
field that is really a constant but is hardcoded is a latent
contradiction.

### The rule of thumb

> A field is constant **because** of the hardware, never **despite** it.

The `_major_for_android_release` function is the pattern to follow: the
Chromium major is looked up from the Android release, so a device that
ships Android 13 gets a Chromium major that actually shipped with
Android 13.  Hardcoding `DEFAULT_BROWSER_MAJOR = 120` broke that and
produced 400+ consistency warnings across the fleet.

### How it is checked

```powershell
python -m novaprofile.scripts.diversity --count 200
```

The report classifies all 479 fields.  Read the `constant` entries: a
constant that is *not* an engine invariant is a hardcoding bug.

---

## 4. R3 — Cross-axis coherence

The pairs that must agree:

| Pair | The rule |
|---|---|
| GPU ↔ SoC | the GPU is the one that ships inside that SoC |
| GPU ↔ ABI | the ABI matches the GPU family |
| Android release ↔ Chromium major | the major shipped with that release |
| Android release ↔ client hints | the platform version matches |
| Locale ↔ timezone ↔ country | all three describe the same place |
| Currency ↔ country | the currency is the one used there |
| Network type ↔ downlink ↔ RTT | the radio band bounds the numbers |
| Sensors ↔ gravity ↔ linear acceleration | $a = g + a_{\text{linear}}$ |
| WebGPU ↔ GPU | the adapter matches the GPU |
| Texture compression ↔ GPU family | BC for desktop/Adreno, ASTC/ETC2 for Mali |
| Screen ↔ viewport | both in logical CSS pixels |
| Permissions ↔ notification + geolocation | never a synthetic empty state |

### The physical modelling rule

> Every number that a real device reports must be **derivable**, not
> random.

The accelerometer is the clearest case.  Real readings are
$9.80665 \text{ m/s}^2$ plus drift, plus the user's motion.  So the
generator models exactly that: a gravity term with a per-device
calibration drift, a linear-acceleration vector, and a sampling rate
from the set a real sensor uses (60, 100, 200 Hz).  A flat random
number in $[0, 10]$ would have been "diverse" and would have been
physically impossible.

The same applies to the network: `downlink` and `rtt` are bounded by
the radio technology — a 3G connection cannot report 50 Mbit/s, and a
WiFi connection is not bounded by the cellular limits.

### How it is checked

```powershell
python -m novaprofile.scripts.audit --count 2000 --dimension coherent
```

---

## 5. R4 — Stop on the first error

This is the rule that governs **how to work**, not what to build.

1. Test on a **small batch** first.  100 to 2,000 profiles.
2. If a collision or a consistency error appears: **stop immediately**.
3. Analyse the root cause **mathematically**.  Find the axis, find the
   entropy deficit, find the missing derivation.
4. Fix the **root cause**, not the symptom.
5. Re-test on a small batch.
6. Only when the small batch is clean, scale up.

The 100M run is the **last** step, not the first.  It takes hours; a
logic bug found during it wastes the run and still proves nothing.

### The order of operations

```
small batch  ->  audit  ->  entropy budget  ->  diversity survey
             ->  2M scale check  ->  (only then)  100M
```

---

## 6. R5 — Measure, do not assume

Every measurement is filed in `docs/measurements/` with a SHA-256
checksum and an autogenerated index.

```powershell
python -m novaprofile.scripts.measurements_cli list      # what is known
python -m novaprofile.scripts.measurements_cli verify    # checksums
python -m novaprofile.scripts.measurements_cli record --kind <kind> ...
```

**Before running a test, check the ledger.**  If the measurement is
already there, read it instead of repeating it.  This is what stops
the project from spending a day re-discovering a result it already
has.

The ledger is also the audit trail: it records what was tested, when,
on how many profiles, and what the conclusion was.  When a claim is
made, the record that proves it is named.

---

## 7. R6 — Never delete without looking

Before removing a file:

1. **Read it.**  Know what it does.
2. **Check it is not referenced.**  Grep for its name.
3. **Check it is not generated data** that a rebuild would recreate.
4. **Check it is not a core project file.**  The rule for the whole
   project, the tests, the patch series and the schema.

Cleanup is part of the work, but a deleted core file ends the project.
A scratch file left behind is an annoyance; a deleted core file is a
rewrite.

---

## 8. R7 — The account state is not part of the project

The Cursor profile holds OAuth tokens, the remote-agent computer key
and the cloud-agent credentials.  They live in
`%APPDATA%/Cursor/User/globalStorage/state.vscdb` and they are **not**
project data.

- They never enter a repository.
- The chat-transfer export carries them so that the account follows the
  chat to the other machine, so that export is a **local file** — USB,
  a drive, a private share.  Not a public cloud folder, not git.
- `.gitignore` blocks the export directory.

---

## 9. The working environment

| Rule | Detail |
|---|---|
| Python | 3.12, `python -m pytest`, `python -m ruff check .` |
| Line endings | `.gitattributes` pins LF for the patch series; a patch with CRLF context lines does not apply |
| Secrets | the only credential-shaped strings in the repository are test fixtures |
| Machine paths | no absolute path from one machine may be committed; the interpreter path lives in `.python_path`, which is ignored |

---

## 10. The state of the work

`PROGRESS.md` carries the current status and the open items.  It is
updated whenever the state changes, so that the next person does not
have to reconstruct it from the git log.

The open items, in order:

1. **Compile the patch series** against a real Chromium checkout,
   versions 100 through the newest.  The Python contract is enforced by
   the test suite; the C++ has not been through a compiler.
2. **The `js_runtime` layer** — the fallback for the surfaces the
   native core does not cover.  It is currently empty.
3. **End-to-end verification** against a real Chromium, compared
   against the simulator.
4. **The installer.**

---

## 11. Carrying this to another machine

The repository is the project.  The chat is not, but it can travel:

```powershell
# on this machine
python tools/cursor_chat_transfer.py export `
    --project-dir "C:\Users\me\Desktop\NOVAPROFILE" `
    --output "C:\Users\me\Desktop\NOVAPROFILE_CHAT_TRANSFER"

# copy that directory to the other machine by hand, then there
python tools/cursor_chat_transfer.py import `
    --export "C:\Users\me\Desktop\NOVAPROFILE_CHAT_TRANSFER"

# close Cursor completely and start it again
```

The export carries the messages, the transcripts, the chat metadata and
the account state.  The import is a merge: what is already on the
target machine wins, so running it twice is harmless.
