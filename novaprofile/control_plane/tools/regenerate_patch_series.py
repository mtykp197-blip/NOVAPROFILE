"""Regenerate the NOVA patch series from a patched Chromium checkout.

The series is three patches, applied in order:

  1. the identity -- the User-Agent, the navigator's own attributes,
     the client hints, the WebDriver flag, and the NOVA sources that
     serve them;
  2. the hardware surfaces -- the screen, the viewport, the canvas and
     the WebGL;
  3. the independent surfaces and the TLS -- the fonts, the sensors,
     the audio, the storage estimate, the GPU adapter and the cipher
     suites.

The patches are generated from a checkout that already carries the
changes, because that checkout is the one that was compiled and run.
A patch series that is written by hand and a tree that is compiled are
two different things, and only one of them is true.

The build workarounds -- the patched Python scripts the Windows build
needs -- are *not* in the series.  They are local fixes to the build,
not part of the core, and shipping them in the series would apply a
local fix to every future checkout.

Usage::

    python tools/regenerate_patch_series.py --chromium C:\\chromium\\src
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: The repository root, derived from this file's location.
ROOT = Path(__file__).resolve().parents[2]

#: Where the series lives.
PATCH_DIR = ROOT / "novaprofile" / "core_patch" / "patches"

#: The header of each patch, in the order the patches are applied.
HEADERS: tuple[tuple[str, str], ...] = (
    (
        "0001-nova-profile-core.patch",
        """# NOVA Profile Core -- Chromium patch series, part 1 of 3: the identity.
#
# Apply to a Chromium checkout with:
#
#     git apply novaprofile/core_patch/patches/0001-nova-profile-core.patch
#     git apply novaprofile/core_patch/patches/0002-nova-profile-hardware-surfaces.patch
#     git apply novaprofile/core_patch/patches/0003-nova-profile-independent-surfaces-and-tls.patch
""",
    ),
    (
        "0002-nova-profile-hardware-surfaces.patch",
        """# NOVA Profile Core -- Chromium patch series, part 2 of 3: the hardware.
#
# The screen, the viewport, the canvas and the WebGL.  These are the
# surfaces that report the shape of the machine and the graphics part
# it carries, so they are patched together: a canvas that reports one
# panel and a screen that reports another is a device that never
# existed.
""",
    ),
    (
        "0003-nova-profile-independent-surfaces-and-tls.patch",
        """# NOVA Profile Core -- Chromium patch series, part 3 of 3: the rest.
#
# The fonts, the motion sensors, the audio, the storage estimate, the
# GPU adapter, the media devices and the cipher suites.
#
# The name of this patch is the point of it: these surfaces are
# *independent* of one another.  A site that reads the audio and the
# canvas of the same page and finds them agreeing finds one device; a
# site that finds them disagreeing finds two devices wearing one
# browser, which is the shape of a farm.
""",
    ),
)

#: The files of each patch.
#
# The first patch carries the NOVA sources themselves; the build
# copies them into the tree, so the series has to carry them too.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "0001-nova-profile-core.patch",
        (
            "third_party/blink/renderer/core/events/navigator_events.cc",
            "third_party/blink/renderer/core/execution_context/navigator_base.cc",
            "third_party/blink/renderer/core/frame/navigator.cc",
            "third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc",
            "third_party/blink/renderer/core/frame/navigator_device_memory.cc",
            "third_party/blink/renderer/core/frame/navigator_id.cc",
            "third_party/blink/renderer/core/frame/navigator_language.cc",
            "third_party/blink/renderer/core/frame/navigator_ua.cc",
            "third_party/blink/renderer/core/frame/navigator_ua_data.cc",
            "third_party/blink/renderer/core/nova/BUILD.gn",
            "third_party/blink/renderer/core/nova/nova_blink_patch.cc",
            "third_party/blink/renderer/core/nova/nova_chromium_hooks.h",
            "third_party/blink/renderer/core/nova/nova_core_config.cc",
            "third_party/blink/renderer/core/nova/nova_core_config.h",
            "third_party/blink/renderer/core/nova/nova_js_runtime_code.cc",
            "third_party/blink/renderer/core/nova/nova_pixel_noise.cc",
            "third_party/blink/renderer/core/nova/nova_pixel_noise.h",
            "third_party/blink/renderer/core/nova/nova_transport.cc",
            "third_party/blink/renderer/core/nova/nova_transport.h",
            "third_party/blink/renderer/core/nova/nova_unit_values.cc",
            "third_party/blink/renderer/core/nova/nova_unit_values.h",
            "third_party/blink/renderer/core/BUILD.gn",
            "third_party/blink/renderer/modules/BUILD.gn",
            "content/app/BUILD.gn",
            "content/app/content_main_runner_impl.cc",
            "content/browser/BUILD.gn",
            "content/browser/client_hints/client_hints.cc",
            "content/browser/nova/nova_client_hints.cc",
            "content/browser/nova/nova_client_hints.h",
            "content/browser/service_host/utility_process_host.cc",
        ),
    ),
    (
        "0002-nova-profile-hardware-surfaces.patch",
        (
            "third_party/blink/renderer/core/frame/local_dom_window.cc",
            "third_party/blink/renderer/core/frame/screen.cc",
            "third_party/blink/renderer/core/css/media_values.cc",
            "third_party/blink/renderer/core/nova_js_layer.cc",
            "third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc",
            "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc",
            "third_party/blink/renderer/modules/webgl/webgl2_rendering_context_base.cc",
            "third_party/blink/renderer/modules/webgl/webgl_rendering_context_webgpu_base.cc",
        ),
    ),
    (
        "0003-nova-profile-independent-surfaces-and-tls.patch",
        (
            "content/browser/gpu/gpu_process_host.cc",
            "content/browser/network_service_instance_impl.cc",
            "content/browser/renderer_host/render_process_host_impl.cc",
            "content/gpu/BUILD.gn",
            "content/gpu/gpu_main.cc",
            "content/utility/BUILD.gn",
            "content/utility/services.cc",
            "net/BUILD.gn",
            "net/socket/ssl_client_socket_impl.cc",
            "third_party/blink/renderer/core/css/css_font_selector.cc",
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
        ),
    ),
)


def _git(chromium: Path, *args: str) -> str:
    """Run a git command in the checkout and return its output."""

    result = subprocess.run(
        ["git", "-C", str(chromium), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def _diff(chromium: Path, paths: tuple[str, ...]) -> str:
    """Return the diff of the given paths, as a patch."""

    # The untracked files are the NOVA sources the build adds; they are
    # staged first so that the diff carries them as new files.
    existing = [p for p in paths if (chromium / p).exists()]
    missing = [p for p in paths if p not in existing]
    if missing:
        raise RuntimeError(f"the checkout does not have: {', '.join(missing)}")

    return _git(chromium, "diff", "--binary", "--no-color", "--", *existing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chromium",
        default=r"C:\chromium\src",
        help="The patched Chromium checkout.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the series out.  Without it the result is only reported.",
    )
    args = parser.parse_args()

    chromium = Path(args.chromium)
    if not (chromium / ".git").exists():
        print(f"not a git checkout: {chromium}", file=sys.stderr)
        return 2

    headers = dict(HEADERS)
    for name, paths in GROUPS:
        body = _diff(chromium, paths)
        if not body.strip():
            print(f"{name}: no differences")
            continue
        content = headers[name] + "\n" + body
        target = PATCH_DIR / name
        if args.write:
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"{name}: {len(content.splitlines())} lines")
        else:
            print(f"{name}: {len(content.splitlines())} lines (not written)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
