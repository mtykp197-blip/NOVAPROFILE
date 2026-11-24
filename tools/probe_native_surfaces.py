"""Report the surfaces the native core owns and the ones it is missing."""

import asyncio
import json
import sys

import websockets


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
        return (reply.get("result") or {}).get("result", {}).get("value")


EXPRESSION = """
JSON.stringify({
  surfaces: window.__NOVA_NATIVE_SURFACES__ || null,
  ready: window.__NOVA_READY__ === true,
  hasChrome: 'chrome' in navigator,
  chromeType: typeof navigator.chrome,
  pdfIn: 'PDFViewer' in navigator,
  plugins: navigator.plugins ? navigator.plugins.length : null,
  pluginNames: navigator.plugins ? Array.prototype.map.call(navigator.plugins, function (p) { return p.name; }) : null,
  mimeTypes: navigator.mimeTypes ? navigator.mimeTypes.length : null,
  mimeNames: navigator.mimeTypes ? Array.prototype.map.call(navigator.mimeTypes, function (m) { return m.type; }) : null
})
"""


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        print(await client.evaluate(EXPRESSION))


if __name__ == "__main__":
    asyncio.run(main())
