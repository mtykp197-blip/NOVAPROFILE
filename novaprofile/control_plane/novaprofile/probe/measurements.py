"""In-page JavaScript measuring harness for the NOVA runtime probe.

Every fingerprint axis is measured by a self-contained JavaScript snippet that
is injected into the browser page and evaluated over CDP.  The snippets are
plain module-level string constants -- **no JavaScript engine is involved in
the Python process**, so the harness can be inspected, diffed and unit-tested
without ever launching a browser.

Design rules for every snippet:

* self contained -- it only relies on globals that exist in a top-level
  document (``navigator``, ``screen``, ``window``, ``document``);
* side effect free apart from allocating its own offscreen elements, which it
  removes again before returning;
* deterministic -- the same browser + the same injected noise must always
  produce the same output;
* defensive -- every axis degrades to an ``{"error": ...}`` payload rather
  than throwing, so a single unsupported API can never abort the whole probe.

The Python side never interprets the raw JavaScript; it only stores it and
inlines it into the harness page.
"""

from __future__ import annotations

import json
from typing import Final

# --------------------------------------------------------------------------
# Shared prelude -- hashing helpers used by several axes.
# --------------------------------------------------------------------------

_FNV_HELPER_JS: Final[str] = r"""
(function () {
  // FNV-1a over a byte sequence, 32-bit, returned as 8 hex characters.
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
  function fnv1aHexFromString(text) {
    var enc = new TextEncoder();
    return fnv1aHex(enc.encode(text));
  }
  function toHexString(text) {
    var enc = new TextEncoder();
    var b = enc.encode(text);
    var out = "";
    for (var i = 0; i < b.length; i++) {
      var v = b[i];
      out += (v < 16 ? "0" : "") + v.toString(16);
    }
    return out;
  }
  return { fnv1aHex: fnv1aHex, fnv1aHexFromString: fnv1aHexFromString, toHexString: toHexString };
})()
"""

# --------------------------------------------------------------------------
# Canvas 2D
# --------------------------------------------------------------------------

