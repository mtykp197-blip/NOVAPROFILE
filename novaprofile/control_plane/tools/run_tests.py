"""Run a subset of the test suite and write the report to a file.

The Windows shell mangles the report of a long run, so the report goes
to a file and the file is read back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parent.parent / "test_report.txt"


def main(argv: list[str]) -> int:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *argv,
        "-q",
        "-p",
        "no:cacheprovider",
        "--tb=line",
        "-rf",
        "--basetemp",
        str(Path(__file__).resolve().parent.parent / ".pytest_tmp"),
    ]
    result = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
    )
    REPORT.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    print(f"exit code {result.returncode}; report at {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
