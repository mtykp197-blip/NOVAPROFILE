"""Check that the JavaScript runtime carries the fixes the core needs.

The core hands the payload to the layer as the argument of the wrapper
it opens around the layer's code.  The layer has to read it there, or
the layer does nothing at all on a native core.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.js_runtime import builder  # noqa: E402

js = builder.RUNTIME_JS

checks = {
    "payload local lookup": "typeof __NOVA_PAYLOAD__" in js,
    "payload window fallback": "window.__NOVA_PROFILE__" in js,
    "navigator.chrome": "navigator.chrome" in js,
    "chrome app": "isInstalled" in js,
    "no legacy PDFViewer": "PDFViewer" not in js,
}

for name, ok in checks.items():
    print(("  ok   " if ok else "  FAIL ") + name)

sys.exit(0 if all(checks.values()) else 1)