CANVAS_HASH_JS: Final[str] = r"""
(function () {
  var H = __NOVA_HASH__;
  try {
    var W = 280, HGT = 120;
    var canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = HGT;
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) {
      return { error: "2d-context-unavailable" };
    }
    // Deterministic scene: solid fill, linear gradient, radial gradient,
    // several arcs / rects / triangles, a stroke and some text.
    ctx.fillStyle = "#f2f4f7";
    ctx.fillRect(0, 0, W, HGT);

    var lin = ctx.createLinearGradient(0, 0, W, HGT);
    lin.addColorStop(0, "#1a73e8");
    lin.addColorStop(0.5, "#34a853");
    lin.addColorStop(1, "#ea4335");
    ctx.fillStyle = lin;
    ctx.fillRect(6, 6, W - 12, HGT - 12);

    var rad = ctx.createRadialGradient(W * 0.35, HGT * 0.4, 4, W * 0.35, HGT * 0.4, 90);
    rad.addColorStop(0, "rgba(255,255,255,0.85)");
    rad.addColorStop(1, "rgba(0,0,0,0.05)");
    ctx.fillStyle = rad;
    ctx.beginPath();
    ctx.arc(W * 0.35, HGT * 0.4, 60, 0, Math.PI * 2, true);
    ctx.fill();

    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = "rgba(255, 196, 0, 0.65)";
    ctx.beginPath();
    ctx.moveTo(150, 20);
    ctx.lineTo(260, 30);
    ctx.lineTo(205, 105);
    ctx.closePath();
    ctx.fill();

    ctx.globalCompositeOperation = "source-over";
    ctx.strokeStyle = "#202124";
    ctx.lineWidth = 2;
    ctx.strokeRect(12.5, 12.5, W - 25, HGT - 25);

    ctx.beginPath();
    ctx.arc(230, 78, 18, 0, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(20, 100);
    ctx.quadraticCurveTo(70, 55, 120, 100);
    ctx.stroke();

    ctx.font = "16px Arial";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#0b8043";
    ctx.fillText("NOVA-2D", 24, 48);

    ctx.font = "italic 13px serif";
    ctx.fillStyle = "#3c4043";
    ctx.fillText("fingerprint", 24, 68);

    ctx.font = "11px monospace";
    ctx.fillText("0123456789", 24, 84);

    var data = ctx.getImageData(0, 0, W, HGT).data;
    var bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    var hash = H.fnv1aHex(bytes);
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
    var fontSignature = H.fnv1aHexFromString(
      ctx.font + "|" + String(ctx.textBaseline) + "|" + String(ctx.lineWidth)
    );
    canvas.width = 0;
    canvas.height = 0;
    return {
      hash: hash,
      dataUrlHash: dataUrl ? H.fnv1aHexFromString(dataUrl) : "",
      dataUrlLength: dataUrl.length,
      width: W,
      height: HGT,
      byteLength: bytes.length,
      firstPixels: sample,
      fontSignature: fontSignature
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# WebGL
# --------------------------------------------------------------------------

WEBGL_READPIXELS_JS: Final[str] = r"""
(function () {
  var H = __NOVA_HASH__;
  function readPixelsHash(gl, w, h) {
    var pixels = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    return H.fnv1aHex(pixels);
  }
  try {
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
      gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, antialias: false });
      if (gl) { contextName = "webgl"; }
    }
    if (!gl) {
      gl = canvas.getContext("experimental-webgl");
      if (gl) { contextName = "experimental-webgl"; }
    }
    if (!gl) {
      return { error: "webgl-unavailable" };
    }

    var vsrc = [
      "attribute vec2 aPos;",
      "varying vec2 vUv;",
      "void main() {",
      "  vUv = aPos * 0.5 + 0.5;",
      "  gl_Position = vec4(aPos, 0.0, 1.0);",
      "}"
    ].join("\n");
    var fsrc = [
      "precision mediump float;",
      "varying vec2 vUv;",
      "void main() {",
      "  float r = vUv.x;",
      "  float g = vUv.y;",
      "  float b = 0.5 + 0.5 * sin((vUv.x + vUv.y) * 3.14159265);",
      "  float a = 1.0 - 0.25 * vUv.y;",
      "  gl_FragColor = vec4(r, g, b, a);",
      "}"
    ].join("\n");

    function compile(type, src) {
      var sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        var log = gl.getShaderInfoLog(sh);
        gl.deleteShader(sh);
        throw new Error("shader-compile: " + log);
      }
      return sh;
    }

    var vs = compile(gl.VERTEX_SHADER, vsrc);
    var fs = compile(gl.FRAGMENT_SHADER, fsrc);
    var prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      var plog = gl.getProgramInfoLog(prog);
      throw new Error("program-link: " + plog);
    }
    gl.useProgram(prog);

    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW
    );
    var loc = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    gl.viewport(0, 0, 64, 64);
    gl.clearColor(0.0, 0.0, 0.0, 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.finish();

    var pixelHash = readPixelsHash(gl, 64, 64);

    var debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
    var unmaskedVendor = debugInfo
      ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL)
      : gl.getParameter(gl.VENDOR);
    var unmaskedRenderer = debugInfo
      ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
      : gl.getParameter(gl.RENDERER);

    var version = gl.getParameter(gl.VERSION);
    var shading = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
    var maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    var maxVarying = gl.getParameter(gl.MAX_VARYING_VECTORS);
    var maxFragUnif = gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS);
    var maxVertUnif = gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS);
    var maxTexUnits = gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS);
    var aliasedLine = Array.prototype.slice.call(
      gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE) || []
    );
    var aliasedPoint = Array.prototype.slice.call(
      gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) || []
    );
    var extensions = gl.getSupportedExtensions() || [];
    var sortedExtensions = extensions.slice().sort();

    var result = {
      context: contextName,
      pixelHash: pixelHash,
      unmaskedVendor: String(unmaskedVendor),
      unmaskedRenderer: String(unmaskedRenderer),
      version: String(version),
      shadingLanguageVersion: String(shading),
      maxTextureSize: maxTex,
      maxVaryingVectors: maxVarying,
      maxFragmentUniformVectors: maxFragUnif,
      maxVertexUniformVectors: maxVertUnif,
      maxTextureImageUnits: maxTexUnits,
      aliasedLineWidthRange: aliasedLine,
      aliasedPointSizeRange: aliasedPoint,
      extensionCount: sortedExtensions.length,
      extensionHash: H.fnv1aHexFromString(sortedExtensions.join("|")),
      vendor: String(gl.getParameter(gl.VENDOR)),
      renderer: String(gl.getParameter(gl.RENDERER))
    };

    gl.deleteBuffer(buf);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    gl.deleteProgram(prog);
    canvas.width = 0;
    canvas.height = 0;
    return result;
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Worker / cross-context synchronisation
# --------------------------------------------------------------------------

WORKER_JS: Final[str] = r"""
self.__NOVA_WORKER_RESULT__ = null;
self.onmessage = function (event) {
  try {
    // A dedicated worker has no ``screen`` and no ``window`` -- only the
    // navigator and the Intl database are shared with the main frame.
    var out = {
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      language: navigator.language,
      languages: Array.prototype.slice.call(navigator.languages || []),
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory,
      maxTouchPoints: navigator.maxTouchPoints,
      vendor: navigator.vendor,
      webdriver: navigator.webdriver === true,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      locale: Intl.DateTimeFormat().resolvedOptions().locale,
      hash: null,
      hashError: null
    };
    try {
      var c = new OffscreenCanvas(32, 32);
      var g = c.getContext("2d");
      g.fillStyle = "#123456";
      g.fillRect(0, 0, 32, 32);
      g.fillStyle = "#abcdef";
      g.fillRect(4, 4, 24, 24);
      var d = g.getImageData(0, 0, 32, 32).data;
      var h = 0x811c9dc5;
      for (var i = 0; i < d.length; i++) {
        h ^= d[i];
        h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
      }
      out.hash = ("0000000" + h.toString(16)).slice(-8);
    } catch (e) {
      out.hashError = String((e && e.message) || e);
    }
    self.__NOVA_WORKER_RESULT__ = out;
    self.postMessage(out);
  } catch (err) {
    self.postMessage({ error: String((err && err.message) || err) });
  }
};
"""

