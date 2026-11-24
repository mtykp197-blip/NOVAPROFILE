"""Check that the JavaScript layer stands down where the native core owns.

The native core publishes the list of surfaces it owns, and the
JavaScript layer has to stand down on exactly those.  When it does not,
the page sees the native value with the layer's value on top of it,
which is a value that belongs to neither the profile nor the host.

The list is not published on the window -- it is bound inside a wrapper
-- so the check asks the layer what it read, through the one channel it
reports on.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

EXPRESSION = r"""
(function () {
  // The layer records what it read, for exactly this check.
  var surfaces = window.__NOVA_SURFACES_SEEN__;
  return JSON.stringify({
    seen: surfaces === undefined ? null : surfaces,
    // A double perturbation shows up as a disagreement between the
    // value the page reads and the value the profile describes.
    ua: navigator.userAgent,
    platform: navigator.platform,
    screen: screen.width + 'x' + screen.height
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
        return (reply.get("result") or {}).get("result", {}).get("value")


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        raw = await client.evaluate(EXPRESSION)
        print(raw)


if __name__ == "__main__":
    asyncio.run(main())
