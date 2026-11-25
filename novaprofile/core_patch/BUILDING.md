# Building the native core

The native core is a Chromium fork.  It reads its configuration from a
file, and the file is the same on every platform -- what differs is the
binary that Chromium was compiled into.

Chromium has one hard rule about this:

```gn
# build/config/BUILDCONFIG.gn
assert(host_os == "linux", "Android builds are only supported on Linux.")
```

The rule is in the build configuration of Chromium itself, and it fires
during `gn gen` -- before a single source file is compiled.  There is
no flag, no environment variable and no patch that makes an Android
target build on a Windows host.  The check is deliberate: the Android
build needs the Android NDK, the Android SDK, and a Linux sysroot, and
the build scripts for those assume a Linux host.

So there are two builds, and they are not interchangeable:

| Output directory | Host | Target | What it is for |
|---|---|---|---|
| `out/nova` | Windows | `win`/`x64` | Verifying that the core patch compiles and behaves |
| `out/nova-<major>` | Linux | `android`/`arm64` | The binary that ships |

The Windows build is a **test bench**.  It is built from the same
sources, the same patch series and the same configuration file, so it
proves the patch is correct.  It is not a product.

## The build script

`build.py` drives the whole thing.  It reads `build_config.json`, which
lists every Chromium version the core can be built for, the patch series
to apply, and the GN arguments for each.

```bash
# from the repository root
python novaprofile/core_patch/build.py build --major 159
```

On a Linux host with the Android SDK and NDK in place, that command
fetches the branch, applies the patch series, runs `gn gen`, runs
`ninja`, and writes the result to `out/nova-159`.

## What a Linux host needs

| Requirement | Version | Notes |
|---|---|---|
| Linux | x86_64 | Any modern distribution; Ubuntu 22.04 or newer works |
| Python | 3.11+ | The build scripts use it |
| `depot_tools` | current | `gclient`, `gn`, `ninja` |
| Android SDK | API 34 | Platform tools only |
| Android NDK | r26 or newer | The Clang toolchain builds the native code |
| Disk | 60 GB+ | The checkout is about 23 GB, the output directory about 30 GB |
| RAM | 16 GB+ | Linking `chrome` needs several gigabytes |

The NDK is the part that is easy to get wrong.  The toolchain lives
inside the NDK, and the build looks for it by path:

```bash
export ANDROID_NDK_ROOT=$HOME/Android/Sdk/ndk/26.1.10909125
export ANDROID_SDK_ROOT=$HOME/Android/Sdk
```

### Getting the NDK

```bash
sdkmanager "platform-tools" "platforms;android-34"
sdkmanager --install "ndk;26.1.10909125"
```

## A core that is already built

The core is large -- a Windows build of it is about five hundred
megabytes -- and Git refuses an object of that size, so it does not
live in the repository.  A build that has already been made is
published on the project's releases page instead, and it is fetched
from there.

```powershell
# The published core, unpacked where the shell expects it.
Invoke-WebRequest -Uri "<release-url>" -OutFile core.zip
Expand-Archive core.zip -DestinationPath core\build\cores\160.0.8177.0
```

The shell reads the core from `core/build/cores/<version>`, and the
version is the one the manifest names.

## Verifying a build

A build that compiled is not a build that works.  The check that matters
is whether the fork reads back the values it was given:

```bash
python novaprofile/core_patch/build.py verify --major 159
```

This starts the built binary with a profile, reads the surfaces back
out of the running browser, and compares them against the profile the
control plane generated.  A surface that disagrees is a bug in the
patch, and the command says which one.

## The patch series

Three patches, applied in order:

| Patch | What it does |
|---|---|
| `0001-nova-profile-core.patch` | Wires the configuration file into Blink and the browser |
| `0002-nova-profile-hardware-surfaces.patch` | Adds the hardware surfaces that need native access |
| `0003-nova-profile-independent-surfaces-and-tls.patch` | Gives each profile its own surfaces, and the TLS stack its own fingerprint |

The series is regenerated against the Chromium tree it belongs to by
`tools/regenerate_patch_series.py` and
`novaprofile/control_plane/tools/regenerate_patch_series.py`.  When
Chromium moves underneath the patches, the series is regenerated
rather than the checks loosened.

They are applied with `git apply`, which refuses a patch whose line
counts do not match the file it is applied to.  A patch that needs
`--recount` to apply is a patch that has drifted away from the tree it
was written for, and it is a broken patch -- so the build does not
recover from it.  When Chromium moves underneath the patches, the fix
is to regenerate them against the new tree, not to loosen the check.
