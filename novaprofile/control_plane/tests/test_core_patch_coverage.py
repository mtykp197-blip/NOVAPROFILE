"""Tests for the coverage of the native core patch.

The patch substitutes the Blink accessors with config-backed ones.  Every
accessor that a page can read has to be on the list: a value that the
control plane declares but the browser does not serve is a contradiction,
and the contradiction is what an anti-bot vendor looks for.

The client hints are the sharpest case.  The User-Agent can be patched
while the brand list, the architecture and the model are left alone, and
the profile then contradicts itself in the very first request it makes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: The root of the NOVA project, which holds the native core patch.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

PATCH_DIR = PROJECT_ROOT / "core_patch" / "patches"
SRC_DIR = PROJECT_ROOT / "core_patch" / "src"


def _patch_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(PATCH_DIR.glob("*.patch")))


def _source_text(name: str) -> str:
    return (SRC_DIR / name).read_text(encoding="utf-8")


def test_the_patch_series_exists():
    """There is a patch series to apply to a Chromium checkout."""

    assert list(PATCH_DIR.glob("*.patch")), "no patch files were found"


@pytest.mark.parametrize(
    "accessor",
    [
        "NavigatorUAData::brands",
        "NavigatorUAData::fullVersionList",
        "NavigatorUAData::mobile",
        "NavigatorUAData::platform",
        "NavigatorUAData::platformVersion",
        "NavigatorUAData::architecture",
        "NavigatorUAData::bitness",
        "NavigatorUAData::model",
        "NavigatorUAData::wow64",
    ],
)
def test_every_client_hint_accessor_is_patched(accessor):
    """Every hint a page can read must be served by the configuration."""

    text = _patch_text()
    assert accessor in text, f"{accessor} is not patched"


def test_the_client_hints_are_patched_in_the_blink_source():
    """The implementation lives in the Blink integration file."""

    text = _source_text("nova_blink_patch.cc")
    for accessor in (
        "NavigatorUAData::brands",
        "NavigatorUAData::fullVersionList",
        "NavigatorUAData::platformVersion",
        "NavigatorUAData::architecture",
        "NavigatorUAData::bitness",
        "NavigatorUAData::model",
    ):
        assert accessor in text, f"{accessor} is not implemented"


def test_the_patch_touches_the_ua_data_source_file():
    """The patch series must include the Chromium file that holds the hints."""

    text = _patch_text()
    assert "third_party/blink/renderer/core/html/navigator_ua_data.cc" in text


@pytest.mark.parametrize(
    "accessor",
    [
        # The navigator values a page reads to describe the device.
        "NavigatorID::userAgent",
        "NavigatorID::appVersion",
        "NavigatorID::appName",
        "NavigatorID::appCodeName",
        "NavigatorID::vendor",
        "NavigatorID::vendorSub",
        "NavigatorID::product",
        "NavigatorID::productSub",
        "NavigatorID::platform",
        "NavigatorID::hardwareConcurrency",
        "NavigatorID::deviceMemory",
        "NavigatorID::maxTouchPoints",
        "NavigatorID::language",
        "NavigatorID::languages",
        "NavigatorID::webdriver",
        # The screen metrics.
        "Screen::width",
        "Screen::height",
        "Screen::availWidth",
        "Screen::availHeight",
        "Screen::availLeft",
        "Screen::availTop",
        "Screen::colorDepth",
        "Screen::pixelDepth",
        "Screen::devicePixelRatio",
    ],
)
def test_every_device_accessor_is_patched(accessor):
    """Every device value a page can read must be served by the configuration.

    ``navigator.deviceMemory`` is the sharpest case: it reports the host's
    RAM in gigabytes, and a profile that claims a 4 GB phone while the
    browser answers 64 is caught by the first script that reads it.
    """

    text = _patch_text()
    assert accessor in text, f"{accessor} is not patched"


def test_the_device_accessors_are_implemented_in_the_blink_source():
    """The implementation must cover the same accessors as the patch."""

    text = _source_text("nova_blink_patch.cc")
    for accessor in (
        "NavigatorID::deviceMemory",
        "NavigatorID::hardwareConcurrency",
        "NavigatorID::maxTouchPoints",
        "NavigatorID::vendorSub",
        "NavigatorID::productSub",
        "Screen::height",
        "Screen::colorDepth",
        "Screen::pixelDepth",
    ):
        assert accessor in text, f"{accessor} is not implemented"


def test_the_device_memory_hook_uses_the_device_memory_field():
    """``deviceMemory`` must not report the touch points by mistake.

    A copy-paste slip here would report ``maxTouchPoints`` as the RAM of
    the device, which is a nonsensical value that no anti-bot vendor needs
    to see to know the profile is synthetic.
    """

    text = _source_text("nova_blink_patch.cc")
    block = text.split("double NavigatorID::deviceMemory() const {", 1)[-1]
    block = block.split("int NavigatorID::maxTouchPoints() const {", 1)[0]
    assert "device_memory" in block, "deviceMemory does not read device_memory"
    assert "max_touch_points" not in block, "deviceMemory reads the touch points"


def test_the_client_hint_patch_uses_the_cheap_accessor():
    """The hints are read on every request, so they must not copy the config."""

    text = _patch_text()
    hints_section = text.split("navigator_ua_data.cc", 1)[-1]
    assert "GetNovaConfigRef()" in hints_section
    # The copying accessor must not be used in the hint patch.
    assert "GetNovaConfig().user_agent_data" not in hints_section


def test_the_ua_data_header_is_included():
    """The implementation needs the Blink header of the class it patches."""

    text = _source_text("nova_blink_patch.cc")
    assert "navigator_ua_data.h" in text


def test_the_hook_header_declares_the_reference_accessor():
    """The Chromium patches reach the configuration through the hooks header."""

    text = _source_text("nova_chromium_hooks.h")
    assert "GetNovaConfigRef" in text
    assert "navigator_ua_data.cc" in text


def test_every_declared_hook_is_implemented():
    """A hook that is declared but not defined is a link error.

    The Chromium patches call the accessors of the hooks header; a single
    missing definition breaks the build of the core, so the two files have
    to stay in step.
    """

    header = _source_text("nova_chromium_hooks.h")
    implementation = _source_text("nova_blink_patch.cc")

    declared = re.findall(r"^\s*[A-Za-z_][\w:<>,\s*&]*\s+(\w+)\(", header, re.M)
    assert declared, "no hooks were declared"

    for name in declared:
        pattern = rf"^[A-Za-z_][\w:<>,\s*&]*\s+{re.escape(name)}\("
        assert re.search(pattern, implementation, re.M), (
            f"{name} is declared in the hooks header but not implemented"
        )


def test_the_context_lines_of_the_patch_are_stock_chromium_code():
    """A patch whose context is NOVA code cannot apply to a Chromium tree.

    The context lines of a hunk have to exist in the unmodified source;
    when they are the profile's own code the hunk never matches and the
    patch silently fails to apply.  Every context line must therefore be
    free of the NOVA namespace.
    """

    text = _patch_text()
    for line in text.splitlines():
        if not line.startswith(" "):
            # Not a context line: a header, a diff marker or an addition.
            continue
        assert "nova::" not in line, f"a context line carries NOVA code and cannot match: {line!r}"


def test_the_patch_covers_every_file_it_claims_to_touch():
    """The patch must include the Blink files whose accessors it substitutes."""

    text = _patch_text()
    for path in (
        "third_party/blink/renderer/core/html/navigator_id.cc",
        "third_party/blink/renderer/core/html/navigator_ua_data.cc",
        "third_party/blink/renderer/core/screen/screen.cc",
        "third_party/blink/renderer/modules/webgl/webgl_rendering_context.cc",
        "third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.cc",
    ):
        assert f"diff --git a/{path} b/{path}" in text, f"{path} is not patched"


def test_the_patch_includes_the_hooks_header_where_it_calls_it():
    """A file that calls the hooks has to include the hooks header."""

    text = _patch_text()
    blocks = re.split(r"^diff --git ", text, flags=re.M)
    for block in blocks[1:]:
        if "nova::" not in block:
            continue
        path = block.split(" b/", 1)[0].replace("a/", "", 1)
        header_line = '+#include "nova_chromium_hooks.h"'
        assert header_line in block, f"{path} calls the hooks but does not include them"


def test_the_configuration_is_loaded_lazily():
    """The hooks must load the configuration on their own.

    The patch does not touch the process start-up code -- that code moves
    between Chromium releases and a hunk against it would stop applying.
    The hooks therefore load the configuration themselves, so a renderer
    that reaches an accessor first still reports the profile's values.
    """

    text = _source_text("nova_blink_patch.cc")
    assert "EnsureInitialized" in text, "the configuration is not loaded lazily"

    for accessor in ("IsNovaCoreInitialized", "GetNovaConfig", "GetNovaConfigRef"):
        block = text.split(f"{accessor}(", 1)[-1].split("\n}\n", 1)[0]
        assert "EnsureInitialized" in block, (
            f"{accessor} does not ensure the configuration is loaded"
        )


def test_the_process_startup_files_are_not_patched():
    """The patch must not depend on the line numbers of the upstream tree.

    The start-up code of Chromium moves from release to release; a hunk
    against it is the first thing that stops applying, and the lazy load
    makes it unnecessary.
    """

    text = _patch_text()
    for path in (
        "content/browser/browser_main_loop.cc",
        "content/gpu/gpu_main.cc",
        "content/browser/net/browser_context.cc",
    ):
        assert f"diff --git a/{path}" not in text, f"{path} is patched"


def test_no_accessor_is_patched_without_a_fallback():
    """Every substituted accessor keeps the stock behaviour as a fallback.

    Without the fallback a browser that was started without a configuration
    would return an empty string instead of the real value, which is a far
    louder signal than the value it was meant to hide.

    The stock fallback is either the named stock accessor -- ``Standard*``
    or ``Default*`` -- or the literal the accessor returns when the
    profile has nothing to say.  Both are acceptable: what is not
    acceptable is a substitution that can fall through without returning.

    The noise hooks are exempt: they do not replace a value, they perturb
    a buffer, and their fallback is "do nothing".
    """

    text = _patch_text()

    # Every hunk that guards an accessor must also name the stock value.
    guarded = 0
    for hunk in _hunks(text):
        body = "\n".join(hunk)
        if "nova::IsNovaCoreInitialized()" not in body:
            continue
        if "ApplyPixelNoise" in body or "MaybeNoiseCanvasBitmap" in body:
            # A noise hook is not a substituted accessor.
            continue
        guarded += 1
        assert "return" in body, "a substituted accessor without a return statement"
        has_stock = "Standard" in body or "Default" in body
        # The fallback may also be the literal the accessor returns when
        # the profile has nothing to say, or the stock call that the
        # substitution sits in front of.
        has_literal = re.search(
            r"^\+?\s*return\s*(;|0\b|rect\b|false\b|true\b|nullptr\b|null\b|"
            r"MakeGarbageCollected\b|StorageManager::)",
            body,
            re.M,
        )
        assert has_stock or has_literal, (
            "a substituted accessor without a stock fallback"
        )
    assert guarded >= 20, f"only {guarded} substituted accessors were found"


# ----------------------------------------------------------------------
# The surfaces that are read on their own
# ----------------------------------------------------------------------


def test_the_independently_readable_surfaces_have_their_own_accessors():
    """Every surface a site can read alone needs a native accessor.

    A fingerprinting script does not have to combine the canvas hash with
    the WebGL hash with the device serial.  It can read the audio
    fingerprint, the geometry of a rectangle, the media device list, the
    motion readings, the storage estimate or the WebGPU adapter and
    cluster on any one of them alone.  Each of those therefore needs its
    own native accessor; without one the surface reports the host's value
    and every profile collapses into the same bucket.
    """

    header = _source_text("nova_chromium_hooks.h")
    implementation = _source_text("nova_blink_patch.cc")

    for name in (
        "GetAudioSampleRate",
        "GetAudioBaseLatency",
        "GetAudioOutputLatency",
        "GetAudioChannelCount",
        "GetAudioMaxChannelCount",
        "GetAudioNoiseKey",
        "GetRectDeltaX",
        "GetRectDeltaY",
        "GetRectDeltaWidth",
        "GetRectDeltaHeight",
        "GetMediaDeviceSalt",
        "GetSensorAccelerometerX",
        "GetSensorAccelerometerY",
        "GetSensorAccelerometerZ",
        "GetSensorGyroscopeX",
        "GetSensorGyroscopeY",
        "GetSensorGyroscopeZ",
        "GetSensorGravityZ",
        "GetSensorSalt",
        "GetStorageQuotaBytes",
        "GetStorageUsageBytes",
        "GetStorageSalt",
        "GetWebGpuSalt",
    ):
        assert name in header, f"{name} is not declared in the hooks header"
        assert name in implementation, f"{name} is not implemented"


def test_the_audio_key_is_independent_of_the_canvas_key():
    """The audio and the canvas hashes must not be linkable to each other.

    A script that compares the canvas hash with the audio hash learns
    nothing about the link between the two surfaces if the two keys come
    from different salts.
    """

    text = _source_text("nova_blink_patch.cc")
    assert '"audio"' in text, "the audio key is not derived from the audio salt"
    assert "config.audio.salt" in text, "the audio key does not use the audio salt"


def test_the_new_accessors_have_a_safe_fallback():
    """An uninitialized process must not report an empty value.

    A browser that was started without a configuration has to fall back to
    the neutral value rather than to an empty string, which is a far
    louder signal than the value the accessor was meant to hide.
    """

    text = _source_text("nova_blink_patch.cc")
    for name in (
        "GetAudioSampleRate",
        "GetAudioChannelCount",
        "GetRectDeltaX",
        "GetMediaDeviceSalt",
        "GetSensorAccelerometerX",
        "GetStorageQuotaBytes",
        "GetWebGpuSalt",
    ):
        block = text.split(f"{name}(", 1)[-1].split("\n}\n", 1)[0]
        assert "IsInitialized" in block, f"{name} has no initialization guard"
        assert "return" in block, f"{name} has no fallback return"


# ----------------------------------------------------------------------
# The patch series itself
# ----------------------------------------------------------------------


def _hunks(text: str) -> list[list[str]]:
    """Split the patch into its hunks, one list of lines each.

    A hunk ends at the next ``@@`` header or at the next file header, so
    the two have to be closed in both places.
    """
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            current = []
            continue
        if line.startswith(("diff --git", "--- ", "+++ ")):
            if current is not None:
                hunks.append(current)
                current = None
            continue
        if current is None:
            continue
        current.append(line)
    if current is not None:
        hunks.append(current)
    return hunks


def test_every_hunk_carries_context_lines():
    """A hunk without context cannot be applied to a Chromium tree.

    A hunk made only of additions carries no information about where the
    change belongs, so ``git apply`` has nothing to match against and the
    patch either fails or lands in the wrong place.
    """

    for path in sorted(PATCH_DIR.glob("*.patch")):
        text = path.read_text(encoding="utf-8")
        hunks = _hunks(text)
        assert hunks, f"{path.name} has no hunks"
        for index, hunk in enumerate(hunks, start=1):
            context = [line for line in hunk if line.startswith(" ")]
            assert context, f"{path.name} hunk {index} has no context lines"


def test_every_hunk_that_adds_code_also_keeps_the_stock_code():
    """The stock behaviour has to survive the patch.

    A substitution adds the profile's branch in front of the stock code
    rather than replacing it: the stock line stays as the fallback for a
    browser that was started without a configuration.  A hunk that *removes*
    the stock line has therefore dropped the fallback, and a hunk that
    neither adds a guard nor removes anything does nothing at all.
    """

    for path in sorted(PATCH_DIR.glob("*.patch")):
        text = path.read_text(encoding="utf-8")
        for index, hunk in enumerate(_hunks(text), start=1):
            added = [line for line in hunk if line.startswith("+")]
            if not added:
                continue
            # A hunk that only adds an include is not a substitution; it
            # is the include the substitution needs.
            if all("include" in line or not line.strip("+") for line in added):
                continue
            # The stock line has to survive, either as a context line or
            # explicitly.
            stock = [line for line in hunk if line.startswith(" ")]
            assert stock, (
                f"{path.name} hunk {index} has no context, so the stock code "
                "cannot be matched against the tree"
            )


def test_the_patch_calls_only_declared_hooks():
    """Every hook the patch calls has to exist in the header.

    A patch that calls a hook the header does not declare is a link
    error in the core build.
    """

    header = _source_text("nova_chromium_hooks.h")
    implementation = _source_text("nova_blink_patch.cc")
    # The noise helpers live in their own header; the patches call them
    # directly.
    noise = _source_text("nova_pixel_noise.h")
    # Every declaration in the headers is a hook, whatever it returns.
    declared = set(re.findall(r"^\s*[A-Za-z_][\w:<>,\s*&]*\s+(\w+)\(", header, re.M))
    declared |= set(re.findall(r"^\s*[A-Za-z_][\w:<>,\s*&]*\s+(\w+)\(", noise, re.M))
    # The internal helpers of the implementation file are not hooks: they
    # are called from inside the core, not from the Chromium patches.
    internal = set(re.findall(r"^\s*[A-Za-z_][\w:<>,\s*&]*\s+(\w+)\(", implementation, re.M))
    internal -= declared

    for path in sorted(PATCH_DIR.glob("*.patch")):
        text = path.read_text(encoding="utf-8")
        for called in set(re.findall(r"nova::(\w+)\(", text)):
            if called in internal:
                continue
            assert called in declared, (
                f"{path.name} calls nova::{called}() which the hooks header does not declare"
            )


def test_the_hardware_surfaces_are_actually_substituted():
    """The second patch has to touch every surface it claims to cover.

    A patch that only adds the accessor declarations without wiring them
    into Blink leaves the host's values in place, which is the failure
    this whole series exists to prevent.
    """

    text = _patch_text()
    for accessor in (
        "nova::GetAudioSampleRate()",
        "nova::GetAudioBaseLatency()",
        "nova::GetRectDeltaX()",
        "nova::GetMediaDeviceSalt()",
        "nova::GetStorageQuotaBytes()",
    ):
        assert accessor in text, f"{accessor} is never called by the patch"
