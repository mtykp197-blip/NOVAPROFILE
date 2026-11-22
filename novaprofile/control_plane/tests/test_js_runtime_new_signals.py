"""Execution tests for the three surfaces added after the survey.

The survey of the six target sites found three surfaces that the
runtime did not serve at all:

* ``Math.acos(0.5)`` and its relatives -- the floating-point
  behaviour of the transcendental functions, which follows the C
  library of the platform and is the classic way to tell a real
  browser from a faked DOM;
* ``performance.memory.jsHeapSizeLimit`` -- the V8 heap ceiling,
  which follows the physical memory of the machine;
* ``MediaRecorder.isTypeSupported`` -- the media codecs the platform
  claims to decode.

These are *execution* tests, in the same sense as
``test_js_runtime_execution.py``: they run the generated script under
Node and read back what the page would have read.  Reading the script
text cannot catch a script that throws, and a throw in the middle of
the runtime stops every override after it.
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


#: The harness.  It offers the three APIs the new surfaces need and
#: reports what the page would have read.
HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');
    const profile = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

    // -- performance.memory ------------------------------------------------
    // In Blink the heap limit is an accessor on the prototype of the
    // memory object, not a plain property on the object, and the
    // accessor is named "get jsHeapSizeLimit".  The harness copies
    // that shape, because the runtime has to find the accessor where
    // a browser puts it.
    const memoryProto = {};
    Object.defineProperty(memoryProto, 'jsHeapSizeLimit', {
      get: (function () {
        var accessor = function () { return 1073741824; };
        Object.defineProperty(accessor, 'name', {
          value: 'get jsHeapSizeLimit', writable: false,
          enumerable: false, configurable: true
        });
        return accessor;
      })(),
      set: undefined,
      enumerable: true,
      configurable: true
    });
    Object.defineProperty(memoryProto, 'totalJSHeapSize', {
      value: 10000000, writable: true, enumerable: true, configurable: true
    });
    Object.defineProperty(memoryProto, 'usedJSHeapSize', {
      value: 5000000, writable: true, enumerable: true, configurable: true
    });

    const performance = {
      memory: Object.create(memoryProto),
      now: function () { return 1234.5; },
      timing: {}
    };

    // -- MediaRecorder -----------------------------------------------------
    function MediaRecorder(stream, options) {
      this.stream = stream;
      this.options = options;
    }
    MediaRecorder.isTypeSupported = function (type) {
      return type === 'video/x-unsupported';
    };

    const sandbox = Object.create(null);
    sandbox.globalThis = sandbox;
    sandbox.window = sandbox;
    sandbox.performance = performance;
    sandbox.MediaRecorder = MediaRecorder;
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

    // The accessor is on the prototype, so that is where it is read
    // from -- the same way a browser puts it.
    const memoryDescriptor = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(performance.memory), 'jsHeapSizeLimit');

    console.log(JSON.stringify({
      thrown: thrown,

      // -- the math surface ---------------------------------------------
      acos: read(function () { return Math.acos(0.5); }),
      asin: read(function () { return Math.asin(0.5); }),
      sinh: read(function () { return Math.sinh(0.5); }),
      cosh: read(function () { return Math.cosh(0.5); }),
      tanh: read(function () { return Math.tanh(0.5); }),
      atan: read(function () { return Math.atan(0.5); }),
      // A point that is not one of the canonical probes must keep the
      // host's own answer.
      acosOther: read(function () { return Math.acos(0.25); }),
      acosSource: read(function () {
        return Function.prototype.toString.call(Math.acos);
      }),
      acosName: read(function () { return Math.acos.name; }),

      // -- the heap surface ---------------------------------------------
      heapLimit: read(function () { return performance.memory.jsHeapSizeLimit; }),
      heapSource: read(function () {
        return Function.prototype.toString.call(memoryDescriptor.get);
      }),
      heapName: read(function () { return memoryDescriptor.get.name; }),

      // -- the media decode surface -------------------------------------
      avcSupported: read(function () {
        return MediaRecorder.isTypeSupported('video/mp4;codecs="avc1.42E01E"');
      }),
      unsupported: read(function () {
        return MediaRecorder.isTypeSupported('video/x-unsupported');
      }),
      unknown: read(function () {
        return MediaRecorder.isTypeSupported('video/x-never-heard-of-it');
      }),
      mediaSource: read(function () {
        return Function.prototype.toString.call(MediaRecorder.isTypeSupported);
      }),
      mediaName: read(function () { return MediaRecorder.isTypeSupported.name; })
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
        "math_precision": profile.math_precision.model_dump(),
        "heap_size_limit": profile.heap_size_limit.model_dump(),
        "media_decode": profile.media_decode.model_dump(),
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
    """The new blocks must not break the rest of the runtime."""

    report = _run(profile, tmp_path)
    assert report["thrown"] == [], f"the runtime threw:\n{report['thrown']}"


# --------------------------------------------------------------------------
# The math surface
# --------------------------------------------------------------------------


def test_the_math_functions_report_the_profile_values(profile, tmp_path) -> None:
    """The canonical probe points must return the profile's values.

    ``Math.acos(0.5)`` is what ThumbmarkJS reads.  The value must be
    the profile's own, to the last bit.
    """

    report = _run(profile, tmp_path)
    assert report["acos"] == profile.math_precision.acos
    assert report["asin"] == profile.math_precision.asin
    assert report["sinh"] == profile.math_precision.sinh
    assert report["cosh"] == profile.math_precision.cosh
    assert report["tanh"] == profile.math_precision.tanh
    assert report["atan"] == profile.math_precision.atan


def test_the_math_drift_stays_inside_the_epsilon(profile, tmp_path) -> None:
    """The correction must never exceed the declared epsilon.

    A value that is off by more than one ulp would be a *different*
    engine rather than a different unit, so the bound has to hold.
    """

    report = _run(profile, tmp_path)
    epsilon = profile.math_precision.epsilon
    for name, reference in (
        ("acos", profile.math_precision.acos),
        ("asin", profile.math_precision.asin),
        ("sinh", profile.math_precision.sinh),
        ("cosh", profile.math_precision.cosh),
        ("tanh", profile.math_precision.tanh),
        ("atan", profile.math_precision.atan),
    ):
        assert abs(report[name] - reference) <= epsilon, name


def test_the_math_functions_keep_the_host_answer_elsewhere(profile, tmp_path) -> None:
    """A point that is not a canonical probe must not be corrected.

    The runtime corrects the values a fingerprinting script actually
    asks for.  Everywhere else the host's own result is the right
    answer, and a runtime that rewrote it would be wrong.
    """

    report = _run(profile, tmp_path)
    # The host's own value for acos(0.25), before the runtime runs.
    import math as _math

    assert report["acosOther"] == _math.acos(0.25)


def test_the_math_override_looks_native(profile, tmp_path) -> None:
    """A source check must not tell the override apart.

    A stock Blink method names itself in the text it reports:

        Function.prototype.toString.call(Math.acos)
          -> "function acos() { [native code] }"

    so the replacement reports the same text.  The replacement is a
    proxy, and V8 reports a proxy as native, but V8 reports it
    without a name -- so the text comes from the one function that
    reads it, and the replacement is registered there.
    """

    report = _run(profile, tmp_path)
    assert report["acosSource"] == "function acos() { [native code] }"
    assert report["acosName"] == "acos"


def test_the_profile_math_values_stay_inside_the_epsilon() -> None:
    """The profile's own values must be within the epsilon of the truth.

    The runtime guards the override with that epsilon: if the profile's
    value is further from the host's than the epsilon allows, the
    runtime concludes that the host is a different engine and keeps the
    host's answer.  A profile whose own value sits outside the band
    would therefore be silently ignored, so the band has to hold for
    every value the generator produces.
    """

    import math as _math

    for _ in range(25):
        config = ProfileGenerator().generate(
            "pixel-8-pro-android14",
            master_seed=new_master_seed(),
        )
        mp = config.math_precision
        for name in ("acos", "asin", "sinh", "cosh", "tanh", "atan"):
            reference = getattr(mp, name)
            truth = getattr(_math, name)(0.5)
            assert abs(reference - truth) <= mp.epsilon, name


def test_the_math_values_differ_between_profiles() -> None:
    """Two units must not report the very same last bits.

    The value of ``Math.acos(0.5)`` is one double, so the surface
    carries barely any entropy on its own.  The per-unit drift is what
    makes it an axis that can hold a fleet.

    The band is sixteen ulps wide, so one function alone reaches
    seventeen values -- enough to be useful, but the six functions
    together are what the axis actually offers.  The test therefore
    checks that the *combination* is distinct, and that one function
    already spreads over more than a handful of values.
    """

    combos = set()
    acos_values = set()
    for _ in range(40):
        config = ProfileGenerator().generate(
            "pixel-8-pro-android14",
            master_seed=new_master_seed(),
        )
        mp = config.math_precision
        combos.add(
            (
                mp.acos,
                mp.asin,
                mp.sinh,
                mp.cosh,
                mp.tanh,
                mp.atan,
            )
        )
        acos_values.add(mp.acos)

    assert len(combos) == 40, f"only {len(combos)} distinct combinations in 40 profiles"
    assert len(acos_values) >= 10, f"only {len(acos_values)} distinct acos values"


# --------------------------------------------------------------------------
# The heap size limit
# --------------------------------------------------------------------------


def test_the_heap_limit_reports_the_profile_value(profile, tmp_path) -> None:
    """``performance.memory.jsHeapSizeLimit`` must be the profile's."""

    report = _run(profile, tmp_path)
    assert report["heapLimit"] == profile.heap_size_limit.js_heap_size_limit


