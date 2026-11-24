"""Create profiles, launch them, and compare what the browser reports.

The comparison is the one a site makes: it reads a handful of values
off the running browser and asks whether two profiles agree.  The
canvas is the sharpest of those readings, because the encoded PNG is a
hash of every pixel, so two profiles whose canvases agree byte for
byte are one profile as far as the site is concerned.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import urllib.request

import websockets

BASE = "http://127.0.0.1:8799"
KEY = sys.argv[1]

EXPRESSION = r"""
(function () {
  function canvas() {
    var c = document.createElement('canvas');
    c.width = 240; c.height = 60;
    var g = c.getContext('2d');
    g.textBaseline = 'top';
    g.font = "14px 'Arial'";
    g.fillStyle = '#f60';
    g.fillRect(100, 1, 62, 20);
    g.fillStyle = '#069';
    g.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
    return c.toDataURL();
  }
  function audio() {
    var ctx = new OfflineAudioContext(1, 5000, 44100);
    var osc = ctx.createOscillator();
    osc.type = 'triangle';
    osc.frequency.value = 10000;
    var comp = ctx.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ctx.destination);
    osc.start(0);
    ctx.startRendering();
    return ctx.oncomplete ? 'sync' : 'async';
  }
  var gl = document.createElement('canvas').getContext('webgl');
  var ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return JSON.stringify({
    ua: navigator.userAgent,
    platform: navigator.platform,
    cores: navigator.hardware_concurrency,
    deviceMemory: navigator.deviceMemory,
    touch: navigator.maxTouchPoints,
    screen: [screen.width, screen.height, screen.colorDepth, window.devicePixelRatio],
    gpu: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'n/a',
    canvas: canvas(),
  });
})()
"""


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "x-nova-key": KEY,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as handle:
        raw = handle.read().decode()
    return json.loads(raw) if raw else None


async def read_target(port: int):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=30) as handle:
        targets = json.load(handle)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError(f"no page target on port {port}")
    # A target that carries a real page is preferred over the blank one
    # the browser opens with, but the blank one is still a target.
    page = next(
        (t for t in pages if t.get("url", "").startswith("http")), pages[0]
    )
    async with websockets.connect(
        page["webSocketDebuggerUrl"], max_size=30 * 1024 * 1024
    ) as ws:
        ident = 1
        await ws.send(
            json.dumps(
                {
                    "id": ident,
                    "method": "Runtime.evaluate",
                    "params": {"expression": EXPRESSION, "returnByValue": True},
                }
            )
        )
        while True:
            raw = await ws.recv()
            message = json.loads(raw)
            if message.get("id") == ident:
                result = (message.get("result") or {}).get("result") or {}
                return json.loads(result.get("value"))


async def main():
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    print("creating profiles...")
    created = []
    for index in range(count):
        response = api("POST", "/api/profiles", {"preset_id": "pixel-8-pro-android14"})
        profile = response.get("identity", response)
        profile_id = profile.get("id") or response.get("id")
        created.append((profile_id, response))

    ports = []
    for profile_id, _ in created:
        print(f"launching {profile_id}...")
        api("POST", f"/api/profiles/{profile_id}/launch")
        ports.append(profile_id)

    await asyncio.sleep(12)

    readings = []
    for profile_id in ports:
        detail = api("GET", f"/api/profiles/{profile_id}")
        if isinstance(detail, dict) and "items" in detail:
            detail = detail["items"][0] if detail["items"] else {}
        config = detail.get("config", detail)
        port = config.get("debug_port") or config.get("cdp_port")
        if not port:
            digest = hashlib.sha256(profile_id.encode("utf-8")).digest()
            port = 9333 + (int.from_bytes(digest[:2], "big") % 1000)
        try:
            value = await read_target(int(port))
        except Exception as exc:
            print(f"  {profile_id}: port {port} -> {exc}")
            continue
        readings.append((profile_id, value))
        print(f"  {profile_id}: port {port}")

    print()
    hashes = []
    for profile_id, value in readings:
        digest = hashlib.sha256(value["canvas"].encode()).hexdigest()[:16]
        hashes.append(digest)
        print(f"  {profile_id}")
        print(f"    keys = {sorted(value.keys())}")
        print(f"    canvas sha256[:16] = {digest}  len={len(value['canvas'])}")
        print(f"    ua    = {value.get('ua')}")
        print(f"    gpu   = {value.get('gpu')}")
        print(
            "    cores={cores} mem={mem} touch={touch} platform={platform}".format(
                cores=value.get("cores"),
                mem=value.get("deviceMemory"),
                touch=value.get("touch"),
                platform=value.get("platform"),
            )
        )
        print(f"    screen={value['screen']}")

    print()
    if len(hashes) > 1:
        print("canvas hashes distinct:", len(set(hashes)) == len(hashes))
        print("RESULT:", "PASS" if len(set(hashes)) == len(hashes) else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