WEBSOCKET_TO_WORKER_JS: Final[str] = r"""
(function () {
  // Verifies that a dedicated Web Worker reports the SAME fingerprint values
  // as the main frame.  A mismatch means the browser core failed to patch the
  // worker global scope -- one of the classic Android-emulation leaks.
  function once() {
    return new Promise(function (resolve) {
      var settled = false;
      function finish(value) {
        if (!settled) { settled = true; resolve(value); }
      }
      var timer = setTimeout(function () { finish({ error: "worker-timeout" }); }, 8000);
      try {
        var blob = new Blob([__NOVA_WORKER_SOURCE__], { type: "text/javascript" });
        var url = URL.createObjectURL(blob);
        var worker = new Worker(url);
        worker.onmessage = function (event) {
          clearTimeout(timer);
          try { URL.revokeObjectURL(url); } catch (e) { /* ignore */ }
          worker.terminate();
          finish(event.data || { error: "empty-worker-message" });
        };
        worker.onerror = function (event) {
          clearTimeout(timer);
          try { URL.revokeObjectURL(url); } catch (e) { /* ignore */ }
          worker.terminate();
          finish({ error: "worker-error: " + (event && event.message) });
        };
        worker.postMessage({ probe: true });
      } catch (err) {
        clearTimeout(timer);
        finish({ error: String((err && err.message) || err) });
      }
    });
  }
  return once();
})()
"""

# --------------------------------------------------------------------------
# Navigator
# --------------------------------------------------------------------------

