"""Look at what the canvas noise actually does to the encoded image."""

import asyncio
import json
import urllib.request

import websockets


EXPRESSION = r"""
(function () {
  var c = document.createElement('canvas');
  c.width = 240; c.height = 60;
  var g = c.getContext('2d');
  g.textBaseline = 'top';
  g.font = "14px 'Arial'";
  g.fillStyle = '#f60';
  g.fillRect(100, 1, 62, 20);
  g.fillStyle = '#069';
  g.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
  var url = c.toDataURL();
  var img = g.getImageData(0, 0, 240, 60).data;
  var pix = 0;
  for (var i = 0; i < img.length; i++) { pix = (pix * 31 + img[i]) >>> 0; }
  return JSON.stringify({len: url.length, pixhash: pix, head: url.slice(0, 100)});
})()
"""


async def main():
    for port in (10078, 9416):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as handle:
            targets = json.load(handle)
        page = next(
            t for t in targets
            if t.get("type") == "page" and t.get("url", "").startswith("http")
        )
        async with websockets.connect(
            page["webSocketDebuggerUrl"], max_size=30 * 1024 * 1024
        ) as ws:
            ident = 1
            await ws.send(
                json.dumps(
                    {
                        "id": ident,
                        "method": "Runtime.evaluate",
                        "params": {"expression": EXPRESSION, "returnByValue": True},
                    }
                )
            )
            while True:
                raw = await ws.recv()
                message = json.loads(raw)
                if message.get("id") == ident:
                    result = (message.get("result") or {}).get("result") or {}
                    print(port, result.get("value"))
                    break


if __name__ == "__main__":
    asyncio.run(main())
