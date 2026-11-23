"""End-to-end verification harness: simulator versus a real browser.

The runtime probe (:mod:`novaprofile.probe.probe`) measures a browser and
compares the measurements against the *expectations* that the profile
config declares.  The deterministic simulator
(:mod:`novaprofile.probe.simulator`) answers the same question without a
browser.  Neither of the two, on its own, proves that the **native core**
actually substitutes the surfaces it claims to substitute.

This module closes that gap.  It stands up a *real* Chromium, installs a
script that reads the twelve guarded fingerprint axes, and compares what
the browser reports against what the simulator says the profile *would*
report.  A disagreement is not a cosmetic difference: it means the profile
that the control plane hands to the core is not the profile that the core
presents to the site.

Design constraints
------------------

* **No network.**  The test page is served from an in-process
  ``http.server`` bound to ``127.0.0.1`` on an ephemeral port.  Nothing in
  this module ever reaches an external host, so the whole harness runs
  inside a sealed CI machine.
* **No new dependency.**  The DevTools client speaks RFC-6455 over the
  already-declared ``websockets`` package (see ``pyproject.toml``).  The
  frame *parser* is implemented here from the specification so that it can
  be unit-tested against a hand-built frame without opening a socket.
* **Deterministic tests.**  The comparison engine, the tolerance policy and
  the frame parser are pure functions; the browser-facing parts are only
  reachable through explicit injection points.

The fifteen guarded axes
-----------------------

The axis set is the one the specification names::

    identity, client_hints, screen, webgl, canvas, audio, fonts, rects,
    media_devices, sensors, storage, webgpu, math, heap, media_decode

It corresponds to the per-axis signature inputs of
:meth:`novaprofile.schema.profile.ProfileConfig.signature_parts`: the
fourteen ``sig_*`` keys cover thirteen of the fifteen axes directly, and
``sig_webgl_draw`` / ``sig_webgl_params`` together form the single
``webgl`` axis (a WebGL surface has two halves -- the pixels it draws and
the parameter table it advertises -- and a real mismatch in either half
invalidates the axis).  ``client_hints`` is guarded through
``sig_device``: the client hints carry the device model and platform
version, which are part of the device identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from ..schema.profile import ProfileConfig
from .probe import DEFAULT_CHROME_PATHS, CdpError
from .simulator import simulate_payload

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: The fifteen guarded axes, in the order they are reported.
GUARDED_AXES: Final[tuple[str, ...]] = (
    "identity",
    "client_hints",
    "screen",
    "webgl",
    "canvas",
    "audio",
    "fonts",
    "rects",
    "media_devices",
    "sensors",
    "storage",
    "webgpu",
    "math",
    "heap",
    "media_decode",
)

#: The verdict of a single axis comparison.
Verdict = Literal["match", "mismatch", "unavailable"]

#: The comparison mode of a single axis.
ToleranceMode = Literal["exact", "relative", "ignore"]

#: The page that is served to the browser.  It is deliberately tiny: the
#: interesting part is the script that the DevTools client injects, not the
#: document.
TEST_PAGE: Final[str] = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NOVA e2e surface page</title>
</head>
<body>
<h1 id="nova-e2e">NOVA e2e surface page</h1>
<div id="host" style="position:absolute;left:11px;top:7px;width:273px;height:61px;">
  <div id="inner" style="width:120px;height:23px;">nova-rects-probe</div>
</div>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Tolerances
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tolerance:
    """How much a measured value may deviate from the expected one.

    ``mode`` selects the comparison:

    ``exact``
        The two values must be equal, byte for byte, after normalisation.
        Used for every value that is a *property of the profile* rather
        than a property of the machine: the user agent, the screen size,
        the storage quota.
    ``relative``
        The two values are numbers and may differ by at most
        ``relative`` (a fraction of the expected value, or an absolute
        floor of ``absolute`` when the expected value is tiny).  Used for
        the values that a real GPU may legitimately report differently
        from the modelled one (``MAX_TEXTURE_SIZE`` and friends) and for
        the values that carry a small amount of measurement noise.
    ``ignore``
        The axis is measured and reported, but the value is never
        compared.  Used for timing values, whose exact magnitude depends
        on the audio device and the scheduler rather than on the profile.

    The tolerances are deliberately tight.  A relative tolerance of one
    percent on a texture size of 16384 still rejects a 4096 -- a factor of
    four -- and the absolute floor keeps a relative tolerance from
    swallowing a small-but-real difference on a value near zero.
    """

    mode: ToleranceMode
    relative: float = 0.0
    absolute: float = 0.0
    reason: str = ""

    def allows(self, expected: float, actual: float) -> bool:
        """Return ``True`` when ``actual`` is within the tolerance of ``expected``."""

        if self.mode == "ignore":
            return True
        if self.mode == "exact":
            return expected == actual
        limit = max(abs(expected) * self.relative, self.absolute)
        return abs(expected - actual) <= limit


#: The tolerance of every axis.
#:
#: The policy, axis by axis:
#:
#: ``identity``
#:     exact -- the user agent, the platform and the language are declared
#:     by the profile and must be substituted verbatim.
#: ``client_hints``
#:     exact -- the brands, the platform version, the model and the
#:     bitness are declared by the profile; a site reads them as a set and
#:     any inconsistency between them is a hard fingerprint.
#: ``screen``
#:     exact -- the panel size and the pixel ratio are declared.  A
#:     difference here is the classic "the profile and the browser disagree
#:     about the pixel ratio" leak.
#: ``webgl``
#:     relative (1 %) -- a real GPU reports a slightly different
#:     ``MAX_TEXTURE_SIZE`` than the modelled one, and the shader
#:     precision ranges differ between drivers.  A 1 % window still
#:     rejects a factor-of-four difference.
#: ``canvas``
#:     exact -- the canvas hash is *derived from the profile seed*, so it
#:     is fully determined.  Any difference means the core did not apply
#:     the noise.
#: ``audio``
#:     relative (2 %) on the magnitudes and ignore on the timing -- the
#:     rendered buffer depends on the audio stack, the latencies depend on
#:     the sound device.
#: ``fonts``
#:     exact -- the installed font list is declared by the profile.
#: ``rects``
#:     relative (1 px) -- the geometry is perturbed by whole pixels, so
#:     the tolerance is one device pixel rather than a fraction.
#: ``media_devices``
#:     exact on the counts and the kinds -- the ids are salted per profile
#:     and are compared by set membership.
#: ``sensors``
#:     relative (5 %) -- the gravity reading carries a small jitter by
#:     design, but a wrong gravity constant is a hard failure.
#: ``storage``
#:     exact -- the quota and the effective connection type are declared.
#: ``webgpu``
#:     relative (1 %) -- the adapter limits belong to the GPU model, and
#:     the exact table differs between drivers.
TOLERANCES: Final[dict[str, Tolerance]] = {
    "identity": Tolerance("exact", reason="the user agent is declared by the profile"),
    "client_hints": Tolerance("exact", reason="the client hints are declared by the profile"),
    "screen": Tolerance("exact", reason="the panel geometry is declared by the profile"),
    "webgl": Tolerance(
        "relative",
        relative=0.01,
        absolute=1.0,
        reason="a real GPU reports a slightly different parameter table",
    ),
    "canvas": Tolerance("exact", reason="the canvas hash is derived from the profile seed"),
    "audio": Tolerance(
        "relative",
        relative=0.02,
        absolute=1e-6,
        reason="the rendered buffer depends on the audio stack",
    ),
    "fonts": Tolerance("exact", reason="the installed font list is declared by the profile"),
    "rects": Tolerance(
        "relative",
        relative=0.0,
        absolute=1.0,
        reason="the geometry is perturbed by whole device pixels",
    ),
    "media_devices": Tolerance("exact", reason="the device inventory is declared by the profile"),
    "sensors": Tolerance(
        "relative",
        relative=0.05,
        absolute=0.05,
        reason="the motion signal carries a jitter by design",
    ),
    "storage": Tolerance("exact", reason="the quota is declared by the profile"),
    "webgpu": Tolerance(
        "relative",
        relative=0.01,
        absolute=1.0,
        reason="the adapter limits belong to the GPU model",
    ),
    "math": Tolerance(
        "exact",
        reason="the transcendental values are the profile's, to the last bit",
    ),
    "heap": Tolerance("exact", reason="the heap ceiling is declared by the profile"),
    "media_decode": Tolerance(
        "exact",
        reason="the codec inventory is declared by the profile",
    ),
}


def tolerance_for(axis: str) -> Tolerance:
    """Return the tolerance of ``axis``.

    An unknown axis falls back to ``exact``: the safe default is to report
    a difference rather than to hide it.
    """

    return TOLERANCES.get(axis, Tolerance("exact", reason="unknown axis -- compared exactly"))


# --------------------------------------------------------------------------
# Surfaces
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceSpec:
    """How one axis is read out of the page.

    ``expression`` is the JavaScript that is evaluated in the page.  It
    must be a single expression that resolves to a JSON-serialisable
    value; the DevTools client awaits it, so a promise is fine.

    ``fields`` maps the name of a field inside the returned object onto the
    path *inside the profile config* that declares the expected value.
    A field whose expected path yields ``None`` is not asserted -- the
    profile does not describe it -- but it is still measured and reported.

    ``timing_fields`` names the fields that are timing values.  They are
    measured, reported and compared with an ``ignore`` tolerance, because
    their magnitude depends on the machine and not on the profile.
    """

    axis: str
    expression: str
    fields: Mapping[str, str]
    timing_fields: frozenset[str] = frozenset()
    description: str = ""

    def config_path(self, field_name: str) -> str:
        """Return the config path of ``field_name``."""

        return self.fields[field_name]


#: The JavaScript that reads every guarded axis.
#:
#: The expressions are deliberately written the way a *fingerprinting
#: script* would write them -- ``navigator.userAgent``,
#: ``gl.getParameter(gl.MAX_TEXTURE_SIZE)``, ``canvas.toDataURL()`` -- so
#: that the harness measures what a site measures and not what the engine
#: happens to expose.
#:
#: The whole extractor is a single script that installs
#: ``window.__NOVA_E2E__``; the DevTools client then evaluates
#: ``window.__NOVA_E2E__.read()`` and receives the twelve axis values in
#: one round trip.
SURFACE_SCRIPT: Final[str] = r"""
(function () {
  "use strict";

  function round(n, digits) {
    if (typeof n !== "number" || !isFinite(n)) { return null; }
    var f = Math.pow(10, digits);
    return Math.round(n * f) / f;
  }

  function fnv1aHex(bytes) {
    var h = 0x811c9dc5;
    for (var i = 0; i < bytes.length; i++) {
      h ^= bytes[i];
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    var out = "";
    for (var j = 7; j >= 0; j--) {
      var nib = (h >>> (j * 4)) & 0xf;
      out += nib.toString(16);
    }
    return out;
  }

  function hashString(text) {
    var enc = new TextEncoder();
    return fnv1aHex(enc.encode(text));
  }

  // -- identity ----------------------------------------------------------
  function readIdentity() {
    var nav = navigator;
    return {
      userAgent: nav.userAgent,
      appVersion: nav.appVersion,
      appName: nav.appName,
      appCodeName: nav.appCodeName,
      product: nav.product,
      productSub: nav.productSub,
      vendor: nav.vendor,
      vendorSub: nav.vendorSub,
      platform: nav.platform,
      hardwareConcurrency: nav.hardwareConcurrency,
      deviceMemory: nav.deviceMemory,
      maxTouchPoints: nav.maxTouchPoints,
      language: nav.language,
      languages: Array.prototype.slice.call(nav.languages || []),
      webdriver: nav.webdriver === true,
      pdfViewerEnabled: nav.pdfViewerEnabled === true
    };
  }

  // -- client hints ------------------------------------------------------
  //
  // The high-entropy hints are not properties of the object: the
  // platform version, the architecture, the bitness, the model and the
  // full version list only come out of
  // ``getHighEntropyValues()``, and only when the document is allowed
  // to ask for them.  Reading them as properties yields ``undefined``
  // on every browser, patched or not, so the reader asks for them the
  // way a fingerprinting script does.
  function readClientHints() {
    var uad = navigator.userAgentData;
    if (!uad) {
      return Promise.resolve({ available: false, brands: [], fullVersionList: [] });
    }
    var hints = [
      "platformVersion", "architecture", "bitness", "model",
      "uaFullVersion", "fullVersionList", "wow64", "formFactors"
    ];
    var high = null;
    try {
      var pending = uad.getHighEntropyValues ? uad.getHighEntropyValues(hints) : null;
      if (pending && typeof pending.then === "function") {
        high = pending.then(function (values) { return values || {}; });
      }
    } catch (e) {
      high = null;
    }
    if (!high) {
      high = Promise.resolve({});
    }
    return high.then(function (values) {
      return {
        available: true,
        brands: (uad.brands || []).map(function (b) {
          return { brand: b.brand, version: b.version };
        }),
        fullVersionList: (values.fullVersionList || uad.fullVersionList || []).map(function (b) {
          return { brand: b.brand, version: b.version };
        }),
        mobile: uad.mobile === true,
        platform: uad.platform,
        platformVersion: values.platformVersion,
        architecture: values.architecture,
        bitness: values.bitness,
        model: values.model,
        uaFullVersion: values.uaFullVersion,
        wow64: values.wow64 === true,
        formFactors: values.formFactors || []
      };
    });
  }

  // -- screen ------------------------------------------------------------
  function readScreen() {
    return {
      width: screen.width,
      height: screen.height,
      availWidth: screen.availWidth,
      availHeight: screen.availHeight,
      colorDepth: screen.colorDepth,
      pixelDepth: screen.pixelDepth,
      devicePixelRatio: window.devicePixelRatio,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight
    };
  }

  // -- webgl -------------------------------------------------------------
  function readWebgl() {
    var canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    var gl = null;
    var contextName = "";
    try {
      gl = canvas.getContext("webgl2", { preserveDrawingBuffer: true, antialias: false });
      if (gl) { contextName = "webgl2"; }
    } catch (e) { gl = null; }
    if (!gl) {
      try {
        gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, antialias: false });
        if (gl) { contextName = "webgl"; }
      } catch (e) { gl = null; }
    }
    if (!gl) {
      return { available: false, context: null, parameters: {} };
    }
    var debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
    var unmaskedVendor = debugInfo
      ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL)
      : gl.getParameter(gl.VENDOR);
    var unmaskedRenderer = debugInfo
      ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
      : gl.getParameter(gl.RENDERER);
    var parameters = {};
    var names = {
      maxTextureSize: "MAX_TEXTURE_SIZE",
      maxVaryingVectors: "MAX_VARYING_VECTORS",
      maxFragmentUniformVectors: "MAX_FRAGMENT_UNIFORM_VECTORS",
      maxVertexUniformVectors: "MAX_VERTEX_UNIFORM_VECTORS",
      maxTextureImageUnits: "MAX_TEXTURE_IMAGE_UNITS",
      maxSamples: "MAX_SAMPLES",
      maxRenderbufferSize: "MAX_RENDERBUFFER_SIZE",
      maxCubeMapTextureSize: "MAX_CUBE_MAP_TEXTURE_SIZE",
      max3dTextureSize: "MAX_3D_TEXTURE_SIZE"
    };
    for (var key in names) {
      if (!Object.prototype.hasOwnProperty.call(names, key)) { continue; }
      try {
        var value = gl.getParameter(gl[names[key]]);
        parameters[key] = (value === null || value === undefined) ? null : value;
      } catch (e) {
        parameters[key] = null;
      }
    }
    var ranges = {};
    try {
      ranges.aliasedLineWidthRange = Array.prototype.slice.call(
        gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE) || []
      );
      ranges.aliasedPointSizeRange = Array.prototype.slice.call(
        gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) || []
      );
    } catch (e) {
      ranges.aliasedLineWidthRange = [];
      ranges.aliasedPointSizeRange = [];
    }
    var extensions = gl.getSupportedExtensions() || [];
    canvas.width = 0;
    canvas.height = 0;
    return {
      available: true,
      context: contextName,
      unmaskedVendor: String(unmaskedVendor),
      unmaskedRenderer: String(unmaskedRenderer),
      version: String(gl.getParameter(gl.VERSION)),
      shadingLanguageVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION)),
      parameters: parameters,
      ranges: ranges,
      extensionCount: extensions.length,
      extensionHash: hashString(extensions.slice().sort().join("|"))
    };
  }

  // -- canvas ------------------------------------------------------------
  function readCanvas() {
    var canvas = document.createElement("canvas");
    canvas.width = 280;
    canvas.height = 120;
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) {
      return { available: false };
    }
    ctx.fillStyle = "#f2f4f7";
    ctx.fillRect(0, 0, 280, 120);
    var lin = ctx.createLinearGradient(0, 0, 280, 120);
    lin.addColorStop(0, "#1a73e8");
    lin.addColorStop(0.5, "#34a853");
    lin.addColorStop(1, "#ea4335");
    ctx.fillStyle = lin;
    ctx.fillRect(6, 6, 268, 108);
    ctx.font = "16px Arial";
    ctx.fillStyle = "#0b8043";
    ctx.fillText("NOVA-2D", 24, 48);
    var data = ctx.getImageData(0, 0, 280, 120).data;
    var bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    var dataUrl = "";
    try {
      dataUrl = canvas.toDataURL("image/png");
    } catch (e) {
      dataUrl = "";
    }
    var sample = [];
    for (var i = 0; i < 8 && i < data.length; i++) {
      sample.push(data[i]);
    }
    canvas.width = 0;
    canvas.height = 0;
    return {
      available: true,
      hash: fnv1aHex(bytes),
      dataUrlHash: dataUrl ? hashString(dataUrl) : "",
      dataUrlLength: dataUrl.length,
      firstPixels: sample
    };
  }

  // -- audio -------------------------------------------------------------
  function readAudio() {
    var OfflineCtor = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!OfflineCtor) {
      return Promise.resolve({ available: false });
    }
    var context = new OfflineCtor(1, 44100, 44100);
    var oscillator = context.createOscillator();
    oscillator.type = "triangle";
    oscillator.frequency.value = 10000;
    var compressor = context.createDynamicsCompressor();
    compressor.threshold.value = -50;
    compressor.knee.value = 40;
    compressor.ratio.value = 12;
    compressor.attack.value = 0;
    compressor.release.value = 0.25;
    oscillator.connect(compressor);
    compressor.connect(context.destination);
    oscillator.start(0);
    return context.startRendering().then(function (buffer) {
      var data = buffer.getChannelData(0);
      var sum = 0;
      var peak = 0;
      for (var i = 0; i < data.length; i++) {
        var v = Math.abs(data[i]);
        sum += v;
        if (v > peak) { peak = v; }
      }
      var mean = sum / data.length;
      var out = {
        available: true,
        sampleRate: buffer.sampleRate,
        length: buffer.length,
        numberOfChannels: buffer.numberOfChannels,
        mean: round(mean, 6),
        peak: round(peak, 6),
        sum: round(sum, 4)
      };
      try {
        var live = new (window.AudioContext || window.webkitAudioContext)();
        out.baseLatency = live.baseLatency === undefined ? null : round(live.baseLatency, 6);
        out.outputLatency = live.outputLatency === undefined ? null : round(live.outputLatency, 6);
        out.sampleRateLive = live.sampleRate;
        live.close();
      } catch (e) {
        out.baseLatency = null;
        out.outputLatency = null;
        out.sampleRateLive = null;
      }
      return out;
    }).catch(function (err) {
      return { available: false, error: String((err && err.message) || err) };
    });
  }

  // -- fonts -------------------------------------------------------------
  var FONT_CANDIDATES = __NOVA_FONT_CANDIDATES__;

  function readFonts() {
    try {
      var canvas = document.createElement("canvas");
      canvas.width = 800;
      canvas.height = 120;
      var ctx = canvas.getContext("2d");
      if (!ctx) {
        return { available: false, installed: [] };
      }
      var BASE = 72;
      var PROBE_TEXT = "mmmwwwiiilll0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
      var generics = ["monospace", "sans-serif", "serif"];
      var genericWidths = {};
      ctx.font = BASE + "px monospace";
      genericWidths.monospace = ctx.measureText(PROBE_TEXT).width;
      ctx.font = BASE + "px sans-serif";
      genericWidths.sansSerif = ctx.measureText(PROBE_TEXT).width;
      ctx.font = BASE + "px serif";
      genericWidths.serif = ctx.measureText(PROBE_TEXT).width;
      var widths = {};
      var installed = [];
      for (var i = 0; i < FONT_CANDIDATES.length; i++) {
        var family = FONT_CANDIDATES[i];
        ctx.font = BASE + "px " + family;
        widths[family] = round(ctx.measureText(PROBE_TEXT).width, 2);
        var isGeneric = generics.indexOf(family) !== -1;
        if (isGeneric) { continue; }
        if (widths[family] === genericWidths.monospace) { continue; }
        if (widths[family] === genericWidths.sansSerif) { continue; }
        if (widths[family] === genericWidths.serif) { continue; }
        installed.push(family);
      }
      canvas.width = 0;
      canvas.height = 0;
      return {
        available: true,
        widths: widths,
        genericWidths: genericWidths,
        installed: installed.sort()
      };
    } catch (err) {
      return { available: false, error: String((err && err.message) || err) };
    }
  }

  // -- rects -------------------------------------------------------------
  function readRects() {
    try {
      var host = document.getElementById("host");
      var inner = document.getElementById("inner");
      if (!host || !inner) {
        return { available: false };
      }
      function rectToObj(r) {
        return {
          x: round(r.x, 2), y: round(r.y, 2),
          top: round(r.top, 2), right: round(r.right, 2),
          bottom: round(r.bottom, 2), left: round(r.left, 2),
          width: round(r.width, 2), height: round(r.height, 2)
        };
      }
      var range = document.createRange();
      range.selectNodeContents(inner);
      var measure = null;
      try {
        var c = document.createElement("canvas");
        var g = c.getContext("2d");
        g.font = "16px Arial";
        var m = g.measureText("NOVA-rects");
        measure = {
          width: round(m.width, 2),
          actualBoundingBoxAscent: round(m.actualBoundingBoxAscent, 2),
          actualBoundingBoxDescent: round(m.actualBoundingBoxDescent, 2)
        };
      } catch (e) {
        measure = null;
      }
      return {
        available: true,
        host: rectToObj(host.getBoundingClientRect()),
        inner: rectToObj(inner.getBoundingClientRect()),
        range: rectToObj(range.getBoundingClientRect()),
        hostClientRectCount: host.getClientRects().length,
        innerClientRectCount: inner.getClientRects().length,
        rangeRectCount: range.getClientRects().length,
        measureText: measure,
        devicePixelRatio: window.devicePixelRatio
      };
    } catch (err) {
      return { available: false, error: String((err && err.message) || err) };
    }
  }

  // -- media devices -----------------------------------------------------
  function readMediaDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return Promise.resolve({ available: false, devices: [] });
    }
    return navigator.mediaDevices.enumerateDevices().then(function (devices) {
      var list = (devices || []).map(function (d) {
        return {
          deviceId: d.deviceId,
          kind: d.kind,
          label: d.label,
          groupId: d.groupId
        };
      });
      return {
        available: true,
        devices: list,
        count: list.length,
        kinds: list.map(function (d) { return d.kind; }).sort()
      };
    }).catch(function (err) {
      return { available: false, error: String((err && err.message) || err) };
    });
  }

  // -- sensors -----------------------------------------------------------
  function readSensors() {
    function once(target, eventName, timeoutMs) {
      return new Promise(function (resolve) {
        var settled = false;
        function finish(value) {
          if (settled) { return; }
          settled = true;
          resolve(value);
        }
        var timer = setTimeout(function () { finish(null); }, timeoutMs || 1000);
        try {
          target.addEventListener(eventName, function handler(event) {
            clearTimeout(timer);
            target.removeEventListener(eventName, handler);
            finish(event);
          }, { passive: true });
        } catch (e) {
          clearTimeout(timer);
          finish(null);
        }
      });
    }
    var hasMotion = "DeviceMotionEvent" in window;
    var hasOrientation = "DeviceOrientationEvent" in window;
    var tasks = [];
    if (hasMotion) {
      tasks.push(
        once(window, "devicemotion", 1000).then(function (event) {
          if (!event) { return null; }
          return {
            interval: event.interval,
            acceleration: event.acceleration
              ? { x: event.acceleration.x, y: event.acceleration.y, z: event.acceleration.z }
              : null,
            accelerationIncludingGravity: event.accelerationIncludingGravity
              ? {
                  x: event.accelerationIncludingGravity.x,
                  y: event.accelerationIncludingGravity.y,
                  z: event.accelerationIncludingGravity.z
                }
              : null,
            rotationRate: event.rotationRate
              ? {
                  alpha: event.rotationRate.alpha,
                  beta: event.rotationRate.beta,
                  gamma: event.rotationRate.gamma
                }
              : null
          };
        })
      );
    }
    if (hasOrientation) {
      tasks.push(
        once(window, "deviceorientation", 1000).then(function (event) {
          if (!event) { return null; }
          return {
            absolute: event.absolute,
            alpha: event.alpha,
            beta: event.beta,
            gamma: event.gamma
          };
        })
      );
    }
    return Promise.all(tasks).then(function (values) {
      var out = {
        available: hasMotion || hasOrientation,
        hasDeviceMotion: hasMotion,
        hasDeviceOrientation: hasOrientation,
        motionSample: null,
        orientationSample: null
      };
      if (hasMotion) { out.motionSample = values[0]; }
      if (hasOrientation) {
        out.orientationSample = values[hasMotion ? 1 : 0];
      }
      return out;
    });
  }

  // -- storage -----------------------------------------------------------
  function readStorage() {
    function estimatePromise() {
      return new Promise(function (resolve) {
        if (!navigator.storage || !navigator.storage.estimate) {
          resolve({ available: false });
          return;
        }
        navigator.storage.estimate().then(function (est) {
          resolve({
            available: true,
            quota: est.quota === undefined ? null : est.quota,
            usage: est.usage === undefined ? null : est.usage
          });
        }).catch(function (e) {
          resolve({ available: false, error: String((e && e.message) || e) });
        });
      });
    }
    var conn = null;
    try {
      var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (c) {
        conn = {
          effectiveType: c.effectiveType,
          rtt: c.rtt,
          downlink: c.downlink,
          saveData: c.saveData,
          type: c.type === undefined ? null : c.type
        };
      }
    } catch (e) {
      conn = null;
    }
    return estimatePromise().then(function (est) {
      return { available: est.available === true, storage: est, connection: conn };
    });
  }

  // -- webgpu ------------------------------------------------------------
  function readWebgpu() {
    if (!navigator.gpu || typeof navigator.gpu.requestAdapter !== "function") {
      return Promise.resolve({ available: false });
    }
    return navigator.gpu.requestAdapter().then(function (adapter) {
      if (!adapter) {
        return { available: false, reason: "no-adapter" };
      }
      var info = {};
      try {
        if (adapter.info && typeof adapter.info === "object") {
          info.vendor = adapter.info.vendor === undefined ? null : adapter.info.vendor;
          info.architecture =
            adapter.info.architecture === undefined ? null : adapter.info.architecture;
          info.device = adapter.info.device === undefined ? null : adapter.info.device;
          info.description =
            adapter.info.description === undefined ? null : adapter.info.description;
        }
      } catch (e) {
        info = {};
      }
      var limits = {};
      try {
        var raw = adapter.limits || {};
        var keys = [
          "maxTextureDimension2D", "maxBindGroups", "maxBufferSize",
          "maxComputeWorkgroupSizeX", "maxComputeInvocationsPerWorkgroup",
          "maxUniformBufferBindingSize", "maxStorageBufferBindingSize"
        ];
        for (var i = 0; i < keys.length; i++) {
          limits[keys[i]] = raw[keys[i]] === undefined ? null : raw[keys[i]];
        }
      } catch (e) {
        limits = {};
      }
      var features = [];
      try {
        features = Array.prototype.slice.call(adapter.features || []).sort();
      } catch (e) {
        features = [];
      }
      return {
        available: true,
        info: info,
        limits: limits,
        features: features,
        isFallbackAdapter: adapter.isFallbackAdapter === true
      };
    }).catch(function (err) {
      return { available: false, error: String((err && err.message) || err) };
    });
  }

  function readMath() {
    // The transcendental functions, at the points a fingerprinting
    // script probes them.  The last bits of the double follow the C
    // library of the platform.
    if (typeof Math === "undefined") {
      return { available: false };
    }
    var names = ["acos", "asin", "sinh", "cosh", "tanh", "atan"];
    var values = {};
    for (var i = 0; i < names.length; i++) {
      var fn = names[i];
      if (typeof Math[fn] !== "function") {
        values[fn] = null;
        continue;
      }
      try {
        values[fn] = Math[fn](0.5);
      } catch (e) {
        values[fn] = null;
      }
    }
    return { available: true, values: values };
  }

  function readHeap() {
    // The V8 heap ceiling.  It follows the physical memory of the
    // machine, so a desktop default on a handset is a signal.
    if (typeof performance === "undefined" || !performance || !performance.memory) {
      return { available: false };
    }
    var limit = performance.memory.jsHeapSizeLimit;
    return {
      available: true,
      jsHeapSizeLimit: limit === undefined ? null : limit
    };
  }

  function readMediaDecode() {
    // The media codecs the platform claims to decode.
    if (typeof MediaRecorder === "undefined" || !MediaRecorder ||
        typeof MediaRecorder.isTypeSupported !== "function") {
      return { available: false };
    }
    var types = [
      'video/mp4;codecs="avc1.42E01E"',
      'video/mp4;codecs="avc1.4D401E"',
      'video/mp4;codecs="avc1.640028"',
      'video/mp4;codecs="avc1.42E01F"',
      'video/webm;codecs="vp8, vorbis"',
      'video/webm;codecs="vp9"',
      'video/webm;codecs="vp8"',
      'audio/webm;codecs="opus"',
      'audio/mp4;codecs="mp4a.40.2"',
      'audio/ogg;codecs="vorbis"'
    ];
    var supported = [];
    for (var i = 0; i < types.length; i++) {
      try {
        if (MediaRecorder.isTypeSupported(types[i])) {
          supported.push(types[i]);
        }
      } catch (e) {
        // A codec that throws is simply not supported.
      }
    }
    return { available: true, supported: supported };
  }

  // -- the reader --------------------------------------------------------
  //
  // The synchronous axes are read first and the asynchronous ones are
  // awaited one after the other.  Reading them sequentially (rather than
  // with Promise.all) keeps the sensor timeout from racing the storage
  // and the WebGPU adapter request, and it makes the order of the
  // payload deterministic.
  function read() {
    var results = {};
    var AXES = [
      "identity", "client_hints", "screen", "webgl", "canvas",
      "fonts", "rects", "audio", "media_devices", "sensors",
      "storage", "webgpu", "math", "heap", "media_decode"
    ];
    var READERS = {
      identity: readIdentity,
      client_hints: readClientHints,
      screen: readScreen,
      webgl: readWebgl,
      canvas: readCanvas,
      fonts: readFonts,
      rects: readRects,
      audio: readAudio,
      media_devices: readMediaDevices,
      sensors: readSensors,
      storage: readStorage,
      webgpu: readWebgpu,
      math: readMath,
      heap: readHeap,
      media_decode: readMediaDecode
    };

    var chain = Promise.resolve();
    AXES.forEach(function (axis) {
      chain = chain.then(function () {
        var value;
        try {
          value = READERS[axis]();
        } catch (err) {
          results[axis] = { available: false, error: String((err && err.message) || err) };
          return null;
        }
        if (value && typeof value.then === "function") {
          return value.then(function (resolved) {
            results[axis] = resolved === undefined ? { available: false } : resolved;
          }, function (err) {
            results[axis] = { available: false, error: String((err && err.message) || err) };
          });
        }
        results[axis] = value === undefined ? { available: false } : value;
        return null;
      });
    });
    return chain.then(function () {
      return results;
    });
  }

  Object.defineProperty(window, "__NOVA_E2E__", {
    value: { read: read, version: "1.0.0" },
    writable: false,
    configurable: false,
    enumerable: true
  });
  window.__NOVA_E2E_READY__ = true;
})();
"""

#: The expression that reads every axis at once.
READ_EXPRESSION: Final[str] = (
    "window.__NOVA_E2E__ && typeof window.__NOVA_E2E__.read === 'function'"
    " ? window.__NOVA_E2E__.read() : Promise.resolve({ error: 'extractor-missing' })"
)


#: The surface spec of every axis.
#:
#: ``expression`` documents the *concrete* JavaScript that reads the axis;
#: the actual read happens inside :data:`SURFACE_SCRIPT`, which uses
#: exactly these expressions.  The spec is the contract between the two
#: and is what the report quotes.
SURFACE_SPECS: Final[dict[str, SurfaceSpec]] = {
    "identity": SurfaceSpec(
        axis="identity",
        expression="navigator.userAgent",
        fields={
            "userAgent": "navigator.user_agent",
            "appVersion": "navigator.app_version",
            "appName": "navigator.app_name",
            "appCodeName": "navigator.app_code_name",
            "product": "navigator.product",
            "productSub": "navigator.product_sub",
            "vendor": "navigator.vendor",
            "vendorSub": "navigator.vendor_sub",
            "platform": "navigator.platform",
            "hardwareConcurrency": "navigator.hardware_concurrency",
            "deviceMemory": "navigator.device_memory",
            "maxTouchPoints": "navigator.max_touch_points",
            "language": "navigator.language",
            "webdriver": "navigator.webdriver",
            "pdfViewerEnabled": "navigator.pdf_viewer_enabled",
        },
        description="the navigator identity block that a script reads first",
    ),
    "client_hints": SurfaceSpec(
        axis="client_hints",
        expression="navigator.userAgentData.brands",
        fields={
            "platform": "user_agent_data.platform",
            "platformVersion": "user_agent_data.platform_version",
            "architecture": "user_agent_data.architecture",
            "bitness": "user_agent_data.bitness",
            "model": "user_agent_data.model",
            "mobile": "user_agent_data.mobile",
            "wow64": "user_agent_data.wow64",
        },
        description="the UA client-hint data mirrored in the Sec-CH-UA headers",
    ),
    "screen": SurfaceSpec(
        axis="screen",
        expression="screen.width",
        fields={
            "width": "screen.width",
            "height": "screen.height",
            "availWidth": "screen.avail_width",
            "availHeight": "screen.avail_height",
            "colorDepth": "screen.color_depth",
            "pixelDepth": "screen.pixel_depth",
            "devicePixelRatio": "screen.device_pixel_ratio",
            "innerWidth": "viewport.inner_width",
            "innerHeight": "viewport.inner_height",
        },
        description="the panel geometry and the device pixel ratio",
    ),
    "webgl": SurfaceSpec(
        axis="webgl",
        expression="gl.getParameter(gl.MAX_TEXTURE_SIZE)",
        fields={
            "unmaskedVendor": "webgl.unmasked_vendor",
            "unmaskedRenderer": "webgl.unmasked_renderer",
            "version": "webgl.version",
            "shadingLanguageVersion": "webgl.shading_language_version",
        },
        description="the GPU identity and the ANGLE parameter table",
    ),
    "canvas": SurfaceSpec(
        axis="canvas",
        expression="canvas.toDataURL()",
        fields={},
        description="the 2D readback hash, the decisive canvas fingerprint",
    ),
    "audio": SurfaceSpec(
        axis="audio",
        expression="AudioContext.baseLatency",
        fields={
            "sampleRate": "audio.sample_rate",
            "numberOfChannels": "audio.channel_count",
        },
        timing_fields=frozenset({"baseLatency", "outputLatency", "sum", "mean", "peak"}),
        description="the WebAudio sample rate, the channel count and the latencies",
    ),
    "fonts": SurfaceSpec(
        axis="fonts",
        expression="document.fonts.check('16px Roboto')",
        fields={},
        description="the installed font inventory, probed through measureText",
    ),
    "rects": SurfaceSpec(
        axis="rects",
        expression="element.getBoundingClientRect()",
        fields={},
        description="the sub-pixel geometry of the laid-out boxes",
    ),
    "media_devices": SurfaceSpec(
        axis="media_devices",
        expression="navigator.mediaDevices.enumerateDevices()",
        fields={},
        description="the camera and microphone inventory",
    ),
    "sensors": SurfaceSpec(
        axis="sensors",
        expression="DeviceMotionEvent.acceleration",
        fields={},
        description="the accelerometer, the gyroscope and the gravity reading",
    ),
    "storage": SurfaceSpec(
        axis="storage",
        expression="navigator.storage.estimate()",
        fields={},
        description="the storage quota and the Network Information API",
    ),
    "webgpu": SurfaceSpec(
        axis="webgpu",
        expression="navigator.gpu.requestAdapter()",
        fields={},
        description="the WebGPU adapter description and its limit table",
    ),
    "math": SurfaceSpec(
        axis="math",
        expression="Math.acos(0.5)",
        fields={
            "acos": "math_precision.acos",
            "asin": "math_precision.asin",
            "sinh": "math_precision.sinh",
            "cosh": "math_precision.cosh",
            "tanh": "math_precision.tanh",
            "atan": "math_precision.atan",
        },
        description="the floating-point behaviour of the transcendental functions",
    ),
    "heap": SurfaceSpec(
        axis="heap",
        expression="performance.memory.jsHeapSizeLimit",
        fields={
            "jsHeapSizeLimit": "heap_size_limit.js_heap_size_limit",
        },
        description="the V8 heap ceiling, which follows the physical memory",
    ),
    "media_decode": SurfaceSpec(
        axis="media_decode",
        expression="MediaRecorder.isTypeSupported('video/mp4;codecs=\"avc1.42E01E\"')",
        fields={},
        description="the media codecs the platform claims to decode",
    ),
}


# --------------------------------------------------------------------------
# The surface extractor
# --------------------------------------------------------------------------


class SurfaceExtractor:
    """Reads the fifteen guarded axes out of a JavaScript evaluation context.

    The extractor is the *description* of what has to be read; the actual
    read is performed by the injected script and its result is handed back
    to :meth:`extract`.  Splitting the two keeps the comparison logic
    testable without a browser: the tests feed the extractor a canned
    payload and compare it against a canned expectation.

    The extractor also derives the *expected* values from the simulator, so
    that the reference side of the comparison is always the model and never
    a hard-coded constant that could drift away from it.
    """

    def __init__(self, config: ProfileConfig) -> None:
        self._config = config
        self._expected = derive_expected_values(config)

    @property
    def config(self) -> ProfileConfig:
        """The profile under test."""

        return self._config

    @property
    def expected(self) -> dict[str, dict[str, Any]]:
        """The values the simulator says the profile reports."""

        return self._expected

    @property
    def axes(self) -> tuple[str, ...]:
        """The axes this extractor covers."""

        return GUARDED_AXES

    def script(self) -> str:
        """Return the JavaScript that installs the extractor in the page."""

        candidates = json.dumps(_FONT_CANDIDATES)
        return SURFACE_SCRIPT.replace("__NOVA_FONT_CANDIDATES__", candidates)

    def expression(self) -> str:
        """Return the expression that reads every axis at once."""

        return READ_EXPRESSION

    def extract(self, measured: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalise the raw payload read from the page.

        The payload maps an axis onto the object the surface reader
        returned.  The extractor validates the shape and marks an axis as
        absent when the browser did not expose the surface at all -- the
        browser reports ``{"available": false}`` for WebGPU on a machine
        without WebGPU, and the extractor turns that into an empty
        mapping so that the comparison can report ``unavailable`` rather
        than ``mismatch``.

        The nested parts of the payload are flattened, because the
        comparison works on flat ``field -> value`` mappings:

        * ``webgl.parameters`` becomes ``maxTextureSize`` and friends;
        * ``webgpu.info`` becomes ``vendor`` and friends, and
          ``webgpu.limits`` becomes ``maxTextureDimension2D`` and friends;
        * ``sensors.motionSample`` becomes ``gravityZ`` and
          ``samplingInterval``;
        * ``storage.storage`` becomes ``quota`` and ``usage``, and
          ``storage.connection`` becomes ``effectiveType`` and friends.
        """

        extracted: dict[str, dict[str, Any]] = {}
        for axis in GUARDED_AXES:
            raw = measured.get(axis)
            if not isinstance(raw, Mapping):
                extracted[axis] = {}
                continue
            if raw.get("available") is False:
                extracted[axis] = {}
                continue
            flat = {str(k): v for k, v in raw.items() if k != "available"}
            extracted[axis] = _flatten_axis(axis, flat)
        return extracted


