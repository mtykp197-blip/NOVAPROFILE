"""How the JavaScript layer reaches the page.

There are three ways to run a script before the page's own code, and they
are not equally good.  The choice matters, because the whole point of
the layer is that the page must not be able to tell.

The three ways
--------------
``document_start`` via a content script
    The earliest possible moment, but the script lands in an **isolated
    world**.  Its overrides are not visible to the page, so they have
    to be smuggled across with ``window.wrappedJSObject`` (Firefox only)
    or by injecting a ``<script>`` element into the document (Chrome).
    That second step is visible: the injected element is in the DOM and
    a page can find it.

``Page.addScriptToEvaluateOnNewDocument`` via the DevTools protocol
    The script runs in the page's own world, at ``document_start``,
    before any page script.  This is the correct mechanism.  It is what
    Puppeteer and Playwright use, and it is what the launcher uses when
    it drives a browser through the DevTools endpoint.

``--enable-features=...`` plus a native preload
    The only mechanism that leaves no trace, because it does not go
    through JavaScript at all.  This is what the native core does.

The launcher therefore uses the DevTools protocol when it has a browser
to attach to, and the native core when it has a patched Chromium.

Why the payload is still written to disk
----------------------------------------
The script has to be delivered somehow, and the delivery mechanism is
the weakest part of the chain.  Writing it into the user data
directory and passing the path through a command-line flag keeps the
payload out of the process arguments on most platforms -- the arguments
are readable by every process on the machine, the user data directory
is not.

So the file is written with restrictive permissions, it is removed when
the browser exits, and its digest is recorded so that a stale file from
a previous run is never served.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..schema.profile import ProfileConfig
from .builder import JSRuntimeBuilder

#: The name of the script inside the user data directory.
JS_RUNTIME_FILENAME = "nova_js_runtime.js"

#: The name of the manifest that records which script the browser was
#: given, so a stale file is detected instead of served.
JS_RUNTIME_MANIFEST = "nova_js_runtime.json"

#: The name of the payload inside the user data directory.
#:
#: The payload is the profile's values without the code that installs
#: them.  The code is compiled into the browser, so only the payload
#: travels, and the payload is what the browser reads.
JS_PAYLOAD_FILENAME = "nova_js_payload.js"

#: The name of the manifest that records which payload the browser was
#: given.
JS_PAYLOAD_MANIFEST = "nova_js_payload.json"


class InjectionPoint(str, Enum):
    """Where the script is injected."""

    #: ``Page.addScriptToEvaluateOnNewDocument`` -- the page's own world,
    #: before the first page script.  The correct mechanism.
    DOCUMENT_START = "document_start"

    #: A ``<script>`` element appended to the document.  The fallback for
    #: a browser that cannot be driven through the DevTools protocol.
    #: It is visible in the DOM.
    DOM_SCRIPT = "dom_script"

    #: Not injected at all.  The native core is doing the work.
    NATIVE = "native"


@dataclass(frozen=True)
class WrittenRuntime:
    """The result of writing the runtime to disk."""

    path: Path
    digest: str
    injection_point: InjectionPoint
    bytes_written: int


def _restrict(path: Path) -> None:
    """Make the file readable by its owner only, where the platform allows it."""

    try:
        mode = stat.S_IRUSR | stat.S_IWUSR
        os.chmod(path, mode)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        # Windows has no POSIX permission bits; the user data directory is
        # already per-user.
        pass


def write_payload(
    config: ProfileConfig,
    user_data_dir: Path,
    builder: JSRuntimeBuilder | None = None,
) -> WrittenRuntime:
    """Write the *payload* for ``config`` into ``user_data_dir``.

    The layer is two parts: the code, which is the same for every
    profile and is compiled into the browser, and the payload, which
    carries the profile's values.

    The payload is what travels.  The code is seventy-five kilobytes
    and does not fit on a Windows command line next to the payload;
    compiled into the browser it costs nothing to carry and cannot
    arrive late.

    The function is idempotent: if the file on disk already carries the
    digest of the payload that was just built, it is not rewritten.
    """

    builder = builder or JSRuntimeBuilder()
    payload = builder.build_payload_only(config)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    user_data_dir.mkdir(parents=True, exist_ok=True)
    path = user_data_dir / JS_PAYLOAD_FILENAME
    manifest_path = user_data_dir / JS_PAYLOAD_MANIFEST

    existing = _read_manifest(manifest_path)
    if existing.get("digest") == digest and path.exists():
        return WrittenRuntime(
            path=path,
            digest=digest,
            injection_point=InjectionPoint.DOCUMENT_START,
            bytes_written=path.stat().st_size,
        )

    path.write_text(payload, encoding="utf-8")
    _restrict(path)

    manifest_path.write_text(
        json.dumps(
            {
                "digest": digest,
                "profile_id": config.profile_id,
                "injection_point": InjectionPoint.DOCUMENT_START.value,
                "bytes": len(payload.encode("utf-8")),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _restrict(manifest_path)

    return WrittenRuntime(
        path=path,
        digest=digest,
        injection_point=InjectionPoint.DOCUMENT_START,
        bytes_written=len(payload.encode("utf-8")),
    )


def write_runtime(
    config: ProfileConfig,
    user_data_dir: Path,
    injection_point: InjectionPoint = InjectionPoint.DOCUMENT_START,
    builder: JSRuntimeBuilder | None = None,
) -> WrittenRuntime:
    """Write the runtime for ``config`` into ``user_data_dir``.

    The function is idempotent: if the file on disk already carries the
    digest of the script that was just built, it is not rewritten.
    """

    builder = builder or JSRuntimeBuilder()
    script = builder.build(config)
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()

    user_data_dir.mkdir(parents=True, exist_ok=True)
    path = user_data_dir / JS_RUNTIME_FILENAME
    manifest_path = user_data_dir / JS_RUNTIME_MANIFEST

    existing = _read_manifest(manifest_path)
    if existing.get("digest") == digest and path.exists():
        return WrittenRuntime(
            path=path,
            digest=digest,
            injection_point=injection_point,
            bytes_written=path.stat().st_size,
        )

    path.write_text(script, encoding="utf-8")
    _restrict(path)

    manifest_path.write_text(
        json.dumps(
            {
                "digest": digest,
                "profile_id": config.profile_id,
                "injection_point": injection_point.value,
                "bytes": len(script.encode("utf-8")),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _restrict(manifest_path)

    return WrittenRuntime(
        path=path,
        digest=digest,
        injection_point=injection_point,
        bytes_written=len(script.encode("utf-8")),
    )


def _read_manifest(path: Path) -> dict[str, object]:
    """Read the manifest, tolerating a missing or damaged file."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def remove_runtime(user_data_dir: Path) -> bool:
    """Remove the runtime from the user data directory.

    Called when the browser exits.  The payload is a fingerprint, and a
    fingerprint left on disk in the clear is a liability: the next
    profile in the same directory would be able to read it.
    """

    removed = False
    for name in (
        JS_RUNTIME_FILENAME,
        JS_RUNTIME_MANIFEST,
        JS_PAYLOAD_FILENAME,
        JS_PAYLOAD_MANIFEST,
    ):
        path = user_data_dir / name
        if path.exists():
            path.unlink()
            removed = True
    return removed


def devtools_script(exported: WrittenRuntime) -> dict[str, object]:
    """The CDP command that installs the script.

    ``Page.addScriptToEvaluateOnNewDocument`` takes the source of the
    script, not a path, so the file is read here.  The command returns an
    identifier that can be handed to ``Page.removeScriptToEvaluateOnNewDocument``
    when the browser closes.
    """

    source = exported.path.read_text(encoding="utf-8")
    return {
        "method": "Page.addScriptToEvaluateOnNewDocument",
        "params": {"source": source},
    }


__all__ = [
    "JS_RUNTIME_FILENAME",
    "JS_RUNTIME_MANIFEST",
    "InjectionPoint",
    "WrittenRuntime",
    "write_runtime",
    "remove_runtime",
    "devtools_script",
]
