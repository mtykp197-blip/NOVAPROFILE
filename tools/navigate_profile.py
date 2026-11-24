"""Send a running profile to a page, over the developer protocol."""

import json
import sys
import urllib.request

import websockets


async def navigate(port: int, url: str) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as handle:
        targets = json.load(handle)

    page = next(
        (t for t in targets if t.get("type") == "page" and "newtab" not in t.get("url", "")),
        None,
    )
    if page is None:
        page = next(t for t in targets if t.get("type") == "page")

    async with websockets.connect(
        page["webSocketDebuggerUrl"], max_size=30 * 1024 * 1024
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Page.navigate",
                    "params": {"url": url},
                }
            )
        )
        while True:
            raw = await ws.recv()
            message = json.loads(raw)
            if message.get("id") == 1:
                print(f"{port}: {message.get('result') or message.get('error')}")
                return


if __name__ == "__main__":
    import asyncio

    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/"
    asyncio.run(
        navigate(
            int(sys.argv[2]) if len(sys.argv) > 2 else 10078,
            target,
        )
    )
