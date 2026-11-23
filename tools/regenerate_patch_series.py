"""Regenerate the NOVA patch series from the Chromium checkout.

The series has three parts:

* ``0001-nova-profile-core.patch`` -- the identity: the User-Agent, the
  navigator's own attributes, the client hints and the WebDriver flag,
  together with the NOVA sources that serve them, the build wiring that
  compiles those sources into Blink and the bootstrap that installs the
  profile configuration in the browser process.

* ``0002-nova-profile-hardware-surfaces.patch`` -- the surfaces that are
  read on their own: the screen metrics, the viewport, the canvas and
  WebGL readback pixels, and the WebGL identity strings.

* ``0003-nova-profile-independent-surfaces-and-tls.patch`` -- the
  surfaces a script may read alone (the geometry, the audio, the media
  devices, the motion sensors, the storage estimate, the fonts and the
  WebGPU adapter) and the TLS handshake, which is produced by the
  network service before any script runs.

Why the series is rebuilt from scratch trees
--------------------------------------------

The Chromium checkout holds every change at once, so a plain ``git
diff`` would produce one patch with everything in it.  The series has
to be split, because the parts are applied in order and a part must not
carry a change that belongs to a later one.

The split is done by building four trees in a scratch directory -- the
upstream release, then the release plus each part -- and diffing
between them.  The Chromium checkout that the browser is built from is
never modified, and the scratch trees are thrown away afterwards.

The local build-environment accommodations this host needs -- the relaxed
Windows SDK guard, the ATL shim -- are deliberately *not* part of the
series: they are about the machine the verification runs on, not about
the profile engine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

#: The Chromium checkout the patches are generated from.
#:
#: The environment variable wins, so the same script works on every
#: machine; the default is where this repository's build puts it.
CHROMIUM = Path(os.environ.get("NOVA_CHROMIUM_SRC", r"C:\chromium\src"))

#: The scratch directory the four trees are built in.
SCRATCH = Path(os.environ.get("NOVA_SCRATCH_DIR", r"C:\chromium\_series_tmp"))

#: The commit the upstream side of the diff is taken from.
#:
#: The Chromium checkout holds the NOVA changes and they are committed
#: there, so the head of the checkout is not upstream.  The series is
#: the difference between the upstream release and the checkout, and
#: the upstream release is this commit.
#:
#: The environment variable wins, so the same script works on every
#: machine.
BASELINE = os.environ.get("NOVA_CHROMIUM_BASELINE", "339b0817fb")

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

#: The files the first patch creates, so that the upstream side of the
#: diff is absent rather than empty.
CREATED = {
    "third_party/blink/renderer/core/nova/BUILD.gn",
    "third_party/blink/renderer/core/nova/nova_blink_patch.cc",
    "third_party/blink/renderer/core/nova/nova_chromium_hooks.h",
    "third_party/blink/renderer/core/nova/nova_core_config.cc",
    "third_party/blink/renderer/core/nova/nova_core_config.h",
    "third_party/blink/renderer/core/nova/nova_js_runtime_code.cc",
    "third_party/blink/renderer/core/nova/nova_pixel_noise.cc",
    "third_party/blink/renderer/core/nova/nova_pixel_noise.h",
}

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
    *sorted(CREATED),
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

INDEPENDENT_FILES = [
    # The configuration has to reach the processes that do the work.
    "content/browser/network_service_instance_impl.cc",
    "content/browser/renderer_host/render_process_host_impl.cc",
    "content/gpu/BUILD.gn",
    "content/gpu/gpu_main.cc",
    "content/utility/BUILD.gn",
    "content/utility/services.cc",
    # The TLS handshake.
    "net/BUILD.gn",
    "net/socket/ssl_client_socket_impl.cc",
    # The surfaces that are read on their own.
    "third_party/blink/renderer/core/css/font_face_set.cc",
    "third_party/blink/renderer/core/dom/element.cc",
    "third_party/blink/renderer/core/dom/range.cc",
    "third_party/blink/renderer/modules/device_orientation/BUILD.gn",
    "third_party/blink/renderer/modules/device_orientation/device_motion_event_pump.cc",
    "third_party/blink/renderer/modules/mediastream/BUILD.gn",
    "third_party/blink/renderer/modules/mediastream/media_devices.cc",
    "third_party/blink/renderer/modules/quota/BUILD.gn",
    "third_party/blink/renderer/modules/quota/storage_manager.cc",
    "third_party/blink/renderer/modules/webaudio/BUILD.gn",
    "third_party/blink/renderer/modules/webaudio/audio_context.cc",
    "third_party/blink/renderer/modules/webaudio/audio_destination_node.cc",
    "third_party/blink/renderer/modules/webaudio/base_audio_context.h",
    "third_party/blink/renderer/modules/webaudio/offline_audio_destination_handler.cc",
    "third_party/blink/renderer/modules/webgpu/BUILD.gn",
    "third_party/blink/renderer/modules/webgpu/gpu_adapter_info.cc",
]

ALL_FILES = CORE_FILES + SURFACE_FILES + INDEPENDENT_FILES


# --------------------------------------------------------------------------
# The headers.
# --------------------------------------------------------------------------

_APPLY_LINES = """#
# Apply to a Chromium 159 checkout with:
#
#     git apply novaprofile/core_patch/patches/0001-nova-profile-core.patch
#     git apply novaprofile/core_patch/patches/0002-nova-profile-hardware-surfaces.patch
#     git apply novaprofile/core_patch/patches/0003-nova-profile-independent-surfaces-and-tls.patch
"""

CORE_HEADER = f"""\
# NOVA Profile Core -- Chromium patch series, part 1 of 3: the identity.
{_APPLY_LINES}
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

