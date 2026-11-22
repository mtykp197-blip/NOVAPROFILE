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
| R8 | **Do not stop and do not ask.** Work through the whole task without checking in. | An interruption costs more than a wrong turn that is caught by a test. |
| R9 | **A plan is not a result.** A step is done when it is verified, not when it is written. | "Wrote the code" and "the code works" are different claims. |
| R10 | **Use agents, but manage them tightly.** Parallel agents accelerate the work; unmanaged agents waste tokens and collide on files. | The token cost of a badly scoped agent exceeds the time it saves. |
| R11 | **A test that makes its own input cannot fail.** Verify the artefact against the real thing, not against a copy of it. | 82 tests passed on a patch series that `git apply` rejected outright. |

---

## 0. R11 — A test that makes its own input cannot fail

This rule exists because of a concrete failure, not because it reads
well.

### What happened

The Chromium patch series carried 54 hunks.  **All 54 declared the
wrong number of lines**, so `git apply` refused both patches with
"corrupt patch".  The series had never applied to a real Chromium
checkout.

Eighty-two tests covered the patch machinery.  All of them passed.
Every one of them built its patch from the same data the patch was
checked against, so the tests were verifying that the code was
self-consistent — and a self-consistent patch series is exactly what a
broken one is.

### The rule

> **A test that makes its own input cannot fail.**  When the artefact is
> meant to meet the outside world — a patch against a third-party tree,
> a package that a customer installs, a profile that a site sees — the
> verification has to run against that outside world.

### What counts as a real verification

| The artefact | A test that cannot fail | The verification that counts |
|---|---|---|
| A patch for Chromium | applying the patch to a patch the test generated | `git apply --check` against a **pristine Chromium checkout**, with no `--recount`, no `--3way`, no fuzz |
| A released wheel | importing it from the source tree | installing it into a clean virtualenv and importing it there |
| A profile | asserting the fields equal the schema | running it in a **real browser** and reading the surfaces back |
| A configuration | parsing it with the project's own parser | parsing it with the **upstream** parser |

### The habit

When a component's whole purpose is to meet an external system, the
first thing to arrange is **a copy of that external system**.  A 23 GB
Chromium checkout costs an hour and a disk; it is cheaper than any
number of hours of tests that cannot fail.

Two rules follow:

- **Fetch the real thing early**, even when nothing depends on it yet.
- **Never verify an artefact against a copy the test itself produced.**
  The copy carries the same mistake.

---

## 1. R10 — Use agents, but manage them tightly

The user permits parallel agents.  They are worth using **when the work
splits cleanly**, and they are a waste **when it does not**.

### The rule

> **One agent, one file group, one verification.**  Two agents must never
> edit the same file.  And the agent that writes the code does not get
> to decide whether the code works.

### How to scope an agent

Give it:

1. **The exact file paths it may create or edit.**  A list, not a
   directory.  "You own `novaprofile/control_plane/novaprofile/js_runtime/`"
   is acceptable; "you own the backend" is not.
2. **The interface it must satisfy**, copied from `AGENT_CONTRACT.md`.
   The agent must not invent a new interface.
3. **The tests it must make pass.**  Named, with the command to run them.
4. **The command to run the tests**, and the instruction to report the
   output rather than a claim.

### The token budget

| Rule | Reason |
|---|---|
| Do not paste whole files into the task description | the agent can read them |
| Do not ask the agent to explore the whole repository | it will; give it the paths |
| Do not ask for a summary of what it did | that is what its final message is |
| Do not run two agents on the same subsystem | they will both rewrite the same file |
| Do not let an agent run the full test suite | it will take longer than the work |

The cost of an agent is roughly proportional to **how much of the
repository it reads**, not to how much it writes.  An agent told to
"investigate the collision module" will read the whole module and every
test that touches it; an agent told "read `core/collision.py` and
`tests/test_collision_and_persistence.py`, fix the X" will read two
files.

### What the agent must report back

The agent's final message is the evidence.  It must contain:

- the files it changed,
- the command it ran and its **actual output**,
- anything it could not do, and why.

An agent that reports "done" without output has not verified anything
(R9 applies to agents too).

### While the agents run

The main agent does **not** wait.  It takes the work that is not
assigned to anybody: the parts that are on the critical path, or that
need judgement about the whole project.  Waiting for an agent is the
one thing that makes parallel agents slower than doing the work
directly.

#### The division of labour in practice

The main agent keeps:

- the **integration points** — the launcher, the CLI, the package
  exports.  These are touched by every agent, so two agents editing
  them would collide.
- the **cross-cutting judgement** — deciding that a value is wrong
  across the whole fleet is a decision about the product, not about one
  module.
- the **verification** — running the suite, fixing what broke,
  checking that the agents' work fits together.

The agents take:

- the **self-contained modules** — a table, a builder, a harness.
  A module that can be written against a fixed interface and verified
  by its own test file is the ideal unit of delegation.
- the **bulk work** — 117 coordinate entries, a version matrix, a
  twelve-axis surface extractor.  This is where the parallelism pays
  for itself, because it is mechanical and large.

#### What never gets delegated

| Task | Why it stays with the main agent |
|---|---|
| Fixing a bug in a shared module | two agents would edit the same file |
| Changing a public interface | it affects every agent's work |
| Deciding that the product is wrong | that is a product decision |
| The final verification | only one agent can see the whole picture |

#### The handoff contract

An agent is given:

1. the exact file paths it owns,
2. the interface it must satisfy,
3. the test command it must make pass,
4. what it must report back.

It is **not** given permission to edit anything else.  When an agent
finds a bug outside its area, it reports it — it does not fix it.  That
rule is what keeps the merge trivial.

---

## 0. R8 — Do not stop, do not ask

The working rule for this project, and the one that matters most for
getting a large task done.

> **Do not stop to ask for permission, and do not stop to report progress
> in the middle of a task.** Keep going until the work is finished.

The user has granted full authority over this project.  Every decision
that would normally be a question -- which approach, which library, how
to structure a module, whether to keep a file -- is a decision to make,
not a question to ask.

What this means in practice:

| Situation | What to do |
|---|---|
| A design choice with two valid answers | pick one, write down why, move on |
| A dependency that is not installed | install it, or write the code against the documented API |
| A test that fails | diagnose it, fix it, re-run -- do not report it and wait |
| A file that looks like it might be garbage | read it, check its references, then decide |
| A task that turns out to be bigger than expected | do it anyway, in order |
| The user says "do everything" | do everything, in the order that unblocks the rest |

**The one exception** is a decision that cannot be reversed cheaply and
that would change the *product* rather than the *implementation* -- for
example, deleting a core file, or changing the meaning of a public
interface.  Those are recorded in `PROGRESS.md` rather than asked about,
and the safer option is taken.

### R9 — A plan is not a result

A step counts as done when it has been **verified**, not when it has been
written down.

| Claim | How it is verified |
|---|---|
| "The code works" | the test passes |
| "It is fast enough" | the measurement is filed in the ledger |
| "It is portable" | it was run on the target platform |
| "It is complete" | every surface of the axis is covered |

Writing the code is the easy half.  The half that counts is the one
that produces evidence.  A step that was written but not run is
**not** done, and it must not be reported as done.

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
