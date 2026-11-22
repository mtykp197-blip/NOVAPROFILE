"""Read every fingerprint surface from a running browser.

The measurement runs inside the browser, through the DevTools protocol,
and writes the result to a file.  The script that runs in the page is
deliberately defensive: every read is wrapped, because a browser that
refuses one surface must not cost the other forty.

The output is a dictionary of surfaces.  It is what the comparison
against a profile reads.
"""

from __future__ import annotations

import sys

#: The script that runs in the page.
PAGE_SCRIPT = r"""
(() => {
  const out = {};
  const record = (name, fn) => {
    try {
      const value = fn();
      out[name] = value === undefined ? null : value;
    } catch (error) {
      out[name] = { error: String(error && error.message || error) };
    }
  };

  // -- the navigator ------------------------------------------------------
  record('user_agent', () => navigator.userAgent);
  record('platform', () => navigator.platform);
  record('language', () => navigator.language);
  record('languages', () => navigator.languages ? Array.from(navigator.languages) : null);
  record('hardware_concurrency', () => navigator.hardwareConcurrency);
  record('device_memory', () => navigator.deviceMemory);
  record('max_touch_points', () => navigator.maxTouchPoints);
  record('do_not_track', () => navigator.doNotTrack);
  record('webdriver', () => navigator.webdriver);
  record('user_agent_data', () => {
    const uad = navigator.userAgentData;
    if (!uad) return null;
    return {
      brands: Array.from(uad.brands || []),
      mobile: uad.mobile,
      platform: uad.platform,
    };
  });

  // -- the screen ---------------------------------------------------------
  record('screen', () => ({
    width: screen.width,
    height: screen.height,
    avail_width: screen.availWidth,
    avail_height: screen.availHeight,
    color_depth: screen.colorDepth,
    pixel_depth: screen.pixelDepth,
    pixel_ratio: window.devicePixelRatio,
    orientation: screen.orientation ? screen.orientation.type : null,
  }));

  // -- the graphics -------------------------------------------------------
  record('canvas_2d', () => {
    const canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 60;
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.textBaseline = 'top';
    context.font = '14px "Arial"';
    context.fillStyle = '#f60';
    context.fillRect(125, 1, 62, 20);
    context.fillStyle = '#069';
    context.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
    context.fillStyle = 'rgba(102, 204, 0, 0.7)';
    context.fillText('Cwm fjordbank glyphs vext quiz', 4, 17);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 0;
    for (let i = 0; i < data.length; i++) {
      hash = (hash * 31 + data[i]) | 0;
    }
    return {
      hash: hash,
      text: context.measureText('Cwm fjordbank glyphs vext quiz').width,
    };
  });

  record('webgl', () => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return null;
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const param = (name, fallback) => debug
      ? gl.getParameter(debug[name])
      : gl.getParameter(fallback);
    return {
      vendor: param('UNMASKED_VENDOR_WEBGL', gl.VENDOR),
      renderer: param('UNMASKED_RENDERER_WEBGL', gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      shading_language: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      max_texture_size: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      max_viewport: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS) || []),
      max_render_buffer_size: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
      max_texture_image_units: gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS),
      max_vertex_attribs: gl.getParameter(gl.MAX_VERTEX_ATTRIBS),
      extensions: (gl.getSupportedExtensions() || []).slice().sort(),
    };
  });

  record('webgpu', () => null);

  // -- the audio ----------------------------------------------------------
  record('audio', () => new Promise((resolve) => {
    const AudioContextClass = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!AudioContextClass) {
      resolve(null);
      return;
    }
    try {
      const context = new AudioContextClass(1, 5000, 44100);
      const oscillator = context.createOscillator();
      oscillator.type = 'triangle';
      oscillator.frequency.value = 10000;
      const compressor = context.createDynamicsCompressor();
      compressor.threshold.value = -50;
      compressor.knee.value = 40;
      compressor.ratio.value = 12;
      compressor.attack.value = 0;
      compressor.release.value = 0.25;
      oscillator.connect(compressor);
      compressor.connect(context.destination);
      oscillator.start(0);
      context.startRendering();
      context.oncomplete = (event) => {
        try {
          const buffer = event.renderedBuffer;
          const data = buffer.getChannelData(0);
          let sum = 0;
          for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
          resolve({ sum: sum, length: buffer.length, sample_rate: buffer.sampleRate });
        } catch (error) {
          resolve({ error: String(error) });
        }
      };
    } catch (error) {
      resolve({ error: String(error) });
    }
  }));

  // -- the fonts ----------------------------------------------------------
  record('fonts', () => {
    const candidates = [
      'Arial', 'Arial Black', 'Arial Narrow', 'Arial Rounded MT Bold',
      'Bookman Old Style', 'Bradley Hand ITC', 'Century', 'Century Gothic',
      'Comic Sans MS', 'Courier', 'Courier New', 'Georgia', 'Gentium',
      'Impact', 'King', 'Lucida Console', 'Lalit', 'Modena', 'Monaco',
      'Microsoft JhengHei', 'Microsoft New Taipei', 'Microsoft Sans Serif',
      'Microsoft Tai Le', 'Microsoft YaHei', 'MS Gothic', 'MV Boli',
      'News Gothic', 'Palatino Linotype', 'SimSun', 'Songti SC', 'STSong',
      'SimHei', 'Symbol', 'Tahoma', 'Times', 'Times New Roman', 'Trebuchet MS',
      'Verdana', 'Wingdings', 'Yu Gothic', 'Yu Gothic UI',
    ];
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    const base = ['monospace', 'sans-serif', 'serif'];
    const found = {};
    for (const font of candidates) {
      const widths = {};
      for (const fallback of base) {
        context.font = '72px "' + font + '", ' + fallback;
        widths[fallback] = context.measureText('MMMMPppppWWWwwwwiiii').width;
      }
      const distinct = new Set(Object.values(widths)).size;
      found[font] = distinct >= base.length;
    }
    return found;
  });

  // -- the locale ---------------------------------------------------------
  record('timezone', () => ({
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date().getTimezoneOffset(),
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
  }));

  // -- the media ----------------------------------------------------------
  record('media_devices', () => navigator.mediaDevices
    ? navigator.mediaDevices.enumerateDevices()
        .then((devices) => devices.map((device) => ({
          kind: device.kind,
          label: device.label,
          group_id: device.groupId,
        })))
    : null);

  record('permissions', () => navigator.permissions
    ? Promise.all([
        'notifications', 'geolocation', 'camera', 'microphone', 'clipboard-read',
      ].map((name) => navigator.permissions.query({ name }).then(
        (status) => [name, status.state],
        () => [name, 'unsupported'],
      ))).then(Object.fromEntries)
    : null);

  // -- the storage --------------------------------------------------------
  record('storage_estimate', () => navigator.storage
    ? navigator.storage.estimate()
    : null);

  record('indexed_db', () => indexedDB.databases
    ? indexedDB.databases().then((list) => list.map((db) => db.name))
    : null);

  // -- the sensors --------------------------------------------------------
  record('device_motion', () => new Promise((resolve) => {
    if (!('DeviceMotionEvent' in window)) {
      resolve(null);
      return;
    }
    const timer = setTimeout(() => resolve(null), 800);
    window.addEventListener('devicemotion', (event) => {
      clearTimeout(timer);
      resolve({
        acceleration: event.acceleration,
        rotation_rate: event.rotationRate,
        interval: event.interval,
      });
    }, { once: true });
  }));

  // -- the network --------------------------------------------------------
  record('webrtc', () => new Promise((resolve) => {
    if (!window.RTCPeerConnection) {
      resolve(null);
      return;
    }
    try {
      const connection = new RTCPeerConnection({ iceServers: [] });
      const found = [];
      connection.onicecandidate = (event) => {
        if (event.candidate) {
          found.push(event.candidate.candidate);
        } else {
          resolve({ candidates: found });
          connection.close();
        }
      };
      connection.createDataChannel('probe');
      connection.createOffer()
        .then((offer) => connection.setLocalDescription(offer))
        .catch(() => resolve({ candidates: found, error: true }));
      setTimeout(() => resolve({ candidates: found, timeout: true }), 2000);
    } catch (error) {
      resolve({ error: String(error) });
    }
  }));

  record('cookie', () => document.cookie);
  record('local_storage', () => {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
    return keys;
  });

  // The asynchronous reads are gathered here.
  return Promise.resolve(out).then(async (result) => {
    const waits = [];
    for (const key of Object.keys(result)) {
      const value = result[key];
      if (value && typeof value.then === 'function') {
        waits.push(value.then((resolved) => { result[key] = resolved; }));
      }
    }
    await Promise.all(waits);

    // The high-entropy client hints are read last, because they need
    // the user agent data object.
    try {
      const uad = navigator.userAgentData;
      if (uad && uad.getHighEntropyValues) {
        const high = await uad.getHighEntropyValues([
          'architecture', 'bitness', 'model', 'platformVersion',
          'uaFullVersion', 'fullVersionList', 'wow64',
        ]);
        result.user_agent_data_high_entropy = high;
      }
    } catch (error) {
      result.user_agent_data_high_entropy = { error: String(error) };
    }

    // The webgpu adapter is read here, because it is asynchronous and
    // the renderer needs a device.
    try {
      if (navigator.gpu) {
        const adapter = await navigator.gpu.requestAdapter();
        if (adapter) {
          const info = adapter.info || (adapter.requestAdapterInfo
            ? await adapter.requestAdapterInfo()
            : {});
          result.webgpu = {
            vendor: info.vendor || null,
            architecture: info.architecture || null,
            device: info.device || null,
            description: info.description || null,
            features: Array.from(adapter.features || []).sort(),
          };
        } else {
          result.webgpu = null;
        }
      }
    } catch (error) {
      result.webgpu = { error: String(error) };
    }

    return result;
  });
})()
"""


def main(argv: list[str]) -> int:
    """Print the page script.

    The script is meant to be run through the DevTools protocol by the
    probe; run on its own it does nothing but explain itself.
    """

    if "--help" in argv or not argv:
        print(__doc__)
        print("This module is used by the probe; it is not run directly.")
        return 0

    print(PAGE_SCRIPT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
