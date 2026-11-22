"""The permissions surface has to answer like a browser, not like a table.

A script reads the permission states and it reads them in two ways: it
asks for the state of a permission it knows, and it asks for a
permission it does not know.  The second one is the interesting one,
because the answer to it is a mistake -- a browser rejects a name it
does not know, and a profile that answers every question has never
made one.

The states also have to hang together with each other, because a
profile that grants the camera and denies the microphone on the same
handset, at the same moment, without the user having done anything in
between, is a shape no person produces.
"""

from __future__ import annotations

import json
import shutil
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


def _payload_of(preset_id: str = "pixel-8-pro-android14", seed: str = "a" * 64) -> dict:
    from novaprofile.js_runtime.builder import JSRuntimeBuilder

    generator = ProfileGenerator()
    config = generator.generate(preset_id, master_seed=seed)
    script = JSRuntimeBuilder().build(config)
    marker = "window.__NOVA_PROFILE__="
    start = script.index(marker) + len(marker)
    end = script.index(";\n", start)
    return json.loads(script[start:end])


class TestThePolicyTable:
    """The table carries the states a script reads."""

    def test_the_table_is_not_empty(self) -> None:
        payload = _payload_of()

        assert payload["permissions"]

    def test_every_state_is_one_the_api_knows(self) -> None:
        payload = _payload_of(seed="b" * 64)

        for name, state in payload["permissions"].items():
            assert state in ("granted", "denied", "prompt"), (
                f"{name} carries the state {state!r}"
            )

    def test_the_location_and_the_camera_are_both_there(self) -> None:
        payload = _payload_of()

        assert "geolocation" in payload["permissions"]
        assert "camera" in payload["permissions"]
        assert "microphone" in payload["permissions"]

    def test_the_states_differ_between_profiles(self) -> None:
        """A fleet where every profile carries the same states is a fleet
        a vendor groups on for free."""
        states = set()
        generator = ProfileGenerator()
        for index in range(30):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            states.add(
                tuple(sorted((k, str(v)) for k, v in config.permissions.policies.items()))
            )

        assert len(states) > 10


class TestTheStatesHangTogether:
    """A person's permissions have a shape."""

    def test_a_profile_that_denied_everything_is_rare(self) -> None:
        """Denying the location, the notifications and the camera at once
        is the shape of a fresh disposable browser."""
        generator = ProfileGenerator()
        harsh = 0
        total = 200
        for index in range(total):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            policies = {k: str(v) for k, v in config.permissions.policies.items()}
            if (
                policies.get("geolocation") == "denied"
                and policies.get("notifications") == "denied"
                and policies.get("camera") == "denied"
            ):
                harsh += 1

        assert harsh / total < 0.05

    def test_the_camera_is_never_granted_without_a_visit(self) -> None:
        """A camera permission is granted by a person, not by a browser."""
        generator = ProfileGenerator()
        granted = 0
        total = 200
        for index in range(total):
            config = generator.generate(
                "pixel-8-pro-android14",
                master_seed=f"{index:064x}",
            )
            policies = {k: str(v) for k, v in config.permissions.policies.items()}
            if policies.get("camera") == "granted":
                granted += 1

        assert granted / total < 0.1


class TestTheRuntimeAnswers:
    """The layer that hands the states to the page."""

    @pytest.mark.parametrize(
        "name", ["geolocation", "notifications", "camera", "microphone"]
    )
    def test_the_runtime_carries_the_state(self, name: str) -> None:
        payload = _payload_of()

        assert name in payload["permissions"]

    def test_the_runtime_knows_the_permission_names(self) -> None:
        """The list of names the browser accepts has to be in the script,
        or the runtime cannot reject the names it does not know."""
        from novaprofile.js_runtime.builder import JSRuntimeBuilder

        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="c" * 64)
        script = JSRuntimeBuilder().build(config)

        assert "KNOWN_PERMISSIONS" in script
        assert "background-sync" in script
        assert "payment-handler" in script

    def test_the_runtime_rejects_an_unknown_name(self) -> None:
        """The behaviour is checked by running the script, not by reading
        it."""
        import subprocess
        import tempfile
        from pathlib import Path

        from novaprofile.js_runtime.builder import JSRuntimeBuilder

        node = _find_node()
        if node is None:
            pytest.skip("node is not installed")


        generator = ProfileGenerator()
        config = generator.generate("pixel-8-pro-android14", master_seed="d" * 64)
        script = JSRuntimeBuilder().build(config)

        harness = (
            """
// A browser-shaped environment with just enough of it for the
// permissions surface to run in.
const vm = require('node:vm');

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

const PermissionStatus = function () {};
const permissions = {
  query: function (descriptor) {
    // The stock browser answers a known name and rejects an unknown
    // one; the harness only has to know the names.
    return Promise.reject(new TypeError('stock'));
  }
};

const navigator = new Navigator();
navigator.permissions = permissions;

const sandbox = {
  navigator: navigator,
  permissions: permissions,
  PermissionStatus: PermissionStatus,
  Navigator: Navigator,
  Screen: function () {},
  Promise: Promise,
  Object: Object,
  Array: Array,
  Error: Error,
  TypeError: TypeError,
  JSON: JSON,
  Math: Math,
  String: String,
  Number: Number,
  Boolean: Boolean,
  RegExp: RegExp,
  Date: Date,
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
vm.runInContext(require('fs').readFileSync(process.argv[2], 'utf8'), sandbox);

var failures = [];
function check(name, ok) { if (!ok) { failures.push(name); } }

// A permission the browser knows is answered with a state.
sandbox.navigator.permissions.query({name: 'geolocation'}).then(function (status) {
  check('the state is a string', typeof status.state === 'string');
  check('the name is the one asked for', status.name === 'geolocation');
  check('it carries the listener methods',
        typeof status.addEventListener === 'function');
}).catch(function () {
  failures.push('the known permission was rejected');
});

// A permission the browser does not know is rejected.
sandbox.navigator.permissions.query({name: 'not-a-permission'}).then(function () {
  failures.push('an unknown name was answered');
}).catch(function (err) {
  check('the unknown name is a TypeError', err instanceof TypeError);
});

setTimeout(function () {
  if (failures.length) {
    console.log('FAIL: ' + failures.join('; '));
    process.exit(1);
  }
  console.log('PASS');
}, 300);
"""
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.js"
            path.write_text(harness, encoding="utf-8")
            runtime_path = Path(folder) / "runtime.js"
            runtime_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, str(path), str(runtime_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS" in result.stdout
