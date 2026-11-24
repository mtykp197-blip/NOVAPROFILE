"""Report which surfaces the JavaScript layer guards.

A surface the native core owns must be guarded, or the page sees the
native value with the layer's value on top of it.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.js_runtime import builder  # noqa: E402

js = builder.RUNTIME_JS

guarded = sorted(set(re.findall(r"nativeOwns\('([a-z0-9_]+)'\)", js)))
print("guarded surfaces:", guarded)

print()
print("payload local:", "typeof __NOVA_PAYLOAD__" in js)
print("surfaces local:", "typeof __NOVA_NATIVE_SURFACES__" in js)
