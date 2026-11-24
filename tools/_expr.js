JSON.stringify((async () => {  return await (function() { return (
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
)(); })();})())