SURFACE_HEADER = f"""\
# NOVA Profile Core -- Chromium patch series, part 2 of 3: the surfaces.
{_APPLY_LINES}
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
# The viewport -- ``innerWidth``, ``innerHeight``, ``outerWidth``,
# ``outerHeight``, ``screenX`` and ``screenY`` -- is served from the
# profile as well: on a desktop those are the size the user dragged the
# window to, which has no relation to the handset the user agent claims.
#
# The noise hooks are not substitutions: they do not replace a value, they
# perturb a buffer, and their fallback is "do nothing".  A browser that
# was started without a configuration therefore returns the pixels the
# renderer produced, which is exactly what an unpatched browser does.
#
# The geometry perturbation is applied as an integer pixel offset, so the
# layout, the painting and the hit testing stay consistent with each other.
#
# Part 1 of 3 carries the identity surfaces, the NOVA sources and the build
# wiring; it has to be applied first.
#
"""

INDEPENDENT_HEADER = f"""\
# NOVA Profile Core -- Chromium patch series, part 3 of 3: the independent
# surfaces and the TLS handshake.
{_APPLY_LINES}
# A fingerprinting script does not have to combine the canvas hash with the
# WebGL hash with the device serial.  It can read one surface on its own --
# the audio fingerprint, the geometry of a rectangle, the media device
# list, the motion readings or the storage estimate -- and cluster on that
# alone.  Each of those therefore needs its own native accessor, and each
# of them is derived from the profile's own salt, so that two profiles
# never report the same value.
#
# The geometry
#
# ``getBoundingClientRect`` and ``getClientRects`` are read by every
# layout-aware script.  The patch applies the profile's own integer pixel
# offsets, to the element rectangle and to the range rectangle separately,
# so that the two readbacks stay independent observations of the same
# layout.
#
# The audio
#
# ``AudioContext.sampleRate``, ``baseLatency``, ``outputLatency`` and
# ``maxChannelCount`` are read on their own, and the rendered samples
# themselves are the fingerprint.  The patch substitutes the parameters
# and perturbs the rendered samples at the twenty-fourth bit of the
# mantissa, which is below the level of the audio pipeline's own
# quantisation and above the level of a reference render.
#
# The media devices, the motion sensors and the storage estimate
#
# The device identifiers of the host are stable across every profile that
# runs on the machine, which makes them an effective way to link two
# profiles that are supposed to be two different devices.  The patch
# rewrites them with the profile's salt while keeping the shape of the
# identifier.
#
# ``DeviceMotionEvent`` hands out the raw accelerometer, gyroscope and
# gravity readings of the host, which on a desktop is a stream of NaN.
# The patch serves the profile's own readings.
#
# ``navigator.storage.estimate()`` reports the quota and the usage of the
# host's disk.  The patch serves the profile's own numbers.
#
# The fonts and the WebGPU adapter
#
# ``document.fonts.check`` reports whether a family is installed, and the
# installed list is a property of the host's font configuration.  The
# patch serves the profile's own list.
#
# ``navigator.gpu.requestAdapter()`` returns the adapter name, the vendor
# and the architecture of the host's graphics card.  The patch serves the
# profile's own adapter.
#
# The TLS handshake
#
# JA3 is the MD5 of the ClientHello -- the cipher suites, the extension
# order, the elliptic curves and the point formats, in the order the
# client sent them.  It is produced by the network service, in C++, before
# any script runs, so no amount of JavaScript can change it: the only way
# to change it is to change the bytes the client puts on the wire.
#
# The patch therefore installs the profile's cipher list, curve list,
# signature algorithm list and ALPN list on the BoringSSL context before
# the handshake, and hands the configuration down to the network service
# and to the GPU process on the command line, so that every process
# reports the same profile.
#
"""


