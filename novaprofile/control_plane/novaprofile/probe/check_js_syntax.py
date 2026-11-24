# Source Generated with Decompyle++
# File: check_js_syntax.pyc (Python 3.12)

"""Check that the assembled script is syntactically valid JavaScript.

The script the renderer runs is assembled from three parts: the
wrapper that holds the native core's list of the surfaces it owns, the
profile's payload, and the layer's code.  A mistake in any of them --
a missing semicolon, a stray brace -- is a script that fails to parse,
and a script that fails to parse takes the profile's fingerprint with
it.

The check runs in the browser that was built for the product, because
the browser is the thing that has to run it.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

def assemble(payload_json = None, surfaces = None):
    '''Assemble the script the way the native core does.'''
    return '(function () {\nvar __NOVA_NATIVE_SURFACES__ = ' + json.dumps(surfaces) + ';\nvar __NOVA_PAYLOAD__ = ' + payload_json + ';\n' + _runtime_code() + '\n})();\n'


def _runtime_code():
    RUNTIME_JS = RUNTIME_JS
    import novaprofile.js_runtime.builder
    return RUNTIME_JS


async def _check(browser = None, source = None, timeout = None):
    pass
# WARNING: Decompyle incomplete


def main(argv = None):
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument('--browser', type = Path, required = True)
    parser.parse_args(argv)
    print('not implemented yet')
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
