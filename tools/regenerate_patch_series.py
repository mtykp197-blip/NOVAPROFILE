"""Regenerate the NOVA patch series from the Chromium checkout.

The series has two parts:

* ``0001-nova-profile-core.patch`` -- the identity: the User-Agent, the
  navigator's own attributes, the client hints and the WebDriver flag,
  together with the NOVA sources that serve them, the build wiring that
  compiles those sources into Blink and the bootstrap that installs the
  profile configuration in the browser process.

* ``0002-nova-profile-hardware-surfaces.patch`` -- the surfaces that are
  read on their own: the screen metrics, the canvas and WebGL readback
  pixels, and the WebGL identity strings.

The local build-environment accommodations this host needs -- the relaxed
Windows SDK guard, the ATL shim -- are deliberately *not* part of the
series: they are about the machine the verification runs on, not about
the profile engine.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: The Chromium checkout the patches are generated from.
#:
#: The environment variable wins, so the same script works on every
#: machine; the default is where this repository's build puts it.
CHROMIUM = Path(os.environ.get("NOVA_CHROMIUM_SRC", r"C:\chromium\src"))

#: Where the generated patches are written.
#:
#: The default is resolved from the location of this file rather than
#: written out, because a hard-coded path is a path that is wrong on the
#: next machine.
_PATCH_DIR = Path(__file__).resolve().parent.parent / "novaprofile" / "core_patch" / "patches"
PATCH_DIR = Path(os.environ.get("NOVA_PATCH_DIR", _PATCH_DIR))

# --------------------------------------------------------------------------
# The files each patch carries.
# --------------------------------------------------------------------------

CORE_FILES = [
    # The identity surfaces.
    "third_party/blink/renderer/core/events/navigator_events.cc",
    "third_party/blink/renderer/core/execution_context/navigator_base.cc",
    "third_party/blink/renderer/core/frame/navigator.cc",
    "third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc",
    "third_party/blink/renderer/core/frame/navigator_device_memory.cc",
    "third_party/blink/renderer/core/frame/navigator_id.cc",
    "third_party/blink/renderer/core/frame/navigator_language.cc",
    "third_party/blink/renderer/core/frame/navigator_ua.cc",
    "third_party/blink/renderer/core/frame/navigator_ua_data.cc",
    # The NOVA sources.
    "third_party/blink/renderer/core/nova/BUILD.gn",
    "third_party/blink/renderer/core/nova/nova_blink_patch.cc",
    "third_party/blink/renderer/core/nova/nova_chromium_hooks.h",
    "third_party/blink/renderer/core/nova/nova_core_config.cc",
    "third_party/blink/renderer/core/nova/nova_core_config.h",
    "third_party/blink/renderer/core/nova/nova_pixel_noise.cc",
    "third_party/blink/renderer/core/nova/nova_pixel_noise.h",
    # The build wiring.
    "third_party/blink/renderer/core/BUILD.gn",
    "third_party/blink/renderer/modules/BUILD.gn",
    # The configuration bootstrap.
    "content/app/BUILD.gn",
    "content/app/content_main_runner_impl.cc",
]

SURFACE_FILES = [
    "third_party/blink/renderer/core/frame/local_dom_window.cc",
    "third_party/blink/renderer/core/frame/screen.cc",
    "third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc",
    "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc",
]


def git(*args: str) -> str:
    """Run a git command in the Chromium checkout and return its stdout."""

    completed = subprocess.run(
        ["git", *args],
        cwd=str(CHROMIUM),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def diff_for(paths: list[str]) -> str:
    """The unified diff for the given paths, in the given order."""

    if not paths:
        return ""
    return git("diff", "--no-color", "--no-ext-diff", "--", *paths)


# --------------------------------------------------------------------------
# The headers.
# --------------------------------------------------------------------------

CORE_HEADER = """\
# NOVA Profile Core -- Chromium patch series, part 1 of 2: the identity.
#
# Apply to a Chromium 159 checkout with:
#
#     git apply novaprofile/core_patch/patches/0001-nova-profile-core.patch
#     git apply novaprofile/core_patch/patches/0002-nova-profile-hardware-surfaces.patch
#
# This patch substitutes the *identity* surfaces: the User-Agent, the
# navigator's own attributes, the client hints and the WebDriver flag.  It
# also carries the NOVA sources that serve them, the build wiring that
# compiles those sources into Blink, and the bootstrap that installs the
# profile configuration in the browser process.
#
# Every change is a *substitution* of an existing accessor with a
# config-backed accessor; no new DOM property is introduced, so a page
# cannot detect the patch by enumerating the prototype.
#
# Every substituted accessor keeps the stock behaviour as its fallback: if
# the profile configuration has not been loaded -- which is the case in the
# browser's own pages and in a crash handler -- the original value is
# returned unchanged.  A patch that replaced the fallback would make the
# browser itself inconsistent.  The stock fallback is therefore the *body*
# that the accessor already had, not a call to a separate helper: the
# ``NavigatorID::Standard*`` and ``NavigatorUAData::Default*`` helpers that
# the previous series relied on no longer exist in this tree.
#
# The configuration is loaded lazily by the hooks themselves
# (``NovaConfigHolder::EnsureInitialized``), so a renderer or the GPU
# process that reaches an accessor before the browser's own initialization
# has run still reports the profile's values, never the host's.
#
#
# Where the accessors live in this tree
#
# Blink was reorganised, so the paths the previous series named no longer
# exist.  The accessors are now spread over the following files:
#
#   third_party/blink/renderer/core/events/navigator_events.cc
#   third_party/blink/renderer/core/execution_context/navigator_base.cc
#   third_party/blink/renderer/core/frame/navigator.cc
#   third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc
#   third_party/blink/renderer/core/frame/navigator_device_memory.cc
#   third_party/blink/renderer/core/frame/navigator_id.cc
#   third_party/blink/renderer/core/frame/navigator_language.cc
#   third_party/blink/renderer/core/frame/navigator_ua.cc
#   third_party/blink/renderer/core/frame/navigator_ua_data.cc
#
#
# The hook layer
#
# The layer that serves the values lives next to them, in
#
#   third_party/blink/renderer/core/nova/
#
# and is built as the target
#
#   //third_party/blink/renderer/core/nova:nova_core
#
# The target is added to the dependencies of the Blink ``core`` component,
# of the Blink ``modules`` component and of ``content/app``, so the patched
# translation units find the hooks header and the browser process finds
# the bootstrap.
#
# The hooks header is included by its bare name -- ``nova_chromium_hooks.h``
# -- and the directory is put on the include path of every consumer through
# the ``nova_include_dirs`` configuration that the target exports.
#
#
# The configuration bootstrap
#
# ``nova::InitializeFromCommandLine()`` is called from
# ``content::ContentMainRunnerImpl::Initialize()``, right after the process
# type is read and before any child process is forked.  The renderer, the
# GPU process and the network service therefore inherit the configuration
# from the same ``--nova-config`` switch, and there is no window in which a
# page can observe the host's values.
#
"""

SURFACE_HEADER = """\
# NOVA Profile Core -- Chromium patch series, part 2 of 2: the surfaces.
#
# Apply to a Chromium 159 checkout with:
#
#     git apply novaprofile/core_patch/patches/0001-nova-profile-core.patch
#     git apply novaprofile/core_patch/patches/0002-nova-profile-hardware-surfaces.patch
#
# This patch substitutes the surfaces that a fingerprinting script can read
# on their own:
#
#   third_party/blink/renderer/core/frame/local_dom_window.cc
#   third_party/blink/renderer/core/frame/screen.cc
#   third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc
#   third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc
#
# The screen metrics are served from the profile's own configuration.  The
# canvas and the WebGL readback pixels are perturbed with a key that is
# derived from the profile's own seed, so that two profiles never collapse
# into the same canvas bucket and so that the canvas hash and the WebGL
# hash cannot be linked to each other.  The WebGL identity strings are
# served through the same configuration.
#
# The noise hooks are not substitutions: they do not replace a value, they
# perturb a buffer, and their fallback is "do nothing".  A browser that
# was started without a configuration therefore returns the pixels the
# renderer produced, which is exactly what an unpatched browser does.
#
# The geometry perturbation is applied as an integer pixel offset, so the
# layout, the painting and the hit testing stay consistent with each other.
#
# Part 1 of 2 carries the identity surfaces, the NOVA sources and the build
# wiring; it has to be applied first.
#
"""


def write_patch(name: str, header: str, paths: list[str]) -> None:
    """Write one patch of the series."""

    body = diff_for(paths)
    if not body.strip():
        raise SystemExit(f"{name}: the diff is empty")
    target = PATCH_DIR / name
    target.write_text(header + body, encoding="utf-8", newline="\n")
    added = body.count("\n+") - body.count("\n+++")
    removed = body.count("\n-") - body.count("\n---")
    print(f"{name}: {len(paths)} files, +{added}/-{removed}")


def main() -> None:
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    write_patch(
        "0001-nova-profile-core.patch", CORE_HEADER, CORE_FILES
    )
    write_patch(
        "0002-nova-profile-hardware-surfaces.patch", SURFACE_HEADER, SURFACE_FILES
    )


if __name__ == "__main__":
    main()