# --------------------------------------------------------------------------
# The trees.
# --------------------------------------------------------------------------


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


def write_tree(root: Path, contents: dict[str, str]) -> None:
    """Write the given files into ``root``."""

    for rel, text in contents.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


#: The names of the scratch trees the diffs are taken between.  They are
#: the first component of every path git writes and are not part of the
#: repository path the patch has to carry.
_SCRATCH_TREES = ("base", "one", "two", "three")


def _normalise_patch_paths(text: str, posix_rel: str) -> str:
    """Rewrite the paths in a git diff to the repository's own paths.

    ``git diff --no-index`` writes the path of the scratch tree it
    compared, with the separator of the host operating system and, when
    the path holds a character git considers special, in double quotes
    with every backslash doubled.  On Windows that is

        diff --git "a/base\\\\third_party\\\\blink\\\\..." "b/one\\\\..."

    which is neither a repository path nor a patch that applies to a
    POSIX checkout.  This rewrites it to

        diff --git a/third_party/blink/... b/third_party/blink/...

    The four scratch tree names are the only first components that can
    appear, because the diffs are always taken between two of them.
    """

    def _clean(path: str) -> str:
        """Turn one path as git wrote it into a repository path."""

        path = path.strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        # Git doubles every backslash it writes inside a quoted path.
        path = path.replace("\\\\", "\\")
        # The separator of the host, which is never the repository's.
        path = path.replace("\\", "/")
        # The side prefix, which the caller adds back.
        for side in ("a/", "b/"):
            if path.startswith(side):
                path = path[len(side):]
                break
        # The scratch tree the diff was taken against.
        for tree in _SCRATCH_TREES:
            if path.startswith(f"{tree}/"):
                path = path[len(tree) + 1:]
                break
        return path

    # The ``diff --git`` header carries the old and the new path.
    text = re.sub(
        r'diff --git (?P<old>"[^"]*"|\S+) (?P<new>"[^"]*"|\S+)',
        lambda m: f"diff --git a/{_clean(m.group('old'))} b/{_clean(m.group('new'))}",
        text,
    )

    # The ``---`` and ``+++`` lines carry the same paths, each with the
    # prefix of its side.
    text = re.sub(
        r'^--- (?P<path>"[^"]*"|\S*)$',
        lambda m: f"--- a/{_clean(m.group('path'))}",
        text,
        flags=re.M,
    )
    text = re.sub(
        r'^\+\+\+ (?P<path>"[^"]*"|\S*)$',
        lambda m: f"+++ b/{_clean(m.group('path'))}",
        text,
        flags=re.M,
    )

    # The rename and copy headers, if any, carry the paths as well.
    text = re.sub(
        r'^(?P<kind>rename (?:from|to)|copy (?:from|to)) (?P<path>"[^"]*"|\S+)$',
        lambda m: f"{m.group('kind')} {_clean(m.group('path'))}",
        text,
        flags=re.M,
    )

    return text


