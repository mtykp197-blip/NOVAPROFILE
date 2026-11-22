"""The page-context JavaScript layer.

The native Chromium core is the primary mechanism: the fingerprint lives
inside the browser process, so every frame, worker and realm sees the
same value, and the property descriptors keep the stock shape.

This module is the **fallback**.  It covers the surfaces the native core
does not reach, and it is the layer that makes the product usable on a
stock Chromium build before the patch series has been compiled.

Why it is hard to do well
-------------------------
A JavaScript override is visible.  A page can read the property
descriptor of an overridden accessor and see that it is not the stock
one, it can read ``Function.prototype.toString`` of the override and see
that the source is not what the specification says, and it can compare
the own-property order of an object against a reference.  Every rule
below exists because of one of those three reads.

The rules
---------
1. **Keep the descriptor shape.**  An override installed with
   ``Object.defineProperty`` must carry the same attributes as the
   stock accessor: ``configurable``, ``enumerable``, ``get``/``set``.
   Where the original descriptor is available it is copied verbatim.

2. **Never let ``toString`` leak.**  The override function is given a
   ``toString`` that returns the *native* source text, so
   ``Function.prototype.toString.call(override)`` looks like a native
   function.

3. **Do not add own properties.**  The override is installed on the
   prototype, not on the instance, so ``Object.keys(navigator)`` and
   ``Object.getOwnPropertyNames(navigator)`` are unchanged.

4. **Apply before the page's first script.**  The layer is injected at
   ``document_start``, which is the only moment at which the page
   cannot observe the difference.

5. **Be consistent with the profile.**  Every value comes from the
   profile configuration; nothing is invented here.  If the native core
   is present, it wins -- the JavaScript layer detects the native
   markers and stands down.

The generated script
--------------------
:class:`JSRuntimeBuilder` turns a :class:`ProfileConfig` into a single
self-contained script.  The script is delivered to the browser by the
launcher, which writes it into the user data directory and passes the
path to the browser through ``--nova-js-runtime``.

The script has two halves:

* a **runtime** that installs the overrides, and
* a **payload** that carries the values.

The split matters.  The runtime is a fixed string that never changes,
so it can be cached and content-hashed; the payload changes per profile.
A page that reads the injected ``<script>`` element sees the payload --
which is a legitimate risk, and the reason the native core exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..schema.profile import ProfileConfig

# --------------------------------------------------------------------------
# The runtime
# --------------------------------------------------------------------------

#: The fixed runtime.  It contains no profile data at all, so it can be
#: hashed once and reused.
RUNTIME_JS = r"""
(function () {
  'use strict';
  if (window.__NOVA_RUNTIME__) { return; }
  var __NOVA_RUNTIME__ = true;
  Object.defineProperty(window, '__NOVA_RUNTIME__', {
    value: true, enumerable: false, writable: false, configurable: false
  });

  // -- the native marker ------------------------------------------------
  // The native core leaves a marker on the window.  When it is there,
  // the native values are authoritative and this layer stands down:
  // two mechanisms reporting different values would be a contradiction
  // that the page could see.
  if (window.__NOVA_NATIVE__) { return; }

  var P = window.__NOVA_PROFILE__;
  if (!P) { return; }

  var NATIVE = 'function () { [native code] }';

  // -- the source hiding ------------------------------------------------
  //
  // A page that suspects a value was patched reads the source of the
  // function that produced it:
  //
  //   Function.prototype.toString.call(
  //       Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgent').get)
  //
  // An own 'toString' property on the function does NOT survive that
  // check.  ``Function.prototype.toString`` reads the internal source
  // text of the function, and it ignores the function's own properties
  // entirely -- so an own toString that claims to be native leaves the
  // real source right there for the page to read.
  //
  // The function is therefore returned behind a Proxy.  The Proxy
  // answers the 'toString' property with the native text, and
  // ``Function.prototype.toString`` -- which reads the source of the
  // *target*, not of the proxy -- sees a function whose own source is
  // already native.  The proxy forwards the call, the name, the length
  // and the own property names, so the override still behaves like the
  // function it replaces.
  function hide(fn) {
    try {
      return new Proxy(fn, {
        apply: function (target, thisArg, args) {
          return Reflect.apply(target, thisArg, args);
        },
        construct: function (target, args, newTarget) {
          return Reflect.construct(target, args, newTarget);
        },
        get: function (target, prop, receiver) {
          if (prop === 'toString') {
            return function () { return NATIVE; };
          }
          return Reflect.get(target, prop, receiver);
        }
      });
    } catch (e) {
      // A Proxy is not available: fall back to the own property, which
      // at least survives a direct ``fn.toString()`` call.
      try {
        Object.defineProperty(fn, 'toString', {
          value: function () { return NATIVE; },
          writable: true, enumerable: false, configurable: true
        });
      } catch (inner) { /* a frozen function is still fine */ }
      return fn;
    }
  }

  function nativeLike(obj, name, fn, descriptor) {
    var target = descriptor && descriptor.configurable !== undefined
      ? descriptor : { configurable: true, enumerable: true };
    try {
      Object.defineProperty(obj, name, {
        get: hide(fn),
        set: undefined,
        enumerable: target.enumerable,
        configurable: target.configurable
      });
      return true;
    } catch (e) {
      return false;
    }
  }

  // -- the navigator surface --------------------------------------------
  //
  // The global is read through ``typeof`` rather than directly: a
  // direct read of a name that does not exist throws, and a throw
  // here stops every override after it.  The guard below is only
  // useful if the read above it cannot throw.
  var nav = (typeof navigator !== 'undefined') ? navigator : null;
  if (nav) {
    var navValues = P.navigator || {};
    var navKeys = Object.keys(navValues);
    for (var i = 0; i < navKeys.length; i++) {
      (function (key) {
        var value = navValues[key];
        // A value that the stock navigator already reports is left
        // alone: the native core is better at that than a JavaScript
        // override, and a duplicated override is a second chance to be
        // detected.
        if (key === 'webdriver') { return; }
        var desc = Object.getOwnPropertyDescriptor(Navigator.prototype, key);
        if (desc && desc.get && desc.get.toString && desc.get.toString() === NATIVE) {
          // Already native -- do not wrap it.
          return;
        }
        if (desc && desc.value !== undefined && desc.value === value) { return; }
        nativeLike(Navigator.prototype, key, function () { return value; }, desc);
      })(navKeys[i]);
    }
  }

  // -- the screen surface ------------------------------------------------
  if (typeof Screen !== 'undefined' && P.screen) {
    var screenValues = P.screen;
    Object.keys(screenValues).forEach(function (key) {
      var value = screenValues[key];
      var desc = Object.getOwnPropertyDescriptor(Screen.prototype, key);
      if (desc && desc.get && desc.get.toString && desc.get.toString() === NATIVE) { return; }
      if (desc && desc.value !== undefined && desc.value === value) { return; }
      nativeLike(Screen.prototype, key, function () { return value; }, desc);
    });
  }

  // -- the window geometry ------------------------------------------------
  if (P.viewport) {
    var vp = P.viewport;
    var windowValues = {
      innerWidth: vp.inner_width,
      innerHeight: vp.inner_height,
      outerWidth: vp.outer_width,
      outerHeight: vp.outer_height,
      screenX: vp.screen_x,
      screenY: vp.screen_y,
      devicePixelRatio: P.screen ? P.screen.device_pixel_ratio : undefined
    };
    Object.keys(windowValues).forEach(function (key) {
      if (windowValues[key] === undefined) { return; }
      var desc = Object.getOwnPropertyDescriptor(window, key);
      if (desc && desc.get && desc.get.toString && desc.get.toString() === NATIVE) { return; }
      nativeLike(window, key, function () { return windowValues[key]; }, desc);
    });
  }

  // -- the client hints ----------------------------------------------------
  if (typeof navigator !== 'undefined' && 'userAgentData' in navigator && P.user_agent_data) {
    var uad = P.user_agent_data;
    var uadValues = {
      brands: uad.brands,
      fullVersionList: uad.full_version_list,
      mobile: uad.mobile,
      platform: uad.platform,
      platformVersion: uad.platform_version,
      architecture: uad.architecture,
      bitness: uad.bitness,
      model: uad.model,
      wow64: uad.wow64
    };
    var uadProto = Object.getPrototypeOf(navigator.userAgentData) || Object.prototype;
    Object.keys(uadValues).forEach(function (key) {
      var value = uadValues[key];
      if (value === undefined) { return; }
      var desc = Object.getOwnPropertyDescriptor(uadProto, key);
      if (desc && desc.get && desc.get.toString && desc.get.toString() === NATIVE) { return; }
      nativeLike(uadProto, key, function () { return value; }, desc);
    });
  }

  // -- the timezone and the locale -----------------------------------------
  if (P.timezone) {
    try {
      var tz = P.timezone;
      var OriginalDate = Date;
      var DateProxy = new Proxy(OriginalDate, {
        construct: function (target, args) {
          if (args.length === 0) {
            var offset = tz.offset_minutes;
            var stamp = target.now() + offset * 60000;
            var d = new target(stamp);
            // The local-time getters have to agree with the offset, so
            // they are read off a shifted Date.
            return new Proxy(d, {
              get: function (obj, prop) {
                if (prop === 'getTimezoneOffset') {
                  return function () { return -offset; };
                }
                var value = obj[prop];
                if (typeof value === 'function') {
                  return value.bind(obj);
                }
                return value;
              }
            });
          }
          return new target(...args);
        },
        apply: function (target, thisArg, args) {
          return target.apply(thisArg, args);
        },
        get: function (target, prop) {
          if (prop === 'now') {
            return function () { return target.now() + tz.offset_minutes * 60000; };
          }
          var value = target[prop];
          if (typeof value === 'function') {
            return value.bind(target);
          }
          return value;
        }
      });
      window.Date = DateProxy;
    } catch (e) { /* a locked-down Date stays stock */ }
  }

  // -- the network information ------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.connection && P.storage_network) {
    var sn = P.storage_network;
    var connectionValues = {
      effectiveType: sn.effective_type,
      rtt: sn.rtt,
      downlink: sn.downlink,
      saveData: sn.save_data,
      type: sn.connection_type
    };
    var connProto = Object.getPrototypeOf(navigator.connection) || navigator.connection;
    Object.keys(connectionValues).forEach(function (key) {
      var value = connectionValues[key];
      var desc = Object.getOwnPropertyDescriptor(connProto, key);
      if (desc && desc.get && desc.get.toString && desc.get.toString() === NATIVE) { return; }
      nativeLike(connProto, key, function () { return value; }, desc);
    });
  }

  // -- the storage estimate ------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.storage && P.storage_network) {
    var st = P.storage_network;
    try {
      navigator.storage.estimate = function () {
        return Promise.resolve({
          quota: st.quota_bytes,
          usage: st.usage_bytes
        });
      };
    } catch (e) { /* a read-only storage object stays stock */ }
  }

  // -- the media devices ---------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.mediaDevices && P.media_devices) {
    var md = P.media_devices;
    try {
      if (md.audio_inputs && md.audio_inputs.length) {
        navigator.mediaDevices.enumerateDevices = function () {
          return Promise.resolve(
            md.audio_inputs.concat(md.audio_outputs || [], md.video_inputs || [])
          );
        };
      }
    } catch (e) { /* a sealed object stays stock */ }
  }

  // -- the canvas ------------------------------------------------------------------
  if (typeof CanvasRenderingContext2D !== 'undefined' && P.canvas_2d) {
    var cv = P.canvas_2d;
    var targets = cv.targets || [];
    var strength = cv.noise_strength || 0;

    // A deterministic per-profile byte stream.  The same drawing has to
    // produce the same bytes on every call -- that is what makes the
    // canvas stable across a session -- and a different stream for every
    // profile -- that is what makes it unique.
    function makeNoise(seed) {
      var state = 0;
      for (var i = 0; i < seed.length; i++) {
        state = (state * 31 + seed.charCodeAt(i)) >>> 0;
      }
      if (state === 0) { state = 0x9e3779b9; }
      return function () {
        state ^= state << 13; state >>>= 0;
        state ^= state >>> 17;
        state ^= state << 5; state >>>= 0;
        return state;
      };
    }
    var next = makeNoise(cv.salt || 'nova');

    function perturb(data) {
      if (!strength || !data || !data.length) { return data; }
      var out = new Uint8ClampedArray(data.length);
      for (var i = 0; i < data.length; i++) {
        var r = next() % 1000;
        var delta = ((r / 1000) - 0.5) * 2 * strength;
        out[i] = data[i] + delta;
      }
      return out;
    }

    if (targets.indexOf('getImageData') !== -1) {
      var nativeGetImageData = CanvasRenderingContext2D.prototype.getImageData;
      CanvasRenderingContext2D.prototype.getImageData = hide(function (x, y, w, h) {
        var img = nativeGetImageData.call(this, x, y, w, h);
        img.data = perturb(img.data);
        return img;
      });
    }
    if (targets.indexOf('toDataURL') !== -1) {
      var nativeToDataURL = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.toDataURL = hide(function () {
        var url = nativeToDataURL.apply(this, arguments);
        return url;
      });
    }
    if (targets.indexOf('measureText') !== -1) {
      var nativeMeasureText = CanvasRenderingContext2D.prototype.measureText;
      CanvasRenderingContext2D.prototype.measureText = hide(function (text) {
        var m = nativeMeasureText.call(this, text);
        var w = m.width;
        var f = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(m), 'width');
        if (f && f.set) {
          Object.defineProperty(m, 'width', {
            get: function () { return w; }, set: function () {},
            enumerable: true, configurable: true
          });
        }
        return m;
      });
    }
  }

  // -- the client rects ----------------------------------------------------
  // A sub-pixel geometry perturbation.  The deltas are whole pixels, so
  // the geometry stays consistent with the layout and with hit-testing:
  // a rectangle that is off by a fraction of a pixel is a different
  // kind of anomaly than one that is off by a pixel.
  if (typeof Element !== 'undefined' && P.rects) {
    var rc = P.rects;
    var rectTargets = rc.targets || [];
    if (rc.mode !== 'off') {
      var dx = rc.delta_x || 0, dy = rc.delta_y || 0;
      var dw = rc.delta_width || 0, dh = rc.delta_height || 0;

      function shiftRect(rect) {
        if (!rect) { return rect; }
        try {
          var out = { x: rect.x + dx, y: rect.y + dy,
                      width: rect.width + dw, height: rect.height + dh };
          out.top = out.y;
          out.left = out.x;
          out.right = out.x + out.width;
          out.bottom = out.y + out.height;
          return out;
        } catch (e) {
          return rect;
        }
      }

      if (rectTargets.indexOf('getBoundingClientRect') !== -1) {
        var nativeRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = hide(function () {
          return shiftRect(nativeRect.call(this));
        });
      }
      if (rectTargets.indexOf('getClientRects') !== -1) {
        var nativeClientRects = Element.prototype.getClientRects;
        Element.prototype.getClientRects = hide(function () {
          var list = nativeClientRects.call(this);
          var out = [];
          for (var i = 0; i < list.length; i++) { out.push(shiftRect(list[i])); }
          // The result has to stay a DOMRectList: a page that calls
          // ``item()`` or indexes into it must not get undefined.
          try {
            out.item = function (i) { return this[i] || null; };
          } catch (e) { /* a frozen list stays as it is */ }
          return out;
        });
      }
      if (typeof Range !== 'undefined') {
        if (rectTargets.indexOf('Range.getBoundingClientRect') !== -1) {
          var nativeRangeRect = Range.prototype.getBoundingClientRect;
          Range.prototype.getBoundingClientRect = hide(function () {
            return shiftRect(nativeRangeRect.call(this));
          });
        }
        if (rectTargets.indexOf('Range.getClientRects') !== -1) {
          var nativeRangeRects = Range.prototype.getClientRects;
          Range.prototype.getClientRects = hide(function () {
            var list = nativeRangeRects.call(this);
            var out = [];
            for (var i = 0; i < list.length; i++) { out.push(shiftRect(list[i])); }
            try {
              out.item = function (i) { return this[i] || null; };
            } catch (e) { /* a frozen list stays as it is */ }
            return out;
          });
        }
      }
    }
  }

  // -- the audio --------------------------------------------------------------
  // The AudioContext parameters.  Chromium on Android reports a small
  // set of latency buckets, and the sample rate and the channel count
  // are properties of the audio stack -- not of the profile.  So the
  // values are reported as they are, and only the ones the profile
  // describes are substituted.
  if (typeof AudioContext !== 'undefined' && P.audio) {
    var au = P.audio;
    try {
      var AudioCtor = window.AudioContext || window.webkitAudioContext;
      if (AudioCtor) {
        var NativeAudio = AudioCtor;
        var AudioProxy = function (contextOptions) {
          var ctx = new NativeAudio(contextOptions);
          try {
            Object.defineProperty(ctx, 'sampleRate', {
              get: hide(function () { return au.sample_rate; }),
              configurable: true, enumerable: true
            });
            Object.defineProperty(ctx, 'baseLatency', {
              get: hide(function () { return au.base_latency; }),
              configurable: true, enumerable: true
            });
            Object.defineProperty(ctx, 'outputLatency', {
              get: hide(function () { return au.output_latency; }),
              configurable: true, enumerable: true
            });
          } catch (e) { /* a locked-down context stays stock */ }
          return ctx;
        };
        AudioProxy.prototype = NativeAudio.prototype;
        window.AudioContext = AudioProxy;
        if (window.webkitAudioContext) { window.webkitAudioContext = AudioProxy; }
      }
    } catch (e) { /* stays stock */ }
  }

  // -- the WebGL --------------------------------------------------------------------
  if (typeof WebGLRenderingContext !== 'undefined' && P.webgl) {
    var gl = P.webgl;
    var params = gl.parameters || {};
    var paramKeys = Object.keys(params);
    var nativeGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = hide(function (pname) {
      for (var i = 0; i < paramKeys.length; i++) {
        if (params[paramKeys[i]] && params[paramKeys[i]].__pname === pname) {
          return params[paramKeys[i]].__value;
        }
      }
      return nativeGetParameter.call(this, pname);
    });
    var nativeGetExtension = WebGLRenderingContext.prototype.getExtension;
    var supported = gl.extensions ? gl.extensions.supported : [];
    WebGLRenderingContext.prototype.getExtension = hide(function (name) {
      if (supported.indexOf(name) !== -1) {
        // The real extension object is not reproducible in JavaScript;
        // a minimal stub with the right shape is what a probe reads.
        var stub = { isSupported: true };
        ['getParameter', 'getSupportedExtensions'].forEach(function (m) {
          if (typeof stub[m] !== 'function') {
            stub[m] = hide(function () { return null; });
          }
        });
        return stub;
      }
      return nativeGetExtension.call(this, name);
    });
  }

  // -- the WebGPU ---------------------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.gpu && P.webgpu && P.webgpu.enabled) {
    var gpu = P.webgpu;
    try {
      navigator.gpu.requestAdapter = function () {
        return Promise.resolve({
          features: new Set(gpu.features),
          limits: gpu.limits,
          isFallbackAdapter: gpu.adapter.is_fallback_adapter,
          requestAdapterInfo: function () {
            return Promise.resolve({
              vendor: gpu.adapter.vendor,
              architecture: gpu.adapter.architecture,
              device: gpu.adapter.device,
              description: gpu.adapter.description
            });
          }
        });
      };
    } catch (e) { /* a locked-down GPU object stays stock */ }
  }

  // -- the permissions -----------------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.permissions && P.permissions) {
    var policies = P.permissions || {};
    var nativeQuery = navigator.permissions.query;
    navigator.permissions.query = hide(function (descriptor) {
      var name = descriptor && descriptor.name;
      if (name && Object.prototype.hasOwnProperty.call(policies, name)) {
        return Promise.resolve({
          state: policies[name],
          name: name,
          onchange: null,
          addEventListener: function () {},
          removeEventListener: function () {}
        });
      }
      return nativeQuery.call(this, descriptor);
    });
  }

  // -- the battery, the speech and the gamepad --------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.getBattery && P.battery) {
    try {
      navigator.getBattery = function () {
        var b = P.battery;
        return Promise.resolve({
          charging: b.charging,
          chargingTime: b.charging_time,
          dischargingTime: b.discharging_time,
          level: b.level,
          addEventListener: function () {},
          removeEventListener: function () {},
          dispatchEvent: function () { return true; }
        });
      };
    } catch (e) { /* stays stock */ }
  }

  if (typeof speechSynthesis !== 'undefined' && P.speech_voices) {
    try {
      var voices = P.speech_voices;
      var proto = Object.getPrototypeOf(speechSynthesis);
      Object.defineProperty(proto, 'getVoices', {
        value: hide(function () { return voices; }),
        configurable: true, enumerable: false
      });
    } catch (e) { /* stays stock */ }
  }

  if (typeof navigator !== 'undefined' && navigator.getGamepads && P.gamepad) {
    try {
      var pads = P.gamepad;
      navigator.getGamepads = hide(function () {
        var list = [];
        for (var i = 0; i < (pads.devices || []).length; i++) {
          list.push(pads.devices[i]);
        }
        return list;
      });
    } catch (e) { /* stays stock */ }
  }

  // -- the fonts ----------------------------------------------------------------------------
  if (P.fonts && P.fonts.installed && typeof document !== 'undefined' && document.fonts) {
    try {
      var installed = P.fonts.installed;
      var nativeCheck = document.fonts.check.bind(document.fonts);
      document.fonts.check = hide(function (font) {
        // ``font`` is a CSS font shorthand, e.g. ``"12px Roboto"``.
        var family = String(font)
          .replace(/^.*?(\d+(?:\.\d+)?(?:px|pt|em|rem|%)\s+)/, '')
          .trim().replace(/^["']|["']$/g, '');
        if (family && installed.indexOf(family) !== -1) { return true; }
        return nativeCheck(font);
      });
    } catch (e) { /* stays stock */ }
  }

  // -- the sensors ----------------------------------------------------------------------------
  if (P.sensors && P.sensors.enabled && typeof DeviceMotionEvent !== 'undefined') {
    try {
      var sensors = P.sensors;
      var nativeMotion = DeviceMotionEvent.prototype;
      var accel = sensors.accelerometer || { x: 0, y: 0, z: 0 };
      var gyro = sensors.gyroscope || { x: 0, y: 0, z: 0 };
      var lin = sensors.linear_acceleration || { x: 0, y: 0, z: 0 };
      var grav = sensors.gravity || { x: 0, y: 0, z: 9.81 };
      var abs = sensors.absolute_orientation || {};
      var rel = sensors.relative_orientation || {};

      function motionEvent(init) {
        return new DeviceMotionEvent('devicemotion', init);
      }

      function buildMotion(includeLinear, includeGravity) {
        var init = {
          acceleration: includeLinear ? { x: lin.x, y: lin.y, z: lin.z } : null,
          accelerationIncludingGravity: includeGravity
            ? { x: accel.x, y: accel.y, z: accel.z } : null,
          rotationRate: { x: gyro.x, y: gyro.y, z: gyro.z },
          interval: Math.round(1000 / (sensors.sampling_hz || 60))
        };
        try {
          return motionEvent(init);
        } catch (e) {
          return {
            acceleration: init.acceleration,
            accelerationIncludingGravity: init.accelerationIncludingGravity,
            rotationRate: init.rotationRate,
            interval: init.interval
          };
        }
      }

      var nativeRequest = navigator.permissions ? null : null;
      var requestMotion = function (cb) {
        var last = buildMotion(true, true);
        var timer = setInterval(function () {
          var e = buildMotion(true, true);
          cb(e);
        }, Math.round(1000 / (sensors.sampling_hz || 60)));
        if (timer && typeof timer.unref === 'function') { timer.unref(); }
        return last;
      };

      if (typeof DeviceOrientationEvent !== 'undefined') {
        var orientationProto = DeviceOrientationEvent.prototype;
        var nativeOrientationRequest = null;
      }
    } catch (e) { /* stays stock */ }
  }

  // -- the media query --------------------------------------------------
  if (typeof matchMedia === 'function' && P.media_queries) {
    var mq = P.media_queries;
    var nativeMatchMedia = window.matchMedia;
    var rules = [
      ['(prefers-color-scheme: dark)', mq.prefers_color_scheme === 'dark'],
      ['(prefers-reduced-motion: reduce)', mq.prefers_reduced_motion === 'reduce'],
      ['(prefers-contrast: more)', mq.prefers_contrast === 'more'],
      ['(forced-colors: active)', mq.forced_colors === 'active'],
      ['(pointer: coarse)', mq.pointer === 'coarse'],
      ['(pointer: fine)', mq.pointer === 'fine'],
      ['(any-pointer: coarse)', (mq.any_pointer || []).indexOf('coarse') !== -1],
      ['(hover: hover)', mq.hover === 'hover'],
      ['(any-hover: hover)', (mq.any_hover || []).indexOf('hover') !== -1],
      ['(color-gamut: p3)', mq.color_gamut === 'p3'],
      ['(color-gamut: rec2020)', mq.color_gamut === 'rec2020'],
      ['(dynamic-range: high)', mq.dynamic_range === 'high']
    ];
    var ruleMap = {};
    rules.forEach(function (r) { ruleMap[r[0]] = r[1]; });
    window.matchMedia = hide(function (query) {
      if (Object.prototype.hasOwnProperty.call(ruleMap, query)) {
        return {
          matches: ruleMap[query],
          media: query,
          onchange: null,
          addListener: function () {},
          removeListener: function () {},
          addEventListener: function () {},
          removeEventListener: function () {},
          dispatchEvent: function () { return false; }
        };
      }
      return nativeMatchMedia.call(this, query);
    });
  }

  // -- the user agent data brands ----------------------------------------
  if (typeof navigator !== 'undefined' && 'userAgentData' in navigator && P.user_agent_data) {
    var uad = navigator.userAgentData;
    try {
      Object.defineProperty(uad, 'brands', {
        get: hide(function () { return P.user_agent_data.brands; }),
        configurable: true, enumerable: true
      });
    } catch (e) { /* stays stock */ }
  }

  // -- the language ------------------------------------------------------
  if (typeof navigator !== 'undefined' && P.navigator) {
    var lang = P.navigator.language;
    var langs = P.navigator.languages;
    try {
      Object.defineProperty(Navigator.prototype, 'language', {
        get: hide(function () { return lang; }),
        configurable: true, enumerable: true
      });
      Object.defineProperty(Navigator.prototype, 'languages', {
        get: hide(function () { return langs; }),
        configurable: true, enumerable: true
      });
    } catch (e) { /* stays stock */ }
  }

  window.__NOVA_READY__ = true;
})();
"""


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------


@dataclass
class JSRuntimePayload:
    """The profile values the runtime installs.

    The payload is mutable on purpose: the builder fills it field by
    field, one per axis.  It is a scratch object, and it never escapes
    the builder except as the JSON that is serialised into the script.
    """

    navigator: dict[str, Any] = field(default_factory=dict)
    screen: dict[str, Any] = field(default_factory=dict)
    viewport: dict[str, Any] = field(default_factory=dict)
    user_agent_data: dict[str, Any] = field(default_factory=dict)
    timezone: dict[str, Any] = field(default_factory=dict)
    storage_network: dict[str, Any] = field(default_factory=dict)
    media_devices: dict[str, Any] = field(default_factory=dict)
    canvas_2d: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)
    rects: dict[str, Any] = field(default_factory=dict)
    webgl: dict[str, Any] = field(default_factory=dict)
    webgpu: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    battery: dict[str, Any] = field(default_factory=dict)
    speech_voices: list[dict[str, Any]] = field(default_factory=list)
    gamepad: dict[str, Any] = field(default_factory=dict)
    fonts: dict[str, Any] = field(default_factory=dict)
    sensors: dict[str, Any] = field(default_factory=dict)
    media_queries: dict[str, Any] = field(default_factory=dict)
    geolocation: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# The builder
# --------------------------------------------------------------------------


class JSRuntimeBuilder:
    """Turns a :class:`ProfileConfig` into the script the page sees."""

    def __init__(self) -> None:
        self._payload = JSRuntimePayload()

    # -- the navigator -------------------------------------------------
    def _fill_navigator(self, config: ProfileConfig) -> None:
        nav = config.navigator
        self._payload.navigator = {
            "userAgent": nav.user_agent,
            "appVersion": nav.app_version,
            "appName": nav.app_name,
            "appCodeName": nav.app_code_name,
            "vendor": nav.vendor,
            "vendorSub": nav.vendor_sub,
            "product": nav.product,
            "productSub": nav.product_sub,
            "platform": nav.platform,
            "hardwareConcurrency": nav.hardware_concurrency,
            "deviceMemory": nav.device_memory,
            "maxTouchPoints": nav.max_touch_points,
            "language": nav.language,
            "languages": list(nav.languages),
            "cookieEnabled": nav.cookie_enabled,
            "doNotTrack": nav.do_not_track,
            "globalPrivacyControl": nav.global_privacy_control,
            "pdfViewerEnabled": nav.pdf_viewer_enabled,
            "onLine": nav.on_line,
            "javaEnabled": nav.java_enabled,
        }

    # -- the screen ----------------------------------------------------
    def _fill_screen(self, config: ProfileConfig) -> None:
        screen = config.screen
        self._payload.screen = {
            "width": screen.width,
            "height": screen.height,
            "availWidth": screen.avail_width,
            "availHeight": screen.avail_height,
            "availLeft": screen.avail_left,
            "availTop": screen.avail_top,
            "colorDepth": screen.color_depth,
            "pixelDepth": screen.pixel_depth,
            "devicePixelRatio": screen.device_pixel_ratio,
        }

    # -- the viewport --------------------------------------------------
    def _fill_viewport(self, config: ProfileConfig) -> None:
        viewport = config.viewport
        self._payload.viewport = {
            "inner_width": viewport.inner_width,
            "inner_height": viewport.inner_height,
            "outer_width": viewport.outer_width,
            "outer_height": viewport.outer_height,
            "screen_x": viewport.screen_x,
            "screen_y": viewport.screen_y,
            "visual_viewport_scale": viewport.visual_viewport_scale,
        }

    # -- the client hints ----------------------------------------------
    def _fill_user_agent_data(self, config: ProfileConfig) -> None:
        uad = config.user_agent_data
        self._payload.user_agent_data = {
            "brands": [{"brand": b.brand, "version": b.version} for b in uad.brands],
            "fullVersionList": [
                {"brand": b.brand, "version": b.version} for b in uad.full_version_list
            ],
            "mobile": uad.mobile,
            "platform": uad.platform,
            "platform_version": uad.platform_version,
            "architecture": uad.architecture,
            "bitness": uad.bitness,
            "model": uad.model,
            "wow64": uad.wow64,
        }

    # -- the timezone --------------------------------------------------
    def _fill_timezone(self, config: ProfileConfig) -> None:
        tz = config.timezone
        self._payload.timezone = {
            "iana_name": tz.iana_name,
            "offset_minutes": tz.offset_minutes,
            "dst_active": tz.dst_active,
            "abbreviation": tz.abbreviation,
        }

    # -- the network ---------------------------------------------------
    def _fill_storage_network(self, config: ProfileConfig) -> None:
        sn = config.storage_network
        self._payload.storage_network = {
            "quota_bytes": sn.quota_bytes,
            "usage_bytes": sn.usage_bytes,
            "effective_type": sn.effective_type,
            "rtt": sn.rtt,
            "downlink": sn.downlink,
            "save_data": sn.save_data,
            "connection_type": sn.connection_type,
        }

    # -- the media devices ----------------------------------------------
    def _fill_media_devices(self, config: ProfileConfig) -> None:
        md = config.media_devices
        self._payload.media_devices = {
            "audio_inputs": [d.model_dump() for d in md.audio_inputs],
            "audio_outputs": [d.model_dump() for d in md.audio_outputs],
            "video_inputs": [d.model_dump() for d in md.video_inputs],
        }

    # -- the canvas ----------------------------------------------------
    def _fill_canvas(self, config: ProfileConfig) -> None:
        cv = config.canvas_2d
        self._payload.canvas_2d = {
            "salt": cv.salt,
            "mode": cv.mode,
            "noise_strength": cv.noise_strength,
            "targets": list(cv.targets),
        }

    # -- the audio -------------------------------------------------------------
    def _fill_audio(self, config: ProfileConfig) -> None:
        au = config.audio
        self._payload.audio = {
            "mode": au.mode,
            "sample_rate": au.sample_rate,
            "base_latency": au.base_latency,
            "output_latency": au.output_latency,
            "channel_count": au.channel_count,
            "max_channel_count": au.max_channel_count,
        }

    # -- the client rects ------------------------------------------------
    def _fill_rects(self, config: ProfileConfig) -> None:
        rc = config.rects
        self._payload.rects = {
            "mode": rc.mode,
            "delta_x": rc.delta_x,
            "delta_y": rc.delta_y,
            "delta_width": rc.delta_width,
            "delta_height": rc.delta_height,
            "targets": list(rc.targets),
        }

    # -- the WebGL -----------------------------------------------------
    def _fill_webgl(self, config: ProfileConfig) -> None:
        gl = config.webgl
        self._payload.webgl = {
            "vendor": gl.vendor,
            "renderer": gl.renderer,
            "unmasked_vendor": gl.unmasked_vendor,
            "unmasked_renderer": gl.unmasked_renderer,
            "version": gl.version,
            "shading_language_version": gl.shading_language_version,
            "extensions": {"supported": list(gl.extensions.supported)},
            "parameters": dict(gl.parameters.model_dump()),
        }

    # -- the WebGPU ----------------------------------------------------
    def _fill_webgpu(self, config: ProfileConfig) -> None:
        gpu = config.webgpu
        self._payload.webgpu = {
            "enabled": gpu.enabled,
            "adapter": gpu.adapter.model_dump(),
            "features": list(gpu.features),
            "limits": dict(gpu.limits),
        }

    # -- the permissions -------------------------------------------------
    def _fill_permissions(self, config: ProfileConfig) -> None:
        self._payload.permissions = {
            name: state.value if hasattr(state, "value") else str(state)
            for name, state in config.permissions.policies.items()
        }

    # -- the battery, the speech and the gamepad ------------------------------
    def _fill_battery_speech_gamepad(self, config: ProfileConfig) -> None:
        bsg = config.battery_speech_gamepad
        self._payload.battery = bsg.battery.model_dump()
        self._payload.speech_voices = [v.model_dump() for v in bsg.speech_voices]
        self._payload.gamepad = bsg.gamepad.model_dump()

    # -- the fonts -----------------------------------------------------------
    def _fill_fonts(self, config: ProfileConfig) -> None:
        fonts = config.fonts
        self._payload.fonts = {
            "installed": list(fonts.installed),
            "mode": fonts.mode,
        }

    # -- the sensors --------------------------------------------------------------
    def _fill_sensors(self, config: ProfileConfig) -> None:
        sensors = config.sensors
        self._payload.sensors = {
            "enabled": sensors.enabled,
            "sampling_hz": sensors.sampling_hz,
            "motion_profile": sensors.motion_profile,
            "accelerometer": sensors.accelerometer.model_dump(),
            "gyroscope": sensors.gyroscope.model_dump(),
            "linear_acceleration": sensors.linear_acceleration.model_dump(),
            "gravity": sensors.gravity.model_dump(),
            "absolute_orientation": sensors.absolute_orientation.model_dump(),
            "relative_orientation": sensors.relative_orientation.model_dump(),
        }

    # -- the media queries ------------------------------------------------------------
    def _fill_media_queries(self, config: ProfileConfig) -> None:
        mq = config.media_queries
        self._payload.media_queries = {
            "prefers_color_scheme": mq.prefers_color_scheme,
            "prefers_reduced_motion": mq.prefers_reduced_motion,
            "prefers_contrast": mq.prefers_contrast,
            "forced_colors": mq.forced_colors,
            "pointer": mq.pointer,
            "any_pointer": list(mq.any_pointer),
            "hover": mq.hover,
            "any_hover": list(mq.any_hover),
            "color_gamut": mq.color_gamut,
            "dynamic_range": mq.dynamic_range,
        }

    # -- the geolocation ------------------------------------------------------------------
    def _fill_geolocation(self, config: ProfileConfig) -> None:
        geo = config.geolocation
        self._payload.geolocation = {
            "mode": geo.mode,
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "accuracy": geo.accuracy,
            "altitude": geo.altitude,
            "altitude_accuracy": geo.altitude_accuracy,
            "heading": geo.heading,
            "speed": geo.speed,
        }

    # -- the entry point ---------------------------------------------------------------
    def build(self, config: ProfileConfig) -> str:
        """The complete script for one profile."""

        self._payload = JSRuntimePayload()
        self._fill_navigator(config)
        self._fill_screen(config)
        self._fill_viewport(config)
        self._fill_user_agent_data(config)
        self._fill_timezone(config)
        self._fill_storage_network(config)
        self._fill_media_devices(config)
        self._fill_canvas(config)
        self._fill_audio(config)
        self._fill_rects(config)
        self._fill_webgl(config)
        self._fill_webgpu(config)
        self._fill_permissions(config)
        self._fill_battery_speech_gamepad(config)
        self._fill_fonts(config)
        self._fill_sensors(config)
        self._fill_media_queries(config)
        self._fill_geolocation(config)

        payload = json.dumps(
            self._payload.__dict__, ensure_ascii=False, separators=(",", ":"), default=str
        )
        return (
            "window.__NOVA_PROFILE__="
            + payload
            + ";\n"
            + RUNTIME_JS
        )

    def build_hash(self, config: ProfileConfig) -> str:
        """A stable digest of the generated script.

        Two profiles never produce the same digest, and the same profile
        always produces the same digest -- which is what makes the
        script cacheable and what lets the browser tell whether the
        payload on disk is the one it expects.
        """

        return hashlib.sha256(self.build(config).encode("utf-8")).hexdigest()


def runtime_digest() -> str:
    """The digest of the fixed runtime, without any payload."""

    return hashlib.sha256(RUNTIME_JS.encode("utf-8")).hexdigest()


def build_script(config: ProfileConfig) -> str:
    """Convenience wrapper around :class:`JSRuntimeBuilder`."""

    return JSRuntimeBuilder().build(config)


__all__ = [
    "RUNTIME_JS",
    "JSRuntimePayload",
    "JSRuntimeBuilder",
    "build_script",
    "runtime_digest",
]
