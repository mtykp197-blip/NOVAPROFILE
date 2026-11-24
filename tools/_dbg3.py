import asyncio, json, tempfile, time, urllib.request, sys
from pathlib import Path
import websockets
sys.path.insert(0, r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\tools")
import audit_surfaces as A
from novaprofile.core.generator import ProfileGenerator
from novaprofile.launcher.launcher import BrowserLauncher
from novaprofile.launcher.isolation import ProfileIsolation

cfg = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)
l = BrowserLauncher(isolation=ProfileIsolation(Path(tempfile.mkdtemp())))
l.launch(cfg, extra_args=["--remote-debugging-port=9413", "about:blank"])
time.sleep(6)
tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9413/json"))
ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]

exprs = [
    "1+1",
    "(async () => { return 1; })()",
    "(async () => { return await (function(){ return (1); })(); })()",
    "(async () => { return await (function(){ return (" + A.AUDIT_SCRIPT + ")(); })(); })()",
]

async def main():
    async with websockets.connect(ws_url, max_size=None) as ws:
        for i, e in enumerate(exprs):
            await ws.send(json.dumps({"id": i + 1, "method": "Runtime.evaluate",
                                      "params": {"expression": e, "awaitPromise": True, "returnByValue": True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i + 1:
                    txt = json.dumps(m.get("result", {}))
                    print(i, "len=", len(txt), txt[:220])
                    break

asyncio.run(main())
l.terminate(cfg.identity.id, graceful=False)