def build_trees() -> dict[str, dict[str, str]]:
    """Build the four trees the series is diffed between.

    ``base`` is the upstream release, and each of the other three holds
    the release plus the part it is named after.

    The upstream side comes from a *baseline* commit rather than from
    the head of the checkout.  The checkout holds the NOVA changes and
    they are committed there, so the head of it is not upstream: diffing
    against it would produce an empty patch for every file the series
    touches.
    """

    upstream: dict[str, str] = {}
    for rel in ALL_FILES:
        if rel in CREATED:
            upstream[rel] = ""
        else:
            upstream[rel] = git("show", f"{BASELINE}:{rel}")

    current: dict[str, str] = {
        rel: (CHROMIUM / rel).read_text(encoding="utf-8") for rel in ALL_FILES
    }

    one = {**upstream, **{rel: current[rel] for rel in CORE_FILES}}
    two = {**one, **{rel: current[rel] for rel in SURFACE_FILES}}
    three = {**two, **{rel: current[rel] for rel in INDEPENDENT_FILES}}

    return {"base": upstream, "one": one, "two": two, "three": three}


def diff_trees(old: dict[str, str], new: dict[str, str], paths: list[str]) -> str:
    """The unified diff of the given files, in the git patch format."""

    chunks: list[str] = []
    for rel in paths:
        old_text = old.get(rel, "")
        new_text = new.get(rel, "")
        if old_text == new_text:
            continue

        old_path = SCRATCH / "base" / rel
        new_path = SCRATCH / "one" / rel
        old_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text(old_text, encoding="utf-8", newline="\n")
        new_path.write_text(new_text, encoding="utf-8", newline="\n")

        completed = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-color",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                "--",
                str(old_path.relative_to(SCRATCH)),
                str(new_path.relative_to(SCRATCH)),
            ],
            cwd=str(SCRATCH),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = completed.stdout
        if not text:
            continue

        # The path that git wrote carries the name of the scratch tree
        # and a quoted backslash; the patch has to carry the repository
        # path instead.  The repository path is always written with
        # forward slashes: a patch that carries backslashes does not
        # apply on a POSIX checkout, and the coverage tests look the
        # file up by its forward-slash path.
        text = _normalise_patch_paths(text, rel.replace("\\", "/"))

        if not old_text:
            # A file that the part creates.  Git has to be told that the
            # old side is absent, or the patch does not apply to a tree
            # that does not have the file yet.
            text = re.sub(
                r'^--- (?:\"?a/[^\n]*\"?)$',
                "--- /dev/null",
                text,
                count=1,
                flags=re.M,
            )
            text = re.sub(
                r"^index e69de29\.\.",
                "new file mode 100644\nindex 0000000000..",
                text,
                count=1,
                flags=re.M,
            )

        chunks.append(text)

    return "".join(chunks)


def write_patch(name: str, header: str, body: str) -> None:
    """Write one patch of the series."""

    if not body.strip():
        raise SystemExit(f"{name}: the diff is empty")
    target = PATCH_DIR / name
    target.write_text(header + body, encoding="utf-8", newline="\n")
    added = body.count("\n+") - body.count("\n+++")
    removed = body.count("\n-") - body.count("\n---")
    print(f"{name}: {len(body)} bytes, +{added}/-{removed}")


def main() -> None:
    PATCH_DIR.mkdir(parents=True, exist_ok=True)

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)

    trees = build_trees()
    for name, contents in trees.items():
        write_tree(SCRATCH / name, contents)

    write_patch(
        "0001-nova-profile-core.patch",
        CORE_HEADER,
        diff_trees(trees["base"], trees["one"], CORE_FILES),
    )
    write_patch(
        "0002-nova-profile-hardware-surfaces.patch",
        SURFACE_HEADER,
        diff_trees(trees["one"], trees["two"], SURFACE_FILES),
    )
    write_patch(
        "0003-nova-profile-independent-surfaces-and-tls.patch",
        INDEPENDENT_HEADER,
        diff_trees(trees["two"], trees["three"], INDEPENDENT_FILES),
    )

    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    main()
