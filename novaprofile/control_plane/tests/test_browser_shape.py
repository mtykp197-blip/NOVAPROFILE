"""The fingerprint must look like the browser it claims to be.

The runtime is checked against the browser it says it is: a Chrome on
Android.  Every surface in this file is one that a detector reads, and
every assertion is a value that the real browser has.

The reason the file exists is a class of bug that the other tests could
not see.  The tests that were here before checked that the runtime
*installed the profile's values*: the user agent is the profile's, the
screen is the profile's, the timezone is the profile's.  They never
checked the surfaces the profile says nothing about -- the ones the
browser has on its own.  A runtime that leaves ``navigator.chrome``
missing produces a browser that claims to be Chrome and is not: the
user agent says Chrome, the client hints say Chrome, and the object
that only Chrome has is gone.  That is a detector's cheapest check, and
it is the one a test that only compares against the profile cannot see.

The same argument works in the other direction.  A runtime that *adds*
a surface the browser does not have is just as wrong: ``PDFViewer`` was
a property of ``navigator.plugins`` in Chrome 60 and is gone in Chrome
141, and a page that reads it finds a browser that carries a plugin
object it should not have.

So the assertions here are about the *stock* shape of a Chrome, not
about the profile.  The profile's own values are checked elsewhere.
"""

from __future__ import annotations

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
    """The Node interpreter, if the machine has one."""

    for candidate in NODE_CANDIDATES:
        if shutil.which(candidate):
            return candidate
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


NODE = _find_node()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


