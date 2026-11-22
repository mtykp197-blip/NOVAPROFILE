"""The runtime must not leave a trace a page could read.

A page that suspects a value was patched reads the source of the
function that produced it, and it reads the shape of the function --
its own property names, its length, its name.  A stock Blink accessor
reports:

    Function.prototype.toString.call(stockGetter)
      -> "function get userAgent() { [native code] }"
    Object.getOwnPropertyNames(stockGetter) -> ["length", "name"]
    stockGetter.name -> "get userAgent"
    stockGetter.length -> 0

and the two routes -- the built-in call and the own ``toString`` --
answer the same text.  A replacement that answers differently on
either route is the giveaway.

These tests run the runtime in a bare context and read the
replacement back the way a page would.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from novaprofile.core.generator import ProfileGenerator
from novaprofile.js_runtime.builder import JSRuntimeBuilder

NODE = r"C:\chromium\src\third_party\node\win\node.exe"

#: The harness builds a stand-in for the browser: the accessors a page
#: would read, named the way Blink names them.
HARNESS = r"""
const vm = require('node:vm');
const fs = require('fs');

const runtime = fs.readFileSync(process.argv[2], 'utf8');

function defineGetter(obj, name, value) {
  var accessor = function () { return value; };
  Object.defineProperty(accessor, 'name', {
    value: 'get ' + name, writable: false, enumerable: false, configurable: true
  });
  Object.defineProperty(obj, name, {
    get: accessor, set: undefined, enumerable: true, configurable: true
  });
}

const Navigator = function () {};
defineGetter(Navigator.prototype, 'userAgent', 'stock-ua');
defineGetter(Navigator.prototype, 'platform', 'stock-platform');
defineGetter(Navigator.prototype, 'hardwareConcurrency', 4);
defineGetter(Navigator.prototype, 'language', 'en-US');
defineGetter(Navigator.prototype, 'languages', ['en-US']);

const Screen = function () {};
defineGetter(Screen.prototype, 'width', 800);
defineGetter(Screen.prototype, 'height', 600);

const Math2 = {};
Math2.acos = function (x) { return Math.acos(x); };
Math2.acos.name = 'acos';

const sandbox = Object.create(null);
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
sandbox.navigator = new Navigator();
sandbox.Navigator = Navigator;
sandbox.Screen = Screen;
sandbox.screen = new Screen();
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

const context = vm.createContext(sandbox);
const thrown = [];
try {
  new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(context);
} catch (e) {
  thrown.push(String(e && e.stack || e));
}

const report = vm.runInContext(`(function () {
  function probe(label, obj, name) {
    var d = Object.getOwnPropertyDescriptor(obj, name);
    if (!d) { return { missing: true }; }
    if (!d.get) { return { data: true }; }
    var g = d.get;
    return {
      call: Function.prototype.toString.call(g),
      own: g.toString(),
      name: g.name,
      length: g.length,
      ownNames: Object.getOwnPropertyNames(g),
      symbols: Object.getOwnPropertySymbols(g).map(String),
      hasPrototype: 'prototype' in g
    };
  }
  var out = { thrown: [] };
  out.navUA = probe('navUA', Navigator.prototype, 'userAgent');
  out.navPlat = probe('navPlat', Navigator.prototype, 'platform');
  out.navCores = probe('navCores', Navigator.prototype, 'hardwareConcurrency');
  out.navLang = probe('navLang', Navigator.prototype, 'language');
  out.scrW = probe('scrW', Screen.prototype, 'width');
  out.fpts = {
    call: Function.prototype.toString.call(Function.prototype.toString),
    own: Function.prototype.toString.toString(),
    name: Function.prototype.toString.name,
    length: Function.prototype.toString.length,
    ownNames: Object.getOwnPropertyNames(Function.prototype.toString),
    symbols: Object.getOwnPropertySymbols(Function.prototype.toString).map(String)
  };
  // The functions that are not replaced have to be left alone.
  out.plain = Function.prototype.toString.call(function q() {});
  out.acos = Function.prototype.toString.call(Math.acos);
  out.max = Function.prototype.toString.call(Math.max);
  out.json = Function.prototype.toString.call(JSON.stringify);
  out.value = navigator.userAgent;
  return out;
})()`, context);