NAVIGATOR_JS: Final[str] = r"""
(function () {
  try {
    var nav = navigator;
    var uad = null;
    try {
      if (nav.userAgentData) {
        var full = nav.userAgentData.fullVersionList || [];
        uad = {
          brands: (nav.userAgentData.brands || []).map(function (b) {
            return { brand: b.brand, version: b.version };
          }),
          fullVersionList: full.map(function (b) {
            return { brand: b.brand, version: b.version };
          }),
          mobile: nav.userAgentData.mobile === true,
          platform: nav.userAgentData.platform,
          platformVersion: nav.userAgentData.platformVersion,
          architecture: nav.userAgentData.architecture,
          bitness: nav.userAgentData.bitness,
          model: nav.userAgentData.model,
          wow64: nav.userAgentData.wow64 === true
        };
      }
    } catch (e) {
      uad = { error: String((e && e.message) || e) };
    }
    return {
      userAgent: nav.userAgent,
      userAgentData: uad,
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
      pdfViewerEnabled: nav.pdfViewerEnabled === true,
      cookieEnabled: nav.cookieEnabled === true,
      doNotTrack: nav.doNotTrack === undefined ? null : nav.doNotTrack,
      globalPrivacyControl:
        nav.globalPrivacyControl === undefined ? null : nav.globalPrivacyControl,
      onLine: nav.onLine === true,
      javaEnabled: nav.javaEnabled === true,
      plugins: (nav.plugins ? Array.prototype.slice.call(nav.plugins) : []).map(function (p) {
        return { name: p.name, filename: p.filename, description: p.description };
      }),
      mimeTypes: (
        nav.mimeTypes ? Array.prototype.slice.call(nav.mimeTypes) : []
      ).map(function (m) {
        return { type: m.type, suffixes: m.suffixes };
      })
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Screen / viewport / media queries
# --------------------------------------------------------------------------

SCREEN_JS: Final[str] = r"""
(function () {
  try {
    function mq(query) {
      try {
        return window.matchMedia(query).matches === true;
      } catch (e) {
        return null;
      }
    }
    var vv = null;
    try {
      if (window.visualViewport) {
        vv = {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          scale: window.visualViewport.scale,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop,
          pageLeft: window.visualViewport.pageLeft,
          pageTop: window.visualViewport.pageTop
        };
      }
    } catch (e) {
      vv = { error: String((e && e.message) || e) };
    }
    return {
      width: screen.width,
      height: screen.height,
      availWidth: screen.availWidth,
      availHeight: screen.availHeight,
      availLeft: screen.availLeft,
      availTop: screen.availTop,
      colorDepth: screen.colorDepth,
      pixelDepth: screen.pixelDepth,
      devicePixelRatio: window.devicePixelRatio,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      outerWidth: window.outerWidth,
      outerHeight: window.outerHeight,
      screenX: window.screenX,
      screenY: window.screenY,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      visualViewport: vv,
      // The resolved media-query values: exactly one entry per capability is
      // true, which mirrors how a real device answers matchMedia().
      prefersColorScheme: mq("(prefers-color-scheme: dark)") ? "dark" : "light",
      prefersReducedMotion: mq("(prefers-reduced-motion: reduce)") ? "reduce" : "no-preference",
      prefersContrast: mq("(prefers-contrast: more)")
        ? "more"
        : (mq("(prefers-contrast: less)") ? "less" : "no-preference"),
      forcedColors: mq("(forced-colors: active)") ? "active" : "none",
      pointer: mq("(pointer: coarse)")
        ? "coarse"
        : (mq("(pointer: fine)") ? "fine" : "none"),
      anyPointer: mq("(any-pointer: coarse)")
        ? "coarse"
        : (mq("(any-pointer: fine)") ? "fine" : "none"),
      hover: mq("(hover: hover)") ? "hover" : "none",
      anyHover: mq("(any-hover: hover)") ? "hover" : "none",
      colorGamut: mq("(color-gamut: rec2020)")
        ? "rec2020"
        : (mq("(color-gamut: p3)") ? "p3" : "srgb"),
      dynamicRange: mq("(dynamic-range: high)") ? "high" : "standard",
      // The raw matchMedia booleans are kept for the fine-grained checks.
      prefersColorSchemeDark: mq("(prefers-color-scheme: dark)"),
      prefersLightColorScheme: mq("(prefers-color-scheme: light)"),
      prefersReducedMotionReduce: mq("(prefers-reduced-motion: reduce)"),
      prefersContrastMore: mq("(prefers-contrast: more)"),
      forcedColorsActive: mq("(forced-colors: active)"),
      pointerCoarse: mq("(pointer: coarse)"),
      pointerFine: mq("(pointer: fine)"),
      pointerNone: mq("(pointer: none)"),
      anyPointerCoarse: mq("(any-pointer: coarse)"),
      anyPointerFine: mq("(any-pointer: fine)"),
      hoverHover: mq("(hover: hover)"),
      hoverNone: mq("(hover: none)"),
      anyHoverHover: mq("(any-hover: hover)"),
      anyHoverNone: mq("(any-hover: none)"),
      colorGamutSrgb: mq("(color-gamut: srgb)"),
      colorGamutP3: mq("(color-gamut: p3)"),
      colorGamutRec2020: mq("(color-gamut: rec2020)"),
      dynamicRangeStandard: mq("(dynamic-range: standard)"),
      dynamicRangeHigh: mq("(dynamic-range: high)"),
      videoHigh: mq("(video-dynamic-range: high)")
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------

AUDIO_JS: Final[str] = r"""
(function () {
  try {
    var OfflineCtor =
      window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!OfflineCtor) {
      return { error: "offline-audio-unavailable" };
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

    var analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.8;

    return context.startRendering().then(function (buffer) {
      var data = buffer.getChannelData(0);
      var sum = 0;
      for (var i = 0; i < data.length; i++) {
        sum += Math.abs(data[i]);
      }
      var mean = sum / data.length;
      var peak = 0;
      for (var j = 0; j < data.length; j++) {
        var v = Math.abs(data[j]);
        if (v > peak) { peak = v; }
      }
      var samples = 4096;
      var step = Math.max(1, Math.floor(data.length / samples));
      var frame = [];
      for (var k = 0; k < data.length && frame.length < 64; k += step) {
        frame.push(data[k]);
      }
      var freq = new Float32Array(analyser.frequencyBinCount);
      analyser.getFloatFrequencyData(freq);
      var fft = [];
      for (var f = 0; f < freq.length && fft.length < 32; f++) {
        if (isFinite(freq[f])) { fft.push(Math.round(freq[f] * 1000) / 1000); }
      }
      var h = 0x811c9dc5;
      for (var q = 0; q < frame.length; q++) {
        var b = Math.round(Math.abs(frame[q]) * 100000) & 0xff;
        h ^= b;
        h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
      }
      var fh = 0x811c9dc5;
      for (var z = 0; z < fft.length; z++) {
        var fv = Math.abs(Math.round(fft[z])) & 0xff;
        fh ^= fv;
        fh = (fh + ((fh << 1) + (fh << 4) + (fh << 7) + (fh << 8) + (fh << 24))) >>> 0;
      }
      return {
        sampleRate: buffer.sampleRate,
        length: buffer.length,
        numberOfChannels: buffer.numberOfChannels,
        // Quantised so that float noise cannot destabilise the comparison.
        mean: Math.round(mean * 1e6) / 1e6,
        peak: Math.round(peak * 1e6) / 1e6,
        sum: Math.round(sum * 1e4) / 1e4,
        hash: ("0000000" + h.toString(16)).slice(-8),
        fftHash: ("0000000" + fh.toString(16)).slice(-8),
        fft: fft
      };
    }).catch(function (err) {
      return { error: String((err && err.message) || err) };
    });
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Rects
# --------------------------------------------------------------------------

RECTS_JS: Final[str] = r"""
(function () {
  try {
    var host = document.createElement("div");
    host.setAttribute(
      "style",
      "position:absolute;left:11px;top:7px;width:273px;height:61px;padding:3px;" +
        "border:2px solid #333;margin:5px;overflow:hidden;"
    );
    var inner = document.createElement("div");
    inner.setAttribute(
      "style",
      "width:120px;height:23px;margin:4px;border:1px solid #999;padding:2px;"
    );
    inner.textContent = "nova-rects-probe";
    host.appendChild(inner);
    document.body.appendChild(host);

    function round(n) {
      return Math.round(n * 100) / 100;
    }
    function rectToObj(r) {
      return {
        x: round(r.x), y: round(r.y),
        top: round(r.top), right: round(r.right),
        bottom: round(r.bottom), left: round(r.left),
        width: round(r.width), height: round(r.height)
      };
    }
    var hostRect = host.getBoundingClientRect();
    var innerRect = inner.getBoundingClientRect();
    var hostRects = host.getClientRects();
    var innerRects = inner.getClientRects();

    var range = document.createRange();
    range.selectNodeContents(inner);
    var rangeRect = range.getBoundingClientRect();
    var rangeRects = range.getClientRects();

    var measure = {};
    try {
      var c = document.createElement("canvas");
      var g = c.getContext("2d");
      g.font = "16px Arial";
      var m = g.measureText("NOVA-rects");
      measure = {
        width: round(m.width),
        actualBoundingBoxLeft: round(m.actualBoundingBoxLeft),
        actualBoundingBoxRight: round(m.actualBoundingBoxRight),
        actualBoundingBoxAscent: round(m.actualBoundingBoxAscent),
        actualBoundingBoxDescent: round(m.actualBoundingBoxDescent),
        fontBoundingBoxAscent: round(m.fontBoundingBoxAscent),
        fontBoundingBoxDescent: round(m.fontBoundingBoxDescent)
      };
    } catch (e) {
      measure = { error: String((e && e.message) || e) };
    }

    var out = {
      host: rectToObj(hostRect),
      inner: rectToObj(innerRect),
      hostClientRectCount: hostRects.length,
      innerClientRectCount: innerRects.length,
      range: rectToObj(rangeRect),
      rangeRectCount: rangeRects.length,
      measureText: measure,
      devicePixelRatio: window.devicePixelRatio
    };
    host.remove();
    return out;
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

FONTS_JS: Final[str] = r"""
(function () {
  var CANDIDATES = __NOVA_FONT_CANDIDATES__;
  try {
    var canvas = document.createElement("canvas");
    canvas.width = 800;
    canvas.height = 120;
    var ctx = canvas.getContext("2d");
    var BASE = 72;
    var PROBE_TEXT = "mmmwwwiiilll0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    var genericWidths = {};
    var generics = ["monospace", "sans-serif", "serif"];
    ctx.font = BASE + "px monospace";
    genericWidths.monospace = ctx.measureText(PROBE_TEXT).width;
    ctx.font = BASE + "px sans-serif";
    genericWidths.sansSerif = ctx.measureText(PROBE_TEXT).width;
    ctx.font = BASE + "px serif";
    genericWidths.serif = ctx.measureText(PROBE_TEXT).width;

    var widths = {};
    var installed = [];
    var notInstalled = [];
    for (var i = 0; i < CANDIDATES.length; i++) {
      var family = CANDIDATES[i];
      ctx.font = BASE + "px " + family;
      var w = ctx.measureText(PROBE_TEXT).width;
      widths[family] = Math.round(w * 100) / 100;
      var isGeneric = false;
      for (var g = 0; g < generics.length; g++) {
        if (family === generics[g]) { isGeneric = true; }
      }
      if (isGeneric) { continue; }
      var mono = widths[family] === genericWidths.monospace;
      var sans = widths[family] === genericWidths.sansSerif;
      var serif = widths[family] === genericWidths.serif;
      if (mono || sans || serif) {
        notInstalled.push(family);
      } else {
        installed.push(family);
      }
    }
    canvas.width = 0;
    canvas.height = 0;
    return {
      widths: widths,
      genericWidths: genericWidths,
      installed: installed.sort(),
      notInstalled: notInstalled.sort(),
      installedCount: installed.length,
      probed: CANDIDATES.length
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Media devices
# --------------------------------------------------------------------------

MEDIA_DEVICES_JS: Final[str] = r"""
(function () {
  function collect(devices) {
    return (devices || []).map(function (d) {
      return {
        deviceId: d.deviceId,
        kind: d.kind,
        label: d.label,
        groupId: d.groupId
      };
    });
  }
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      return { error: "media-devices-unavailable" };
    }
    return navigator.mediaDevices.enumerateDevices().then(function (devices) {
      var list = collect(devices);
      var withLabels = list.filter(function (d) { return !!d.label; }).length;
      return {
        devices: list,
        count: list.length,
        withLabelCount: withLabels,
        kinds: list.map(function (d) { return d.kind; }).sort(),
        groups: list
          .map(function (d) { return d.groupId; })
          .filter(function (v, i, a) { return v && a.indexOf(v) === i; })
          .sort()
      };
    }).catch(function (err) {
      return { error: String((err && err.message) || err) };
    });
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Sensors
# --------------------------------------------------------------------------

SENSORS_JS: Final[str] = r"""
(function () {
  function once(target, eventName, timeoutMs) {
    return new Promise(function (resolve) {
      var settled = false;
      function finish(value) {
        if (!settled) { settled = true; resolve(value); }
      }
      var timer = setTimeout(function () { finish(null); }, timeoutMs || 1500);
      try {
        target.addEventListener(
          eventName,
          function handler(event) {
            clearTimeout(timer);
            target.removeEventListener(eventName, handler);
            finish(event);
          },
          { passive: true }
        );
      } catch (e) {
        clearTimeout(timer);
        finish(null);
      }
    });
  }
  try {
    var hasMotion = "DeviceMotionEvent" in window;
    var hasOrientation = "DeviceOrientationEvent" in window;
    var hasAbsolute = hasOrientation && "absolute" in window.DeviceOrientationEvent;

    var tasks = [];
    if (hasMotion) {
      tasks.push(
        once(window, "devicemotion", 1500).then(function (event) {
          if (!event) { return null; }
          return {
            interval: event.interval,
            acceleration: event.acceleration
              ? {
                  x: event.acceleration.x,
                  y: event.acceleration.y,
                  z: event.acceleration.z
                }
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
        once(window, "deviceorientation", 1500).then(function (event) {
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
        hasDeviceMotion: hasMotion,
        hasDeviceOrientation: hasOrientation,
        hasAbsoluteOrientation: hasAbsolute,
        motionSample: null,
        orientationSample: null
      };
      if (hasMotion) { out.motionSample = values[0]; }
      if (hasOrientation) {
        out.orientationSample = values[hasMotion ? 1 : 0];
      }
      return out;
    });
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Storage / network / battery
# --------------------------------------------------------------------------

STORAGE_JS: Final[str] = r"""
(function () {
  function batteryPromise() {
    return new Promise(function (resolve) {
      try {
        if (!navigator.getBattery) {
          resolve({ available: false });
          return;
        }
        navigator.getBattery().then(function (b) {
          resolve({
            available: true,
            charging: b.charging,
            chargingTime: b.chargingTime,
            dischargingTime: b.dischargingTime,
            level: b.level
          });
        }).catch(function (e) {
          resolve({ available: false, error: String((e && e.message) || e) });
        });
      } catch (e) {
        resolve({ available: false, error: String((e && e.message) || e) });
      }
    });
  }
  function estimatePromise() {
    return new Promise(function (resolve) {
      try {
        if (!navigator.storage || !navigator.storage.estimate) {
          resolve({ available: false });
          return;
        }
        navigator.storage.estimate().then(function (est) {
          resolve({
            available: true,
            quota: est.quota,
            usage: est.usage,
            usageDetails: est.usageDetails || null
          });
        }).catch(function (e) {
          resolve({ available: false, error: String((e && e.message) || e) });
        });
      } catch (e) {
        resolve({ available: false, error: String((e && e.message) || e) });
      }
    });
  }
  try {
    var conn = null;
    try {
      var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (c) {
        conn = {
          effectiveType: c.effectiveType,
          rtt: c.rtt,
          downlink: c.downlink,
          saveData: c.saveData,
          type: c.type === undefined ? null : c.type,
          effectiveTypeSet: c.effectiveType !== undefined,
          rttSet: c.rtt !== undefined,
          downlinkSet: c.downlink !== undefined,
          saveDataSet: c.saveData !== undefined
        };
      }
    } catch (e) {
      conn = { error: String((e && e.message) || e) };
    }
    return Promise.all([estimatePromise(), batteryPromise()]).then(function (parts) {
      return {
        storage: parts[0],
        battery: parts[1],
        connection: conn
      };
    });
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Timezone / locale
# --------------------------------------------------------------------------

TIMEZONE_JS: Final[str] = r"""
(function () {
  try {
    var resolved = Intl.DateTimeFormat().resolvedOptions();
    var localeTime = null;
    var localeDate = null;
    try {
      var df = new Intl.DateTimeFormat("en-US", {
        timeZone: resolved.timeZone,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      });
      localeTime = df.format(new Date(1700000000000));
    } catch (e) {
      localeTime = null;
    }
    try {
      var ddf = new Intl.DateTimeFormat("en-US", {
        timeZone: resolved.timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        timeZoneName: "short"
      });
      localeDate = ddf.format(new Date(1700000000000));
    } catch (e) {
      localeDate = null;
    }
    var offsetMinutes = -new Date(1700000000000).getTimezoneOffset();
    return {
      timeZone: resolved.timeZone,
      locale: resolved.locale,
      calendar: resolved.calendar,
      numberingSystem: resolved.numberingSystem,
      hourCycle: resolved.hourCycle,
      timeZoneName: resolved.timeZoneName,
      offsetMinutes: offsetMinutes,
      sampleTime: localeTime,
      sampleDate: localeDate,
      supportedLocales: (function () {
        try {
          return Intl.DateTimeFormat.supportedLocalesOf(["en-US", "vi-VN", "id-ID"])
            .slice()
            .sort();
        } catch (e) {
          return [];
        }
      })()
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Cross-context synchronisation -- main frame vs. iframe vs. srcdoc vs. sandbox
# --------------------------------------------------------------------------

IFRAME_SYNC_JS: Final[str] = r"""
(function () {
  function readFrame(win) {
    try {
      var nav = win.navigator;
      return {
        userAgent: nav.userAgent,
        platform: nav.platform,
        language: nav.language,
        vendor: nav.vendor,
        hardwareConcurrency: nav.hardwareConcurrency,
        deviceMemory: nav.deviceMemory,
        maxTouchPoints: nav.maxTouchPoints,
        screenWidth: win.screen.width,
        screenHeight: win.screen.height,
        devicePixelRatio: win.devicePixelRatio,
        timezone: win.Intl.DateTimeFormat().resolvedOptions().timeZone
      };
    } catch (e) {
      return { error: String((e && e.message) || e) };
    }
  }
  function probeIframe(srcdoc, sandboxAttr) {
    return new Promise(function (resolve) {
      var iframe = document.createElement("iframe");
      iframe.setAttribute("style", "position:absolute;left:-9999px;width:300px;height:200px;");
      if (srcdoc) {
        iframe.setAttribute("srcdoc", "<!doctype html><html><body></body></html>");
      }
      if (sandboxAttr) {
        iframe.setAttribute("sandbox", sandboxAttr);
      }
      var done = false;
      function finish(value) {
        if (done) { return; }
        done = true;
        try { iframe.remove(); } catch (e) { /* ignore */ }
        resolve(value);
      }
      var timer = setTimeout(function () { finish({ error: "iframe-timeout" }); }, 4000);
      iframe.onload = function () {
        clearTimeout(timer);
        try {
          var win = iframe.contentWindow;
          if (!win) { finish({ error: "no-content-window" }); return; }
          finish(readFrame(win));
        } catch (e) {
          finish({ error: String((e && e.message) || e) });
        }
      };
      iframe.onerror = function () {
        clearTimeout(timer);
        finish({ error: "iframe-error" });
      };
      document.body.appendChild(iframe);
    });
  }
  try {
    return Promise.all([
      probeIframe(false, ""),
      probeIframe(true, ""),
      probeIframe(false, "allow-scripts")
    ]).then(function (parts) {
      return {
        iframe: parts[0],
        srcdocIframe: parts[1],
        sandboxedIframe: parts[2]
      };
    });
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
})()
"""

# --------------------------------------------------------------------------
# Font candidate list
# --------------------------------------------------------------------------

FONT_CANDIDATES: Final[list[str]] = [
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
]

# --------------------------------------------------------------------------
# Harness assembly
# --------------------------------------------------------------------------

#: Axis name -> (JavaScript snippet, is_async).
#: ``is_async`` marks snippets returning a promise.
AXIS_SNIPPETS: Final[dict[str, tuple[str, bool]]] = {
    "canvas": (CANVAS_HASH_JS, False),
    "webgl": (WEBGL_READPIXELS_JS, False),
    "navigator": (NAVIGATOR_JS, False),
    "screen": (SCREEN_JS, False),
    "audio": (AUDIO_JS, True),
    "rects": (RECTS_JS, False),
    "fonts": (FONTS_JS, False),
    "media_devices": (MEDIA_DEVICES_JS, True),
    "sensors": (SENSORS_JS, True),
    "storage": (STORAGE_JS, True),
    "timezone": (TIMEZONE_JS, False),
    "iframe_sync": (IFRAME_SYNC_JS, True),
    "worker_sync": (WEBSOCKET_TO_WORKER_JS, True),
}

#: The axes the probe reports on, in a stable order.
AXIS_ORDER: Final[tuple[str, ...]] = tuple(AXIS_SNIPPETS.keys())


def _js_literal(value: object) -> str:
    """Serialise a Python value into a JavaScript literal."""

    return json.dumps(value, ensure_ascii=False)


def render_snippet(snippet: str) -> str:
    """Substitute the ``__NOVA_*__`` placeholders inside a snippet.

    The placeholders are replaced with self-contained JavaScript expressions so
    that the rendered snippet can be evaluated directly in the page without
    any helper being installed on ``window``.
    """

    rendered = snippet.replace("__NOVA_HASH__", f"({_FNV_HELPER_JS})")
    rendered = rendered.replace("__NOVA_WORKER_SOURCE__", _js_literal(WORKER_JS))
    rendered = rendered.replace("__NOVA_FONT_CANDIDATES__", _js_literal(FONT_CANDIDATES))
    return rendered


def build_harness_html() -> str:
    """Return the complete, self-contained probe harness page.

    The page defines ``window.__NOVA_PROBE__.run()`` which resolves to a JSON
    object containing every measured axis.  The generated HTML contains no
    external references at all -- no CDN, no fonts, no images -- so it can be
    loaded from a ``file://`` URL in an isolated profile without any network
    traffic.
    """

    blocks: list[str] = []
    registry: list[str] = []
    for axis in AXIS_ORDER:
        snippet, _is_async = AXIS_SNIPPETS[axis]
        # The rendered snippet is stripped: a snippet that starts with a
        # newline would make the JavaScript automatic semicolon insertion
        # terminate the surrounding ``return`` statement.
        body = render_snippet(snippet).strip()
        blocks.append(
            f"  // ---- axis: {axis} ----\n  function _axis_{axis}() {{\n    return {body};\n  }}"
        )
        registry.append(f'    "{axis}": _axis_{axis}')

    axes_literal = _js_literal(list(AXIS_ORDER))
    functions_block = "\n\n".join(blocks)
    registry_block = ",\n".join(registry)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOVA Profile Probe Harness</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #ffffff; color: #202124; }}
  body {{ font: 13px/1.4 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }}
  #nova-probe-root {{ padding: 8px; }}
  #nova-probe-log {{ white-space: pre-wrap; font-family: ui-monospace, monospace; }}
</style>
</head>
<body>
<div id="nova-probe-root"><div id="nova-probe-log">NOVA probe harness ready.</div></div>
<script>
"use strict";
(function () {{
  var AXES = {axes_literal};

{functions_block}

  var AXIS_FNS = {{
{registry_block}
  }};

  function markReady() {{
    var el = document.getElementById("nova-probe-log");
    if (el) {{ el.textContent = "NOVA probe harness ready."; }}
  }}

  function resolveValue(value) {{
    if (value && typeof value.then === "function") {{
      return value.then(
        function (v) {{ return v; }},
        function (e) {{ return {{ error: String((e && e.message) || e) }}; }}
      );
    }}
    return Promise.resolve(value);
  }}

  var API = (function () {{
    var api = {{
      version: "1.0.0",
      axes: AXES,
      run: function () {{
        var results = {{}};
        var chain = Promise.resolve();
        AXES.forEach(function (axis) {{
          chain = chain.then(function () {{
            var fn = AXIS_FNS[axis];
            if (typeof fn !== "function") {{
              results[axis] = {{ error: "missing-axis" }};
              return null;
            }}
            try {{
              return resolveValue(fn()).then(function (value) {{
                results[axis] = value === undefined ? {{ error: "undefined" }} : value;
              }});
            }} catch (err) {{
              results[axis] = {{ error: String((err && err.message) || err) }};
            }}
            return null;
          }});
        }});
        return chain.then(function () {{
          return {{
            schema: "nova.probe/1",
            version: api.version,
            axes: results
          }};
        }});
      }}
    }};
    Object.defineProperty(window, "__NOVA_PROBE__", {{
      value: api,
      writable: false,
      configurable: false,
      enumerable: true
    }});
    markReady();
    return api;
  }})();

  window.__NOVA_PROBE_READY__ = true;
  window.__NOVA_PROBE_API__ = API;
}})();
</script>
</body>
</html>
"""


__all__ = [
    "AUDIO_JS",
    "AXIS_ORDER",
    "AXIS_SNIPPETS",
    "CANVAS_HASH_JS",
    "FONT_CANDIDATES",
    "FONTS_JS",
    "IFRAME_SYNC_JS",
    "MEDIA_DEVICES_JS",
    "NAVIGATOR_JS",
    "RECTS_JS",
    "SCREEN_JS",
    "SENSORS_JS",
    "STORAGE_JS",
    "TIMEZONE_JS",
    "WEBGL_READPIXELS_JS",
    "WEBSOCKET_TO_WORKER_JS",
    "WORKER_JS",
    "build_harness_html",
    "render_snippet",
]
