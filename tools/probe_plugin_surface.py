"""Report the plugin surface of the page the way a detector reads it."""

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
(function () {
  var out = {};
  out.pdfViewerEnabled = navigator.pdfViewerEnabled;
  out.pluginPdf = !!navigator.plugins.PDFViewer;
  out.pluginPdfName = navigator.plugins.PDFViewer ? navigator.plugins.PDFViewer.name : null;
  out.mimePdf = !!navigator.mimeTypes.PDFViewer;
  out.mimePdfType = navigator.mimeTypes.PDFViewer ? navigator.mimeTypes.PDFViewer.type : null;
  out.pluginCount = navigator.plugins.length;
  out.mimeCount = navigator.mimeTypes.length;
  out.pluginNames = Array.prototype.map.call(navigator.plugins, function (p) { return p.name; });
  out.chromeType = typeof navigator.chrome;
  out.chromeKeys = navigator.chrome ? Object.keys(navigator.chrome) : null;
  out.chromeApp = navigator.chrome && navigator.chrome.app ? Object.keys(navigator.chrome.app) : null;
  out.chromeCsi = navigator.chrome ? typeof navigator.chrome.csi : null;
  return JSON.stringify(out);
})()
"""


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        print(await client.evaluate(EXPRESSION))


if __name__ == "__main__":
    asyncio.run(main())