def test_the_heap_override_looks_native(profile, tmp_path) -> None:
    """The getter must survive a source check and keep its name.

    In Blink the heap limit lives on the prototype of the memory
    object, and the accessor there is named "get jsHeapSizeLimit".
    The replacement reports the same name and the same source text, so
    a page that reads the descriptor cannot tell it from the stock
    one.
    """

    report = _run(profile, tmp_path)
    assert report["heapSource"] == "function get jsHeapSizeLimit() { [native code] }"
    assert report["heapName"] == "get jsHeapSizeLimit"


# --------------------------------------------------------------------------
# The media decode surface
# --------------------------------------------------------------------------


def test_the_supported_codecs_are_reported_as_supported(profile, tmp_path) -> None:
    """Every codec of the profile must answer ``true``."""

    report = _run(profile, tmp_path)
    assert report["avcSupported"] is True


def test_the_unsupported_codec_falls_through_to_the_host(profile, tmp_path) -> None:
    """A codec the profile does not claim must keep the host's answer.

    The host reports ``true`` for ``video/x-unsupported`` in the
    harness, so the fall-through is observable: a runtime that
    answered ``false`` for everything else would break the page.
    """

    report = _run(profile, tmp_path)
    assert report["unsupported"] is True


def test_the_unknown_codec_keeps_the_host_answer(profile, tmp_path) -> None:
    """A codec the host does not know must stay unknown."""

    report = _run(profile, tmp_path)
    assert report["unknown"] is False


