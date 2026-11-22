"""Execution tests for the page-context JavaScript layer.

The tests in ``test_js_runtime.py`` read the generated script and check
its *text*.  That catches a missing override but it cannot catch a
script that throws, and a script that throws is the failure that
matters most: the runtime is one long program, and a single undefined
name stops every override after it.

This module therefore *runs* the script.  Node is used because the
runtime needs nothing but the browser API surface, and the harness
below provides that surface.  The tests are skipped when Node is not
installed, so the suite still runs everywhere.

Three defects motivated this module, and all three are now guarded
against:

* the navigator block referred to a name that was never declared, so
  the whole runtime threw on its first line of work;
* the source hiding set an own ``toString`` property on the override,
  which does not survive ``Function.prototype.toString.call`` -- the
  check a fingerprinting script actually runs;
* the navigator global was read directly, so the guard that was meant
  to protect the block could not run: the throw happened on the line
  before it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.seed import new_master_seed
from novaprofile.js_runtime import JSRuntimeBuilder

#: Where a Node interpreter may be found.
NODE_CANDIDATES = (
    "node",
    r"C:\chromium\src\third_party\node\win\node.exe",
    "/usr/bin/node",
    "/usr/local/bin/node",
)


def _find_node() -> str | None:
    """The Node interpreter to run the harness with."""

    for candidate in NODE_CANDIDATES:
        if shutil.which(candidate):
            return candidate
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


NODE = _find_node()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


#: The harness.  It builds a browser-shaped environment, runs the
#: runtime inside it, and reports what the page would have read.
HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');
    const profile = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

    function defineGetter(obj, name, value) {
      Object.defineProperty(obj, name, {
        get: function () { return value; },
        set: undefined,
        enumerable: true,
        configurable: true
      });
    }

    const Navigator = function () {};
    defineGetter(Navigator.prototype, 'userAgent', 'stock-ua');
    defineGetter(Navigator.prototype, 'platform', 'stock-platform');
    defineGetter(Navigator.prototype, 'appVersion', 'stock-appversion');
    defineGetter(Navigator.prototype, 'appName', 'stock-appname');
    defineGetter(Navigator.prototype, 'appCodeName', 'stock-appcodename');
    defineGetter(Navigator.prototype, 'vendor', 'stock-vendor');
    defineGetter(Navigator.prototype, 'vendorSub', 'stock-vendorsub');
    defineGetter(Navigator.prototype, 'product', 'stock-product');
    defineGetter(Navigator.prototype, 'productSub', 'stock-productsub');
    defineGetter(Navigator.prototype, 'hardwareConcurrency', 4);
    defineGetter(Navigator.prototype, 'deviceMemory', 8);
    defineGetter(Navigator.prototype, 'maxTouchPoints', 0);
    defineGetter(Navigator.prototype, 'language', 'en-US');
    defineGetter(Navigator.prototype, 'languages', ['en-US']);
    defineGetter(Navigator.prototype, 'doNotTrack', null);
    defineGetter(Navigator.prototype, 'cookieEnabled', true);
    defineGetter(Navigator.prototype, 'onLine', true);
    defineGetter(Navigator.prototype, 'javaEnabled', false);
    defineGetter(Navigator.prototype, 'pdfViewerEnabled', true);
    defineGetter(Navigator.prototype, 'globalPrivacyControl', null);

    const Screen = function () {};
    defineGetter(Screen.prototype, 'width', 1280);
    defineGetter(Screen.prototype, 'height', 720);
    defineGetter(Screen.prototype, 'availWidth', 1280);
    defineGetter(Screen.prototype, 'availHeight', 700);
    defineGetter(Screen.prototype, 'availLeft', 0);
    defineGetter(Screen.prototype, 'availTop', 0);
    defineGetter(Screen.prototype, 'colorDepth', 24);
    defineGetter(Screen.prototype, 'pixelDepth', 24);

    const uadProto = {
      brands: [{ brand: 'stock', version: '1' }],
      mobile: false,
      platform: 'stock',
      platformVersion: 'stock',
      architecture: 'stock',
      bitness: 'stock',
      model: 'stock',
      wow64: false
    };

    const navigator = new Navigator();
    Object.defineProperty(navigator, 'userAgentData', {
      value: Object.create(uadProto),
      enumerable: true,
      configurable: true
    });

    const sandbox = Object.create(null);
    sandbox.globalThis = sandbox;
    sandbox.window = sandbox;
    sandbox.navigator = navigator;
    sandbox.Navigator = Navigator;
    sandbox.Screen = Screen;
    sandbox.Object = Object;
    sandbox.Array = Array;
    sandbox.String = String;
    sandbox.Number = Number;
    sandbox.Boolean = Boolean;
    sandbox.Function = Function;
    sandbox.Math = Math;
    sandbox.JSON = JSON;
    sandbox.Date = Date;
    sandbox.RegExp = RegExp;
    sandbox.Error = Error;
    sandbox.TypeError = TypeError;
    sandbox.RangeError = RangeError;
    sandbox.Promise = Promise;
    sandbox.Map = Map;
    sandbox.Set = Set;
    sandbox.Symbol = Symbol;
    sandbox.Proxy = Proxy;
    sandbox.Reflect = Reflect;
    sandbox.Intl = Intl;
    sandbox.Uint8Array = Uint8Array;
    sandbox.parseInt = parseInt;
    sandbox.parseFloat = parseFloat;
    sandbox.isNaN = isNaN;
    sandbox.setTimeout = setTimeout;
    sandbox.clearTimeout = clearTimeout;
    sandbox.__NOVA_PROFILE__ = profile;

    const context = vm.createContext(sandbox);
    const thrown = [];

    try {
      new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(context);
    } catch (e) {
      thrown.push(String(e && e.stack || e));
    }

    function read(fn) {
      try { return fn(); } catch (e) { return 'THREW: ' + e.message; }
    }

    const uad = navigator.userAgentData;
    const uaDescriptor = Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgent');
    const screenDescriptor = Object.getOwnPropertyDescriptor(Screen.prototype, 'width');

    console.log(JSON.stringify({
      thrown: thrown,
      userAgent: read(function () { return navigator.userAgent; }),
      platform: read(function () { return navigator.platform; }),
      vendor: read(function () { return navigator.vendor; }),
      hardwareConcurrency: read(function () { return navigator.hardwareConcurrency; }),
      deviceMemory: read(function () { return navigator.deviceMemory; }),
      maxTouchPoints: read(function () { return navigator.maxTouchPoints; }),
      language: read(function () { return navigator.language; }),
      languages: read(function () { return navigator.languages; }),
      screenWidth: read(function () { return Screen.prototype.width; }),
      screenColorDepth: read(function () { return Screen.prototype.colorDepth; }),
      uadBrands: read(function () { return uad.brands; }),
      uadMobile: read(function () { return uad.mobile; }),
      uadPlatform: read(function () { return uad.platform; }),
      uadModel: read(function () { return uad.model; }),
      uaGetterSource: read(function () {
        return Function.prototype.toString.call(uaDescriptor.get);
      }),
      screenGetterSource: read(function () {
        return Function.prototype.toString.call(screenDescriptor.get);
      }),
      uaGetterOwnToString: read(function () {
        return uaDescriptor.get.toString();
      }),
      uaGetterName: read(function () { return uaDescriptor.get.name; }),
      uaGetterLength: read(function () { return uaDescriptor.get.length; }),
      stockGetterName: 'get'
    }));
    """
).strip()


