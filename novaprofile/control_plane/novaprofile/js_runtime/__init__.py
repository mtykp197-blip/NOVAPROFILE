"""The page-context JavaScript layer.

This package builds the script that a stock Chromium executes when the
native core is not available.  See :mod:`novaprofile.js_runtime.builder`
for the rules the runtime follows and :mod:`novaprofile.js_runtime.injector`
for how it reaches the page.
"""

from __future__ import annotations

from .builder import (
    RUNTIME_JS,
    JSRuntimeBuilder,
    JSRuntimePayload,
    build_script,
    runtime_digest,
)
from .injector import (
    JS_PAYLOAD_FILENAME,
    JS_PAYLOAD_MANIFEST,
    JS_RUNTIME_FILENAME,
    JS_RUNTIME_MANIFEST,
    InjectionPoint,
    WrittenRuntime,
    devtools_script,
    remove_runtime,
    write_payload,
    write_runtime,
)

__all__ = [
    "RUNTIME_JS",
    "JSRuntimeBuilder",
    "JSRuntimePayload",
    "build_script",
    "runtime_digest",
    "JS_PAYLOAD_FILENAME",
    "JS_PAYLOAD_MANIFEST",
    "JS_RUNTIME_FILENAME",
    "JS_RUNTIME_MANIFEST",
    "InjectionPoint",
    "WrittenRuntime",
    "devtools_script",
    "remove_runtime",
    "write_payload",
    "write_runtime",
]
