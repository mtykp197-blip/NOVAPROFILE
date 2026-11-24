"""Compare where the Emulation commands take effect: browser session vs page session."""

import asyncio
import json
import sys

import websockets


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def send(self, method, params=None, session=None):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self.ws.send(json.dumps(message))
        return self.next_id

    async def call(self, method, params=None, session=None):
        ident = await self.send(method, params, session)
        while True:
            raw = await self.ws.recv()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if message.get("id") == ident:
                return message

    async def evaluate(self, expression, session=None):
        reply = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session,
        )
        return (reply.get("result") or {}).get("result", {}).get("value")


INSTALL = r"""
window.__probe = [];
['pointerdown','pointermove','pointerup','mousedown','touchstart','touchmove','touchend'].forEach(function (t) {
  document.addEventListener(t, function (e) {
    window.__probe.push(t + (e.pointerType ? ':' + e.pointerType : ''));
  }, true);
});
'installed'
"""


async def probe(client, label, session=None):
    await client.evaluate(INSTALL, session)
    await asyncio.sleep(0.4)
    for kind, x, y in (
        ("mousePressed", 220, 320),
        ("mouseMoved", 260, 380),
        ("mouseReleased", 260, 380),
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
            session,
        )
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.4)
    events = await client.evaluate("JSON.stringify(window.__probe)", session)
    print(f"{label}: {events}")


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)

        await probe(client, "BASELINE (no override)")

        # Browser level: no session id at all.
        reply = await client.call(
            "Emulation.setEmitTouchEventsForMouse",
            {"enabled": True, "configuration": "mobile"},
        )
        print("browser-level setEmitTouchEventsForMouse:", "ERROR" if reply.get("error") else "ok")
        await asyncio.sleep(0.5)
        await probe(client, "AFTER browser-level override")

        # Page level: attach first, then send with the session id.
        targets = await client.call("Target.getTargets")
        pages = [
            item
            for item in (targets.get("result") or {}).get("targetInfos", [])
            if item.get("type") == "page"
        ]
        if pages:
            attached = await client.call(
                "Target.attachToTarget",
                {"targetId": pages[0]["targetId"], "flatten": True},
            )
            session = (attached.get("result") or {}).get("sessionId")
            print("attached session:", bool(session))
            reply = await client.call(
                "Emulation.setEmitTouchEventsForMouse",
                {"enabled": True, "configuration": "mobile"},
                session,
            )
            print("page-level setEmitTouchEventsForMouse:", "ERROR" if reply.get("error") else "ok")
            await asyncio.sleep(0.5)
            await probe(client, "AFTER page-level override", session)


if __name__ == "__main__":
    asyncio.run(main())