def _run(profile, tmp_path: Path) -> dict:
    """Run the runtime for a profile and return what the page would read."""

    builder = JSRuntimeBuilder()
    script = builder.build(profile)

    # The builder emits ``window.__NOVA_PROFILE__=<json>;`` followed by
    # the runtime.  The harness sets the payload itself, so only the
    # runtime half is needed.
    runtime = script.split(";\n", 1)[1]

    runtime_path = tmp_path / "runtime.js"
    runtime_path.write_text(runtime, encoding="utf-8")

    harness_path = tmp_path / "harness.js"
    harness_path.write_text(HARNESS, encoding="utf-8")

    payload = {
        "navigator": {
            "userAgent": profile.navigator.user_agent,
            "appVersion": profile.navigator.app_version,
            "appName": profile.navigator.app_name,
            "appCodeName": profile.navigator.app_code_name,
            "vendor": profile.navigator.vendor,
            "vendorSub": profile.navigator.vendor_sub,
            "product": profile.navigator.product,
            "productSub": profile.navigator.product_sub,
            "platform": profile.navigator.platform,
            "hardwareConcurrency": profile.navigator.hardware_concurrency,
            "deviceMemory": profile.navigator.device_memory,
            "maxTouchPoints": profile.navigator.max_touch_points,
            "language": profile.navigator.language,
            "languages": list(profile.navigator.languages),
        },
        "screen": {
            "width": profile.screen.width,
            "height": profile.screen.height,
            "availWidth": profile.screen.avail_width,
            "availHeight": profile.screen.avail_height,
            "availLeft": profile.screen.avail_left,
            "availTop": profile.screen.avail_top,
            "colorDepth": profile.screen.color_depth,
            "pixelDepth": profile.screen.pixel_depth,
            "device_pixel_ratio": profile.screen.device_pixel_ratio,
        },
        "user_agent_data": {
            "brands": [
                {"brand": b.brand, "version": b.version}
                for b in profile.user_agent_data.brands
            ],
            "full_version_list": [
                {"brand": b.brand, "version": b.version}
                for b in profile.user_agent_data.full_version_list
            ],
            "mobile": profile.user_agent_data.mobile,
            "platform": profile.user_agent_data.platform,
            "platform_version": profile.user_agent_data.platform_version,
            "architecture": profile.user_agent_data.architecture,
            "bitness": profile.user_agent_data.bitness,
            "model": profile.user_agent_data.model,
            "wow64": profile.user_agent_data.wow64,
        },
    }
    payload_path = tmp_path / "profile.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [NODE, str(harness_path), str(runtime_path), str(payload_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"node failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture()