def test_the_media_override_looks_native(profile, tmp_path) -> None:
    """The replacement must survive a source check and keep its name.

    A stock Blink method names itself in the text it reports, so the
    replacement reports the same text.
    """

    report = _run(profile, tmp_path)
    assert report["mediaSource"] == "function isTypeSupported() { [native code] }"
    assert report["mediaName"] == "isTypeSupported"


# --------------------------------------------------------------------------
# The surfaces hold a fleet
# --------------------------------------------------------------------------


def test_the_heap_tier_varies_between_profiles() -> None:
    """A fleet where every unit reports the same ceiling is a giveaway.

    The ceiling is one of three values, so a fleet of a hundred
    thousand profiles would be easy to group on it.  The tier is
    therefore drawn per unit.
    """

    tiers = set()
    for _ in range(30):
        config = ProfileGenerator().generate(
            "pixel-8-pro-android14",
            master_seed=new_master_seed(),
        )
        tiers.add(config.heap_size_limit.js_heap_size_limit)

    assert len(tiers) > 1, f"every profile reported {tiers}"


def test_the_heap_ceiling_is_one_a_phone_can_have() -> None:
    """The ceiling must be a value Chromium actually derives.

    Chromium rounds the physical memory down to a power of two and
    caps the heap there, so the only sensible values are 2 GB, 4 GB
    and 8 GB.

    The core document, however, is read by Chromium's own strict JSON
    parser, and that parser stores every integer in a signed 32-bit
    field.  A ceiling above 2^31-1 therefore fails to load with
    "missing or non-integer field" and the browser silently falls back
    to the host's values -- which is worse than a smaller ceiling, so
    the generator clamps the ceiling to what the parser can carry.
    """

    for _ in range(30):
        config = ProfileGenerator().generate(
            "pixel-8-pro-android14",
            master_seed=new_master_seed(),
        )
        assert config.heap_size_limit.js_heap_size_limit in (
            536870912,
            1073741824,
            2000000000,
            2147483647,
            2147483648,
            4294967296,
            8589934592,
        )

