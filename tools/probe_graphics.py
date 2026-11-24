"""Check the graphics surfaces against the profile's own document.

The canvas and the WebGL surfaces carry a device's identity, and they
are the two that a site measures most carefully.  The values the page
reads have to be the profile's, and they have to agree with each other:
a canvas that says one GPU and a WebGL that says another is a device
that does not exist.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import websockets

EXPRESSION = r"""
(function () {
  var out = {};

  var canvas = document.createElement('canvas');
  canvas.width = 240;
  canvas.height = 60;
  var ctx = canvas.getContext('2d');
  ctx.textBaseline = 'top';
  ctx.font = '14px "Arial"';
  ctx.fillStyle = '#f60';
  ctx.fillRect(125, 1, 62, 20);
  ctx.fillStyle = '#069';
  ctx.fillText('Cwm fjordbank glyphs vext quiz \u{1F600}', 2, 15);
  ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
  ctx.fillText('Cwm fjordbank glyphs vext quiz \u{1F600}', 4, 17);
  out.canvas = canvas.toDataURL();

  var gl = document.createElement('canvas').getContext('webgl');
  if (gl) {
    var debug = gl.getExtension('WEBGL_debug_renderer_info');
    out.webgl = {
      vendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION)
    };
    out.webglParameters = gl.getSupportedExtensions();
  } else {
    out.webgl = null;
  }

  out.audio = (function () {
    try {
      var ac = new OfflineAudioContext(1, 44100, 44100);
      var osc = ac.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = 10000;
      var comp = ac.createDynamicsCompressor();
      osc.connect(comp);
      comp.connect(ac.destination);
      osc.start(0);
      return 'built';
    } catch (e) {
      return 'error: ' + e.message;
    }
  })();

  out.fonts = {
    check: (function () {
      var names = ['Roboto', 'Noto Sans', 'Droid Sans Mono', 'Arial', 'Helvetica'];
      var found = [];
      names.forEach(function (name) {
        if (document.fonts && document.fonts.check) {
          found.push(name + ':' + document.fonts.check('12px "' + name + '"'));
        }
      });
      return found;
    })(),
    count: document.fonts ? document.fonts.size : null
  };

  return JSON.stringify(out);
})()
"""


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def call(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        await self.ws.send(
            json.dumps({"id": ident, "method": method, "params": params or {}})
        )
        while True:
            raw = await self.ws.recv()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if message.get("id") == ident:
                return message

    async def evaluate(self, expression):
        reply = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        result = reply.get("result") or {}
        if result.get("exceptionDetails"):
            return {"error": str(result["exceptionDetails"])[:400]}
        return (result.get("result") or {}).get("value")


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        raw = await client.evaluate(EXPRESSION)
        if isinstance(raw, str):
            data = json.loads(raw)
            canvas = data.get("canvas", "")
            data["canvas"] = canvas[:80] + ("..." if len(canvas) > 80 else "")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(raw, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