def profile():
    return ProfileGenerator().generate(
        "pixel-8-pro-android14",
        master_seed=new_master_seed(),
    )


# --------------------------------------------------------------------------
# The runtime runs
# --------------------------------------------------------------------------


def test_the_runtime_runs_without_throwing(profile, tmp_path) -> None:
    """The whole script must run to the end.

    A reference to a name that does not exist throws a ReferenceError,
    and the throw stops every override that comes after it.  The
    navigator block once referred to a name that was never declared, so
    the runtime threw on the first override and the screen, the viewport
    and the client hints were never reached.
    """

    report = _run(profile, tmp_path)
    assert report["thrown"] == [], f"the runtime threw:\n{report['thrown']}"


# --------------------------------------------------------------------------
# The overrides take effect
# --------------------------------------------------------------------------


def test_the_navigator_reports_the_profile(profile, tmp_path) -> None:
    """The page must read the profile's values, not the host's."""

    report = _run(profile, tmp_path)
    assert report["userAgent"] == profile.navigator.user_agent
    assert report["platform"] == profile.navigator.platform
    assert report["vendor"] == profile.navigator.vendor
    assert report["hardwareConcurrency"] == profile.navigator.hardware_concurrency
    assert report["deviceMemory"] == profile.navigator.device_memory
    assert report["maxTouchPoints"] == profile.navigator.max_touch_points
    assert report["language"] == profile.navigator.language
    assert report["languages"] == list(profile.navigator.languages)


def test_the_screen_reports_the_profile(profile, tmp_path) -> None:
    """The screen metrics must come from the profile."""

    report = _run(profile, tmp_path)
    assert report["screenWidth"] == profile.screen.width
    assert report["screenColorDepth"] == profile.screen.color_depth


def test_the_client_hints_report_the_profile(profile, tmp_path) -> None:
    """The client hints must come from the profile and must agree."""

    report = _run(profile, tmp_path)
    expected = [
        {"brand": b.brand, "version": b.version} for b in profile.user_agent_data.brands
    ]
    assert report["uadBrands"] == expected
    assert report["uadMobile"] == profile.user_agent_data.mobile
    assert report["uadPlatform"] == profile.user_agent_data.platform
    assert report["uadModel"] == profile.user_agent_data.model


# --------------------------------------------------------------------------
# The overrides survive a source check
# --------------------------------------------------------------------------


def test_the_overridden_getter_looks_native(profile, tmp_path) -> None:
    """A source check must not tell the override apart.

    The check a fingerprinting script runs is

        Function.prototype.toString.call(
            Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgent').get)

    An own ``toString`` property on the override does not survive it,
    because ``Function.prototype.toString`` reads the internal source
    text of the function and ignores its own properties.  The override
    therefore has to be returned behind a proxy.
    """

    report = _run(profile, tmp_path)
    assert report["uaGetterSource"] == "function () { [native code] }"
    assert report["screenGetterSource"] == "function () { [native code] }"


