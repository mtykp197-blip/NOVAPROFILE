"""Launch the core by hand and read what it says about the payload.

The browser's own log is the only place that says whether the payload
arrived, and whether the layer was assembled.  The launch flags are the
ones the launcher uses, plus the logging switches, which are what make
the browser write its own diagnostics to the standard error.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(r"C:\chromium\src\out\nova-win\chrome.exe")
PROBE = Path(r"C:\nova_probe")
USER_DATA = Path(r"C:\nova_probe\user-data")


def main() -> int:
    if not CORE.is_file():
        print(f"no core at {CORE}")
        return 1

    command = [
        str(CORE),
        "--remote-debugging-port=10099",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={USER_DATA}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-logging=stderr",
        "--v=1",
        f"--nova-config={PROBE / 'core.json'}",
        f"--nova-js-payload={PROBE / 'payload.js'}",
        "--nova-timezone=Asia/Ho_Chi_Minh",
        "--nova-locale=vi-VN",
        "about:blank",
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    time.sleep(14)
    process.terminate()
    try:
        output = process.stdout.read() if process.stdout else ""
    except Exception:
        output = ""

    lines = [line for line in output.splitlines() if "NOVA" in line]
    print(f"--- {len(lines)} NOVA lines ---")
    for line in lines[:80]:
        print(line)

    if not lines:
        print("--- no NOVA lines; last 40 lines of the log ---")
        for line in output.splitlines()[-40:]:
            print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
