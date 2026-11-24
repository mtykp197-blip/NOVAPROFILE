"""Report which Emulation commands the NOVA core accepts, and with what result."""

import asyncio
import json
import sys

import websockets


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


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)

        steps = [
            (
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 480,
                    "height": 1067,
                    "deviceScaleFactor": 2.625,
                    "mobile": True,
                },
            ),
            ("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 10}),
            (
                "Emulation.setEmitTouchEventsForMouse",
                {"enabled": True, "configuration": "mobile"},
            ),
            (
                "Emulation.setUserAgentOverride",
                {
                    "userAgent": "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
                    "acceptLanguage": "vi-VN,vi;q=0.9",
                },
            ),
        ]

        for method, params in steps:
            reply = await client.call(method, params)
            error = reply.get("error")
            if error:
                print(f"ERROR  {method}: {error.get('message', '')}")
            else:
                print(f"ok     {method}")


if __name__ == "__main__":
    asyncio.run(main())
