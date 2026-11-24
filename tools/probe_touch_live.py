"""Probe the touch emulation of a live NOVA profile browser.

Dispatches a mouse press/move/release and reports which DOM events the page
actually receives, so we can see whether the operator's mouse became a finger.

The Emulation commands are sent without waiting for their reply: Chromium
answers some of them only once, and a probe that waits for a second answer
would hang.
"""

import asyncio
import json
import sys

import websockets

INSTALL = r"""
window.__probe = [];
['pointerdown','pointerup','pointermove','mousedown','mouseup','touchstart','touchmove','touchend','click'].forEach(function (t) {
  document.addEventListener(t, function (e) {
    window.__probe.push(t + (e.pointerType ? ':' + e.pointerType : '') + (e.touches ? ':touches=' + e.touches.length : ''));
  }, true);
});
'installed'
"""


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def send(self, method, params=None):
        self.next_id += 1
        await self.ws.send(
            json.dumps({"id": self.next_id, "method": method, "params": params or {}})
        )
        return self.next_id

    async def call(self, method, params=None):
        ident = await self.send(method, params)
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

        await client.send(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": 10},
        )
        await client.send(
            "Emulation.setEmitTouchEventsForMouse",
            {"enabled": True, "configuration": "mobile"},
        )
        await asyncio.sleep(0.6)

        await client.evaluate(INSTALL)
        await asyncio.sleep(0.4)

        for kind, x, y in (
            ("mousePressed", 200, 300),
            ("mouseMoved", 240, 360),
            ("mouseReleased", 240, 360),
        ):
            await client.send(
                "Input.dispatchMouseEvent",
                {
                    "type": kind,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 0 if kind == "mouseReleased" else 1,
                    "clickCount": 1,
                },
            )
            await asyncio.sleep(0.2)

        await asyncio.sleep(0.5)
        print("EVENTS:", await client.evaluate("JSON.stringify(window.__probe)"))


if __name__ == "__main__":
    asyncio.run(main())
