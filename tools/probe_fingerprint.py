"""Check the surfaces a live profile reports.

The page is asked for the values that make a browser a browser: the
user agent, the screen, the hardware, the plugins, the languages, the
time zone.  Every one of them has to come from the profile rather than
from the host, and they have to agree with each other: a phone that
claims to be an Android phone and reports a desktop screen is a device
that does not exist.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

EXPRESSION = r"""
(function () {
  var out = {};
  var nav = navigator;

  out.userAgent = nav.userAgent;
  out.platform = nav.platform;
  out.vendor = nav.vendor;
  out.language = nav.language;
  out.languages = nav.languages ? Array.prototype.slice.call(nav.languages) : null;
  out.hardwareConcurrency = nav.hardwareConcurrency;
  out.deviceMemory = nav.deviceMemory;
  out.maxTouchPoints = nav.maxTouchPoints;
  out.doNotTrack = nav.doNotTrack;
  out.webdriver = nav.webdriver;

  out.chrome = !!nav.chrome;
  out.chromeApp = nav.chrome && nav.chrome.app ? {
    isInstalled: nav.chrome.app.isInstalled,
    installState: nav.chrome.app.InstallState,
    runningState: nav.chrome.app.RunningState
  } : null;
  out.chromeCsi = nav.chrome && nav.chrome.csi ? typeof nav.chrome.csi() : null;
  out.chromeLoadTimes = nav.chrome && nav.chrome.loadTimes ? typeof nav.chrome.loadTimes() : null;

  out.plugins = nav.plugins ? Array.prototype.map.call(nav.plugins, function (p) { return p.name; }) : null;
  out.mimeTypes = nav.mimeTypes ? Array.prototype.map.call(nav.mimeTypes, function (m) { return m.type; }) : null;

  out.screen = {
    width: screen.width, height: screen.height,
    availWidth: screen.availWidth, availHeight: screen.availHeight,
    colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth
  };
  out.inner = { width: innerWidth, height: innerHeight, dpr: devicePixelRatio };
  out.outer = { width: outerWidth, height: outerHeight };

  out.timezone = {
    resolved: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date().getTimezoneOffset()
  };
  out.intl = {
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
    calendar: Intl.DateTimeFormat().resolvedOptions().calendar,
    numbering: Intl.NumberFormat().resolvedOptions().numberingSystem
  };

  out.ready = window.__NOVA_READY__ === true;
  out.nativeSurfaces = window.__NOVA_NATIVE_SURFACES__ || null;

  out.leaks = {
    hasNOVAProfile: '__NOVA_PROFILE__' in window,
    hasNOVAPayload: '__NOVA_PAYLOAD__' in window,
    hasNOVANative: '__NOVA_NATIVE_SURFACES__' in window,
    hasNOVAReady: '__NOVA_READY__' in window
  };

  out.touch = {
    maxTouchPoints: nav.maxTouchPoints,
    ontouchstart: 'ontouchstart' in window,
    touchEvent: 'TouchEvent' in window
  };

  return JSON.stringify(out);
})()
"""


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def call(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        await self.ws.send(
            json.dumps({"id": ident, "method": method, "params": params or {}})
        )
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
        result = reply.get("result") or {}
        if result.get("exceptionDetails"):
            return {"error": str(result["exceptionDetails"])[:400]}
        return (result.get("result") or {}).get("value")


async def main():
    url = sys.argv[1]
    async with websockets.connect(url, max_size=30 * 1024 * 1024) as ws:
        client = Client(ws)
        raw = await client.evaluate(EXPRESSION)
        if isinstance(raw, str):
            print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(raw, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
