"""Check whether a freshly opened target already has the touch mode.

The launcher applies the emulation to the targets that exist when the browser
starts.  A target that opens later -- a tab, a popup, a link with
``target=_blank`` -- has to be emulated as well, or the operator drags a
mouse in it while the page believes it is a handset.
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
    return await client.evaluate("JSON.stringify(window.__probe)", session)


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
        print(f"pages: {len(pages)}")

        for page in pages:
            attached = await client.call(
                "Target.attachToTarget",
                {"targetId": page["targetId"], "flatten": True},
            )
            session = (attached.get("result") or {}).get("sessionId")
            if not session:
                continue
            events = await probe(client, session)
            state = "TOUCH" if "touchstart" in (events or "") else "MOUSE"
            print(f"  {state}  {page.get('url', '')[:70]}")
            print(f"        {events}")
            await client.send("Target.detachFromTarget", {"sessionId": session})


if __name__ == "__main__":
    asyncio.run(main())
