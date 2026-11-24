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
  // The native core publishes the list of surfaces it owns.  Where it
  // owns a surface, its value is the authoritative one: the browser
  // reports it from inside the process, with the stock descriptor
  // shape, and no page can tell that anything was done at all.
  //
  // A JavaScript override on top of that would be strictly worse.  It
  // would replace a value the page cannot detect having been replaced
  // with one it can, and if the two ever disagreed the page would see
  // the disagreement -- which is exactly the kind of inconsistency that
  // gives a fingerprinting script something to key on.
  //
  // So the layer stands down on exactly the surfaces the native core
  // owns, and only on those.  A surface the native core does not reach
  // is still the layer's job.
  //
  // The list is bound as a local variable of the wrapper the native core
  // opens around this code, and it is *not* published on the window: a
  // window property that does not exist otherwise is a marker of its
  // own.  So the local is read first, and the window only afterwards --
  // a browser that was not patched at all has no wrapper, and there the
  // window is the only place the list could be.
  //
  // Reading only the window would leave the layer with no list on a
  // native core: the layer would stand down on nothing, and would
  // override every surface the native core had already taken care of.
  // The page would then see the native value with the layer's value on
  // top of it, which is a value that belongs to neither the profile nor
  // the host.
  var nativeSurfaces = (typeof __NOVA_NATIVE_SURFACES__ !== 'undefined')
      ? __NOVA_NATIVE_SURFACES__
      : window.__NOVA_NATIVE_SURFACES__;
  if (nativeSurfaces && !nativeSurfaces.length) { nativeSurfaces = null; }
  function nativeOwns(surface) {
    if (!nativeSurfaces) { return false; }
    return nativeSurfaces.indexOf(surface) !== -1;
  }

  // The payload.
  //
  // There are two ways the layer is handed the profile's values, and
  // the layer has to accept both.
  //
  // The native core compiles the code into the browser and hands the
  // payload over as the argument of the wrapper it opens around the
  // code.  That argument is a local variable of the wrapper, and it
  // never becomes a property of the window: a payload on the window
  // would be a fingerprint of its own, because it carries the salts,
  // the device identifiers and the font list.
  //
  // The JavaScript layer, used on a browser that was not patched, is
  // handed the payload as a property of the window instead, because
  // there is no wrapper to pass an argument through.
  //
  // The layer reads the local first and the window second.  Reading
  // only the window would leave the layer with no payload at all on a
  // native core: every override it makes would be skipped, and the
  // page would see the browser's own values.
  var P = (typeof __NOVA_PAYLOAD__ !== 'undefined') ? __NOVA_PAYLOAD__
        : window.__NOVA_PROFILE__;
  if (!P) { return; }

  // The source text a native function reports.
  //
  // A stock Blink accessor names itself in the text it reports:
  //
  //   Function.prototype.toString.call(stockGetter)
  //     -> "function get userAgent() { [native code] }"
  //
  // V8 produces that text for built-ins only.  A function written in
  // JavaScript reports its own source, and a proxy reports the bare
  // "function () { [native code] }" with no name -- which is a
  // different text from the stock one, and the difference is visible
  // without any source check at all.
  //
  // The replacement therefore registers the text it will report, and
  // the source text of every replacement is read back through one
  // place -- ``Function.prototype.toString`` -- which is itself
  // replaced by a proxy that reports the text of the original.
  function nativeSource(name) {
    if (name === undefined || name === null) {
      return 'function () { [native code] }';
    }
    return 'function ' + name + '() { [native code] }';
  }

  var NATIVE = nativeSource();

  // The text each replacement reports.
  //
  // A weak map from the replacement to the text it reports.  The
  // entries are what ``Function.prototype.toString`` answers with, so
  // a replacement and the accessor it took the place of report the
  // same text -- and report it through the same route, which is what
  // a page compares.
  var TEXTS = new WeakMap();

  function registerText(fn, text) {
    try {
      TEXTS.set(fn, text);
    } catch (e) { /* a primitive cannot be a key, but fn is a function */ }
    return fn;
  }

  // Whether an accessor has already been replaced by this runtime.
  //
  // The text alone cannot answer the question, because a stock Blink
  // accessor already reads "function get userAgent() { [native code] }"
  // -- exactly what a correct replacement reads.  Comparing the text
  // would therefore skip the stock accessors and leave the browser's
  // own values in place.
  //
  // So the replacement is marked instead.  The mark is kept in a weak
  // set rather than in a property on the replacement, because a
  // property would be one of the own property names of the function
  // -- and a stock accessor has only "length" and "name".  A weak set
  // leaves no trace on the function at all.
  var REPLACED = new WeakSet();

  function isReplaced(desc) {
    if (!desc || !desc.get) { return false; }
    try {
      return REPLACED.has(desc.get);
    } catch (e) {
      return false;
    }
  }

  function markReplaced(fn) {
    try {
      REPLACED.add(fn);
    } catch (e) { /* a frozen function still works */ }
    return fn;
  }

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
  // There are only two ways for a function to report the internal
  // source text of a built-in: it has to *be* a built-in, or it has to
  // be a proxy -- V8 reports a proxy as native without a name.  Neither
  // gives the name of the accessor, and the name is part of the text a
  // stock accessor reports.
  //
  // So the text is supplied by the one function that reads it.  The
  // original ``Function.prototype.toString`` is kept, a replacement is
  // put in its place, and the replacement answers with the registered
  // text of the function it is asked about.  Every other function --
  // the stock accessors, the built-ins, the ordinary JavaScript
  // functions -- is answered by the original, exactly as before.
  //
  // The replacement is a proxy over an arrow function.  The arrow has
  // no 'prototype' of its own, so the list of the replacement's own
  // property names is the same two entries the original has --
  // "length" and "name".  A proxy over an ordinary function would list
  // a third, and that third would be a difference a page could read.
  //
  // The proxy forwards the call with the receiver the page used, so
  // the original still sees the function it is asked about.
  var ORIGINAL_TO_STRING = Function.prototype.toString;

  function installSourceText() {
    var target = (self) => {
      var text = TEXTS.get(self);
      if (text !== undefined) { return text; }
      return ORIGINAL_TO_STRING.call(self);
    };

    var replacement;
    try {
      replacement = new Proxy(target, {
        apply: function (fn, thisArg, args) {
          // The receiver is the function the page asked about.  It
          // travels as an argument because the arrow function has no
          // 'this' of its own.
          return fn(thisArg);
        },
        get: function (fn, prop, receiver) {
          if (prop === 'name') { return 'toString'; }
          if (prop === 'length') { return 0; }
          if (prop === 'toString') {
            // The own 'toString' of the replacement reports the text
            // of the original, so that the two routes -- the built-in
            // call and the own call -- answer the same.
            return function () { return nativeSource('toString'); };
          }
          return Reflect.get(fn, prop, receiver);
        }
      });
    } catch (e) {
      return;
    }

    // The replacement reports the text of the original, so that the
    // page cannot tell it apart by reading its source.
    registerText(replacement, nativeSource('toString'));

    try {
      Object.defineProperty(Function.prototype, 'toString', {
        value: replacement,
        writable: true,
        enumerable: false,
        configurable: true
      });
    } catch (e) { /* a frozen prototype still works */ }
  }

  // The name of a replacement is the name of the accessor it took the
  // place of.  A native accessor getter is named "get userAgent" in
  // Blink, and the name of the replacement -- "raw", "value", "fn" --
  // is a giveaway that needs no source check to spot.
  //
  // The proxy is over an arrow function rather than over the
  // replacement itself, because the list of own property names is one
  // of the things a page can read.  A stock accessor getter has only
  // "length" and "name"; an ordinary function has a third entry,
  // "prototype", and that third entry would be a difference.
  //
  // The arrow function has no 'prototype' of its own, so the proxy
  // lists exactly the two entries the stock getter lists.  The arrow
  // has no 'this' either, so the receiver travels as an argument --
  // the proxy's apply trap is the one that has it.
  function hide(fn, name) {
    markReplaced(fn);
    var text = nativeSource(name);
    // The text the replacement reports is the text the accessor it
    // took the place of reported.
    registerText(fn, text);
    try {
      var arrow = (receiver, args) => {
        return Reflect.apply(fn, receiver, args || []);
      };
      var replacement = new Proxy(arrow, {
        apply: function (fn2, thisArg, args) {
          return fn2(thisArg, args);
        },
        get: function (fn2, prop, receiver) {
          if (prop === 'name' && name !== undefined) {
            return name;
          }
          if (prop === 'length') {
            return 0;
          }
          if (prop === 'toString') {
            // The own 'toString' has to report the same text as the
            // one the stock getter reports.  Left alone, the proxy
            // would forward it to the built-in, and the built-in
            // reports a proxy as native without a name -- so the two
            // routes would answer differently.
            return function () { return text; };
          }
          return Reflect.get(fn2, prop, receiver);
        }
      });
      // The page reads the proxy, not the function behind it, so the
      // text is registered for the proxy.
      registerText(replacement, text);
      markReplaced(replacement);
      return replacement;
    } catch (e) {
      // A Proxy is not available: fall back to the own property, which
      // at least survives a direct ``fn.toString()`` call.
      try {
        Object.defineProperty(fn, 'toString', {
          value: function () { return nativeSource(name); },
          writable: true, enumerable: false, configurable: true
        });
      } catch (inner) { /* a frozen function is still fine */ }
      return fn;
    }
  }

  // The source text has to be in place before the first replacement,
  // because the replacement of the source text is itself a
  // replacement, and a page that reads the source of the function that
  // reports sources would otherwise read the JavaScript source of the
  // reporter.
  installSourceText();

  // -- the timing shield ----------------------------------------------------
  //
  // Some surfaces leak through their *duration* rather than through
  // their value.  A canvas readback and a font probe both take a time
  // that depends on the hardware underneath, and a site that measures
  // the duration learns which hardware it is talking to -- or, worse
  // for a fleet, that every profile it sees was built on the same
  // machine.
  //
  // The delay below is a **constant of the profile**, not a jitter per
  // call.  Two reads of the same profile therefore take the same
  // time, which is what a real device does; a per-call jitter would
  // be its own signature, because a site that reads the canvas twice
  // and compares the two durations would find them unequal and no
  // real device ever does that.
  //
  // The wait is a spin on the clock rather than a timer.  A timer
  // would add its own quantisation -- Chromium clamps a timeout to
  // about a millisecond -- so the delay would be visible as a
  // rounding artefact, and the profile would report a duration that
  // is a multiple of the tick instead of the number the profile
  // carries.  The spin burns the time exactly, and the clock it
  // reads is the same one the page would read.
  var nowMs = (function () {
    try {
      if (typeof performance !== 'undefined' && performance
          && typeof performance.now === 'function') {
        return function () { return performance.now(); };
      }
    } catch (e) { /* falls through to Date */ }
    return function () { return Date.now(); };
  })();

  // Burn ``ms`` milliseconds.
  //
  // The loop is bounded by the clock, so it always ends; the extra
  // counter is a guard against a clock that does not move at all,
  // which would otherwise hang the page.
  function burn(ms) {
    if (!ms || ms <= 0) { return; }
    var deadline = nowMs() + ms;
    var guard = 0;
    while (nowMs() < deadline && guard < 10000000) { guard++; }
  }

  // The delay a profile carries on a surface.  A surface without a
  // timing shield keeps the host's clock, which is the leak.
  function timingDelay(surface, key) {
    if (!surface || !surface.enabled) { return 0; }
    var value = surface[key];
    return typeof value === 'number' && value > 0 ? value : 0;
  }

  function nativeLike(obj, name, fn, descriptor) {
    var target = descriptor && descriptor.configurable !== undefined
      ? descriptor : { configurable: true, enumerable: true };
    // The name of the getter that is being replaced.  A native accessor
    // is named "get" or "get <property>"; the name of the replacement
    // would be a giveaway, so the replacement reports the name of the
    // getter it took the place of.
    var getterName = 'get';
    try {
      if (descriptor && descriptor.get && descriptor.get.name) {
        getterName = descriptor.get.name;
      } else if (descriptor && descriptor.set && descriptor.set.name) {
        getterName = descriptor.set.name;
      } else if (name) {
        // Without a descriptor the name of the property is the best
        // guess at the name of the accessor, and Blink names its
        // accessors "get <property>".
        getterName = 'get ' + name;
      }
    } catch (e) { /* the name is a nicety, not a requirement */ }
    try {
      if (descriptor && !descriptor.get && !descriptor.set
          && descriptor.value !== undefined) {
        // A data property stays a data property.  Turning it into an
        // accessor would change the shape of the object, and the shape
        // is one of the three things a page can read.
        Object.defineProperty(obj, name, {
          value: fn(),
          writable: descriptor.writable !== false,
          enumerable: target.enumerable,
          configurable: target.configurable
        });
        return true;
      }
      Object.defineProperty(obj, name, {
        get: hide(fn, getterName),
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
  if (nav && !nativeOwns('navigator')) {
    var navValues = P.navigator || {};
    var navKeys = Object.keys(navValues);
    for (var i = 0; i < navKeys.length; i++) {
      (function (key) {
        var value = navValues[key];
        // A value that the stock navigator already reports is left
        // alone: the native core is better at that than a JavaScript
        // override, and a duplicated override is a second chance to be
        // detected.
        //
        // ``webdriver`` used to be exempt here, on the grounds that
        // the native core sets it and a second override would be a
        // second chance to be detected.  That exemption was wrong
        // when the profile is served by the JavaScript runtime alone:
        // headless Chrome reports ``navigator.webdriver`` as true, and
        // a site that reads it sees every profile of the fleet as
        // automated.  The exemption is kept only for the case where
        // the native core is already in place -- the runtime detects
        // that by the accessor being a native one, which the guard
        // below handles.
        var desc = Object.getOwnPropertyDescriptor(Navigator.prototype, key);
        if (isReplaced(desc)) {
          // Already native -- do not wrap it.
          return;
        }
        if (desc && desc.value !== undefined && desc.value === value) { return; }
        nativeLike(Navigator.prototype, key, function () { return value; }, desc);
      })(navKeys[i]);
    }
  }

  // -- the screen surface ------------------------------------------------
  if (typeof Screen !== 'undefined' && P.screen && !nativeOwns('screen')) {
    var screenValues = P.screen;
    Object.keys(screenValues).forEach(function (key) {
      var value = screenValues[key];
      // The device pixel ratio is not a property of the screen object
      // in a browser -- it belongs to the window -- so it is skipped
      // here and set on the window below.
      if (key === 'devicePixelRatio') { return; }
      var desc = Object.getOwnPropertyDescriptor(Screen.prototype, key);
      if (isReplaced(desc)) { return; }
      if (desc && desc.value !== undefined && desc.value === value) { return; }
      nativeLike(Screen.prototype, key, function () { return value; }, desc);
    });
  }

  // -- the window geometry ------------------------------------------------
  if (P.viewport && !nativeOwns('viewport')) {
    var vp = P.viewport;
    var windowValues = {
      innerWidth: vp.inner_width,
      innerHeight: vp.inner_height,
      outerWidth: vp.outer_width,
      outerHeight: vp.outer_height,
      screenX: vp.screen_x,
      screenY: vp.screen_y,
      // The payload carries the camel case name of the screen field,
      // the same name the property has in the browser.
      devicePixelRatio: P.screen ? P.screen.devicePixelRatio : undefined
    };
    Object.keys(windowValues).forEach(function (key) {
      if (windowValues[key] === undefined) { return; }
      var desc = Object.getOwnPropertyDescriptor(window, key);
      if (isReplaced(desc)) { return; }
      nativeLike(window, key, function () { return windowValues[key]; }, desc);
    });
  }

  // -- the client hints ----------------------------------------------------
  if (typeof navigator !== 'undefined' && 'userAgentData' in navigator && P.user_agent_data && !nativeOwns('user_agent_data')) {
    var uad = P.user_agent_data;
    var uadValues = {
      brands: uad.brands,
      // The payload carries the version list under the name the
      // property has in the browser.
      fullVersionList: uad.fullVersionList,
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
      if (isReplaced(desc)) { return; }
      nativeLike(uadProto, key, function () { return value; }, desc);
    });

    // The high entropy values are a *method*, not an accessor, and a
    // page reads them through it:
    //
    //   navigator.userAgentData.getHighEntropyValues(['fullVersionList'])
    //
    // The browser answers with its own version, so a profile that
    // claims to be Chrome 116 is read back as Chrome 160.  The
    // versions in the list are the ones the profile carries, and the
    // rest of the answer comes from the profile as well.
    try {
      var nativeGetHighEntropy = navigator.userAgentData.getHighEntropyValues;
      if (typeof nativeGetHighEntropy === 'function') {
        var highValues = {
          brands: uad.brands,
          fullVersionList: uad.fullVersionList,
          mobile: uad.mobile,
          platform: uad.platform,
          platformVersion: uad.platform_version,
          architecture: uad.architecture,
          bitness: uad.bitness,
          model: uad.model,
          wow64: uad.wow64,
          uaFullVersion: uad.uaFullVersion
        };
        navigator.userAgentData.getHighEntropyValues = hide(function (hints) {
          return nativeGetHighEntropy.call(this, hints).then(function (answer) {
            var out = {};
            try {
              var keys = Array.isArray(hints) ? hints : [];
              keys.forEach(function (hint) {
                if (highValues[hint] !== undefined) {
                  out[hint] = highValues[hint];
                }
              });
              // The brands are the same in both answers.
              out.brands = highValues.brands;
              return out;
            } catch (e) {
              return answer;
            }
          });
        }, 'getHighEntropyValues');
      }
    } catch (e) { /* stays stock */ }
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

    // ``Date.prototype.getTimezoneOffset`` is read directly by most
    // pages -- ``new Date().getTimezoneOffset()`` reaches the
    // prototype method through the proxy, but
    // ``Date.prototype.getTimezoneOffset.call(d)`` and the bare
    // ``d.getTimezoneOffset()`` on a date that was *not* built by the
    // proxy do not.  Without this the host's offset leaks through the
    // one accessor that reports it most directly.
    try {
      var nativeOffset = Date.prototype.getTimezoneOffset;
      if (typeof nativeOffset === 'function') {
        var profileOffset = tz.offset_minutes;
        Object.defineProperty(Date.prototype, 'getTimezoneOffset', {
          value: hide(function () { return -profileOffset; }, 'getTimezoneOffset'),
          writable: true,
          configurable: true,
          enumerable: false
        });
      }
    } catch (e) { /* a locked-down Date stays stock */ }

    // The time zone is read from ``Intl``, not from ``Date``: a page
    // that wants the zone asks the formatter for it.  The offset of
    // ``Date`` and the name of the zone have to agree, so both come
    // from the profile.
    //
    // The *locale* comes from the profile as well.  A formatter built
    // without a locale resolves to the host's -- ``en-US`` on a
    // machine whose profile claims ``es-US`` -- and the resolved
    // options then carry the host's locale and its hour cycle, which
    // a site reads straight out of ``resolvedOptions()``.
    try {
      if (typeof Intl !== 'undefined' && Intl.DateTimeFormat) {
        var NativeDateTimeFormat = Intl.DateTimeFormat;
        var IanaName = tz.iana_name;
        var ProfileLocale = tz.intl_locale || (P.navigator && P.navigator.language) || '';
        var ProfileHourCycle = tz.hour_cycle || '';
        var ProfileCalendar = tz.calendar || '';
        var ProfileNumbering = tz.numbering_system || '';
        var IntlProxy = function (locales, options) {
          // A page that names its own locale keeps it; a page that
          // names none gets the profile's.
          var wanted = (locales === undefined || locales === null || locales === '')
            ? ProfileLocale
            : locales;
          var formatter = new NativeDateTimeFormat(wanted, options);
          var resolved = formatter.resolvedOptions.bind(formatter);
          formatter.resolvedOptions = function () {
            var options = resolved();
            if (options && options.timeZone !== undefined && IanaName) {
              options.timeZone = IanaName;
            }
            if (options && ProfileLocale && options.locale === undefined) {
              options.locale = ProfileLocale;
            }
            if (options && ProfileHourCycle && options.hourCycle === undefined) {
              options.hourCycle = ProfileHourCycle;
            }
            if (options && ProfileCalendar && options.calendar === undefined) {
              options.calendar = ProfileCalendar;
            }
            if (options && ProfileNumbering && options.numberingSystem === undefined) {
              options.numberingSystem = ProfileNumbering;
            }
            return options;
          };
          return formatter;
        };
        IntlProxy.prototype = NativeDateTimeFormat.prototype;
        Intl.DateTimeFormat = IntlProxy;
      }

      // ``Intl.NumberFormat`` resolves to the host's locale in the
      // same way, and a page reads the resolved options of a number
      // formatter just as readily as those of a date formatter.
      if (Intl.NumberFormat) {
        var NativeNumberFormat = Intl.NumberFormat;
        var NumberProxy = function (locales, options) {
          var wanted = (locales === undefined || locales === null || locales === '')
            ? ProfileLocale
            : locales;
          var formatter = new NativeNumberFormat(wanted, options);
          var resolved = formatter.resolvedOptions.bind(formatter);
          formatter.resolvedOptions = function () {
            var options = resolved();
            if (options && ProfileLocale && options.locale === undefined) {
              options.locale = ProfileLocale;
            }
            if (options && ProfileNumbering && options.numberingSystem === undefined) {
              options.numberingSystem = ProfileNumbering;
            }
            return options;
          };
          return formatter;
        };
        NumberProxy.prototype = NativeNumberFormat.prototype;
        Intl.NumberFormat = NumberProxy;
      }

      // ``Intl.Collator`` resolves to the host's locale as well, and
      // the sorting it produces is a reading of the locale.
      if (Intl.Collator) {
        var NativeCollator = Intl.Collator;
        var CollatorProxy = function (locales, options) {
          var wanted = (locales === undefined || locales === null || locales === '')
            ? ProfileLocale
            : locales;
          var collator = new NativeCollator(wanted, options);
          var resolved = collator.resolvedOptions.bind(collator);
          collator.resolvedOptions = function () {
            var options = resolved();
            if (options && ProfileLocale && options.locale === undefined) {
              options.locale = ProfileLocale;
            }
            if (options && ProfileNumbering && options.numberingSystem === undefined) {
              options.numberingSystem = ProfileNumbering;
            }
            return options;
          };
          return collator;
        };
        CollatorProxy.prototype = NativeCollator.prototype;
        Intl.Collator = CollatorProxy;
      }
    } catch (e) { /* a locked-down Intl stays stock */ }
  }

  // -- the network information ------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.connection && P.storage_network && !nativeOwns('storage')) {
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
      if (isReplaced(desc)) { return; }
      nativeLike(connProto, key, function () { return value; }, desc);
    });
  }

  // -- the storage estimate ------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.storage && P.storage_network && !nativeOwns('storage')) {
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
  if (typeof navigator !== 'undefined' && navigator.mediaDevices && P.media_devices && !nativeOwns('media_devices')) {
    var md = P.media_devices;
    try {
      var mdList = []
        .concat(md.audio_inputs || [])
        .concat(md.audio_outputs || [])
        .concat(md.video_inputs || []);
      if (mdList.length) {
        // A real browser hands back MediaDeviceInfo objects, and the
        // shape of one is checked by scripts: the kind, the identifier,
        // the group and the label are own properties, the identifier is
        // an opaque string and the label is empty until the permission
        // is granted.
        var toInfo = function (d) {
          var info = {
            deviceId: d.device_id,
            kind: d.kind,
            label: d.label,
            groupId: d.group_id
          };
          try {
            // The prototype methods of MediaDeviceInfo.
            info.toJSON = function () {
              return {
                deviceId: this.deviceId,
                kind: this.kind,
                label: this.label,
                groupId: this.groupId
              };
            };
          } catch (e) { /* a frozen object keeps stock */ }
          return info;
        };

        navigator.mediaDevices.enumerateDevices = function () {
          return Promise.resolve(mdList.map(toInfo));
        };
      }
    } catch (e) { /* a sealed object stays stock */ }
  }

  // -- the canvas ------------------------------------------------------------------
  if (typeof CanvasRenderingContext2D !== 'undefined' && P.canvas_2d && !nativeOwns('canvas')) {
    var cv = P.canvas_2d;
    var targets = cv.targets || [];
    var strength = cv.noise_strength || 0;

    // The noise of the canvas.
    //
    // The noise is a function of *where* a byte sits in the canvas and
    // of the profile's salt, and of nothing else.  That is what makes
    // the canvas stable: the same drawing produces the same bytes on
    // every call, and the same bytes whether the page reads them with
    // ``getImageData`` or asks for an encoded PNG.  A site that
    // reads the canvas two different ways and gets two different
    // pictures learns that the canvas is being rewritten, which is
    // exactly the thing this is here to prevent.
    //
    // It is also what makes two profiles differ.  The salt is the
    // profile's own, so the same drawing lands on different bytes in
    // different profiles, and a site that collects the drawing from
    // two visitors sees two people.
    var salt = cv.salt || 'nova';

    // The hash of the salt, so that the per-pixel work starts from a
    // value that differs between profiles.
    var saltState = 0;
    for (var si = 0; si < salt.length; si++) {
      saltState = (saltState * 31 + salt.charCodeAt(si)) >>> 0;
    }
    if (saltState === 0) { saltState = 0x9e3779b9; }

    // The noise of one byte at an absolute position.
    function noiseAt(x, y, channel) {
      var h = saltState;
      h = (h ^ (x * 0x1f1f1f1f)) >>> 0;
      h = Math.imul(h, 0x85ebca6b) >>> 0;
      h = (h ^ (y * 0x27d4eb2f)) >>> 0;
      h = Math.imul(h, 0xc2b2ae35) >>> 0;
      h = (h ^ (channel * 0x165667b1)) >>> 0;
      h = Math.imul(h, 0x27d4eb2f) >>> 0;
      h ^= h >>> 15;
      h = Math.imul(h, 0x85ebca6b) >>> 0;
      h ^= h >>> 13;
      return (h >>> 0) % 1000;
    }

    // The noise of a block of pixels, placed at an absolute position.
    //
    // The width of the block is needed to work out where a byte sits:
    // the pixels of a canvas are stored row by row, so the column of a
    // byte is its index within the row.
    function perturb(data, originX, originY, width) {
      if (!strength || !data || !data.length) { return data; }
      var ox = originX || 0;
      var oy = originY || 0;
      var w = width || (data.length / 4);
      var out = new Uint8ClampedArray(data.length);
      for (var i = 0; i < data.length; i += 4) {
        var index = i / 4;
        var x = ox + (index % w);
        var y = oy + Math.floor(index / w);
        for (var c = 0; c < 4; c++) {
          var r = noiseAt(x, y, c);
          var delta = ((r / 1000) - 0.5) * 2 * strength;
          out[i + c] = data[i + c] + delta;
        }
      }
      return out;
    }

    // A copy of a canvas that carries the noise.
    //
    // The copy is what gets encoded, so the picture the page receives
    // is the profile's picture.  Encoding the original would hand the
    // page a picture that two profiles of the same drawing share, and
    // a site that reads the encoded PNG would see one visitor where
    // there are two.
    function noisyCopy(canvas) {
      try {
        var w = canvas.width;
        var h = canvas.height;
        if (!w || !h) { return null; }
        var source = canvas.getContext('2d');
        if (!source) { return null; }
        // The native read is used on purpose: the patched one applies
        // the noise, and the noise is applied here as well, so the
        // picture would be perturbed twice.
        var img = (typeof nativeGetImageData === 'function')
          ? nativeGetImageData.call(source, 0, 0, w, h)
          : source.getImageData(0, 0, w, h);
        img.data = perturb(img.data, 0, 0, w);
        var copy = document.createElement('canvas');
        copy.width = w;
        copy.height = h;
        var target = copy.getContext('2d');
        target.putImageData(img, 0, 0);
        return copy;
      } catch (e) {
        // A canvas that cannot be read -- one that is tainted by a
        // cross-origin image -- is encoded as it is.  The site that
        // reads it cannot compare it with anything either.
        return null;
      }
    }

    // The encoded picture of a canvas, carrying the noise.
    function encodeNoisy(canvas, args) {
      var copy = noisyCopy(canvas);
      if (!copy) {
        return nativeToDataURL.apply(canvas, args);
      }
      try {
        return nativeToDataURL.apply(copy, args);
      } catch (e) {
        return nativeToDataURL.apply(canvas, args);
      }
    }

    if (targets.indexOf('getImageData') !== -1) {
      var nativeGetImageData = CanvasRenderingContext2D.prototype.getImageData;
      CanvasRenderingContext2D.prototype.getImageData = hide(function (x, y, w, h) {
        var img = nativeGetImageData.call(this, x, y, w, h);
        img.data = perturb(img.data, x, y, w);
        // The readback timing.  The delay is a constant of the profile,
        // so two reads of the same canvas take the same time.
        burn(timingDelay(cv.timing, 'delay_ms'));
        return img;
      }, 'getImageData');
    }
    if (targets.indexOf('toDataURL') !== -1) {
      var nativeToDataURL = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.toDataURL = hide(function () {
        var url = encodeNoisy(this, arguments);
        // The readback timing.  A PNG encode is the most expensive
        // readback there is, and a site that times it learns the
        // graphics card underneath.
        burn(timingDelay(cv.timing, 'delay_ms'));
        return url;
      }, 'toDataURL');
    }
    if (targets.indexOf('toBlob') !== -1) {
      var nativeToBlob = HTMLCanvasElement.prototype.toBlob;
      HTMLCanvasElement.prototype.toBlob = hide(function () {
        var args = Array.prototype.slice.call(arguments);
        var callback = args[args.length - 1];
        var canvas = this;
        var type = args[0] || 'image/png';
        var quality = args[1];
        if (typeof callback === 'function') {
          // The delay has to happen before the callback fires, or the
          // page measures the encode and not the readback.
          args[args.length - 1] = hide(function (blob) {
            burn(timingDelay(cv.timing, 'delay_ms'));
            return callback(blob);
          }, 'toBlob');
          // The blob is taken from a copy of the canvas that carries
          // the noise, so the bytes the page receives are the bytes of
          // the profile and not the bytes of the device.
          var noisy = noisyCopy(canvas);
          if (noisy) {
            args.length = 2;
            args[0] = type;
            args[1] = quality;
            return nativeToBlob.apply(noisy, args);
          }
        }
        return nativeToBlob.apply(this, args);
      }, 'toBlob');
    }
    // The font metrics are not a noisy surface and not a target the
    // profile picks: they are always right, because a font the device
    // does not carry measures the same as every other font the device
    // does not carry.  A profile that lists nineteen fonts and a
    // measurement that gives twenty different widths is telling the
    // page that the list is a lie.
    {
      var nativeMeasureText = CanvasRenderingContext2D.prototype.measureText;
      // The font the context is set to.  A context that measures with a
      // font the device does not carry measures with the fallback, and
      // the fallback is the same for every missing font -- which is the
      // whole of what a font fingerprint reads.
      var installedFamilies = (P.fonts && P.fonts.installed) || [];
      var measureSalt = (cv.salt || 'nova');

      // The font the system substitutes when the requested one is not
      // on the device.  It is the generic family, so every missing
      // family measures the same.
      var FALLBACK_FONT = '10px sans-serif';

      var familyOf = function (font) {
        // A CSS font shorthand, e.g. "italic 12px 'Dancing Script'".
        var text = String(font == null ? '' : font);
        var match = text.match(/["']([^"']+)["']/);
        if (match) { return match[1]; }
        // Without quotes the family is the last word of the shorthand,
        // after the size and the style.
        var parts = text.trim().split(/\s+/);
        return parts.length ? parts[parts.length - 1] : '';
      };

      var isInstalled = function (family) {
        if (!family) { return true; }
        var lower = family.toLowerCase();
        for (var i = 0; i < installedFamilies.length; i++) {
          if (installedFamilies[i].toLowerCase() === lower) { return true; }
        }
        return false;
      };

      // The measurement delay.  The width of a string has already been
      // made uniform below -- every family the device does not carry
      // measures the same -- but the *duration* of the measurement
      // still says whether a substitution happened, because the
      // engine has to look the family up either way.  The delay
      // makes the two cases cost the same.
      var fontMeasureDelay = timingDelay(
        (P.fonts && P.fonts.timing) || null,
        'measure_delay_ms'
      );

      CanvasRenderingContext2D.prototype.measureText = hide(function (text) {
        var m = nativeMeasureText.call(this, text);
        var w = m.width;

        var family = familyOf(this && this.font);
        if (!isInstalled(family)) {
          // The font is not on the device, so the width the page reads
          // is the width of whatever the system substituted.  The
          // substituted font is the same one for every missing family,
          // so every missing family measures the same -- and that is
          // exactly what a script that measures twenty fonts and looks
          // for the duplicates is looking for.
          //
          // The substituted font is what the canvas falls back to when
          // the requested one is missing, so the measurement has to be
          // taken with that font set, not with the requested one.
          var savedFont = this.font;
          try {
            this.font = FALLBACK_FONT;
            w = nativeMeasureText.call(this, text).width;
          } catch (e) {
            // A context that refuses to change its font keeps the
            // width it gave, which is better than no width at all.
          } finally {
            try { this.font = savedFont; } catch (e2) { /* stays stock */ }
          }
        }

        // The delay is applied on every path, so that the duration of
        // a measurement says nothing about the font configuration.
        burn(fontMeasureDelay);

        // The width the page reads is an own property of the metrics
        // object in some engines and an accessor on its prototype in
        // others, so both places are looked at.
        var descriptor = Object.getOwnPropertyDescriptor(m, 'width');
        if (!descriptor) {
          var proto = Object.getPrototypeOf(m);
          descriptor = proto
            ? Object.getOwnPropertyDescriptor(proto, 'width')
            : null;
        }
        if (descriptor && (descriptor.set || descriptor.get)) {
          Object.defineProperty(m, 'width', {
            get: hide(function () { return w; }, 'get width'),
            set: function () {},
            enumerable: true, configurable: true
          });
        } else if (descriptor) {
          // A plain data property: it can be written.
          try { m.width = w; } catch (e) { /* frozen */ }
        }
        return m;
      }, 'measureText');
    }
  }

  // -- the client rects ----------------------------------------------------
  // A sub-pixel geometry perturbation.  The deltas are whole pixels, so
  // the geometry stays consistent with the layout and with hit-testing:
  // a rectangle that is off by a fraction of a pixel is a different
  // kind of anomaly than one that is off by a pixel.
  if (typeof Element !== 'undefined' && P.rects && !nativeOwns('rects')) {
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
        }, 'getBoundingClientRect');
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
        }, 'getClientRects');
      }
      if (typeof Range !== 'undefined') {
        if (rectTargets.indexOf('Range.getBoundingClientRect') !== -1) {
          var nativeRangeRect = Range.prototype.getBoundingClientRect;
          Range.prototype.getBoundingClientRect = hide(function () {
            return shiftRect(nativeRangeRect.call(this));
          }, 'getBoundingClientRect');
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
          }, 'getClientRects');
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
  if (typeof AudioContext !== 'undefined' && P.audio && !nativeOwns('audio')) {
    var au = P.audio;
    try {
      var AudioCtor = window.AudioContext || window.webkitAudioContext;
      if (AudioCtor) {
        var NativeAudio = AudioCtor;
        var AudioProxy = function (contextOptions) {
          var ctx = new NativeAudio(contextOptions);
          try {
            Object.defineProperty(ctx, 'sampleRate', {
              get: hide(function () { return au.sample_rate; }, 'get sampleRate'),
              configurable: true, enumerable: true
            });
            Object.defineProperty(ctx, 'baseLatency', {
              get: hide(function () { return au.base_latency; }, 'get baseLatency'),
              configurable: true, enumerable: true
            });
            Object.defineProperty(ctx, 'outputLatency', {
              get: hide(function () { return au.output_latency; }, 'get outputLatency'),
              configurable: true, enumerable: true
            });
            // The channel count of the live context.
            //
            // The offline context carries its channel count in the
            // rendered buffer, and the runtime forces it at construction
            // so that the buffer agrees with the profile.  The live
            // context has no buffer to force, so the property itself is
            // the reading -- and a site that compares
            // ``context.destination.channelCount`` with the count of
            // the channels it renders hears the difference otherwise.
            Object.defineProperty(ctx, 'channelCount', {
              get: hide(function () { return au.channel_count; }, 'get channelCount'),
              configurable: true, enumerable: true
            });
            try {
              Object.defineProperty(ctx.destination, 'channelCount', {
                get: hide(function () { return au.channel_count; }, 'get channelCount'),
                configurable: true, enumerable: true
              });
            } catch (e) { /* a locked-down destination stays stock */ }
          } catch (e) { /* a locked-down context stays stock */ }
          return ctx;
        };
        AudioProxy.prototype = NativeAudio.prototype;
        window.AudioContext = AudioProxy;
        if (window.webkitAudioContext) { window.webkitAudioContext = AudioProxy; }
      }
    } catch (e) { /* stays stock */ }
  }

  // -- the offline audio context -------------------------------------------------
  // The live context is not the only way to read the audio stack.  A site
  // can open an ``OfflineAudioContext`` and read ``sampleRate`` off it,
  // and -- this is the part that matters -- the rendered buffer carries
  // the same number, so overriding the property on the context is not
  // enough: the *rendered* buffer has to carry it too.
  //
  // ``OfflineAudioContext`` takes its sample rate as the third argument,
  // so the value is forced at construction.  A context created with the
  // rate the profile declares renders at that rate, and both
  // ``context.sampleRate`` and ``buffer.sampleRate`` then agree with the
  // profile, which is what a handset's own audio stack would report.
  //
  // The channel count is forced the same way, for the same reason: the
  // rendered buffer's channel count is the one a site reads.
  if (typeof OfflineAudioContext !== 'undefined' && P.audio && !nativeOwns('audio')) {
    try {
      var NativeOffline = window.OfflineAudioContext;
      var OfflineProxy = function (numberOfChannels, length, sampleRate) {
        // The constructor has two shapes: the positional one and the
        // dictionary one.  The positional one is the one that carries
        // the rate, so only that one is rewritten.
        //
        // The rate is forced, not merely defaulted.  A site that opens
        // an offline context at 44100 on a device whose audio stack
        // runs at 48000 would render a buffer at 44100, and the buffer
        // is what the site reads: ``buffer.sampleRate`` is the number
        // that ends up in the fingerprint.  Forcing the rate at
        // construction makes the rendered buffer carry the profile's
        // number, which is what the real handset would have produced.
        var ctx;
        if (typeof numberOfChannels === 'object' && numberOfChannels !== null) {
          var options = {};
          for (var key in numberOfChannels) {
            if (Object.prototype.hasOwnProperty.call(numberOfChannels, key)) {
              options[key] = numberOfChannels[key];
            }
          }
          options.sampleRate = au.sample_rate;
          if (options.numberOfChannels === undefined) {
            options.numberOfChannels = au.channel_count;
          }
          ctx = new NativeOffline(options);
        } else {
          var channels = numberOfChannels;
          if (channels === undefined || channels === null) {
            channels = au.channel_count;
          }
          ctx = new NativeOffline(channels, length, au.sample_rate);
        }
        try {
          Object.defineProperty(ctx, 'sampleRate', {
            get: hide(function () { return au.sample_rate; }, 'get sampleRate'),
            configurable: true, enumerable: true
          });
        } catch (e) { /* a locked-down context stays stock */ }
        return ctx;
      };
      OfflineProxy.prototype = NativeOffline.prototype;
      window.OfflineAudioContext = OfflineProxy;
      if (window.webkitOfflineAudioContext) {
        window.webkitOfflineAudioContext = OfflineProxy;
      }
    } catch (e) { /* stays stock */ }
  }

  // -- the audio samples --------------------------------------------------------
  //
  // The Web Audio fingerprint is a sum over the whole render, and it
  // resolves far below the level of the audio pipeline's own
  // quantisation.  Two profiles that render the same graph therefore
  // produce the same sum, and a site that has collected the sums of
  // two visitors sees one visitor.
  //
  // The samples are perturbed where the page reads them, which is
  // ``getChannelData`` and ``copyFromChannel``.  The perturbation is a
  // function of the sample's position and of the profile's salt, and
  // of nothing else, so the same render reads the same way twice and
  // two profiles read differently.
  if (typeof AudioBuffer !== 'undefined' && P.audio && !nativeOwns('audio')) {
    try {
      var audioCfg = P.audio;
      var auStrength = audioCfg.noise_strength || 0;
      var auSalt = audioCfg.salt || 'nova';

      var auState = 0;
      for (var ai = 0; ai < auSalt.length; ai++) {
        auState = (auState * 31 + auSalt.charCodeAt(ai)) >>> 0;
      }
      if (auState === 0) { auState = 0x9e3779b9; }

      // The perturbation of one sample.
      function audioNoiseAt(channel, index) {
        var h = auState;
        h = (h ^ (channel * 0x1f1f1f1f)) >>> 0;
        h = Math.imul(h, 0x85ebca6b) >>> 0;
        h = (h ^ (index * 0x27d4eb2f)) >>> 0;
        h = Math.imul(h, 0xc2b2ae35) >>> 0;
        h ^= h >>> 15;
        h = Math.imul(h, 0x85ebca6b) >>> 0;
        h ^= h >>> 13;
        return (h >>> 0) % 1000;
      }

      if (auStrength > 0 && typeof AudioBuffer.prototype.getChannelData === 'function') {
        var nativeGetChannelData = AudioBuffer.prototype.getChannelData;
        var perturbedBuffers = new WeakMap();

        AudioBuffer.prototype.getChannelData = hide(function (channel) {
          var samples = nativeGetChannelData.call(this, channel);
          if (!samples) { return samples; }

          // The buffer is perturbed once, and the same perturbed
          // samples are handed out on every read, so that a page
          // which reads a channel twice does not see it change.
          var seen = perturbedBuffers.get(this);
          if (!seen) {
            seen = {};
            perturbedBuffers.set(this, seen);
          }
          if (seen[channel]) { return samples; }
          seen[channel] = true;

          for (var s = 0; s < samples.length; s++) {
            var r = audioNoiseAt(channel, s);
            var delta = ((r / 1000) - 0.5) * 2 * auStrength;
            samples[s] = samples[s] + delta;
          }
          return samples;
        }, 'getChannelData');
      }

      if (auStrength > 0 && typeof AudioBuffer.prototype.copyFromChannel === 'function') {
        var nativeCopyFromChannel = AudioBuffer.prototype.copyFromChannel;
        AudioBuffer.prototype.copyFromChannel = hide(function (destination, channel, startInChannel) {
          nativeCopyFromChannel.call(this, destination, channel, startInChannel || 0);
          var offset = startInChannel || 0;
          for (var s = 0; s < destination.length; s++) {
            var r = audioNoiseAt(channel, offset + s);
            var delta = ((r / 1000) - 0.5) * 2 * auStrength;
            destination[s] = destination[s] + delta;
          }
        }, 'copyFromChannel');
      }
    } catch (e) { /* a locked-down buffer stays stock */ }
  }

  // -- the WebGL --------------------------------------------------------------------
  if (typeof WebGLRenderingContext !== 'undefined' && P.webgl && !nativeOwns('webgl')) {
    var gl = P.webgl;
    var params = gl.parameters || {};
    var paramKeys = Object.keys(params);
    var nativeGetParameter = WebGLRenderingContext.prototype.getParameter;

    // The identity of the graphics card is the loudest fingerprint
    // there is.  It is read through ``getParameter`` with the enum
    // values the WebGL specification gives, and the enums are what
    // the profile carries.
    //
    //   VENDOR                   0x1F00  7936
    //   RENDERER                 0x1F01  7937
    //   VERSION                  0x1F02  7938
    //   SHADING_LANGUAGE_VERSION 0x8B8C  35724
    //   UNMASKED_VENDOR_WEBGL    0x9245  37445
    //   UNMASKED_RENDERER_WEBGL  0x9246  37446
    //
    // The two unmasked ones are reached through the
    // ``WEBGL_debug_renderer_info`` extension, so the extension has to
    // be answered as well: a browser that does not have the extension
    // cannot be asked for the unmasked names.
    var IDENTITY = {
      7936: gl.vendor,
      7937: gl.renderer,
      7938: gl.version,
      35724: gl.shading_language_version,
      37445: gl.unmasked_vendor,
      37446: gl.unmasked_renderer
    };

    var nativeGetExtension = WebGLRenderingContext.prototype.getExtension;
    var supported = gl.extensions ? gl.extensions.supported : [];

    // The limits are read through ``getParameter`` with the enum the
    // specification gives each one.  The enums were read off a live
    // browser, so they are the ones the browser answers with.
    var PARAM_ENUMS = {
      max_texture_size: 3379,
      max_cube_map_texture_size: 34076,
      max_3d_texture_size: 32883,
      max_array_texture_layers: 35071,
      max_renderbuffer_size: 34024,
      max_viewport_dims: 3386,
      max_vertex_attribs: 34921,
      max_vertex_uniform_vectors: 36347,
      max_vertex_uniform_components: 35658,
      max_fragment_uniform_vectors: 36349,
      max_fragment_uniform_components: 35657,
      max_varying_vectors: 36348,
      max_combined_texture_image_units: 35661,
      max_vertex_texture_image_units: 35660,
      max_texture_image_units: 34930,
      max_color_attachments: 36063,
      max_draw_buffers: 34852,
      max_samples: 36183,
      max_uniform_buffer_bindings: 35375,
      aliased_line_width_range: 33902,
      aliased_point_size_range: 33901,
      sample_buffers: 32936,
      samples: 32937
    };

    function makeGetParameter(native) {
      return hide(function (pname) {
        if (Object.prototype.hasOwnProperty.call(IDENTITY, pname)) {
          var value = IDENTITY[pname];
          if (value !== undefined && value !== null) { return value; }
        }
        for (var i = 0; i < paramKeys.length; i++) {
          var entry = params[paramKeys[i]];
          if (entry === undefined || entry === null) { continue; }
          // A parameter may carry its own enum; otherwise the enum is
          // the one the table names.
          var enumValue = entry.__pname !== undefined
            ? entry.__pname
            : PARAM_ENUMS[paramKeys[i]];
          if (enumValue !== undefined && enumValue === pname) {
            return entry.__value !== undefined ? entry.__value : entry;
          }
        }
        return native.call(this, pname);
      }, 'getParameter');
    }

    function makeGetExtension(native) {
      return hide(function (name) {
        if (supported.indexOf(name) !== -1) {
          // The real extension object is not reproducible in JavaScript;
          // a minimal stub with the right shape is what a probe reads.
          var stub = { isSupported: true };
          if (name === 'WEBGL_debug_renderer_info') {
            // The unmasked names travel through this extension's two
            // enum values, so a stub without them would answer every
            // question with the masked name.
            stub.UNMASKED_VENDOR_WEBGL = 37445;
            stub.UNMASKED_RENDERER_WEBGL = 37446;
          }
          ['getParameter', 'getSupportedExtensions'].forEach(function (m) {
            if (typeof stub[m] !== 'function') {
              stub[m] = hide(function () { return null; }, m);
            }
          });
          return stub;
        }
        return native.call(this, name);
      }, 'getExtension');
    }

    // The two contexts are separate interfaces in Blink -- a WebGL 2
    // context does not inherit from the WebGL 1 one -- so the methods
    // have to be replaced on both prototypes.  A page that asks for
    // the graphics card asks the context it has, and it usually asks
    // for the WebGL 2 one.
    WebGLRenderingContext.prototype.getParameter = makeGetParameter(nativeGetParameter);
    WebGLRenderingContext.prototype.getExtension = makeGetExtension(nativeGetExtension);

    if (typeof WebGL2RenderingContext !== 'undefined') {
      WebGL2RenderingContext.prototype.getParameter = makeGetParameter(
        WebGL2RenderingContext.prototype.getParameter);
      WebGL2RenderingContext.prototype.getExtension = makeGetExtension(
        WebGL2RenderingContext.prototype.getExtension);
    }

    // The readback timing.  ``readPixels`` copies the framebuffer into
    // a buffer the caller owns, and the time it takes depends on the
    // bandwidth of the memory behind the graphics card.  Two machines
    // with the same card therefore report the same duration, and a
    // site that measures it groups them.
    //
    // The delay is a constant of the profile: two readbacks of the
    // same context take the same time, which is what a real card
    // does.  It is applied *after* the copy, because the copy is the
    // part whose cost depends on the hardware -- delaying before it
    // would move the copy itself later without changing what the
    // page measures.
    var readPixelsDelay = timingDelay(
      (P.canvas_2d && P.canvas_2d.timing) || null,
      'read_pixels_delay_ms'
    );

    function makeReadPixels(native) {
      return hide(function () {
        var result = native.apply(this, arguments);
        burn(readPixelsDelay);
        return result;
      }, 'readPixels');
    }

    try {
      WebGLRenderingContext.prototype.readPixels = makeReadPixels(
        WebGLRenderingContext.prototype.readPixels);
      if (typeof WebGL2RenderingContext !== 'undefined') {
        WebGL2RenderingContext.prototype.readPixels = makeReadPixels(
          WebGL2RenderingContext.prototype.readPixels);
      }
    } catch (e) { /* a locked-down context stays stock */ }
  }

  // -- the WebGPU ---------------------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.gpu && P.webgpu && !nativeOwns('webgpu')) {
    var gpu = P.webgpu;
    if (gpu.enabled) {
      try {
        navigator.gpu.requestAdapter = hide(function () {
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
        }, 'requestAdapter');
      } catch (e) { /* a locked-down GPU object stays stock */ }
    } else {
      // The profile does not carry WebGPU, and a browser that does not
      // carry it answers ``requestAdapter`` with nothing.  Left alone,
      // the host's own adapter would answer -- and a page that asks
      // for the adapter would read the graphics card of the machine
      // the browser runs on, which is not the device the profile
      // describes.
      try {
        navigator.gpu.requestAdapter = hide(function () {
          return Promise.resolve(null);
        }, 'requestAdapter');
      } catch (e) { /* a locked-down GPU object stays stock */ }
    }
  }

  // -- the permissions -----------------------------------------------------------------
  if (typeof navigator !== 'undefined' && navigator.permissions && P.permissions) {
    var policies = P.permissions || {};
    var nativeQuery = navigator.permissions.query;

    // The names the Permissions API accepts.  A name that is not in
    // the list is not answered with a state: the browser rejects the
    // call with a TypeError, and a script that tries a name the browser
    // does not know and gets an answer instead is being told something
    // no real browser would tell it.
    //
    // The names are held in an array rather than an object because
    // several of them carry a hyphen, and a hyphen is not a legal
    // character in an unquoted object key -- which is a syntax error
    // that only shows up when the script is actually run.
    var KNOWN_PERMISSIONS = [
      'accelerometer',
      'ambient-light-sensor',
      'background-fetch',
      'background-sync',
      'camera',
      'clipboard-read',
      'clipboard-write',
      'geolocation',
      'gyroscope',
      'magnetometer',
      'microphone',
      'midi',
      'nfc',
      'notifications',
      'payment-handler',
      'periodic-background-sync',
      'persistent-storage',
      'push',
      'sensors',
      'storage-access',
      'window-management'
    ];
    var isKnownPermission = function (name) {
      for (var i = 0; i < KNOWN_PERMISSIONS.length; i++) {
        if (KNOWN_PERMISSIONS[i] === name) { return true; }
      }
      return false;
    };

    var makePermissionStatus = function (name, state) {
      // A real PermissionStatus is a PermissionStatus instance, and a
      // script that checks the prototype finds out.  Building one from
      // the real constructor is the only way to be one.
      var status = null;
      try {
        if (typeof PermissionStatus === 'function') {
          status = new PermissionStatus();
        }
      } catch (e) { /* the constructor is not constructible here */ }

      var target = status || {};
      try {
        Object.defineProperty(target, 'name', {
          value: name, enumerable: true, configurable: true
        });
        Object.defineProperty(target, 'state', {
          value: state, enumerable: true, configurable: true
        });
        if (typeof EventTarget === 'function' && !(target instanceof EventTarget)) {
          // The listener methods have to exist; the object does not
          // have to be an EventTarget for a script to use them.
          target.addEventListener = function () {};
          target.removeEventListener = function () {};
          target.dispatchEvent = function () { return false; };
        }
      } catch (e) { /* a frozen prototype stays stock */ }
      return target;
    };

    navigator.permissions.query = hide(function (descriptor) {
      var name = descriptor && descriptor.name;
      if (name && Object.prototype.hasOwnProperty.call(policies, name)) {
        return Promise.resolve(makePermissionStatus(name, policies[name]));
      }
      if (name && !isKnownPermission(name)) {
        // An unknown name is a programming error, and the browser says
        // so.  A profile that answers every question is a profile that
        // never made a mistake.
        return Promise.reject(new TypeError(
          "Failed to read the 'permissions' property from 'Navigator': " +
          "the permission name '" + String(name) + "' is not supported."
        ));
      }
      return nativeQuery.call(this, descriptor);
    }, 'query');
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
      var rawVoices = P.speech_voices;

      // A real voice is a SpeechSynthesisVoice instance, and a script
      // that checks the prototype finds out.  The five attributes are
      // readonly properties on that prototype in Blink
      // (speech_synthesis_voice.idl), so they are defined as own
      // non-writable data properties here rather than as plain fields.
      var makeVoice = function (spec) {
        var voice = null;
        try {
          if (typeof SpeechSynthesisVoice === 'function') {
            voice = new SpeechSynthesisVoice();
          }
        } catch (e) { /* the constructor is not constructible here */ }

        var target = voice || {};
        var define = function (name, value) {
          try {
            Object.defineProperty(target, name, {
              value: value,
              enumerable: true,
              writable: false,
              configurable: true
            });
          } catch (e) {
            try { target[name] = value; } catch (inner) { /* frozen */ }
          }
        };

        // The attribute names are the ones of the Web Speech API:
        // ``voiceURI``, ``name``, ``lang``, ``localService`` and
        // ``default``.
        define('voiceURI', spec.voice_uri);
        define('name', spec.name);
        define('lang', spec.lang);
        define('localService', !!spec.local_service);
        define('default', !!spec.default);
        return target;
      };

      var voices = [];
      for (var i = 0; i < rawVoices.length; i++) {
        voices.push(makeVoice(rawVoices[i]));
      }

      var proto = Object.getPrototypeOf(speechSynthesis);
      Object.defineProperty(proto, 'getVoices', {
        value: hide(function () { return voices; }, 'getVoices'),
        configurable: true, enumerable: false
      });

      // Chromium fires ``voiceschanged`` once the voice list is known.
      // ``SpeechSynthesis::VoicesDidChange`` dispatches the event
      // unconditionally when the list arrives, and it does not wait for
      // a handler to be installed -- a page that installs its handler
      // later still receives the event, because the dispatch is queued
      // behind the script that installs it.
      //
      // The event is therefore queued rather than fired inline, so that
      // the page has had its turn to install the handler.
      try {
        setTimeout(function () {
          try {
            if (typeof speechSynthesis.dispatchEvent === 'function' &&
                typeof Event === 'function') {
              speechSynthesis.dispatchEvent(new Event('voiceschanged'));
            }
          } catch (e) { /* the event cannot be constructed here */ }
        }, 0);
      } catch (e) { /* stays stock */ }
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
      }, 'getGamepads');
    } catch (e) { /* stays stock */ }
  }

  // -- the fonts ----------------------------------------------------------------------------
  if (P.fonts && P.fonts.installed && !nativeOwns('fonts')
      && typeof document !== 'undefined' && document.fonts) {
    try {
      var installed = P.fonts.installed;
      var nativeCheck = document.fonts.check.bind(document.fonts);
      // The font timing shield.  A font probe costs more when the
      // family is really on the device -- the engine has to set it up
      // -- than when it is not, and the substitution is nearly free.
      // A site that times the probe for a family it knows is absent
      // therefore learns that the family is absent, and a site that
      // times twenty probes recovers the whole font configuration
      // without ever reading the installed list.
      //
      // The shield makes the timing *independent of the answer*: the
      // delay is the same whether the family is there or not.  It is
      // a constant of the profile, so the probe costs the same every
      // time it is run, and two profiles do not cost the same.
      var fontProbeDelay = timingDelay(
        (P.fonts && P.fonts.timing) || null,
        'delay_ms'
      );

      document.fonts.check = hide(function (font) {
        // ``font`` is a CSS font shorthand, e.g. ``"12px Roboto"``.
        var family = String(font)
          .replace(/^.*?(\d+(?:\.\d+)?(?:px|pt|em|rem|%)\s+)/, '')
          .trim().replace(/^["']|["']$/g, '');
        // The delay is applied on *both* paths.  A shield that only
        // delayed the answer for an installed family would make the
        // two cases distinguishable by their very duration, which
        // is the opposite of what it is for.
        var present = !!(family && installed.indexOf(family) !== -1);
        var result = present ? true : nativeCheck(font);
        burn(fontProbeDelay);
        return result;
      }, 'check');
    } catch (e) { /* stays stock */ }
  }

  // -- the sensors ----------------------------------------------------------------------------
  if (P.sensors && P.sensors.enabled && !nativeOwns('sensors')
      && typeof DeviceMotionEvent !== 'undefined') {
    try {
      var sensors = P.sensors;
      var nativeMotion = DeviceMotionEvent.prototype;
      var accel = sensors.accelerometer || { x: 0, y: 0, z: 0 };
      var gyro = sensors.gyroscope || { x: 0, y: 0, z: 0 };
      var lin = sensors.linear_acceleration || { x: 0, y: 0, z: 0 };
      var grav = sensors.gravity || { x: 0, y: 0, z: 9.81 };
      var abs = sensors.absolute_orientation || {};
      var rel = sensors.relative_orientation || {};

      // The interval the profile carries.  A motion event says how
      // often it was sampled, and a page that reads the interval and
      // divides it into a frequency is reading the sensor's rate.
      var SAMPLE_INTERVAL = Math.round(1000 / (sensors.sampling_hz || 60));

      function motionEvent(init) {
        return new DeviceMotionEvent('devicemotion', init);
      }

      function buildMotion(includeLinear, includeGravity) {
        var init = {
          acceleration: includeLinear ? { x: lin.x, y: lin.y, z: lin.z } : null,
          accelerationIncludingGravity: includeGravity
            ? { x: accel.x, y: accel.y, z: accel.z } : null,
          rotationRate: { x: gyro.x, y: gyro.y, z: gyro.z },
          interval: SAMPLE_INTERVAL
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

      function buildOrientation() {
        var init = {
          absolute: abs.absolute !== undefined ? abs.absolute : true,
          alpha: abs.alpha !== undefined ? abs.alpha : null,
          beta: abs.beta !== undefined ? abs.beta : null,
          gamma: abs.gamma !== undefined ? abs.gamma : null
        };
        try {
          return new DeviceOrientationEvent('deviceorientation', init);
        } catch (e) {
          return init;
        }
      }

      // The events have to be *delivered*, not merely constructed: a
      // page that adds a listener for ``devicemotion`` and waits for
      // it must receive one, and the one it receives has to carry the
      // profile's readings and the profile's interval.  A browser
      // that has no sensor hardware never delivers the event at all,
      // and a page that waits for it would hang -- which is itself a
      // difference between the profile and the host.
      var motionListeners = [];
      var orientationListeners = [];

      function startSensors() {
        if (motionListeners.length) {
          setInterval(function () {
            var event = buildMotion(true, true);
            motionListeners.forEach(function (fn) {
              try { fn(event); } catch (e) { /* a listener that throws */ }
            });
          }, SAMPLE_INTERVAL);
        }
        if (orientationListeners.length) {
          setInterval(function () {
            var event = buildOrientation();
            orientationListeners.forEach(function (fn) {
              try { fn(event); } catch (e) { /* a listener that throws */ }
            });
          }, SAMPLE_INTERVAL);
        }
      }

      try {
        nativeMotion.addEventListener = nativeMotion.addEventListener
          || function () {};
        var nativeAddEventListener = window.addEventListener;
        window.addEventListener = hide(function (type, listener, options) {
          if (type === 'devicemotion') {
            if (motionListeners.indexOf(listener) === -1) {
              motionListeners.push(listener);
              startSensors();
            }
            return undefined;
          }
          if (type === 'deviceorientation') {
            if (orientationListeners.indexOf(listener) === -1) {
              orientationListeners.push(listener);
              startSensors();
            }
            return undefined;
          }
          return nativeAddEventListener.call(this, type, listener, options);
        }, 'addEventListener');
      } catch (e) { /* a locked-down window stays stock */ }
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
    }, 'matchMedia');
  }

  // -- the geolocation ----------------------------------------------------------
  // The position of the owner.
  //
  // The profile states a province and a fix inside it, and the fix has
  // to be *readable*: a site that asks for the position and is given
  // nothing knows that the browser did not answer, and a browser that
  // answers a permission prompt with an error is a browser that has
  // something to hide.  The stock runtime reports the machine's own
  // position -- which is the one thing this profile must never give
  // away -- or refuses outright.
  //
  // The coordinates are handed out with the accuracy the profile
  // declares, and the timestamp is moved by the jitter the profile
  // carries, because a fix whose timestamp is exactly "now" while its
  // accuracy is twenty kilometres is a fix that came from a database
  // rather than from a chip.
  //
  // The surface is *installed*, not merely replaced.  The browser only
  // exposes ``navigator.geolocation`` in a secure context, so on a
  // page served over ``file://`` -- and on a plain ``http://`` page --
  // the property is not there at all.  A profile that waits for it
  // answers nothing on those pages, which is worse than answering
  // with the wrong position: a page that asks and gets nothing knows
  // the difference between a browser that has no fix and a browser
  // that will not say.
  if (P.geolocation && P.geolocation.mode === 'allow_custom' &&
      typeof navigator !== 'undefined') {
    try {
      var geo = P.geolocation;
      var nativeGeo = navigator.geolocation;
      var geoJitter = geo.timestamp_jitter_ms || 0;
      // The position and the coordinates.
      //
      // They are written as *constructor functions* rather than as
      // object literals.  A literal has no prototype, so
      // ``new GeoCoordinates({...})`` throws "is not a constructor" --
      // and the throw happens inside the callback the page handed
      // over, which means the page waits for a position that never
      // arrives.  A page that asks and gets nothing knows the
      // difference between a browser that has no fix and a browser
      // that will not say.
      var GeoPosition = window.Position || function Position(coords, timestamp) {
        this.coords = coords;
        this.timestamp = timestamp;
      };
      var GeoCoordinates = window.Coordinates || function Coordinates(values) {
        for (var key in values) {
          if (Object.prototype.hasOwnProperty.call(values, key)) {
            this[key] = values[key];
          }
        }
      };
      var buildPosition = function () {
        var coords = new GeoCoordinates({
          latitude: geo.latitude,
          longitude: geo.longitude,
          accuracy: geo.accuracy,
          altitude: geo.altitude,
          altitudeAccuracy: geo.altitude_accuracy,
          heading: geo.heading,
          speed: geo.speed
        });
        var stamp = Date.now();
        if (geoJitter) {
          // The jitter moves the stamp *back* only.  A fix cannot be
          // stamped in the future, and a site that reads a timestamp
          // ahead of the clock knows the position was made up.
          stamp -= Math.floor(Math.random() * geoJitter);
        }
        return new GeoPosition(coords, stamp);
      };
      var geoProxy = {
        getCurrentPosition: hide(function (success, error, options) {
          // The permission is the browser's business, not the
          // runtime's: the callback runs with the profile's own fix,
          // and the error callback only fires when the profile has no
          // fix to give.
          try {
            if (typeof success === 'function') {
              success(buildPosition());
            }
          } catch (e) { /* a throwing callback is the page's problem */ }
        }, 'getCurrentPosition'),
        watchPosition: hide(function (success, error, options) {
          var id = 1;
          try {
            if (typeof success === 'function') {
              success(buildPosition());
            }
          } catch (e) { /* a throwing callback is the page's problem */ }
          return id;
        }, 'watchPosition'),
        clearWatch: hide(function () {}, 'clearWatch')
      };
      try {
        Object.defineProperty(navigator, 'geolocation', {
          get: hide(function () { return geoProxy; }, 'get geolocation'),
          configurable: true, enumerable: true
        });
      } catch (e) {
        navigator.geolocation = geoProxy;
      }
    } catch (e) { /* a locked-down navigator stays stock */ }
  }

  // -- the floating-point precision --------------------------------------------
  // ``Math.acos(0.5)`` and friends are the classic way to tell a real
  // browser from a DOM that was faked in JavaScript: the last bits of
  // the double follow the C library of the platform.  The runtime
  // nudges the result by at most ``epsilon`` so that the profile is
  // consistent with the device it claims to be, and never more.
  if (typeof Math !== 'undefined' && Math && P.math_precision && P.math_precision.enabled) {
    try {
      var mp = P.math_precision;
      var eps = typeof mp.epsilon === 'number' ? mp.epsilon : 1e-16;
      // The canonical probe points are the ones a fingerprinting script
      // uses.  Each function has its own set; the circular ones are
      // probed at the ends of their range as well.
      var probes = {
        acos: [0.5, -0.5, 1, -1, 0],
        asin: [0.5, -0.5, 1, -1, 0],
        sinh: [0.5, -0.5, 1, -1, 0],
        cosh: [0.5, -0.5, 1, -1, 0],
        tanh: [0.5, -0.5, 1, -1, 0],
        atan: [0.5, -0.5, 1, -1, 0]
      };
      ['acos', 'asin', 'sinh', 'cosh', 'tanh', 'atan'].forEach(function (fn) {
        if (typeof Math[fn] !== 'function') { return; }
        var reference = mp[fn];
        if (typeof reference !== 'number') { return; }
        var nativeFn = Math[fn];
        var points = probes[fn] || [0.5];
        Math[fn] = hide(function (x) {
          var value = nativeFn.call(Math, x);
          // Only the canonical probe points are corrected.  Everywhere
          // else the host's own result is the right answer.
          if (points.indexOf(x) !== -1) {
            // The profile value already carries the per-unit drift, so
            // it is returned as it stands.  The epsilon is a guard: if
            // the host's own value is nowhere near the profile's, then
            // the host is a different engine and its answer is the
            // honest one.
            if (Math.abs(reference - value) <= eps) { return reference; }
          }
          return value;
        }, fn);
      });
    } catch (e) { /* stays stock */ }
  }

  // -- the heap size limit -------------------------------------------------------
  if (typeof performance !== 'undefined' && performance
      && performance.memory && P.heap_size_limit) {
    try {
      var hl = P.heap_size_limit;
      // The limit is an accessor, and in Blink it lives on the
      // prototype of the memory object rather than on the object
      // itself.  Both places are looked at, and the replacement is
      // installed on the object that actually holds the accessor --
      // installing it on the instance when the accessor is on the
      // prototype would shadow it, and installing it on the prototype
      // when the accessor is on the instance would miss it.
      var memoryProto = Object.getPrototypeOf(performance.memory);
      var heapDescriptor = Object.getOwnPropertyDescriptor(
        performance.memory, 'jsHeapSizeLimit');
      var heapTarget = performance.memory;
      if (!heapDescriptor && memoryProto) {
        heapDescriptor = Object.getOwnPropertyDescriptor(
          memoryProto, 'jsHeapSizeLimit');
        heapTarget = memoryProto;
      }
      nativeLike(
        heapTarget,
        'jsHeapSizeLimit',
        function () { return hl.js_heap_size_limit; },
        heapDescriptor
      );
    } catch (e) { /* stays stock */ }
  }

  // -- the media decode support ----------------------------------------------------
  if (typeof MediaRecorder !== 'undefined' && MediaRecorder
      && MediaRecorder.isTypeSupported && P.media_decode) {
    try {
      var md = P.media_decode;
      var nativeIsTypeSupported = MediaRecorder.isTypeSupported;
      var supportedList = md.supported || [];
      MediaRecorder.isTypeSupported = hide(function (type) {
        if (typeof type === 'string' && supportedList.indexOf(type) !== -1) { return true; }
        return nativeIsTypeSupported.call(MediaRecorder, type);
      }, 'isTypeSupported');
    } catch (e) { /* stays stock */ }
  }

  // -- the user agent data brands ----------------------------------------
  if (typeof navigator !== 'undefined' && 'userAgentData' in navigator && P.user_agent_data && !nativeOwns('user_agent_data')) {
    var uad = navigator.userAgentData;
    try {
      Object.defineProperty(uad, 'brands', {
        get: hide(function () { return P.user_agent_data.brands; }, 'get brands'),
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
        get: hide(function () { return lang; }, 'get language'),
        configurable: true, enumerable: true
      });
      Object.defineProperty(Navigator.prototype, 'languages', {
        get: hide(function () { return langs; }, 'get languages'),
        configurable: true, enumerable: true
      });
    } catch (e) { /* stays stock */ }
  }

  // -- the chrome object -------------------------------------------------
  //
  // Every Chrome -- on a desktop and on a handset -- carries a
  // ``navigator.chrome`` object with an ``app`` and a ``csi`` in it.
  // A browser that reports the mobile user agent but has no
  // ``navigator.chrome`` is a browser that only pretends to be one:
  // the object is one of the oldest tells there is, and the check
  // costs a page nothing.
  //
  // The object is built here rather than taken from the host: the
  // host is a desktop, and its own object would carry the desktop's
  // values.  The shape is the one Chrome has on a handset.
  //
  // The object is only built when the native core does not already
  // own the surface.  A branded Chrome has the object in the process,
  // and a JavaScript copy on top of that would be a second value for
  // the same property -- which is a thing a page can see.
  if (typeof navigator !== 'undefined' && !nativeOwns('chrome')) {
    try {
      var chromeObject = {
        app: {
          isInstalled: false,
          InstallState: {
            DISABLED: 'disabled',
            INSTALLED: 'installed',
            NOT_INSTALLED: 'not_installed'
          },
          RunningState: {
            CANNOT_RUN: 'cannot_run',
            READY_TO_RUN: 'ready_to_run',
            RUNNING: 'running'
          }
        },
        csi: function csi() {
          var now = Date.now();
          return {
            onloadT: now,
            startE: now,
            pageT: now / 1000,
            tran: 15
          };
        },
        loadTimes: function loadTimes() {
          var now = Date.now();
          return {
            requestTime: now / 1000,
            startLoadTime: now / 1000,
            commitLoadTime: now / 1000,
            finishDocumentLoadTime: now / 1000,
            finishLoadTime: now / 1000,
            firstPaintTime: now / 1000,
            firstPaintAfterLoadTime: 0,
            navigationType: 'Other',
            wasFetchedViaSpdy: true,
            wasNpnNegotiated: true,
            npnNegotiatedProtocol: 'h2',
            wasAlternateProtocolAvailable: false,
            connectionInfo: 'h2'
          };
        }
      };
      // The object is not enumerable: a page that lists the own
      // properties of the navigator finds it the way Chrome has it,
      // among the own properties of the prototype.
      Object.defineProperty(navigator, 'chrome', {
        value: chromeObject,
        writable: true,
        enumerable: true,
        configurable: true
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
    math_precision: dict[str, Any] = field(default_factory=dict)
    heap_size_limit: dict[str, Any] = field(default_factory=dict)
    media_decode: dict[str, Any] = field(default_factory=dict)


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
            # ``webdriver`` is false on a real handset.  A headless
            # browser reports it as true, and a site that reads it
            # learns that the browser is automated -- which is true of
            # every profile of the fleet at once, because they all run
            # through the same runtime.  The value has to be carried
            # so that the runtime can say otherwise.
            "webdriver": nav.webdriver,
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
        # The full version of the browser the profile claims to be.
        #
        # It is *not* read out of the user agent.  Chromium reduced the
        # user agent to the frozen ``Chrome/<major>.0.0.0`` form in
        # major 110, but the full version it reports through
        # ``getHighEntropyValues(['uaFullVersion'])`` still carries the
        # real build.  Reading it out of the user agent therefore
        # reported ``121.0.0.0`` for every profile on major 121, and a
        # site that reads the high-entropy hint grouped the whole
        # fleet on it.
        ua_full_version = config.browser_version.full_version
        if not ua_full_version:
            # The full version of the browser the profile claims to be.
            # It is read out of the user agent, because that is where the
            # version is written: "Chrome/116.0.5845.188".
            agent = config.navigator.user_agent
            marker = "Chrome/"
            index = agent.find(marker)
            if index != -1:
                rest = agent[index + len(marker) :]
                end = rest.find(" ")
                ua_full_version = rest[:end] if end != -1 else rest
        self._payload.user_agent_data = {
            "brands": [{"brand": b.brand, "version": b.version} for b in uad.brands],
            "fullVersionList": [
                {"brand": b.brand, "version": b.version} for b in uad.full_version_list
            ],
            "uaFullVersion": ua_full_version,
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
        # The locale travels with the time zone, because the two are
        # read together: ``Intl.DateTimeFormat().resolvedOptions()``
        # answers with the zone, the locale and the hour cycle in one
        # object, and a formatter built without a locale resolves to
        # the *host's* locale -- which on a build machine is the same
        # for every profile of the fleet.
        locale = config.locale
        self._payload.timezone = {
            "iana_name": tz.iana_name,
            "offset_minutes": tz.offset_minutes,
            "dst_active": tz.dst_active,
            "abbreviation": tz.abbreviation,
            "intl_locale": locale.intl_locale,
            "numbering_system": locale.numbering_system,
            "calendar": locale.calendar,
            "hour_cycle": locale.hour_cycle,
            "currency": locale.currency,
        }

        # The locale, on its own.
        #
        # The time zone carries the locale because the two are read
        # together in one object.  But the locale is read on its own
        # too -- ``navigator.language`` is the primary one and
        # ``navigator.languages`` is the whole list -- and the primary
        # one has to be the country's own, or a page that compares
        # ``navigator.language`` with the language of the content it
        # serves finds a profile that claims Vietnamese and behaves
        # like something else.
        self._payload.locale = {
            "primary": locale.primary,
            "intl_locale": locale.intl_locale,
            "languages": list(config.navigator.languages),
            "numbering_system": locale.numbering_system,
            "calendar": locale.calendar,
            "hour_cycle": locale.hour_cycle,
            "currency": locale.currency,
            "time_zone": tz.iana_name,
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
            "timing": cv.timing.model_dump(),
        }

    # -- the audio -------------------------------------------------------------
    def _fill_audio(self, config: ProfileConfig) -> None:
        au = config.audio
        self._payload.audio = {
            "mode": au.mode,
            "salt": au.salt,
            "sample_rate": au.sample_rate,
            "base_latency": au.base_latency,
            "output_latency": au.output_latency,
            "channel_count": au.channel_count,
            "max_channel_count": au.max_channel_count,
            "noise_strength": au.noise_strength,
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
            # The variant of the parameter table this unit reports, and
            # the salt that identifies the unit within the model.  The
            # limits themselves are already in ``parameters``; the
            # salt is what the native core mixes into the values it
            # hands out, so that two units of one model do not report
            # an identical table.
            "parameter_set": gl.parameter_set.model_dump(),
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
        policies = {
            name: state.value if hasattr(state, "value") else str(state)
            for name, state in config.permissions.policies.items()
        }
        # The geolocation permission follows the geolocation mode.
        #
        # A profile that carries its own coordinates answers the
        # permission with "granted": the browser has been told where
        # the user is, so there is nothing left to ask.  A profile that
        # prompts for a position answers "prompt", because the browser
        # has not been told anything and the page has to ask the user.
        #
        # The two are read together -- a page that gets "granted" from
        # the permissions API and then no position from the geolocation
        # call knows the profile is a fabrication, and the permission
        # is the cheaper half of the pair for a site to check.
        if config.geolocation.mode == "allow_custom":
            policies["geolocation"] = "granted"
        self._payload.permissions = policies

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
            "timing": fonts.timing.model_dump(),
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
            "timestamp_jitter_ms": geo.timestamp_jitter_ms,
            # The place the fix describes.
            #
            # The coordinates alone say where the profile is standing
            # to within a few kilometres, which is not the same thing
            # as saying which province it is standing in.  A fleet of a
            # hundred million profiles that all report one province is
            # one farm, and the names are what tell the fleet apart.
            "region": geo.region,
            "city": geo.city,
            "country_code": geo.country_code,
        }

    # -- the floating-point precision ------------------------------------------------------
    def _fill_math_precision(self, config: ProfileConfig) -> None:
        mp = config.math_precision
        self._payload.math_precision = {
            "enabled": mp.enabled,
            "acos": mp.acos,
            "asin": mp.asin,
            "sinh": mp.sinh,
            "cosh": mp.cosh,
            "tanh": mp.tanh,
            "atan": mp.atan,
            "epsilon": mp.epsilon,
            "salt": mp.salt,
        }

    # -- the heap size limit ------------------------------------------------------------------
    def _fill_heap_size_limit(self, config: ProfileConfig) -> None:
        hl = config.heap_size_limit
        self._payload.heap_size_limit = {
            "enabled": hl.enabled,
            "js_heap_size_limit": hl.js_heap_size_limit,
            "memory_tier_limit": hl.memory_tier_limit,
            "salt": hl.salt,
        }

    # -- the media decode support --------------------------------------------------------------
    def _fill_media_decode(self, config: ProfileConfig) -> None:
        md = config.media_decode
        self._payload.media_decode = {
            "enabled": md.enabled,
            "supported": list(md.supported),
            "salt": md.salt,
        }

    # -- the entry point ---------------------------------------------------------------
    def build(self, config: ProfileConfig) -> str:
        """The complete script for one profile."""

        payload = self._build_payload(config)
        return (
            "window.__NOVA_PROFILE__="
            + payload
            + ";\n"
            + RUNTIME_JS
        )

    def build_payload_only(self, config: ProfileConfig) -> str:
        """The profile payload, without the runtime that installs it.

        The payload is what the runtime reads, and what the
        verification harness reads to know what the browser is supposed
        to report.  A harness that runs without the runtime still needs
        the payload -- otherwise it has nothing to compare against --
        but it must not have the runtime, or the browser would be
        reporting the profile's values rather than its own.
        """

        return "window.__NOVA_PROFILE__=" + self._build_payload(config) + ";\n"

    def _build_payload(self, config: ProfileConfig) -> str:
        """The profile payload, as the JSON the runtime reads."""

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
        self._fill_math_precision(config)
        self._fill_heap_size_limit(config)
        self._fill_media_decode(config)

        return json.dumps(
            self._payload.__dict__, ensure_ascii=False, separators=(",", ":"), default=str
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
