"""Compare the fingerprint of several running NOVA profiles.

Prints the fields a website groups on, side by side, so that two
profiles that agree on any of them can be spotted at a glance.
"""

import asyncio
import json
import sys

import websockets


EXPRESSION = r"""
(function () {
  function canvas() {
    try {
      var c = document.createElement('canvas');
      c.width = 240; c.height = 60;
      var g = c.getContext('2d');
      g.textBaseline = 'top';
      g.font = "14px 'Arial'";
      g.fillStyle = '#f60';
      g.fillRect(100, 1, 62, 20);
      g.fillStyle = '#069';
      g.fillText('Cwm fjordbank glyphs vext quiz 😃', 2, 15);
      g.fillStyle = 'rgba(102,204,0,0.7)';
      g.fillText('Cwm fjordbank glyphs vext quiz 😃', 4, 17);
      return c.toDataURL();
    } catch (e) { return 'error: ' + e; }
  }

  function webgl() {
    try {
      var c = document.createElement('canvas');
      var g = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!g) { return 'no webgl'; }
      var ext = g.getExtension('WEBGL_debug_renderer_info');
      return {
        vendor: ext ? g.getParameter(ext.UNMASKED_VENDOR_WEBGL) : g.getParameter(g.VENDOR),
        renderer: ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER),
      };
    } catch (e) { return 'error: ' + e; }
  }

  function audio() {
    try {
      var ctx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 5000, 44100);
      var osc = ctx.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = 10000;
      var comp = ctx.createDynamicsCompressor();
      osc.connect(comp);
      comp.connect(ctx.destination);
      osc.start(0);
      ctx.startRendering();
      return ctx.oncomplete ? 'pending' : 'sync';
    } catch (e) { return 'error: ' + e; }
  }

  return JSON.stringify({
    ua: navigator.userAgent,
    platform: navigator.platform,
    language: navigator.language,
    cores: navigator.hardwareConcurrency,
    memory: navigator.deviceMemory,
    touch: navigator.maxTouchPoints,
    screen: [screen.width, screen.height, screen.colorDepth],
    inner: [innerWidth, innerHeight, devicePixelRatio],
    tz: (function () {
      try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return '?'; }
    })(),
    canvas: canvas(),
    webgl: webgl(),
    audio: audio(),
  });
})()
"""


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def evaluate(self, expression):
        self.next_id += 1
        ident = self.next_id
        await self.ws.send(
            json.dumps(
                {
                    "id": ident,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        while True:
            raw = await self.ws.recv()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if message.get("id") == ident:
                result = message.get("result") or {}
                if result.get("exceptionDetails"):
                    return {"error": str(result["exceptionDetails"])[:300]}
                return (result.get("result") or {}).get("value")


async def probe(url):
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        raw = await client.evaluate(EXPRESSION)
        return json.loads(raw) if isinstance(raw, str) else raw


async def main():
    ports = [int(port) for port in sys.argv[1:]]
    import urllib.request

    rows = []
    for port in ports:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as handle:
            targets = json.load(handle)
        page = next(
            (t for t in targets if t.get("type") == "page" and t.get("url", "").startswith("http")),
            None,
        )
        if page is None:
            print(f"port {port}: no http page")
            continue
        data = await probe(page["webSocketDebuggerUrl"])
        data["port"] = port
        rows.append(data)

    if not rows:
        return

    for key in ("ua", "platform", "language", "cores", "memory", "touch", "screen", "inner", "tz"):
        values = [json.dumps(row.get(key)) for row in rows]
        mark = "SAME" if len(set(values)) == 1 else "diff"
        print(f"{mark:5} {key:10} " + " | ".join(str(v) for v in values))

    canvas = [row.get("canvas", "")[:60] for row in rows]
    print(f"{'SAME' if len(set(canvas)) == 1 else 'diff':5} canvas    " + " | ".join(c[:40] for c in canvas))

    gl = [json.dumps(row.get("webgl")) for row in rows]
    print(f"{'SAME' if len(set(gl)) == 1 else 'diff':5} webgl     " + " | ".join(gl))


if __name__ == "__main__":
    asyncio.run(main())
