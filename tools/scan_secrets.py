import re
import subprocess
import sys

PATTERNS = [
    ("github token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("openai key", r"sk-[A-Za-z0-9]{20,}"),
    ("google key", r"AIza[0-9A-Za-z_\-]{30,}"),
    ("slack token", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("private key", r"BEGIN [A-Z ]*PRIVATE KEY"),
    ("aws key", r"AKIA[0-9A-Z]{16}"),
    ("bearer literal", r"[Bb]earer\s+[A-Za-z0-9_\-\.]{24,}"),
    ("password literal", r"(?i)pass(word|wd)\s*[=:]\s*['\"][^'\"]{6,}['\"]"),
    ("api key literal", r"(?i)api[_-]?key\s*[=:]\s*['\"][^'\"]{12,}['\"]"),
    ("secret literal", r"(?i)secret\s*[=:]\s*['\"][^'\"]{12,}['\"]"),
]

files = subprocess.run(
    ["git", "ls-files"], capture_output=True, text=True, check=True
).stdout.splitlines()
files = [f for f in files if "node_modules" not in f and "dist-new" not in f]

hits = 0
for path in files:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for number, line in enumerate(handle, 1):
                for label, pattern in PATTERNS:
                    if re.search(pattern, line):
                        print(f"{label}: {path}:{number}: {line.strip()[:120]}")
                        hits += 1
    except (OSError, UnicodeDecodeError):
        continue

print(f"\nscanned {len(files)} tracked files, {hits} suspicious line(s)")
sys.exit(0)
