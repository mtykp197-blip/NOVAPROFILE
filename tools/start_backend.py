"""Start the control plane on a free port and print its handshake.

The packaged application starts the backend itself and keeps the
credential in the shell's main process.  A script that wants to talk
to the control plane has to start one of its own, and the handshake
line is where the port and the credential are announced.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTROL_PLANE = REPO / "novaprofile" / "control_plane"


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / ".tmp_probe_data"

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "novaprofile.cli.main",
            "serve",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
            "--handshake",
        ],
        cwd=str(CONTROL_PLANE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + 60
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            print("backend:", line, file=sys.stderr)
            continue
        payload["_pid"] = process.pid
        print(json.dumps(payload))
        return 0

    process.terminate()
    print("no handshake", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