def test_the_overridden_getter_looks_native_to_a_direct_call(profile, tmp_path) -> None:
    """The direct ``fn.toString()`` must report the same text."""

    report = _run(profile, tmp_path)
    assert report["uaGetterOwnToString"] == "function () { [native code] }"


def test_the_override_still_behaves_like_a_function(profile, tmp_path) -> None:
    """Hiding the source must not break the override.

    The proxy forwards the call, the name, the length and the own
    property names, so a page that introspects the function still sees a
    function.
    """

    report = _run(profile, tmp_path)
    assert not str(report["userAgent"]).startswith("THREW")
    assert report["userAgent"] == profile.navigator.user_agent


def test_the_overridden_getter_keeps_the_native_name(profile, tmp_path) -> None:
    """The name of the getter must not give the override away.

    A native accessor getter is named "get" (or "get userAgent" in
    Blink).  The name of the replacement -- "raw", "value", "fn" -- is
    a giveaway that needs no source check at all, so the replacement
    reports the name of the getter it took the place of.
    """

    report = _run(profile, tmp_path)
    assert report["uaGetterName"] == report["stockGetterName"]
    assert report["uaGetterLength"] == 0


# --------------------------------------------------------------------------
# The runtime survives a page that offers nothing
# --------------------------------------------------------------------------


#: A harness with no browser API at all.  A page does not have to offer
#: the WebGL API, and a runtime that throws on a missing API never
#: reaches the override after it.
BARE_HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');

    const sandbox = Object.create(null);
    sandbox.globalThis = sandbox;
    sandbox.window = sandbox;
    sandbox.Object = Object;
    sandbox.Array = Array;
    sandbox.String = String;
    sandbox.Number = Number;
    sandbox.Boolean = Boolean;
    sandbox.Function = Function;
    sandbox.Math = Math;
    sandbox.JSON = JSON;
    sandbox.Date = Date;
    sandbox.RegExp = RegExp;
    sandbox.Error = Error;
    sandbox.TypeError = TypeError;
    sandbox.RangeError = RangeError;
    sandbox.Promise = Promise;
    sandbox.Map = Map;
    sandbox.Set = Set;
    sandbox.Symbol = Symbol;
    sandbox.Proxy = Proxy;
    sandbox.Reflect = Reflect;
    sandbox.Intl = Intl;
    sandbox.parseInt = parseInt;
    sandbox.parseFloat = parseFloat;
    sandbox.isNaN = isNaN;
    sandbox.setTimeout = setTimeout;
    sandbox.clearTimeout = clearTimeout;

    // Deliberately absent: navigator, Screen, document, performance,
    // localStorage, WebGLRenderingContext, CanvasRenderingContext2D,
    // HTMLCanvasElement, Element, Range, AudioContext, matchMedia.

    sandbox.__NOVA_PROFILE__ = {
      navigator: { userAgent: 'nova-ua', platform: 'Linux armv8l' },
      screen: { width: 1344, height: 2992, colorDepth: 24, pixelDepth: 24 },
      viewport: { inner_width: 412, inner_height: 915 },
      user_agent_data: { brands: [], mobile: true, platform: 'Android' }
    };

    const context = vm.createContext(sandbox);
    const thrown = [];

    try {
      new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(context);
    } catch (e) {
      thrown.push(String(e && e.stack || e));
    }

    console.log(JSON.stringify({ thrown: thrown }));
    """
).strip()


def test_the_runtime_survives_a_page_with_no_browser_api(tmp_path) -> None:
    """The runtime must not assume the browser offers an API.

    Every block that touches a browser object has to check that the
    object exists first.  The navigator global was read directly once,
    so the guard that was meant to protect the block could not run: the
    throw happened on the line before the guard.
    """

    builder = JSRuntimeBuilder()
    script = builder.build(
        ProfileGenerator().generate(
            "pixel-8-pro-android14",
            master_seed=new_master_seed(),
        )
    )
    runtime = script.split(";\n", 1)[1]

    runtime_path = tmp_path / "runtime.js"
    runtime_path.write_text(runtime, encoding="utf-8")
    harness_path = tmp_path / "bare.js"
    harness_path.write_text(BARE_HARNESS, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(harness_path), str(runtime_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"node failed:\n{result.stderr}"
    report = json.loads(result.stdout)
    assert report["thrown"] == [], f"the runtime threw:\n{report['thrown']}"
