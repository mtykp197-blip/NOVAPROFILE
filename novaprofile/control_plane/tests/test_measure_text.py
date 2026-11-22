"""The measured width of a text has to agree with the font list.

A font fingerprint measures.  It sets the canvas font to a dozen
families, asks for the width of a string in each, and looks at the
answers.  Two families the device does not carry measure the same,
because the system substituted the same fallback for both -- that is
the signature the script is looking for.

The runtime therefore has to answer two questions correctly: the
families the device carries measure differently from each other, and
the families it does not carry measure the same as each other.

The test runs the runtime in a real interpreter, because a font
fingerprint is a script and a script has to actually run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator

#: Where a Node interpreter may be found.  The Chromium checkout carries
#: one, so the suite runs its JavaScript on this machine.
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

#: The harness.  A canvas whose width depends on the family, so the
#: runtime has something real to disagree with.
HARNESS = """
const vm = require('node:vm');
const fs = require('fs');

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

const TextMetrics = function () {};

const CanvasRenderingContext2D = function () {
  this._font = '10px sans-serif';
};
Object.defineProperty(CanvasRenderingContext2D.prototype, 'font', {
  get: function () { return this._font; },
  set: function (value) { this._font = value; },
  enumerable: true,
  configurable: true
});
CanvasRenderingContext2D.prototype.measureText = function (text) {
  // The width grows with the length of the family name, so every
  // family has its own width.  The fallback is the same for all of
  // them, which is what the runtime has to reproduce.
  const family = (String(this._font).match(/["']([^"']+)["']/) || [])[1] || 'sans-serif';
  const m = new TextMetrics();
  const width = family === 'sans-serif' ? 100 : 10 + family.length;
  Object.defineProperty(m, 'width', {
    get: function () { return width; },
    set: undefined,
    enumerable: true,
    configurable: true
  });
  return m;
};

const HTMLCanvasElement = function () {};

const navigator = new Navigator();
const sandbox = {
  navigator: navigator,
  Navigator: Navigator,
  Screen: Screen,
  CanvasRenderingContext2D: CanvasRenderingContext2D,
  HTMLCanvasElement: HTMLCanvasElement,
  TextMetrics: TextMetrics,
  Object: Object,
  Array: Array,
  String: String,
  Number: Number,
  Boolean: Boolean,
  Function: Function,
  Math: Math,
  JSON: JSON,
  Date: Date,
  RegExp: RegExp,
  Error: Error,
  TypeError: TypeError,
  Promise: Promise,
  Map: Map,
  Set: Set,
  Symbol: Symbol,
  Proxy: Proxy,
  Reflect: Reflect,
  EventTarget: EventTarget,
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const failures = [];
function check(name, ok) { if (!ok) { failures.push(name); } }

const ctx = new CanvasRenderingContext2D();

const widthOf = function (family) {
  ctx.font = '10px "' + family + '"';
  return ctx.measureText('Hello').width;
};

// A family the device carries, and two it does not.
const installedFamily = 'Roboto';
const missingFamily = 'NoSuchFontFamily';
const otherMissing = 'AnotherNoSuchFont';

const installedWidth = widthOf(installedFamily);
const missingWidth = widthOf(missingFamily);
const otherMissingWidth = widthOf(otherMissing);

check('the installed family keeps its own width', installedWidth !== 100);
check('the missing family measures as the fallback', missingWidth === 100);
check('two missing families measure the same', otherMissingWidth === missingWidth);

setTimeout(function () {
  if (failures.length) {
    console.log('FAIL: ' + failures.join('; '));
    process.exit(1);
  }
  console.log('PASS');
}, 300);
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestTheMeasuredWidth:
    """The runtime answers the way a font fingerprint expects."""

    def test_the_missing_families_measure_the_same(self) -> None:
        from novaprofile.js_runtime.builder import JSRuntimeBuilder

        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="a" * 64)
        script = JSRuntimeBuilder().build(config)

        with tempfile.TemporaryDirectory() as folder:
            harness_path = Path(folder) / "harness.js"
            harness_path.write_text(HARNESS, encoding="utf-8")
            runtime_path = Path(folder) / "runtime.js"
            runtime_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(harness_path), str(runtime_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS" in result.stdout


class TestTheFontList:
    """The list the runtime measures against."""

    def test_the_font_list_reaches_the_runtime(self) -> None:
        from novaprofile.js_runtime.builder import JSRuntimeBuilder

        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="b" * 64)
        script = JSRuntimeBuilder().build(config)

        marker = "window.__NOVA_PROFILE__="
        start = script.index(marker) + len(marker)
        end = script.index(";\n", start)
        payload = json.loads(script[start:end])

        assert payload["fonts"]["installed"]
        assert "Roboto" in payload["fonts"]["installed"]

    def test_the_runtime_keeps_the_font_list(self) -> None:
        """The measurement has to know what the device carries."""
        from novaprofile.js_runtime.builder import JSRuntimeBuilder

        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="c" * 64)
        script = JSRuntimeBuilder().build(config)

        assert "installedFamilies" in script
