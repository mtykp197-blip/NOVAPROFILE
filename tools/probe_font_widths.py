"""Measure the fonts the way a site measures them.

A site that fingerprints the fonts does not ask the page whether a font
is there: it draws text and measures the width, and it compares the
width of a family it suspects against the width of a family the device
certainly does not have.  Every missing family has to measure the same
as every other missing family -- otherwise the page learns which fonts
the device carries, one measurement at a time.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

EXPRESSION = r"""
(function () {
  function width(family) {
    var canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    ctx.font = '72px "' + family + '"';
    return ctx.measureText('mmmwwwiiilll0123456789').width;
  }

  var installed = [
    'Roboto', 'Noto Sans Display', 'Droid Sans Mono', 'Droid Serif',
    'Noto Color Emoji', 'Noto Sans Devanagari', 'Noto Sans Symbols',
    'Noto Sans Thai', 'Coming Soon', 'Cutive Mono', 'Dancing Script',
    'Eczar', 'Sourc Code Pro', 'Ubuntu Medium'
  ];

  var suspects = [
    'Arial', 'Helvetica', 'Times New Roman', 'Courier New', 'Verdana',
    'Segoe UI', 'Calibri', 'Cambria', 'Consolas', 'Tahoma', 'Georgia',
    'Trebuchet MS', 'Impact', 'Comic Sans MS'
  ];

  var widths = {};
  installed.forEach(function (family) { widths[family] = width(family); });
  suspects.forEach(function (family) { widths[family] = width(family); });

  // The width of a family the device certainly does not have.
  var absentReference = width('__nova_absent__');

  var suspectWidths = suspects.map(function (f) { return widths[f]; });
  var sameAsAbsent = suspectWidths.every(function (w) {
    return Math.abs(w - absentReference) < 0.5;
  });
  var allDistinct = new Set(suspectWidths).size === suspectWidths.length;

  return JSON.stringify({
    widths: widths,
    absentReference: absentReference,
    suspectsAllMeasureSameAsAbsent: sameAsAbsent,
    suspectsAllDistinct: allDistinct
  });
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
        data = json.loads(raw)

        print("absent reference width:", data["absentReference"])
        print()
        print("suspects all measure the same as absent:",
              data["suspectsAllMeasureSameAsAbsent"])
        print("suspects all distinct (a leak):", data["suspectsAllDistinct"])
        print()
        for family, value in data["widths"].items():
            marker = "ABSENT-REF" if abs(value - data["absentReference"]) < 0.5 else ""
            print(f"  {family:24s} {value:10.4f} {marker}")


if __name__ == "__main__":
    asyncio.run(main())
