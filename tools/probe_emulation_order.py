"""Check whether the order of the Emulation commands decides the touch mode.

Chromium's ``Emulation.setTouchEmulationEnabled`` installs the touch emulator on
the render widget, and ``Emulation.setEmitTouchEventsForMouse`` decides where
the touch events come from.  The launcher used to send them in the opposite
order to the reference implementation; this script proves which order works.
"""

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

METRICS = {
    "width": 480,
    "height": 1067,
    "deviceScaleFactor": 2.625,
    "mobile": True,
}
TOUCH = {"enabled": True, "maxTouchPoints": 10}
EMIT = {"enabled": True, "configuration": "mobile"}


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
    return events


async def run_order(client, session, label, commands):
    for method, params in commands:
        await client.send(method, params, session)
    await asyncio.sleep(0.6)
    events = await probe(client, session)
    touch = "touchstart" in (events or "")
    print(f"{'TOUCH WORKS' if touch else 'mouse only '} <- {label}")
    print(f"    {events}")
    return touch


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)

        targets = await client.call("Target.getTargets")
        pages = [
            item
            for item in (targets.get("result") or {}).get("targetInfos", [])
            if item.get("type") == "page"
        ]
        attached = await client.call(
            "Target.attachToTarget",
            {"targetId": pages[0]["targetId"], "flatten": True},
        )
        session = (attached.get("result") or {}).get("sessionId")
        if not session:
            print("could not attach")
            return

        # The order the launcher used: the metrics, then the mouse to touch
        # conversion, then the touch emulation.
        await run_order(
            client,
            session,
            "metrics -> emit -> touch",
            [
                ("Emulation.setDeviceMetricsOverride", METRICS),
                ("Emulation.setEmitTouchEventsForMouse", EMIT),
                ("Emulation.setTouchEmulationEnabled", TOUCH),
            ],
        )

        # The order of the reference implementation: the touch emulation first,
        # the mouse to touch conversion last.
        await run_order(
            client,
            session,
            "metrics -> touch -> emit",
            [
                ("Emulation.setDeviceMetricsOverride", METRICS),
                ("Emulation.setTouchEmulationEnabled", TOUCH),
                ("Emulation.setEmitTouchEventsForMouse", EMIT),
            ],
        )

        # The order without the metrics, to see whether the metrics are what
        # switches the touch mode off.
        await run_order(
            client,
            session,
            "touch -> emit (no metrics)",
            [
                ("Emulation.setTouchEmulationEnabled", TOUCH),
                ("Emulation.setEmitTouchEventsForMouse", EMIT),
            ],
        )


if __name__ == "__main__":
    asyncio.run(main())
