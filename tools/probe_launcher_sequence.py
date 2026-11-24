"""Reproduce the launcher's emulation sequence and check the touch mode.

The sequence is the one ``device_emulation.py`` sends, in the same order, and
the probe dispatches a mouse press without re-applying anything afterwards.
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
        attached = await client.call(
            "Target.attachToTarget",
            {"targetId": pages[0]["targetId"], "flatten": True},
        )
        session = (attached.get("result") or {}).get("sessionId")
        print("session:", (session or "")[:12])

        # The sequence of device_emulation.py, in the same order.
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
            reply = await client.call(method, params, session)
            error = reply.get("error")
            print(
                f"{method}: {'ERROR ' + error.get('message', '') if error else 'ok'}"
            )

        await asyncio.sleep(0.6)

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
        print("EVENTS:", await client.evaluate("JSON.stringify(window.__probe)", session))


if __name__ == "__main__":
    asyncio.run(main())
