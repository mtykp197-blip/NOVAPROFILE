"""Show how the JavaScript layer handles the font measurements."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))

from novaprofile.js_runtime import builder  # noqa: E402

js = builder.RUNTIME_JS

index = js.find("measureText")
while index != -1 and index < len(js):
    start = max(0, index - 300)
    print("=" * 70)
    print(js[start:index + 400])
    index = js.find("measureText", index + 1)
    if index > 0 and index > 60000:
        break
