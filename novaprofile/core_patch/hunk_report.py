"""Report the hunk counts of the shipped NOVA patch series.

A hunk header declares how many lines of the old file and of the new
file the hunk covers.  ``git apply`` reads the body against that
declaration and refuses the patch when the two disagree, so the counts
are worth checking on their own -- a diff viewer shows the body and
hides the problem.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: The directory the patch series lives in.
#:
#: Resolved from the location of this file, with an environment override
#: for the rare case where the series sits elsewhere.
PATCH_DIR = Path(
    os.environ.get(
        "NOVA_PATCH_DIR",
        Path(__file__).resolve().parent / "patches",
    )
)

DIFF_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)

total_files = total_hunks = total_add = total_del = 0

for path in sorted(PATCH_DIR.glob("*.patch")):
    text = path.read_text(encoding="utf-8")
    files = DIFF_RE.findall(text)
    hunks = HUNK_RE.findall(text)

    added = removed = 0
    for line in text.split("\n"):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1

    print(f"=== {path.name} ===")
    print(f"  files : {len(files)}")
    print(f"  hunks : {len(hunks)}")
    print(f"  +{added} / -{removed}")
    print(f"  bytes : {len(text.encode('utf-8'))}")
    print(f"  CR    : {text.count(chr(13))}  (must be 0)")
    for _a, b in files:
        print(f"    {b}")
    print()

    total_files += len(files)
    total_hunks += len(hunks)
    total_add += added
    total_del += removed

print(f"TOTAL: {total_files} files, {total_hunks} hunks, +{total_add} / -{total_del}")
