"""Check whether detaching the session undoes the touch emulation.

The launcher opens a websocket, applies the emulation to every page target
and closes the websocket again.  If the detachment undoes the touch
emulation, the profile ends up with a mouse even though the launcher
reported success.
"""

import asyncio
import json
import urllib.request

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
['pointerdown','mousedown','touchstart','touchmove','touchend'].forEach(function (t) {
  document.addEventListener(t, function (e) {
    window.__probe.push(t + (e.pointerType ? ':' + e.pointerType : ''));
  }, true);
});
'installed'
"""


async def probe(client, session):
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
    state = "TOUCH" if "touchstart" in (events or "") else "MOUSE"
    print(f"{state}: {events}")


async def main():
    version = json.load(urllib.request.urlopen("http://127.0.0.1:9839/json/version"))
    async with websockets.connect(
        version["webSocketDebuggerUrl"], max_size=30 * 1024 * 1024
    ) as ws:
        client = Client(ws)

        targets = await client.call("Target.getTargets")
        pages = [
            item
            for item in (targets.get("result") or {}).get("targetInfos", [])
            if item.get("type") == "page"
        ]
        target_id = pages[0]["targetId"]

        # First pass: apply, probe, detach -- the launcher's sequence.
        attached = await client.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session = (attached.get("result") or {}).get("sessionId")
        for method, params in (
            (
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 480,
                    "height": 1067,
                    "deviceScaleFactor": 2.625,
                    "mobile": True,
                },
            ),
            (
                "Emulation.setEmitTouchEventsForMouse",
                {"enabled": True, "configuration": "mobile"},
            ),
            ("Emulation.setTouchEmulationEnabled", {"enabled": True}),
        ):
            await client.call(method, params, session)

        print("while attached:")
        await probe(client, session)

        await client.call("Target.detachFromTarget", {"sessionId": session})
        await asyncio.sleep(0.6)

        # Second pass: attach again and probe without re-applying.
        attached = await client.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session2 = (attached.get("result") or {}).get("sessionId")
        await asyncio.sleep(0.4)
        print("after detach, re-attached, nothing re-applied:")
        await probe(client, session2)


if __name__ == "__main__":
    asyncio.run(main())