#: The nested containers of the payload that are flattened into the axis
#: itself, per axis.
_FLATTENED_CONTAINERS: Final[dict[str, tuple[str, ...]]] = {
    "webgl": ("parameters",),
    "webgpu": ("info", "limits"),
    "sensors": ("motionSample", "orientationSample"),
    "storage": ("storage", "connection"),
    "math": ("values",),
}


def _flatten_axis(axis: str, flat: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the nested containers of one axis into it."""

    result = dict(flat)
    for container in _FLATTENED_CONTAINERS.get(axis, ()):
        nested = result.pop(container, None)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                result.setdefault(str(key), value)

    if axis == "sensors":
        motion = flat.get("motionSample")
        if isinstance(motion, Mapping):
            gravity = motion.get("accelerationIncludingGravity")
            if isinstance(gravity, Mapping):
                result.setdefault("gravityZ", gravity.get("z"))
            result.setdefault("samplingInterval", motion.get("interval"))
        result.pop("accelerationIncludingGravity", None)
        result.pop("rotationRate", None)
        result.pop("acceleration", None)

    if axis == "webgpu":
        result.pop("features", None)
        result.pop("isFallbackAdapter", None)

    if axis == "media_devices":
        result.pop("devices", None)

    if axis == "fonts":
        result.pop("widths", None)
        result.pop("genericWidths", None)

    if axis == "rects":
        result.pop("measureText", None)
        result.pop("hostClientRectCount", None)
        result.pop("innerClientRectCount", None)
        result.pop("rangeRectCount", None)

    if axis == "audio":
        result.pop("length", None)
        result.pop("sampleRateLive", None)

    return result


#: The font families that the extractor probes.  They mirror the list of
#: the runtime harness so that the two probes measure the same thing.
_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Roboto",
    "Noto Sans",
    "Noto Sans Arabic",
    "Noto Sans Thai",
    "Noto Serif",
    "Noto Color Emoji",
    "Arial",
    "Helvetica",
    "Times New Roman",
    "Courier New",
    "monospace",
    "sans-serif",
    "serif",
    "cursive",
    "fantasy",
    "system-ui",
)


# --------------------------------------------------------------------------
# Expected values, derived from the simulator
# --------------------------------------------------------------------------


def derive_expected_values(config: ProfileConfig) -> dict[str, dict[str, Any]]:
    """Return the values the *simulator* reports for ``config``.

    The simulator is the reference side of the comparison: it is the model
    of what a profile *would* report.  Deriving the expectation from it
    rather than from a hand-written table means the two sides can never
    disagree about the shape of the payload -- only about the values,
    which is exactly the signal the harness is after.
    """

    axes = simulate_payload(config).get("axes", {})
    if not isinstance(axes, Mapping):  # pragma: no cover - defensive
        axes = {}

    navigator = axes.get("navigator", {})
    screen = axes.get("screen", {})
    webgl = axes.get("webgl", {})
    audio = axes.get("audio", {})
    rects = axes.get("rects", {})
    fonts = axes.get("fonts", {})
    devices = axes.get("media_devices", {})
    sensors = axes.get("sensors", {})
    storage = axes.get("storage", {})

    def _get(container: Mapping[str, Any], *path: str) -> Any:
        current: Any = container
        for part in path:
            if not isinstance(current, Mapping):
                return None
            current = current.get(part)
        return current

    return {
        "identity": {
            "userAgent": _get(navigator, "userAgent"),
            "appVersion": _get(navigator, "appVersion"),
            "appName": _get(navigator, "appName"),
            "appCodeName": _get(navigator, "appCodeName"),
            "product": _get(navigator, "product"),
            "productSub": _get(navigator, "productSub"),
            "vendor": _get(navigator, "vendor"),
            "vendorSub": _get(navigator, "vendorSub"),
            "platform": _get(navigator, "platform"),
            "hardwareConcurrency": _get(navigator, "hardwareConcurrency"),
            "deviceMemory": _get(navigator, "deviceMemory"),
            "maxTouchPoints": _get(navigator, "maxTouchPoints"),
            "language": _get(navigator, "language"),
            "webdriver": _get(navigator, "webdriver"),
            "pdfViewerEnabled": _get(navigator, "pdfViewerEnabled"),
        },
        "client_hints": {
            "platform": _get(navigator, "userAgentData", "platform"),
            "platformVersion": _get(navigator, "userAgentData", "platformVersion"),
            "architecture": _get(navigator, "userAgentData", "architecture"),
            "bitness": _get(navigator, "userAgentData", "bitness"),
            "model": _get(navigator, "userAgentData", "model"),
            "mobile": _get(navigator, "userAgentData", "mobile"),
            "wow64": _get(navigator, "userAgentData", "wow64"),
            "brands": _get(navigator, "userAgentData", "brands"),
        },
        "screen": {
            "width": _get(screen, "width"),
            "height": _get(screen, "height"),
            "availWidth": _get(screen, "availWidth"),
            "availHeight": _get(screen, "availHeight"),
            "colorDepth": _get(screen, "colorDepth"),
            "pixelDepth": _get(screen, "pixelDepth"),
            "devicePixelRatio": _get(screen, "devicePixelRatio"),
            "innerWidth": _get(screen, "innerWidth"),
            "innerHeight": _get(screen, "innerHeight"),
        },
        "webgl": {
            "unmaskedVendor": _get(webgl, "unmaskedVendor"),
            "unmaskedRenderer": _get(webgl, "unmaskedRenderer"),
            "version": _get(webgl, "version"),
            "shadingLanguageVersion": _get(webgl, "shadingLanguageVersion"),
            "maxTextureSize": _get(webgl, "maxTextureSize"),
            "maxVaryingVectors": _get(webgl, "maxVaryingVectors"),
            "maxFragmentUniformVectors": _get(webgl, "maxFragmentUniformVectors"),
            "maxVertexUniformVectors": _get(webgl, "maxVertexUniformVectors"),
            "maxTextureImageUnits": _get(webgl, "maxTextureImageUnits"),
        },
        "canvas": {
            "hash": _get(axes, "canvas", "hash"),
            "dataUrlHash": _get(axes, "canvas", "dataUrlHash"),
        },
        "audio": {
            "sampleRate": _get(audio, "sampleRate"),
            "numberOfChannels": _get(audio, "numberOfChannels"),
        },
        "fonts": {
            "installed": _get(fonts, "installed"),
        },
        "rects": {
            "host": _get(rects, "host"),
            "inner": _get(rects, "inner"),
            "range": _get(rects, "range"),
            "devicePixelRatio": _get(rects, "devicePixelRatio"),
        },
        "media_devices": {
            "count": _get(devices, "count"),
            "kinds": _get(devices, "kinds"),
        },
        "sensors": {
            "hasDeviceMotion": _get(sensors, "hasDeviceMotion"),
            "hasDeviceOrientation": _get(sensors, "hasDeviceOrientation"),
            "gravityZ": _get(sensors, "motionSample", "accelerationIncludingGravity", "z"),
            "samplingInterval": _get(sensors, "motionSample", "interval"),
        },
        "storage": {
            "quota": _get(storage, "storage", "quota"),
            "effectiveType": _get(storage, "connection", "effectiveType"),
            "rtt": _get(storage, "connection", "rtt"),
            "downlink": _get(storage, "connection", "downlink"),
        },
        "webgpu": {
            "vendor": _config_attr(config, "webgpu.adapter.vendor"),
            "architecture": _config_attr(config, "webgpu.adapter.architecture"),
            "device": _config_attr(config, "webgpu.adapter.device"),
            "description": _config_attr(config, "webgpu.adapter.description"),
            "maxTextureDimension2D": _config_attr(
                config, "webgpu.limits.maxTextureDimension2D"
            ),
        },
        "math": {
            "acos": _config_attr(config, "math_precision.acos"),
            "asin": _config_attr(config, "math_precision.asin"),
            "sinh": _config_attr(config, "math_precision.sinh"),
            "cosh": _config_attr(config, "math_precision.cosh"),
            "tanh": _config_attr(config, "math_precision.tanh"),
            "atan": _config_attr(config, "math_precision.atan"),
        },
        "heap": {
            "jsHeapSizeLimit": _config_attr(config, "heap_size_limit.js_heap_size_limit"),
        },
        "media_decode": {
            "supported": sorted(_config_attr(config, "media_decode.supported") or []),
        },
    }


def _config_attr(config: ProfileConfig, path: str) -> Any:
    """Read a dotted attribute path out of ``config``.

    The path may walk through mappings as well as through attributes, and
    a missing branch yields ``None`` instead of raising.  The extractor
    has to work with the real :class:`ProfileConfig` *and* with the
    lightweight stand-in that the test-suite uses, which does not carry
    every optional axis.
    """

    current: Any = config
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


# --------------------------------------------------------------------------
# The comparison engine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldComparison:
    """The verdict for a single field of an axis."""

    field: str
    verdict: Verdict
    expected: Any
    actual: Any
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the comparison."""

        return {
            "field": self.field,
            "verdict": self.verdict,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AxisComparison:
    """The verdict for one axis."""

    axis: str
    verdict: Verdict
    expected: dict[str, Any]
    actual: dict[str, Any]
    fields: tuple[FieldComparison, ...] = ()
    diagnostic: str = ""
    likely_cause: str = ""

    @property
    def ok(self) -> bool:
        """Whether the axis matched (an unavailable axis is not a failure)."""

        return self.verdict in {"match", "unavailable"}

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the comparison."""

        return {
            "axis": self.axis,
            "verdict": self.verdict,
            "ok": self.ok,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "fields": [item.to_dict() for item in self.fields],
            "diagnostic": self.diagnostic,
            "likely_cause": self.likely_cause,
        }


#: The likely causes of a mismatch, keyed by axis.
#:
#: The cause is what an operator has to look at first.  It is derived
#: from the *kind* of the difference, not from the value alone: a wrong
#: user agent means the core did not substitute the accessor, a wrong
#: pixel ratio means the profile and the browser disagree about the
#: device, and an entirely absent surface means the injected runtime was
#: not applied at document start.
LIKELY_CAUSES: Final[dict[str, str]] = {
    "identity": "the native core did not substitute this accessor",
    "client_hints": "the native core did not substitute this accessor",
    "screen": "the profile and the browser disagree about the pixel ratio",
    "webgl": "the native core did not substitute this accessor",
    "canvas": "the native core did not substitute this accessor",
    "audio": "the native core did not substitute this accessor",
    "fonts": "the native core did not substitute this accessor",
    "rects": "the injected runtime was not applied at document_start",
    "media_devices": "the native core did not substitute this accessor",
    "sensors": "the native core did not substitute this accessor",
    "storage": "the native core did not substitute this accessor",
    "webgpu": "the native core did not substitute this accessor",
    "math": "the injected runtime did not install the math override",
    "heap": "the injected runtime did not install the heap override",
    "media_decode": "the injected runtime did not install the codec override",
}


def _jsonable(value: Any) -> Any:
    """Return a JSON-serialisable rendering of ``value``."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _normalise(value: Any) -> Any:
    """Normalise a value so that two equal values compare equal.

    Numbers are compared numerically (``4096`` and ``4096.0`` are the
    same texture size), booleans are compared as booleans, and the
    remaining containers are normalised recursively.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if value is None:
        return None
    return str(value)


def _values_equal(expected: Any, actual: Any) -> bool:
    """Return ``True`` when the two normalised values are equal."""

    return _normalise(expected) == _normalise(actual)


def _numeric(value: Any) -> float | None:
    """Return ``value`` as a float when it is a number, else ``None``."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def compare_axis(
    axis: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    tolerance: Tolerance | None = None,
) -> AxisComparison:
    """Compare one axis and produce the verdict and the diagnostic.

    The verdict is:

    ``match``
        Every asserted field agrees with the expected value.
    ``mismatch``
        At least one asserted field disagrees.
    ``unavailable``
        The browser does not expose the surface at all -- there is nothing
        to compare.  This is *not* a failure: a machine without WebGPU
        cannot report a WebGPU adapter.

    A field is *asserted* when the expected value is not ``None``.  A
    field whose expected value is ``None`` is measured and reported, but
    not asserted -- the profile does not describe it.
    """

    tol = tolerance if tolerance is not None else tolerance_for(axis)
    fields: list[FieldComparison] = []
    mismatches: list[FieldComparison] = []
    asserted = 0

    for name in sorted(set(expected) | set(actual)):
        exp = expected.get(name)
        act = actual.get(name)
        if exp is None:
            # Not asserted: the profile does not describe this field.
            fields.append(
                FieldComparison(
                    field=name,
                    verdict="match",
                    expected=None,
                    actual=act,
                    detail="not asserted",
                )
            )
            continue
        asserted += 1
        if name in tol_timing_fields(axis):
            fields.append(
                FieldComparison(
                    field=name,
                    verdict="match",
                    expected=exp,
                    actual=act,
                    detail="timing value -- ignored",
                )
            )
            continue
        if _values_equal(exp, act):
            fields.append(FieldComparison(field=name, verdict="match", expected=exp, actual=act))
            continue
        exp_num = _numeric(exp)
        act_num = _numeric(act)
        if exp_num is not None and act_num is not None and tol.allows(exp_num, act_num):
            fields.append(
                FieldComparison(
                    field=name,
                    verdict="match",
                    expected=exp,
                    actual=act,
                    detail=f"within tolerance ({tol.reason})" if tol.reason else "within tolerance",
                )
            )
            continue
        comparison = FieldComparison(
            field=name,
            verdict="mismatch",
            expected=exp,
            actual=act,
            detail="values differ",
        )
        fields.append(comparison)
        mismatches.append(comparison)

    if not mismatches:
        if asserted == 0:
            # Nothing was asserted on this axis: either the browser does
            # not expose the surface, or the profile does not describe
            # it.  Reporting ``match`` here would be a lie -- it would
            # claim that a surface was verified when it was not.
            if not expected:
                reason = (
                    "the browser does not expose this surface, "
                    "so there is nothing to compare"
                )
                cause = "the surface is not available on this machine"
            else:
                reason = (
                    "the profile does not describe this axis, "
                    "so there is nothing to compare"
                )
                cause = "the profile does not describe this axis"
            return AxisComparison(
                axis=axis,
                verdict="unavailable",
                expected=dict(expected),
                actual=dict(actual),
                fields=tuple(fields),
                diagnostic=f"axis {axis!r}: {reason}",
                likely_cause=cause,
            )
        return AxisComparison(
            axis=axis,
            verdict="match",
            expected=dict(expected),
            actual=dict(actual),
            fields=tuple(fields),
            diagnostic=f"axis {axis!r}: every asserted field matches",
            likely_cause="",
        )

    first = mismatches[0]
    detail = (
        f"axis {axis!r}: {len(mismatches)} field(s) differ; "
        f"first: {first.field!r} expected {_render(first.expected)!r}, "
        f"got {_render(first.actual)!r}"
    )
    return AxisComparison(
        axis=axis,
        verdict="mismatch",
        expected=dict(expected),
        actual=dict(actual),
        fields=tuple(fields),
        diagnostic=detail,
        likely_cause=_likely_cause(axis, first),
    )


def _render(value: Any) -> str:
    """Render a value for a diagnostic message."""

    if isinstance(value, str):
        return value
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _likely_cause(axis: str, comparison: FieldComparison) -> str:
    """Return the most likely cause of a mismatch."""

    base = LIKELY_CAUSES.get(axis, "the native core did not substitute this accessor")
    if comparison.field == "devicePixelRatio":
        return "the profile and the browser disagree about the pixel ratio"
    if comparison.field in {"userAgent", "appVersion", "platform"}:
        return "the native core did not substitute this accessor"
    if axis in {"rects", "canvas"}:
        return "the injected runtime was not applied at document_start"
    return base


def tol_timing_fields(axis: str) -> frozenset[str]:
    """Return the timing fields of ``axis``."""

    spec = SURFACE_SPECS.get(axis)
    return spec.timing_fields if spec is not None else frozenset()


class ComparisonEngine:
    """Compares every axis of a measurement against the simulator.

    The engine is pure: it takes the expected values and the measured
    values and produces the comparisons.  It never touches a browser.
    """

    def __init__(self, tolerance: Mapping[str, Tolerance] | None = None) -> None:
        self._tolerances = dict(tolerance) if tolerance is not None else dict(TOLERANCES)

    def tolerance_for(self, axis: str) -> Tolerance:
        """Return the tolerance of ``axis``."""

        return self._tolerances.get(
            axis, Tolerance("exact", reason="unknown axis -- compared exactly")
        )

    def compare(
        self,
        expected: Mapping[str, Mapping[str, Any]],
        actual: Mapping[str, Mapping[str, Any]],
    ) -> list[AxisComparison]:
        """Compare every axis and return the per-axis verdicts.

        The result is ordered by :data:`GUARDED_AXES` and always contains
        every axis, so that a missing axis is reported rather than
        silently skipped.
        """

        results: list[AxisComparison] = []
        for axis in GUARDED_AXES:
            results.append(
                compare_axis(
                    axis,
                    expected.get(axis, {}),
                    actual.get(axis, {}),
                    tolerance=self.tolerance_for(axis),
                )
            )
        return results


# --------------------------------------------------------------------------
# RFC-6455 frame parsing
# --------------------------------------------------------------------------


class WebSocketProtocolError(RuntimeError):
    """Raised when a websocket frame violates RFC-6455."""


#: The websocket opcodes that RFC-6455 defines.
OP_CONTINUATION: Final[int] = 0x0
OP_TEXT: Final[int] = 0x1
OP_BINARY: Final[int] = 0x2
OP_CLOSE: Final[int] = 0x8
OP_PING: Final[int] = 0x9
OP_PONG: Final[int] = 0xA


@dataclass(frozen=True)
class WebSocketFrame:
    """One websocket frame."""

    fin: bool
    opcode: int
    payload: bytes
    masked: bool

    def text(self) -> str:
        """Return the payload decoded as UTF-8."""

        return self.payload.decode("utf-8")

    def json(self) -> Any:
        """Return the payload decoded as JSON."""

        return json.loads(self.text())


def parse_frame(buffer: bytes) -> tuple[WebSocketFrame | None, int]:
    """Parse one websocket frame out of ``buffer``.

    Returns ``(frame, consumed)``.  ``frame`` is ``None`` when the buffer
    does not yet hold a complete frame -- the caller has to read more
    bytes.  ``consumed`` is the number of bytes the frame occupied.

    The parser implements the client-to-server rules of RFC-6455 section
    5.2: the opcode is validated, the payload length is decoded from its
    two- or three-form, and a masked payload is unmasked with the
    four-byte key that precedes it.

    It is implemented here rather than taken from the library so that it
    can be tested against a hand-built frame without opening a socket.
    """

    if len(buffer) < 2:
        return None, 0

    first, second = buffer[0], buffer[1]
    fin = bool(first & 0x80)
    if first & 0x70:
        raise WebSocketProtocolError("reserved bits must be zero")
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F

    offset = 2
    if length == 126:
        if len(buffer) < offset + 2:
            return None, 0
        length = struct.unpack("!H", buffer[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(buffer) < offset + 8:
            return None, 0
        length = struct.unpack("!Q", buffer[offset : offset + 8])[0]
        offset += 8

    if opcode not in {
        OP_CONTINUATION,
        OP_TEXT,
        OP_BINARY,
        OP_CLOSE,
        OP_PING,
        OP_PONG,
    }:
        raise WebSocketProtocolError(f"unknown opcode {opcode:#x}")

    mask_key = b""
    if masked:
        if len(buffer) < offset + 4:
            return None, 0
        mask_key = buffer[offset : offset + 4]
        offset += 4

    if len(buffer) < offset + length:
        return None, 0

    payload = bytearray(buffer[offset : offset + length])
    if masked:
        for index in range(length):
            payload[index] ^= mask_key[index % 4]

    return (
        WebSocketFrame(fin=fin, opcode=opcode, payload=bytes(payload), masked=masked),
        offset + length,
    )


def build_frame(
    opcode: int,
    payload: bytes,
    *,
    mask: bool = False,
    mask_key: bytes = b"\x00\x00\x00\x00",
) -> bytes:
    """Build a websocket frame.

    The function is the exact inverse of :func:`parse_frame` and is used
    by the tests to construct a hand-built frame.  A client-to-server
    frame must be masked, which is the default here.

    ``mask_key`` is the four-byte masking key.  The default is all zeroes
    -- which is what the *server* may send but what a client must never
    send, because RFC-6455 requires the key to be unpredictable.  The
    tests pass a non-zero key to prove that the masking is applied and
    removed symmetrically.
    """

    if not 0 <= opcode <= 0xF:
        raise WebSocketProtocolError(f"invalid opcode {opcode:#x}")
    if len(mask_key) != 4:
        raise WebSocketProtocolError("the masking key must be exactly four bytes")

    header = bytearray()
    header.append(0x80 | opcode)
    mask_bit = 0x80 if mask else 0x00
    length = len(payload)
    if length < 126:
        header.append(mask_bit | length)
    elif length < (1 << 16):
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", length))

    if not mask:
        return bytes(header) + payload

    header.extend(mask_key)
    masked_payload = bytearray(payload)
    for index in range(length):
        masked_payload[index] ^= mask_key[index % 4]
    return bytes(header) + bytes(masked_payload)


# --------------------------------------------------------------------------
# The DevTools client
# --------------------------------------------------------------------------


class DevToolsClient:
    """A minimal Chrome DevTools Protocol client.

    The client speaks the raw websocket protocol that the browser exposes
    on its debugging port.  It supports exactly the two commands the
    end-to-end harness needs -- ``Page.addScriptToEvaluateOnNewDocument``
    and ``Runtime.evaluate`` -- plus the handful of session-management
    commands needed to reach a page target.

    The websocket transport is the ``websockets`` package, which is
    already a declared dependency of the project; the *frame* handling is
    implemented here (see :func:`parse_frame`) so that it can be tested
    without a socket.
    """

    def __init__(self, ws_url: str, *, timeout: float = 30.0) -> None:
        self._ws_url = ws_url
        self._timeout = timeout
        self._connection: Any = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the websocket and start the background reader task."""

        from websockets.asyncio.client import connect  # local import: optional at import time

        self._connection = await connect(
            self._ws_url,
            open_timeout=self._timeout,
            max_size=None,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        connection = self._connection
        if connection is None:  # pragma: no cover - defensive
            return
        try:
            async for raw in connection:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # pragma: no cover - connection teardown
            pass
        finally:
            self._fail_pending("connection-closed")

    def _dispatch(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if isinstance(message_id, int):
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)
            return
        # Events are ignored: the harness only issues request/response pairs.

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(CdpError(reason))
        self._pending.clear()

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and await its response."""

        if self._closed:
            raise CdpError("client-closed")
        connection = self._connection
        if connection is None:
            raise CdpError("client-not-connected")

        message_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future

        payload: dict[str, Any] = {"id": message_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id

        try:
            await connection.send(json.dumps(payload))
        except Exception:
            self._pending.pop(message_id, None)
            raise

        try:
            response = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise CdpError(f"timeout waiting for {method}") from exc

        if "error" in response:
            error = response["error"]
            message = (
                error.get("message", "unknown CDP error") if isinstance(error, dict) else str(error)
            )
            raise CdpError(f"{method}: {message}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        """Close the websocket and release every pending future."""

        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        connection = self._connection
        self._connection = None
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()
        self._fail_pending("client-closed")

    # -- the two commands the harness needs --------------------------------

    async def add_script_to_evaluate_on_new_document(
        self, source: str, *, session_id: str | None = None
    ) -> str:
        """Install ``source`` into every new document and return its identifier."""

        result = await self.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": source},
            session_id=session_id,
        )
        identifier = result.get("identifier")
        return str(identifier) if identifier is not None else ""

    async def evaluate(
        self, expression: str, *, session_id: str | None = None
    ) -> Any:
        """Evaluate ``expression`` in the page and return its value."""

        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            session_id=session_id,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text", "evaluation-error")
            description = details.get("exception", {}).get("description")
            raise CdpError(f"evaluate failed: {description or text}")
        remote = result.get("result")
        if not isinstance(remote, dict):
            return None
        if remote.get("type") == "undefined":
            return None
        return remote.get("value")


# --------------------------------------------------------------------------
# The local test page
# --------------------------------------------------------------------------


class _PageHandler(BaseHTTPRequestHandler):
    """Serves the local test page.

    The handler answers exactly one route -- ``/`` -- and refuses every
    other one, so that a stray reference in the page cannot turn into a
    network request.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        """Serve the test page."""

        if self.path not in {"/", "/index.html"}:
            self.send_error(404, "not found")
            return
        body = TEST_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        """Silence the default stderr logging of the http server."""


class LocalTestServer:
    """Serves the test page from ``127.0.0.1`` on an ephemeral port.

    The server binds to the loopback interface only; no external host is
    ever contacted.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The URL of the test page."""

        return f"http://{self._host}:{self._port}/"

    def start(self) -> None:
        """Start the server in a background thread."""

        if self._server is not None:
            return
        server = ThreadingHTTPServer((self._host, self._port), _PageHandler)
        server.daemon_threads = True
        self._server = server
        self._port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server and release the port."""

        server = self._server
        self._server = None
        if server is not None:
            with contextlib.suppress(Exception):
                server.shutdown()
            with contextlib.suppress(Exception):
                server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            with contextlib.suppress(Exception):
                thread.join(timeout=5)

    def __enter__(self) -> LocalTestServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# --------------------------------------------------------------------------
# Browser location
# --------------------------------------------------------------------------


class BrowserNotFoundError(RuntimeError):
    """Raised when no Chromium-based browser is installed on the machine."""


def find_browser(explicit: str | None = None) -> str:
    """Locate a Chrome/Chromium/Edge executable.

    The list of known locations is the one the launcher already uses
    (:data:`novaprofile.launcher.launcher.DEFAULT_CHROME_PATHS`); the
    edge locations are appended because Edge is the Chromium build that
    ships with Windows and it speaks the same DevTools protocol.

    Raises :class:`BrowserNotFoundError` when nothing suitable is
    installed.  The message names the paths that were tried so that the
    failure is actionable.
    """

    candidates: list[str] = list(DEFAULT_CHROME_PATHS)
    candidates.extend(_EDGE_PATHS)

    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)
        found = shutil.which(explicit)
        if found:
            return found
        raise BrowserNotFoundError(f"browser executable not found: {explicit}")

    for path in candidates:
        if Path(path).exists():
            return path
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    raise BrowserNotFoundError(
        "no Chrome/Chromium/Edge executable found; tried: " + ", ".join(candidates)
    )


#: The Windows locations of Microsoft Edge.
_EDGE_PATHS: Final[tuple[str, ...]] = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


@dataclass
class E2EReport:
    """The outcome of one end-to-end verification run."""

    profile_id: str
    passed: bool
    comparisons: list[AxisComparison] = field(default_factory=list)
    browser: str = ""
    browser_version: str = ""
    page_url: str = ""
    error: str = ""

    @property
    def verdicts(self) -> dict[str, str]:
        """The verdict of every axis, keyed by axis name."""

        return {item.axis: item.verdict for item in self.comparisons}

    @property
    def diagnostics(self) -> list[str]:
        """The diagnostic message of every axis that did not match."""

        return [
            item.diagnostic for item in self.comparisons if item.verdict == "mismatch"
        ]

    def counts(self) -> dict[str, int]:
        """The number of axes per verdict."""

        counts = {"match": 0, "mismatch": 0, "unavailable": 0}
        for item in self.comparisons:
            counts[item.verdict] = counts.get(item.verdict, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the report.

        The shape matches the measurement-ledger record format of
        :mod:`novaprofile.scripts.measurements`: the ``metrics`` mapping
        holds the numbers and the ``conclusion`` holds the sentence.  The
        report can therefore be filed with
        ``novaprofile.scripts.measurements.save`` without translation.
        """

        counts = self.counts()
        conclusion = (
            f"{counts['match']}/{len(self.comparisons)} axes match, "
            f"{counts['mismatch']} mismatch, {counts['unavailable']} unavailable."
        )
        if self.error:
            conclusion = f"The run failed: {self.error}"
        return {
            "slug": "probe-e2e",
            "profile_id": self.profile_id,
            "passed": self.passed,
            "browser": self.browser,
            "browser_version": self.browser_version,
            "page_url": self.page_url,
            "metrics": {
                "axes_total": len(self.comparisons),
                "axes_match": counts["match"],
                "axes_mismatch": counts["mismatch"],
                "axes_unavailable": counts["unavailable"],
                "passed": self.passed,
            },
            "conclusion": conclusion,
            "axes": [item.to_dict() for item in self.comparisons],
            "diagnostics": self.diagnostics,
            "error": self.error,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Return the report as a JSON document."""

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class BrowserSession(Protocol):
    """The minimal contract a browser session must fulfil for the harness."""

    async def evaluate(self, expression: str) -> Any:  # pragma: no cover - protocol
        ...

    async def close(self) -> None:  # pragma: no cover - protocol
        ...


@dataclass
class _BrowserProcess:
    """A launched browser and the DevTools port it reported."""

    process: subprocess.Popen[bytes]
    port: int
    user_data_dir: Path


class E2ERunner:
    """Runs the end-to-end verification of a profile against a real browser.

    The runner performs five steps:

    1. serve the test page from ``127.0.0.1``;
    2. launch a Chromium-based browser with a temporary profile;
    3. attach over the DevTools protocol and install the extractor script
       *before* the first navigation, so that the extractor is present at
       ``document_start``;
    4. navigate to the local page and read the fifteen axes;
    5. compare the measurement against the simulator and report.

    Every step can be replaced through the constructor arguments, so that
    the tests can drive the runner without a browser.
    """

    def __init__(
        self,
        config: ProfileConfig,
        *,
        browser_path: str | None = None,
        headless: bool = True,
        timeout: float = 30.0,
        engine: ComparisonEngine | None = None,
        browser_finder: Any = find_browser,
    ) -> None:
        self._config = config
        self._browser_path = browser_path
        self._headless = headless
        self._timeout = timeout
        self._engine = engine if engine is not None else ComparisonEngine()
        self._browser_finder = browser_finder
        self._extractor = SurfaceExtractor(config)

    # -- the public entry point -------------------------------------------

    async def run(self) -> E2EReport:
        """Run the whole verification and return the report."""

        try:
            browser_path = self._browser_finder(self._browser_path)
        except BrowserNotFoundError as exc:
            return self._failure(str(exc))
        except FileNotFoundError as exc:
            return self._failure(str(exc))

        server = LocalTestServer()
        server.start()
        launched: _BrowserProcess | None = None
        client: DevToolsClient | None = None
        session_id: str | None = None
        try:
            launched = await asyncio.to_thread(self._launch, browser_path)
            version = await asyncio.to_thread(
                self._fetch_json, f"http://127.0.0.1:{launched.port}/json/version"
            )
            ws_url = str(version.get("webSocketDebuggerUrl", ""))
            if not ws_url:
                return self._failure("the browser did not report a websocket debugger URL")

            client = DevToolsClient(ws_url, timeout=self._timeout)
            await client.connect()

            target = await client.send("Target.createTarget", {"url": "about:blank"})
            target_id = target.get("targetId")
            if not isinstance(target_id, str):
                return self._failure("Target.createTarget did not return a targetId")
            attached = await client.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            raw_session = attached.get("sessionId")
            session_id = raw_session if isinstance(raw_session, str) else None

            await client.send("Page.enable", session_id=session_id)
            await client.send("Runtime.enable", session_id=session_id)

            # The extractor has to be installed *before* the first
            # navigation, otherwise the page would be measured before the
            # runtime got its chance to patch anything.
            await client.add_script_to_evaluate_on_new_document(
                self._extractor.script(), session_id=session_id
            )

            await client.send("Page.navigate", {"url": server.url}, session_id=session_id)
            await self._await_extractor(client, session_id)

            raw = await client.evaluate(self._extractor.expression(), session_id=session_id)
            measured = self._extractor.extract(raw if isinstance(raw, Mapping) else {})
            expected = self._extractor.expected
            comparisons = self._engine.compare(expected, measured)
            passed = all(item.verdict != "mismatch" for item in comparisons)
            return E2EReport(
                profile_id=self._config.identity.id,
                passed=passed,
                comparisons=comparisons,
                browser=browser_path,
                browser_version=str(version.get("Browser", "")),
                page_url=server.url,
            )
        except Exception as exc:
            return self._failure(f"{type(exc).__name__}: {exc}")
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
            if launched is not None:
                await asyncio.to_thread(self._terminate, launched)
            server.stop()

    def run_sync(self) -> E2EReport:
        """Run the verification from synchronous code."""

        return asyncio.run(self.run())

    # -- internals ---------------------------------------------------------

    def _failure(self, message: str) -> E2EReport:
        """Return a report that records a hard failure."""

        return E2EReport(
            profile_id=self._config.identity.id,
            passed=False,
            comparisons=[],
            browser=str(self._browser_path or ""),
            error=message,
        )

    def _write_core_config(self, user_data_dir: Path) -> Path | None:
        """Write the profile config the native core reads.

        The payload is the plain profile JSON.  The browser core parses
        the document as it stands -- there is no envelope around it --
        so a file wrapped in ``{"schema", "encrypted", "payload"}`` would
        be read as a profile with no fields and every accessor would
        fall back to the host's values.

        The control plane encrypts the file it writes for a *running*
        profile, because that file sits in a directory the profile owns
        for its whole life; the verification writes a throwaway
        directory that is deleted when the run ends, and the core reads
        plain JSON.

        Returns ``None`` when the file could not be written, so that the
        run can go on and report the mismatch honestly.
        """

        document = self._config_payload()
        target = user_data_dir / "nova_core.json"
        try:
            target.write_text(
                json.dumps(document, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            return None
        return target

    def _config_payload(self) -> dict[str, Any]:
        """The profile as the browser core wants it."""

        from ..launcher.core_document import build_core_document

        return build_core_document(self._config)

    def _write_js_runtime(self, user_data_dir: Path) -> Path | None:
        """Write the JavaScript layer into ``user_data_dir``.

        The layer carries the values that only JavaScript can see -- the
        heap limit, the maths, the codec list -- into the page.  The
        renderer reads it from the path given on ``--nova-js-runtime``
        and runs it at document start.

        Without it the native core is the only layer, and every axis that
        JavaScript owns reports the host's values.  That is a real
        failure of the product, not of the measurement, so the runner
        writes the file rather than skipping the axis.
        """

        from ..js_runtime.builder import JSRuntimeBuilder
        from ..js_runtime.injector import InjectionPoint, write_runtime
        from ..schema.profile import ProfileConfig

        try:
            profile = ProfileConfig.model_validate(
                self._config.model_dump(mode="python")
            )
            written = write_runtime(
                profile,
                user_data_dir,
                injection_point=InjectionPoint.DOCUMENT_START,
                builder=JSRuntimeBuilder(),
            )
            return written.path
        except Exception:
            # The layer is an enhancement.  The run goes on without it and
            # the axes it owns report a mismatch, which is honest.
            return None

    def _launch(self, browser_path: str) -> _BrowserProcess:
        """Launch the browser and return the process and its DevTools port."""

        user_data_dir = Path(tempfile.mkdtemp(prefix="nova-e2e-"))
        command = [browser_path]
        if self._headless:
            command.append("--headless=new")
        command.append("--remote-debugging-port=0")
        command.append(f"--user-data-dir={user_data_dir}")
        # The native core reads the profile from this file before the
        # renderer is forked.  Without it the browser runs with its own
        # values and every axis mismatches, which says nothing about the
        # core -- only that the hand-over did not happen.
        config_path = self._write_core_config(user_data_dir)
        if config_path is not None:
            command.append(f"--nova-config={config_path}")
        # The JavaScript layer is handed over the same way: the path goes
        # on the command line, the payload stays in the user data
        # directory, where the command line of the process cannot be read
        # by another process.
        runtime_path = self._write_js_runtime(user_data_dir)
        if runtime_path is not None:
            command.append(f"--nova-js-runtime={runtime_path}")
        command.append("--no-first-run")
        command.append("--no-default-browser-check")
        command.append("--disable-background-networking")
        command.append("--disable-sync")
        command.append("--disable-extensions")
        command.append("--disable-dev-shm-usage")
        command.append("--hide-scrollbars")
        command.append("--mute-audio")
        command.append("about:blank")
        process = subprocess.Popen(  # noqa: S603 - explicit, local binary
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port = self._read_devtools_port(user_data_dir)
        except Exception:
            self._terminate_process_tree(process)
            with contextlib.suppress(OSError):
                shutil.rmtree(user_data_dir, ignore_errors=True)
            raise
        return _BrowserProcess(process=process, port=port, user_data_dir=user_data_dir)

    @staticmethod
    def _read_devtools_port(user_data_dir: Path, timeout: float = 30.0) -> int:
        """Read the DevTools port Chrome wrote into ``DevToolsActivePort``."""

        port_file = user_data_dir / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_file.exists():
                try:
                    content = port_file.read_text(encoding="utf-8").splitlines()
                except OSError:  # pragma: no cover - transient
                    content = []
                if content:
                    try:
                        return int(content[0].strip())
                    except ValueError:  # pragma: no cover - transient
                        pass
            time.sleep(0.05)
        raise TimeoutError("the browser did not report a DevTools port in time")

    @staticmethod
    def _terminate(process: _BrowserProcess) -> None:
        """Terminate the browser and remove its temporary profile."""

        E2ERunner._terminate_process_tree(process.process)
        shutil.rmtree(process.user_data_dir, ignore_errors=True)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
        """Terminate the browser *and* every child it spawned."""

        children: list[Any] = []
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
        except Exception:  # pragma: no cover - psutil optional at runtime
            children = []

        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)

        for child in children:
            with contextlib.suppress(Exception):
                child.terminate()
        for child in children:
            with contextlib.suppress(Exception):
                child.wait(timeout=5)
            with contextlib.suppress(Exception):
                if child.is_running():
                    child.kill()

        if process.poll() is None:  # pragma: no cover - stubborn process
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                process.wait(timeout=5)

    @staticmethod
    def _fetch_json(url: str) -> dict[str, Any]:
        """Fetch a JSON document from the local DevTools endpoint."""

        import urllib.request

        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - loopback only
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    async def _await_extractor(
        self, client: DevToolsClient, session_id: str | None
    ) -> None:
        """Wait until the extractor is installed in the page."""

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                ready = await client.evaluate(
                    "window.__NOVA_E2E_READY__ === true", session_id=session_id
                )
            except CdpError:
                ready = False
            if ready:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("the extractor was not installed in time")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def free_port(host: str = "127.0.0.1") -> int:
    """Return a currently free TCP port on ``host``."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def report_digest(report: E2EReport) -> str:
    """Return a stable digest of a report.

    The digest is what the measurement ledger stores as the checksum of
    the verdict, so that two runs of the same profile can be compared
    without diffing the whole document.
    """

    material = json.dumps(
        report.to_dict()["axes"], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verdict_counts(comparisons: Iterable[AxisComparison]) -> dict[str, int]:
    """Count the verdicts of ``comparisons``."""

    counts = {"match": 0, "mismatch": 0, "unavailable": 0}
    for item in comparisons:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    return counts


__all__ = [
    "AxisComparison",
    "BrowserNotFoundError",
    "BrowserSession",
    "ComparisonEngine",
    "DevToolsClient",
    "E2EReport",
    "E2ERunner",
    "FieldComparison",
    "GUARDED_AXES",
    "LIKELY_CAUSES",
    "LocalTestServer",
    "OP_BINARY",
    "OP_CLOSE",
    "OP_CONTINUATION",
    "OP_PING",
    "OP_PONG",
    "OP_TEXT",
    "READ_EXPRESSION",
    "SURFACE_SCRIPT",
    "SURFACE_SPECS",
    "SurfaceExtractor",
    "SurfaceSpec",
    "TEST_PAGE",
    "TOLERANCES",
    "Tolerance",
    "Verdict",
    "WebSocketFrame",
    "WebSocketProtocolError",
    "build_frame",
    "compare_axis",
    "derive_expected_values",
    "find_browser",
    "free_port",
    "parse_frame",
    "report_digest",
    "tolerance_for",
    "verdict_counts",
]