report.thrown = thrown;
console.log(JSON.stringify(report));
"""


@pytest.fixture(scope="module")
def report() -> dict:
    """The runtime's own report of how it looks from the outside."""

    config = ProfileGenerator().generate(
        "pixel-8-pro-android14", master_seed="a" * 64
    )
    script = JSRuntimeBuilder().build(config)

    folder = Path(tempfile.mkdtemp())
    runtime_path = folder / "runtime.js"
    runtime_path.write_text(script, encoding="utf-8")
    harness_path = folder / "harness.js"
    harness_path.write_text(HARNESS, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(harness_path), str(runtime_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(not Path(NODE).exists(), reason="node is not on this machine")
class TestTheRuntimeLeavesNoTrace:
    """A replacement has to look like the accessor it took the place of."""

    def test_the_runtime_installed_without_an_error(self, report: dict) -> None:
        assert report["thrown"] == []

    def test_the_replaced_accessor_reports_the_stock_text(self, report: dict) -> None:
        """The source text has to name the accessor, as Blink does."""

        assert report["navUA"]["call"] == "function get userAgent() { [native code] }"

    def test_the_two_routes_to_the_source_answer_the_same(self, report: dict) -> None:
        """``Function.prototype.toString`` and the own ``toString``.

        The built-in call reads the internal source of the function, and
        the own ``toString`` is a property on it.  A page can read
        either, and if the two disagree the disagreement is the
        giveaway -- so the replacement has to answer both with the
        text the stock accessor reports.
        """

        assert report["navUA"]["call"] == report["navUA"]["own"]
        assert report["navPlat"]["call"] == report["navPlat"]["own"]
        assert report["navCores"]["call"] == report["navCores"]["own"]
        assert report["navLang"]["call"] == report["navLang"]["own"]
        assert report["scrW"]["call"] == report["scrW"]["own"]

    def test_the_replaced_accessor_keeps_its_name(self, report: dict) -> None:
        assert report["navUA"]["name"] == "get userAgent"
        assert report["navPlat"]["name"] == "get platform"
        assert report["navCores"]["name"] == "get hardwareConcurrency"
        assert report["scrW"]["name"] == "get width"

    def test_the_replaced_accessor_keeps_its_length(self, report: dict) -> None:
        assert report["navUA"]["length"] == 0
        assert report["scrW"]["length"] == 0

    def test_the_replaced_accessor_lists_only_the_stock_own_names(
        self, report: dict
    ) -> None:
        """A stock accessor has exactly two own property names.

        An ordinary function has a third -- "prototype" -- and a
        replacement that carried one would be a difference a page could
        read without any source check at all.
        """

        for label in ("navUA", "navPlat", "navCores", "navLang", "scrW"):
            assert report[label]["ownNames"] == ["length", "name"]
            assert report[label]["symbols"] == []
            assert report[label]["hasPrototype"] is False

    def test_the_source_reader_looks_native(self, report: dict) -> None:
        """``Function.prototype.toString`` itself must look native.

        The runtime has to replace it -- that is the only way to report
        the source of a replacement -- and the replacement has to look
        exactly like the original.
        """

        assert report["fpts"]["call"] == "function toString() { [native code] }"
        assert report["fpts"]["own"] == report["fpts"]["call"]
        assert report["fpts"]["name"] == "toString"
        assert report["fpts"]["length"] == 0
        assert report["fpts"]["ownNames"] == ["length", "name"]
        assert report["fpts"]["symbols"] == []

    def test_the_functions_that_were_not_replaced_are_left_alone(
        self, report: dict
    ) -> None:
        """Only the replacements may answer differently.

        An ordinary JavaScript function reports its own source, and a
        built-in reports the native text.  If the replacement of the
        source reader changed that, every function in the page would
        look wrong.
        """

        assert report["plain"] in ("function q(){}", "function q() {}")
        assert report["acos"] == "function acos() { [native code] }"
        assert report["max"] == "function max() { [native code] }"
        assert report["json"] == "function stringify() { [native code] }"

    def test_the_runtime_still_delivers_the_profile(self, report: dict) -> None:
        """Hiding the replacement must not break it."""

        assert "Android" in report["value"]
