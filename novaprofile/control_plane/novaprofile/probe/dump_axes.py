# Source Generated with Decompyle++
# File: dump_axes.pyc (Python 3.12)

'''Read the raw surface payload out of the browser and print it.

The verifier only reports the *verdict* of every axis.  When an axis
comes back empty it does not say why, and the reason is usually a
single JavaScript exception in the page.  This runs the same extractor
against the same page and prints the raw object, so that the exception
is visible.
'''
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path

async def _run(browser = None, preset = None, timeout = None):
    pass
# WARNING: Decompyle incomplete


def main(argv = None):
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument('--browser', type = Path, required = True)
    parser.add_argument('--preset', default = 'pixel-8-pro-android14')
    parser.add_argument('--timeout', type = float, default = 60)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.browser, args.preset, args.timeout))

if __name__ == '__main__':
    raise SystemExit(main())
