"""The exhaustive surface audit.

This is the harness that answers the question "what can a website still
see that would tell two of our profiles apart, or tell that the profile
is not real?".  It launches a profile in the browser, reads *every*
surface a fingerprinting script is known to read, and compares each one
against what the profile claims.

The difference from the ordinary probe is that this harness is not
interested in the surfaces the profile already gets right.  It is
interested in the ones it gets *wrong*, because those are the ones that
either expose the host or make two profiles look alike.

Run it directly:

    python tools/audit_surfaces.py --profiles 3 --seed abc123

The audit prints a table of every surface, the value the profile
claims, the value the browser reported, and the verdict.  At the end it
prints the surfaces that leaked -- the ones the browser reported from
the host instead of from the profile -- and the surfaces that collide,
the ones two profiles agree on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE = REPO_ROOT / "novaprofile" / "control_plane"
if str(CONTROL_PLANE) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE))

import websockets  # noqa: E402

from novaprofile.core.generator import ProfileGenerator  # noqa: E402
from novaprofile.core.seed import new_master_seed  # noqa: E402
from novaprofile.launcher.isolation import ProfileIsolation  # noqa: E402
from novaprofile.launcher.launcher import BrowserLauncher  # noqa: E402


#: The script that reads every surface.
#
#: It is deliberately exhaustive rather than clever: the point is to
#: read everything a script could read, so that nothing is left to a
#: fingerprinting vendor's imagination.
AUDIT_SCRIPT = r"""
(() => {
  const out = {};
  const rec = (name, fn) => {
    try {
      const v = fn();
      out[name] = v === undefined ? null : v;
    } catch (e) {
      out[name] = { __error: String((e && e.message) || e) };
    }
  };

  // === navigator ===
  rec('navigator.userAgent', () => navigator.userAgent);
  rec('navigator.appVersion', () => navigator.appVersion);
  rec('navigator.appName', () => navigator.appName);
  rec('navigator.appCodeName', () => navigator.appCodeName);
  rec('navigator.vendor', () => navigator.vendor);
  rec('navigator.vendorSub', () => navigator.vendorSub);
  rec('navigator.product', () => navigator.product);
  rec('navigator.productSub', () => navigator.productSub);
  rec('navigator.platform', () => navigator.platform);
  rec('navigator.oscpu', () => navigator.oscpu);
  rec('navigator.hardwareConcurrency', () => navigator.hardwareConcurrency);
  rec('navigator.deviceMemory', () => navigator.deviceMemory);
  rec('navigator.maxTouchPoints', () => navigator.maxTouchPoints);
  rec('navigator.language', () => navigator.language);
  rec('navigator.languages', () => navigator.languages ? Array.from(navigator.languages) : null);
  rec('navigator.doNotTrack', () => navigator.doNotTrack);
  rec('navigator.globalPrivacyControl', () => navigator.globalPrivacyControl);
  rec('navigator.webdriver', () => navigator.webdriver);
  rec('navigator.pdfViewerEnabled', () => navigator.pdfViewerEnabled);
  rec('navigator.onLine', () => navigator.onLine);
  rec('navigator.javaEnabled', () => navigator.javaEnabled);
  rec('navigator.cookieEnabled', () => navigator.cookieEnabled);
  rec('navigator.plugins', () => navigator.plugins ? Array.from(navigator.plugins).map(p => p.name) : null);
  rec('navigator.mimeTypes', () => navigator.mimeTypes ? Array.from(navigator.mimeTypes).map(m => m.type) : null);
  rec('navigator.connection', () => navigator.connection ? {
    effectiveType: navigator.connection.effectiveType,
    rtt: navigator.connection.rtt,
    downlink: navigator.connection.downlink,
    saveData: navigator.connection.saveData,
    type: navigator.connection.type,
  } : null);
  rec('navigator.userAgentData.low', () => navigator.userAgentData ? {
    brands: Array.from(navigator.userAgentData.brands || []),
    mobile: navigator.userAgentData.mobile,
    platform: navigator.userAgentData.platform,
  } : null);

  // === the touch surface ===
  // This is the surface that separates a handset from a desktop.  A
  // profile that claims to be an Android phone and answers these with
  // the host's desktop answers is a desktop.
  rec('touch.ontouchstart', () => 'ontouchstart' in window);
  rec('touch.ontouchmove', () => 'ontouchmove' in window);
  rec('touch.ontouchend', () => 'ontouchend' in window);
  rec('touch.ontouchcancel', () => 'ontouchcancel' in window);
  rec('touch.TouchEvent', () => typeof window.TouchEvent);
  rec('touch.Touch', () => typeof window.Touch);
  rec('touch.PointerEvent', () => typeof window.PointerEvent);
  rec('touch.maxTouchPoints.ctor', () => window.navigator.maxTouchPoints);

  // === the pointer and hover media queries ===
  // These are what a script uses to tell a phone from a laptop, and
  // they are answered by the *host* in a stock browser.
  const mq = (q) => { try { return window.matchMedia(q).matches; } catch (e) { return 'error'; } };
  rec('media.pointer.coarse', () => mq('(pointer: coarse)'));
  rec('media.pointer.fine', () => mq('(pointer: fine)'));
  rec('media.pointer.none', () => mq('(pointer: none)'));
  rec('media.any-pointer.coarse', () => mq('(any-pointer: coarse)'));
  rec('media.any-pointer.fine', () => mq('(any-pointer: fine)'));
  rec('media.hover.hover', () => mq('(hover: hover)'));
  rec('media.hover.none', () => mq('(hover: none)'));
  rec('media.any-hover.hover', () => mq('(any-hover: hover)'));
  rec('media.hover-capable', () => mq('(hover: hover) and (pointer: coarse)'));
  rec('media.hover-none-coarse', () => mq('(hover: none) and (pointer: coarse)'));
  rec('media.display-mode.standalone', () => mq('(display-mode: standalone)'));
  rec('media.any-hover-none', () => mq('(any-hover: none)'));

  // === screen ===
  rec('screen.width', () => screen.width);
  rec('screen.height', () => screen.height);
  rec('screen.availWidth', () => screen.availWidth);
  rec('screen.availHeight', () => screen.availHeight);
  rec('screen.availLeft', () => screen.availLeft);
  rec('screen.availTop', () => screen.availTop);
  rec('screen.colorDepth', () => screen.colorDepth);
  rec('screen.pixelDepth', () => screen.pixelDepth);
  rec('screen.orientation.type', () => screen.orientation ? screen.orientation.type : null);
  rec('screen.orientation.angle', () => screen.orientation ? screen.orientation.angle : null);
  rec('window.devicePixelRatio', () => window.devicePixelRatio);
  rec('window.innerWidth', () => window.innerWidth);
  rec('window.innerHeight', () => window.innerHeight);
  rec('window.outerWidth', () => window.outerWidth);
  rec('window.outerHeight', () => window.outerHeight);
  rec('window.screenX', () => window.screenX);
  rec('window.screenY', () => window.screenY);
  rec('window.devicePixelContentBoxSize', () => {
    const b = window.visualViewport;
    return b ? { w: b.width, h: b.height, scale: b.scale } : null;
  });

  // === the timezone and the locale ===
  rec('intl.timeZone', () => Intl.DateTimeFormat().resolvedOptions().timeZone);
  rec('intl.locale', () => Intl.DateTimeFormat().resolvedOptions().locale);
  rec('intl.calendar', () => Intl.DateTimeFormat().resolvedOptions().calendar);
  rec('intl.numberingSystem', () => Intl.DateTimeFormat().resolvedOptions().numberingSystem);
  rec('intl.hourCycle', () => Intl.DateTimeFormat().resolvedOptions().hourCycle);
  rec('intl.numberFormat.locale', () => Intl.NumberFormat().resolvedOptions().locale);
  rec('intl.collator.locale', () => Intl.Collator().resolvedOptions().locale);
  rec('date.getTimezoneOffset', () => new Date().getTimezoneOffset());
  rec('date.toString', () => new Date().toString().slice(0, 33));
  rec('intl.format.timeZone', () => {
    const f = new Intl.DateTimeFormat('en-US', { timeZoneName: 'long' });
    return f.format(new Date());
  });
  rec('intl.supportedLocales', () => Intl.DateTimeFormat.supportedLocalesOf(['vi-VN', 'en-US', 'ja-JP']).join(','));

  // === the graphics ===
  rec('webgl', () => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return null;
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const p = (n, f) => debug ? gl.getParameter(debug[n]) : gl.getParameter(f);
    return {
      vendor: p('UNMASKED_VENDOR_WEBGL', gl.VENDOR),
      renderer: p('UNMASKED_RENDERER_WEBGL', gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      maxCubeMapTextureSize: gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE),
      maxRenderbufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
      maxTextureImageUnits: gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS),
      maxVertexTextureImageUnits: gl.getParameter(gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS),
      maxCombinedTextureImageUnits: gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS),
      maxVertexAttribs: gl.getParameter(gl.MAX_VERTEX_ATTRIBS),
      maxVertexUniformVectors: gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS),
      maxFragmentUniformVectors: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS),
      maxVaryingVectors: gl.getParameter(gl.MAX_VARYING_VECTORS),
      maxSamples: gl.getParameter(gl.MAX_SAMPLES),
      maxColorAttachments: gl.getParameter(gl.MAX_COLOR_ATTACHMENTS),
      maxDrawBuffers: gl.getParameter(gl.MAX_DRAW_BUFFERS),
      maxViewportDims: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS) || []),
      maxTextureMaxAnisotropyExt: (() => {
        const ext = gl.getExtension('EXT_texture_filter_anisotropic');
        return ext ? gl.getParameter(ext.MAX_TEXTURE_MAX_ANISOTROPY_EXT) : null;
      })(),
      aliasedLineWidthRange: Array.from(gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE) || []),
      aliasedPointSizeRange: Array.from(gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) || []),
      extensions: (gl.getSupportedExtensions() || []).slice().sort(),
    };
  });
  rec('webgl2.isWebgl2', () => {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  });

  rec('canvas.hash', () => {
    const canvas = document.createElement('canvas');
    canvas.width = 240; canvas.height = 60;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.textBaseline = 'top';
    ctx.font = '14px "Arial"';
    ctx.fillStyle = '#f60'; ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = '#069'; ctx.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)'; ctx.fillText('Cwm fjordbank glyphs vext quiz', 4, 17);
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let h = 0;
    for (let i = 0; i < d.length; i++) h = (h * 31 + d[i]) | 0;
    return { hash: h, text: ctx.measureText('Cwm fjordbank glyphs vext quiz').width };
  });
  rec('canvas2d.measureText', () => {
    const c = document.createElement('canvas').getContext('2d');
    if (!c) return null;
    c.font = '11pt Arial';
    return {
      a: c.measureText('mmmmmmmmmmlli').width,
      b: c.measureText('mmmmmmmmmmlli').width,
      c: c.measureText('WWW').width,
    };
  });
  rec('offscreen.canvas', () => {
    if (!window.OffscreenCanvas) return null;
    const c = new OffscreenCanvas(240, 60);
    const ctx = c.getContext('2d');
    if (!ctx) return null;
    ctx.font = '14px "Arial"';
    ctx.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
    const d = ctx.getImageData(0, 0, 240, 60).data;
    let h = 0;
    for (let i = 0; i < d.length; i++) h = (h * 31 + d[i]) | 0;
    return h;
  });

  // === the audio ===
  rec('audio', () => {
    const C = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!C) return null;
    try {
      const ctx = new C(1, 44100, 44100);
      const osc = ctx.createOscillator();
      osc.type = 'triangle'; osc.frequency.value = 10000;
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -50; comp.knee.value = 40; comp.ratio.value = 12;
      comp.attack.value = 0; comp.release.value = 0.25;
      osc.connect(comp); comp.connect(ctx.destination);
      osc.start(0);
      ctx.startRendering();
      return { sampleRate: ctx.sampleRate, length: ctx.length, channels: ctx.destination.channelCount };
    } catch (e) { return { __error: String(e) }; }
  });
  rec('audio.live', () => {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    try {
      const ctx = new C();
      return { sampleRate: ctx.sampleRate, baseLatency: ctx.baseLatency, outputLatency: ctx.outputLatency, state: ctx.state };
    } catch (e) { return { __error: String(e) }; }
  });

  // === the maths ===
  rec('math', () => ({
    acos: Math.acos(0.5), asin: Math.asin(0.5), sinh: Math.sinh(0.5),
    cosh: Math.cosh(0.5), tanh: Math.tanh(0.5), atan: Math.atan(0.5),
    exp: Math.exp(1), pow: Math.pow(Math.PI, -100),
  }));

  // === the heap and the timing ===
  rec('heap.jsHeapSizeLimit', () => performance.memory ? performance.memory.jsHeapSizeLimit : null);
  rec('heap.totalJSHeapSize', () => performance.memory ? performance.memory.totalJSHeapSize : null);
  rec('timing.now', () => typeof performance.now());
  rec('timing.timeOrigin', () => typeof performance.timeOrigin);
  rec('timing.resolution', () => performance.now() * 1000 % 1);

  // === the fonts ===
  rec('fonts.check', () => {
    const out = {};
    const list = ['Roboto', 'Noto Sans', 'Arial', 'Times New Roman', 'Courier New',
      'Helvetica', 'Segoe UI', 'Tahoma', 'Verdana', 'Georgia', 'Monaco', 'SimSun'];
    for (const f of list) { try { out[f] = document.fonts.check('16px "' + f + '"'); } catch (e) { out[f] = 'error'; } }
    return out;
  });
  rec('fonts.measure', () => {
    const c = document.createElement('canvas').getContext('2d');
    if (!c) return null;
    const out = {};
    for (const f of ['Roboto', 'Arial', 'monospace', 'sans-serif', 'serif']) {
      c.font = '72px "' + f + '"';
      out[f] = c.measureText('MMMMPppppWWWwwwwiiii').width;
    }
    return out;
  });

  // === the storage, the sensors and the media ===
  rec('storage.estimate', () => navigator.storage ? navigator.storage.estimate() : null);
  rec('sensors.deviceMotion', () => 'DeviceMotionEvent' in window);
  rec('sensors.deviceOrientation', () => 'DeviceOrientationEvent' in window);
  rec('sensors.orientation', () => 'DeviceOrientationEvent' in window);
  rec('media.canPlay.h264', () => document.createElement('video').canPlayType('video/mp4; codecs="avc1.42E01E"'));
  rec('media.canPlay.vp9', () => document.createElement('video').canPlayType('video/webm; codecs="vp9"'));
  rec('media.mediaRecorder', () => {
    try { return (window.MediaRecorder.isTypeSupported('video/webm;codecs=vp9') ? 'vp9' : '') + ',' + (window.MediaRecorder.isTypeSupported('video/mp4;codecs=avc1') ? 'avc1' : ''); }
    catch (e) { return 'error'; }
  });
  rec('permissions', () => navigator.permissions ? Promise.all([
    'notifications', 'geolocation', 'camera', 'microphone', 'clipboard-read', 'midi',
  ].map(n => navigator.permissions.query({ name: n }).then(s => [n, s.state], () => [n, 'unsupported']))) : null);

  rec('geolocation', () => new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    const timer = setTimeout(() => resolve({ timeout: true }), 4000);
    navigator.geolocation.getCurrentPosition(
      (pos) => { clearTimeout(timer); resolve({
        lat: pos.coords.latitude, lon: pos.coords.longitude,
        acc: pos.coords.accuracy, alt: pos.coords.altitude,
      }); },
      (err) => { clearTimeout(timer); resolve({ error: err.code }); },
      { timeout: 3500 }
    );
  }));

  rec('mediaDevices', () => navigator.mediaDevices
    ? navigator.mediaDevices.enumerateDevices().then(ds => ds.map(d => ({ kind: d.kind, label: d.label, groupId: d.groupId })))
    : null);

  rec('webrtc.candidates', () => new Promise((resolve) => {
    if (!window.RTCPeerConnection) { resolve(null); return; }
    try {
      const conn = new RTCPeerConnection({ iceServers: [] });
      const found = [];
      conn.onicecandidate = (e) => {
        if (e.candidate) found.push(e.candidate.candidate);
        else { resolve(found); conn.close(); }
      };
      conn.createDataChannel('probe');
      conn.createOffer().then(o => conn.setLocalDescription(o)).catch(() => resolve(found));
      setTimeout(() => resolve(found), 2500);
    } catch (e) { resolve('error'); }
  }));

  rec('webgpu', () => {
    if (!navigator.gpu) return null;
    return navigator.gpu.requestAdapter().then(a => {
      if (!a) return null;
      const i = a.info || {};
      return {
        vendor: i.vendor, architecture: i.architecture, device: i.device,
        description: i.description,
        features: Array.from(a.features || []).sort(),
        limits: a.limits ? Object.fromEntries([
          'maxTextureDimension2D', 'maxBindGroups', 'maxBufferSize',
          'maxComputeWorkgroupSizeX', 'maxStorageBufferBindingSize',
        ].map(k => [k, a.limits[k]])) : null,
      };
    }).catch(e => ({ __error: String(e) }));
  });

  rec('speechSynthesis.voices', () => new Promise((resolve) => {
    if (!window.speechSynthesis) { resolve(null); return; }
    const read = () => speechSynthesis.getVoices().map(v => v.name).sort();
    const now = read();
    if (now.length) { resolve(now); return; }
    const timer = setTimeout(() => resolve([]), 1500);
    speechSynthesis.onvoiceschanged = () => { clearTimeout(timer); resolve(read()); };
  }));

  rec('clientRects', () => {
    const el = document.createElement('div');
    el.style.cssText = 'position:absolute;width:280px;height:12px;left:17px;top:23px;';
    document.body.appendChild(el);
    const r = el.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(el);
    const rr = range.getBoundingClientRect();
    const out = { x: r.x, y: r.y, w: r.width, h: r.height, rx: rr.x, ry: rr.y };
    el.remove();
    return out;
  });

  rec('battery', () => navigator.getBattery ? navigator.getBattery().then(b => ({
    charging: b.charging, level: b.level, chargingTime: b.chargingTime, dischargingTime: b.dischargingTime,
  })).catch(() => null) : null);

  rec('connection.type', () => navigator.connection ? navigator.connection.type : null);
  rec('window.chrome', () => typeof window.chrome);
  rec('window.chrome.runtime', () => (window.chrome && window.chrome.runtime) ? true : false);
  rec('plugins.length', () => navigator.plugins ? navigator.plugins.length : null);
  rec('document.cookie', () => document.cookie);
  rec('history.length', () => history.length);
  rec('appVersion.alt', () => navigator.userAgentData ? navigator.userAgentData.getHighEntropyValues(['architecture','bitness','model','platformVersion','uaFullVersion','fullVersionList','wow64']).then(h => ({
    architecture: h.architecture, bitness: h.bitness, model: h.model,
    platformVersion: h.platformVersion, uaFullVersion: h.uaFullVersion,
    wow64: h.wow64,
    fullVersionList: Array.from(h.fullVersionList || []).map(b => b.brand + '/' + b.version),
  })) : null);

  // Wait for the asynchronous reads.
  return Promise.resolve(out).then(async (result) => {
    const waits = [];
    for (const key of Object.keys(result)) {
      const v = result[key];
      if (v && typeof v.then === 'function') {
        waits.push(v.then(r => { result[key] = r; }));
      }
    }
    await Promise.all(waits);
    return result;
  });
})
"""


#: The surfaces whose value must come from the profile and not from the
#: host.  A surface that reports the host's value is a leak.
#
#: The key is the name in the audit's output; the value is a short
#: description of what a leak means.
LEAK_SURFACES: dict[str, str] = {
    "navigator.userAgent": "the user agent carries the host's identity",
    "navigator.platform": "the platform is the host's",
    "navigator.hardwareConcurrency": "the core count is the host's",
    "navigator.deviceMemory": "the memory is the host's",
    "navigator.maxTouchPoints": "the touch count is the host's",
    "navigator.language": "the language is the host's",
    "navigator.languages": "the language list is the host's",
    "navigator.webdriver": "the automation flag is on",
    "navigator.connection": "the network report is the host's",
    "navigator.userAgentData.low": "the client hints are the host's",
    "navigator.userAgentData.high": "the high-entropy hints are the host's",
    "screen.width": "the panel width is the host's",
    "screen.height": "the panel height is the host's",
    "screen.colorDepth": "the colour depth is the host's",
    "window.devicePixelRatio": "the pixel ratio is the host's",
    "intl.timeZone": "the time zone is the host's",
    "intl.locale": "the locale is the host's",
    "date.getTimezoneOffset": "the clock offset is the host's",
    "webgl": "the graphics identity is the host's",
    "canvas.hash": "the canvas is the host's",
    "audio": "the audio stack is the host's",
    "fonts.check": "the font list is the host's",
    "media.pointer.coarse": "the pointer is a mouse, not a finger",
    "media.hover.hover": "the device can hover, so it is not a handset",
    "media.hover-capable": "the pointer/hover pair is the host's",
    "media.any-pointer.coarse": "the primary pointer is a mouse",
    "media.hover.none-coarse": "the pointer/hover pair is the host's",
    "touch.ontouchstart": "the device has no touch events at all",
    "math": "the maths is the host's",
    "storage.estimate": "the storage quota is the host's",
    "permissions": "the permission states are the host's",
    "geolocation": "the position is the host's or missing",
    "mediaDevices": "the device inventory is the host's",
    "webrtc.candidates": "the ICE candidates carry the host's address",
    "window.chrome.runtime": "the extension surface is present",
    "plugins.length": "the plugin inventory is the host's",
}


class AuditError(RuntimeError):
    """Raised when the audit cannot run."""


def _launch(profile: Any, port: int, extra_args: list[str]) -> tuple[Any, str]:
    """Launch a profile and return the launcher and the debug port."""

    launcher = BrowserLauncher(isolation=ProfileIsolation(Path(tempfile.mkdtemp(prefix="nova-audit-"))))
    launcher.launch(profile, extra_args=[f"--remote-debugging-port={port}", *extra_args])
    return launcher, f"http://127.0.0.1:{port}"


def _wait_for_devtools(base: str, timeout: float = 40.0) -> list[dict[str, Any]]:
    """Wait for the DevTools endpoint and return the open tabs."""

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/json", timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - the browser may not be up yet
            last_error = exc
            time.sleep(0.4)
    raise AuditError(f"the DevTools endpoint never came up: {last_error}")


async def _read_surface(ws_url: str) -> dict[str, Any]:
    """Read every surface out of the page."""

    async with websockets.connect(ws_url, max_size=None, open_timeout=30) as ws:
        # The page has to be ready before the surfaces can be read: a
        # document that is still loading has no body, no fonts and no
        # layout, and every one of those reads would answer "absent"
        # rather than "the host's value".
        await ws.send(
            json.dumps(
                {
                    "id": 100,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": (
                            "new Promise((resolve) => {"
                            "  const ready = () => resolve(1);"
                            "  if (document.readyState === 'complete') { ready(); }"
                            "  else { window.addEventListener('load', ready, { once: true }); }"
                            "})"
                        ),
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            message = json.loads(raw)
            if message.get("id") == 100:
                break

        # The audit script is an arrow function that evaluates to a
        # promise of the result object.
        #
        # The result is returned *as a value* rather than as a JSON
        # string: a JSON string cannot carry the values the DevTools
        # protocol can carry, and one of the surfaces hands back an
        # object that does not survive the round trip through
        # ``JSON.stringify``.
        expression = (
            "(async () => {"
            "  return await (function() { return (" + AUDIT_SCRIPT + ")(); })();"
            "})()"
        )
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                        "allowUnsafeEvalBlockedByCSP": True,
                    },
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            message = json.loads(raw)
            if message.get("id") != 1:
                continue
            result = message.get("result", {})
            if "exceptionDetails" in result:
                raise AuditError(f"the page threw: {result['exceptionDetails']}")
            payload = result.get("result", {}).get("value")
            if payload is None:
                description = result.get("result", {}).get("description", "")
                raise AuditError(f"the page returned nothing: {description}")
            if not isinstance(payload, dict):
                raise AuditError(f"the page returned a {type(payload).__name__}, not a table")
            if not payload:
                raise AuditError("the page returned an empty result")
            return payload


def _expected(profile: Any) -> dict[str, Any]:
    """What the profile claims, in the shape of the audit's output."""

    nav = profile.navigator
    screen = profile.screen
    return {
        "navigator.userAgent": nav.user_agent,
        "navigator.platform": nav.platform,
        "navigator.hardwareConcurrency": nav.hardware_concurrency,
        "navigator.deviceMemory": nav.device_memory,
        "navigator.maxTouchPoints": nav.max_touch_points,
        "navigator.language": nav.language,
        "navigator.languages": list(nav.languages),
        "navigator.webdriver": False,
        "navigator.userAgentData.low": {
            "brands": [
                {"brand": b.brand, "version": b.version}
                for b in profile.user_agent_data.brands
            ],
            "mobile": profile.user_agent_data.mobile,
            "platform": profile.user_agent_data.platform,
        },
        "navigator.userAgentData.high": {
            "architecture": profile.user_agent_data.architecture,
            "bitness": profile.user_agent_data.bitness,
            "model": profile.user_agent_data.model,
            "platformVersion": profile.user_agent_data.platform_version,
            "uaFullVersion": profile.browser_version.full_version,
        },
        "screen.width": screen.width,
        "screen.height": screen.height,
        "screen.colorDepth": screen.color_depth,
        "window.devicePixelRatio": screen.device_pixel_ratio,
        "intl.timeZone": profile.timezone.iana_name,
        "intl.locale": profile.locale.intl_locale,
    }