#: The harness.
#:
#: It builds a browser-shaped environment that has the stock surfaces a
#: Chrome has -- including the ones the runtime is not supposed to
#: touch -- runs the runtime inside it, and reports what the page would
#: have read.
HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');
    const profile = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

    function defineGetter(obj, name, value) {
      var accessor = function () { return value; };
      Object.defineProperty(accessor, 'name', {
        value: 'get ' + name, writable: false, enumerable: false, configurable: true
      });
      Object.defineProperty(obj, name, {
        get: accessor,
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

    // The plugin array of a modern Chrome: the five names the HTML
    // standard prescribes, and nothing else.  Chrome 60 had a
    // ``PDFViewer`` property on it; Chrome 141 does not, and a runtime
    // that adds one back describes a browser that no longer exists.
    const PluginArray = function () {};
    const MimeTypeArray = function () {};
    const pluginNames = [
      'PDF Viewer',
      'Chrome PDF Viewer',
      'Chromium PDF Viewer',
      'Microsoft Edge PDF Viewer',
      'WebKit built-in PDF'
    ];
    const mimeTypes = ['application/pdf', 'text/pdf'];
    const pluginArray = new PluginArray();
    const mimeTypeArray = new MimeTypeArray();
    pluginNames.forEach(function (name, index) {
      pluginArray[index] = { name: name, filename: 'internal-pdf-viewer' };
      pluginArray[name] = pluginArray[index];
    });
    mimeTypes.forEach(function (type, index) {
      mimeTypeArray[index] = { type: type, suffixes: 'pdf' };
      mimeTypeArray[type] = mimeTypeArray[index];
    });
    pluginArray.length = pluginNames.length;
    mimeTypeArray.length = mimeTypes.length;
    pluginArray.item = function (i) { return pluginArray[i] || null; };
    pluginArray.namedItem = function (n) { return pluginArray[n] || null; };
    mimeTypeArray.item = function (i) { return mimeTypeArray[i] || null; };
    mimeTypeArray.namedItem = function (n) { return mimeTypeArray[n] || null; };

    const navigator = new Navigator();
    Object.defineProperty(navigator, 'plugins', {
      value: pluginArray, enumerable: true, configurable: true
    });
    Object.defineProperty(navigator, 'mimeTypes', {
      value: mimeTypeArray, enumerable: true, configurable: true
    });

    const sandbox = Object.create(null);
    sandbox.globalThis = sandbox;
    sandbox.window = sandbox;
    sandbox.navigator = navigator;
    sandbox.Navigator = Navigator;
    sandbox.Screen = Screen;
    sandbox.PluginArray = PluginArray;
    sandbox.MimeTypeArray = MimeTypeArray;
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

    console.log(JSON.stringify({
      thrown: thrown,
      chromeType: read(function () { return typeof navigator.chrome; }),
      chromeKeys: read(function () {
        return navigator.chrome ? Object.keys(navigator.chrome) : null;
      }),
      chromeAppKeys: read(function () {
        return navigator.chrome && navigator.chrome.app
          ? Object.keys(navigator.chrome.app) : null;
      }),
      chromeAppIsInstalled: read(function () {
        return navigator.chrome && navigator.chrome.app
          ? navigator.chrome.app.isInstalled : null;
      }),
      chromeCsiType: read(function () {
        return navigator.chrome ? typeof navigator.chrome.csi : null;
      }),
      chromeLoadTimesType: read(function () {
        return navigator.chrome ? typeof navigator.chrome.loadTimes : null;
      }),
      chromeCsiResult: read(function () {
        return navigator.chrome ? Object.keys(navigator.chrome.csi()) : null;
      }),
      pluginPdfViewerProperty: read(function () {
        return 'PDFViewer' in navigator.plugins;
      }),
      pluginPdfViewerNamedItem: read(function () {
        return navigator.plugins.namedItem('PDF Viewer')
          ? navigator.plugins.namedItem('PDF Viewer').name : null;
      }),
      pluginNames: read(function () {
        return Array.prototype.map.call(navigator.plugins, function (p) {
          return p.name;
        });
      }),
      pluginLength: read(function () { return navigator.plugins.length; }),
      mimeLength: read(function () { return navigator.mimeTypes.length; }),
      mimeTypes: read(function () {
        return Array.prototype.map.call(navigator.mimeTypes, function (m) {
          return m.type;
        });
      }),
      mimePdfViewerProperty: read(function () {
        return 'PDFViewer' in navigator.mimeTypes;
      })
    }));
    """
).strip()


def _run(profile, tmp_path: Path) -> dict:
    """Run the runtime for a profile and return what the page would read."""

    import json

    builder = JSRuntimeBuilder()
    script_path = tmp_path / "runtime.js"
    profile_path = tmp_path / "profile.json"
    script_path.write_text(builder.build(profile), encoding="utf-8")
    profile_path.write_text(
        json.dumps(builder.build_payload_only(profile)), encoding="utf-8"
    )

    harness_path = tmp_path / "harness.js"
    harness_path.write_text(HARNESS, encoding="utf-8")

    completed = subprocess.run(
        [NODE, str(harness_path), str(script_path), str(profile_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def profile():
    """A profile of the fleet."""

    return ProfileGenerator().generate(
        preset_id="pixel-8-pro-android14",
        master_seed=new_master_seed(),
    )


# --------------------------------------------------------------------------
# The chrome object
# --------------------------------------------------------------------------


def test_the_runtime_does_not_throw(profile, tmp_path) -> None:
    """The runtime must run to the end.

    A throw stops every override that comes after it, so a broken
    surface is not one broken surface: it is every surface after it.
    """

    report = _run(profile, tmp_path)
    assert report["thrown"] == [], f"the runtime threw:\n{report['thrown']}"


def test_the_navigator_carries_the_chrome_object(profile, tmp_path) -> None:
    """The browser claims to be Chrome, so it must carry Chrome's object.

    Chrome on every platform has a ``navigator.chrome`` with an ``app``
    and a ``csi`` in it.  A browser whose user agent says Chrome and
    whose navigator has no such object is a browser that a detector can
    name in one line of script.
    """

    report = _run(profile, tmp_path)
    assert report["chromeType"] == "object", "navigator.chrome is missing"


def test_the_chrome_object_has_the_shape_of_chrome(profile, tmp_path) -> None:
    """The object must have the members Chrome's has.

    Chrome 141 exposes ``app``, ``csi`` and ``loadTimes``.  A page that
    reads ``navigator.chrome.csi`` and finds nothing has found a
    browser that never had the object.
    """

    report = _run(profile, tmp_path)
    assert "app" in (report["chromeKeys"] or [])
    assert "csi" in (report["chromeKeys"] or [])
    assert "loadTimes" in (report["chromeKeys"] or [])


def test_the_chrome_app_is_not_installed(profile, tmp_path) -> None:
    """``chrome.app.isInstalled`` must be false.

    A page that is not an app is not an installed app.  The value is
    false on every page of every Chrome that is not a Chrome App.
    """

    report = _run(profile, tmp_path)
    assert report["chromeAppIsInstalled"] is False


def test_the_chrome_object_carries_the_install_state(profile, tmp_path) -> None:
    """``chrome.app`` must carry the states Chrome's carries.

    The enums are part of the shape: ``InstallState`` and
    ``RunningState`` are the two names a page reads to tell whether an
    app is installed.
    """

    report = _run(profile, tmp_path)
    keys = report["chromeAppKeys"] or []
    assert "isInstalled" in keys
    assert "InstallState" in keys
    assert "RunningState" in keys


def test_the_chrome_functions_are_functions(profile, tmp_path) -> None:
    """``csi`` and ``loadTimes`` must be callable.

    They are functions in Chrome, and a page calls them.  A string or
    a number in their place is a shape Chrome never has.
    """

    report = _run(profile, tmp_path)
    assert report["chromeCsiType"] == "function"
    assert report["chromeLoadTimesType"] == "function"


def test_the_chrome_functions_return_an_object(profile, tmp_path) -> None:
    """Calling them must not throw and must return an object."""

    report = _run(profile, tmp_path)
    assert isinstance(report["chromeCsiResult"], list)


# --------------------------------------------------------------------------
# The plugin surface
# --------------------------------------------------------------------------


def test_the_plugin_list_is_the_one_chrome_has(profile, tmp_path) -> None:
    """The five standard names, and no others.

    The list is the one the HTML standard prescribes, so every Chrome
    has the same five names in the same order.
    """

    report = _run(profile, tmp_path)
    assert report["pluginNames"] == [
        "PDF Viewer",
        "Chrome PDF Viewer",
        "Chromium PDF Viewer",
        "Microsoft Edge PDF Viewer",
        "WebKit built-in PDF",
    ]
    assert report["pluginLength"] == 5


def test_the_mime_type_list_is_the_one_chrome_has(profile, tmp_path) -> None:
    """Two mime types, and no others."""

    report = _run(profile, tmp_path)
    assert report["mimeTypes"] == ["application/pdf", "text/pdf"]
    assert report["mimeLength"] == 2


def test_the_runtime_does_not_add_a_pdf_viewer_property(profile, tmp_path) -> None:
    """``navigator.plugins.PDFViewer`` must not be there.

    Chrome 60 had it.  Chrome 141 does not: the plugin array answers
    by name, through ``namedItem``.  A runtime that adds the property
    back describes a browser that stopped being shipped nine years
    ago, and a detector that reads the property learns that the
    fingerprint has been assembled.
    """

    report = _run(profile, tmp_path)
    assert report["pluginPdfViewerProperty"] is False
    assert report["mimePdfViewerProperty"] is False


def test_the_pdf_viewer_is_still_reachable_by_name(profile, tmp_path) -> None:
    """The plugin must still be found the way Chrome finds it.

    ``namedItem('PDF Viewer')`` is the modern way, and it has to keep
    working: the runtime may not replace the array with one that has
    the names but not the lookup.
    """

    report = _run(profile, tmp_path)
    assert report["pluginPdfViewerNamedItem"] == "PDF Viewer"
