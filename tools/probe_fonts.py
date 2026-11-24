"""Ask the page which fonts it can actually draw with.

The font list is one of the loudest parts of a fingerprint, and a site
that measures it does not ask the page: it asks the platform, by
drawing text and measuring the width.  This measures the widths, which
is what the platform would answer.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

EXPRESSION = r"""
(function () {
  function widthOf(family) {
    var canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    ctx.font = '72px monospace';
    var base = ctx.measureText('mmmwwwiiilll0123456789').width;
    ctx.font = '72px "' + family + '", monospace';
    var target = ctx.measureText('mmmwwwiiilll0123456789').width;
    return { base: base, target: target, differs: Math.abs(base - target) > 0.5 };
  }

  var families = [
    'Arial', 'Helvetica', 'Times New Roman', 'Courier New', 'Verdana',
    'Roboto', 'Noto Sans', 'Noto Serif', 'Droid Sans', 'Droid Serif',
    'Droid Sans Mono', 'Roboto Mono', 'Roboto Condensed', 'Noto Color Emoji',
    'Segoe UI', 'Calibri', 'Cambria', 'Consolas', 'Tahoma', 'Georgia'
  ];

  var present = [];
  var absent = [];
  families.forEach(function (family) {
    var result = widthOf(family);
    (result.differs ? present : absent).push(family);
  });

  return JSON.stringify({
    present: present,
    absent: absent,
    fontsApiSize: document.fonts ? document.fonts.size : null
  });
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
        print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