def _matches(expected: Any, measured: Any) -> bool:
    """Whether the measured value agrees with the expected one."""

    if expected is None or measured is None:
        return True
    if isinstance(expected, float) or isinstance(measured, float):
        try:
            return abs(float(expected) - float(measured)) < 0.01
        except (TypeError, ValueError):
            return False
    if isinstance(expected, list) and isinstance(measured, list):
        return len(expected) == len(measured) and all(
            _matches(a, b) for a, b in zip(expected, measured)
        )
    if isinstance(expected, dict) and isinstance(measured, dict):
        return set(expected) == set(measured) and all(
            _matches(expected[k], measured[k]) for k in expected
        )
    return str(expected) == str(measured)


class _AuditServer:
    """A local web page the audit reads the surfaces from.

    The page has to be served over HTTP rather than opened as
    ``about:blank``.  The browser core applies the profile to the web
    and not to the browser's own pages, and ``about:blank`` is one of
    the browser's own -- so a page served over HTTP is the only place
    where the surfaces of a real site are visible.

    Serving it locally also means the audit never touches the network.
    """

    def __init__(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        page = (
            b"<!doctype html><html><head><meta charset='utf-8'>"
            b"<title>NOVA surface audit</title></head>"
            b"<body><h1>NOVA surface audit</h1></body></html>"
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name is fixed
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *args: Any) -> None:
                """Keep the audit's output clean."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """The address of the page."""

        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def close(self) -> None:
        """Stop serving."""

        self._server.shutdown()
        self._server.server_close()


def audit_one(profile: Any, port: int, extra_args: list[str]) -> dict[str, Any]:
    """Audit one profile."""

    server = _AuditServer()
    try:
        launcher, base = _launch(profile, port, [*extra_args, server.url])
        try:
            tabs = _wait_for_devtools(base)
            pages = [t for t in tabs if t.get("type") == "page"]
            if not pages:
                raise AuditError("the browser opened no page")
            measured = asyncio.run(_read_surface(pages[0]["webSocketDebuggerUrl"]))
            # The high-entropy hints come back under their own key.
            high = measured.pop("appVersion.alt", None)
            if high is not None:
                measured["navigator.userAgentData.high"] = high
            return measured
        finally:
            try:
                launcher.terminate(profile.identity.id, graceful=False)
            except Exception:  # noqa: BLE001 - the browser may already be gone
                pass
    finally:
        server.close()

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=int, default=1, help="how many profiles to audit")
    parser.add_argument("--port", type=int, default=9401, help="the first DevTools port")
    parser.add_argument("--seed", default="", help="the master seed; empty means a random one")
    parser.add_argument("--preset", default="pixel-8-pro-android14")
    parser.add_argument("--json", default="", help="write the raw result to this file")
    parser.add_argument("--no-touch", action="store_true", help="do not pass the touch switches")
    args = parser.parse_args(argv)

    generator = ProfileGenerator()
    extra_args = [] if args.no_touch else ["--touch-events=enabled"]

    results: list[dict[str, Any]] = []
    profiles: list[Any] = []
    for index in range(args.profiles):
        seed = args.seed or new_master_seed()
        profile = generator.generate(
            args.preset,
            name=f"Audit {index + 1}",
            master_seed=seed,
        )
        profiles.append(profile)
        print(f"--- auditing profile {index + 1} ({profile.identity.id}) ---", flush=True)
        measured = audit_one(profile, args.port + index, extra_args)
        results.append(measured)

    # -- the leaks --------------------------------------------------------
    leaks: list[tuple[str, Any, Any]] = []
    for index, measured in enumerate(results):
        expected = _expected(profiles[index])
        for name, claimed in expected.items():
            if name not in measured:
                continue
            if not _matches(claimed, measured[name]):
                leaks.append((name, claimed, measured[name]))

    # -- the collisions ---------------------------------------------------
    collisions: dict[str, list[Any]] = {}
    if len(results) > 1:
        for name in results[0]:
            values = [r.get(name) for r in results]
            if all(isinstance(v, (str, int, float, bool, list, dict, type(None))) for v in values):
                collisions[name] = values

    # -- the pairs -------------------------------------------------------
    #
    # A site groups two profiles that answer the same on the surfaces it
    # reads.  The surfaces it reads are many, and two profiles that
    # differ on one of them are still two profiles -- but two that agree
    # on *all* of the surfaces below are one profile wearing two hats,
    # and that is the failure the whole product exists to prevent.
    #
    # The signatures are the fields that actually separate one unit of
    # a family from another.  The rest -- the model, the graphics, the
    # browser major -- belong to the family and are shared by every
    # unit of it, exactly as they are on real hardware, so they are not
    # part of the signature.
    SIGNATURE_FIELDS = (
        "navigator.hardwareConcurrency",
        "navigator.deviceMemory",
        "navigator.maxTouchPoints",
        "screen.width",
        "screen.height",
        "window.devicePixelRatio",
        "canvas.hash",
        "audio",
        "webgl",
        "math",
        "fonts.measure",
    )

    pairs: list[tuple[int, int, list[str]]] = []
    if len(results) > 1:
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                same = [
                    name
                    for name in SIGNATURE_FIELDS
                    if name in results[i]
                    and name in results[j]
                    and str(results[i][name]) == str(results[j][name])
                ]
                if same:
                    pairs.append((i, j, same))

    print()
    print("=== LEAKS: the browser reported the host instead of the profile ===")
    if not leaks:
        print("  (none)")
    for name, claimed, got in leaks:
        print(f"  {name}")
        print(f"      profile: {claimed}")
        print(f"      browser: {got}")
        print(f"      why:     {LEAK_SURFACES.get(name, 'the value does not match the profile')}")

    print()
    print("=== TOUCH AND POINTER: the surfaces that tell a phone from a laptop ===")
    for name in (
        "touch.ontouchstart",
        "touch.TouchEvent",
        "media.pointer.coarse",
        "media.pointer.fine",
        "media.hover.hover",
        "media.hover.none",
        "media.any-pointer.coarse",
        "media.hover-capable",
        "media.hover-none-coarse",
    ):
        values = [r.get(name) for r in results]
        print(f"  {name:32s} {values}")

    if len(results) > 1:
        print()
        print("=== COLLISIONS: the surfaces two profiles agree on ===")
        for name, values in collisions.items():
            if len(set(map(str, values))) == 1:
                print(f"  {name:34s} {values[0]!r}")

        print()
        print("=== PAIRS: the profiles that a site could group ===")
        if not pairs:
            print("  (none)")
        for i, j, same in pairs:
            print(f"  profile {i + 1} and profile {j + 1} agree on:")
            for name in same:
                print(f"      {name}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "profiles": [
                        {
                            "id": p.identity.id,
                            "seed": p.seed_config.master_seed,
                            "expected": _expected(p),
                            "measured": m,
                        }
                        for p, m in zip(profiles, results, strict=True)
                    ],
                    "leaks": [
                        {"surface": n, "profile": c, "browser": g} for n, c, g in leaks
                    ],
                    "pairs": [
                        {"a": i + 1, "b": j + 1, "agree_on": same}
                        for i, j, same in pairs
                    ],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nthe raw result was written to {args.json}")

    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
