"""A measurement ledger for the project.

Every number the project claims about itself has to be reproducible and
has to be findable again later.  Measuring the collision rate of a
hundred million profiles takes ten minutes; nobody should ever have to
do it twice, and nobody should have to trust a figure quoted in a chat
message.

This module is the single place where measurements are recorded.  A
measurement is a JSON file under ``docs/measurements/data`` plus a row
in ``docs/measurements/INDEX.md``.

The shape of a record
---------------------

.. code-block:: json

    {
        "slug": "scale-collision",
        "measured_at": "2026-11-21T22:50:00+07:00",
        "environment": {"python": "3.12", "platform": "win32"},
        "command": "python -m novaprofile.scripts.scale_check --count 200000",
        "metrics": {"total_collisions": 0},
        "conclusion": "Zero collisions on every guarded axis.",
        "reusable": true,
        "sha256": "..."
    }

``reusable`` is the field that answers the question the ledger exists
for: *may I use this number, or do I have to measure again?*  A record
with ``reusable`` false is kept -- it still tells the next person what
was tried -- but it must not be cited as the current answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The project root, which holds ``docs/measurements``.
#:
#: ``measurements.py`` lives at
#: ``<project>/control_plane/novaprofile/scripts/measurements.py``, so the
#: project directory is three levels up.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: The measurement ledger directory.
MEASUREMENTS_DIR = PROJECT_ROOT / "docs" / "measurements"

#: Where the raw records live.
DATA_DIR = MEASUREMENTS_DIR / "data"

#: The human-readable index.
INDEX_PATH = MEASUREMENTS_DIR / "INDEX.md"

#: The prefix every record file carries.
_RECORD_PREFIX = "record-"


@dataclass(frozen=True)
class Measurement:
    """One recorded measurement."""

    slug: str
    measured_at: str
    command: str
    metrics: dict[str, Any]
    conclusion: str
    reusable: bool
    environment: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "measured_at": self.measured_at,
            "command": self.command,
            "metrics": self.metrics,
            "conclusion": self.conclusion,
            "reusable": self.reusable,
            "environment": self.environment,
            "notes": self.notes,
        }


def _utc_now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _current_environment() -> dict[str, Any]:
    """Describe the machine the measurement was taken on."""
    env: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    try:
        env["cpu_physical"] = len(
            [
                line
                for line in subprocess.run(  # noqa: S603
                    ["wmic", "cpu", "get", "NumberOfCores"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                ).stdout.splitlines()[1:]
                if line.strip()
            ]
        )
    except (OSError, subprocess.SubprocessError, IndexError):
        env["cpu_physical"] = None
    return env


def _payload_digest(payload: dict[str, Any]) -> str:
    """The checksum of a record's content, excluding the checksum itself."""
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_path(slug: str, measured_at: str) -> Path:
    """The file a record with this slug and timestamp lives in."""
    stamp = measured_at.replace(":", "").replace("-", "").replace("+", "_")
    stamp = stamp.split(".")[0]
    return DATA_DIR / f"{_RECORD_PREFIX}{slug}-{stamp}.json"


def save(
    slug: str,
    metrics: dict[str, Any],
    conclusion: str,
    command: str,
    reusable: bool = True,
    notes: str = "",
    measured_at: str | None = None,
) -> Path:
    """Write a measurement into the ledger.

    Returns the path of the record that was written.
    """

    measured_at = measured_at or _utc_now()
    payload: dict[str, Any] = {
        "slug": slug,
        "measured_at": measured_at,
        "command": command,
        "metrics": metrics,
        "conclusion": conclusion,
        "reusable": reusable,
        "environment": _current_environment(),
        "notes": notes,
    }
    payload["sha256"] = _payload_digest(payload)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = record_path(slug, measured_at)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _rebuild_index()
    return path


def load_all() -> list[Measurement]:
    """Every record in the ledger, oldest first."""
    if not DATA_DIR.exists():
        return []
    records: list[Measurement] = []
    for path in sorted(DATA_DIR.glob(f"{_RECORD_PREFIX}*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "slug" not in payload:
            continue
        records.append(
            Measurement(
                slug=str(payload["slug"]),
                measured_at=str(payload.get("measured_at", "")),
                command=str(payload.get("command", "")),
                metrics=dict(payload.get("metrics", {})),
                conclusion=str(payload.get("conclusion", "")),
                reusable=bool(payload.get("reusable", False)),
                environment=dict(payload.get("environment", {})),
                notes=str(payload.get("notes", "")),
            )
        )
    records.sort(key=lambda m: m.measured_at)
    return records


def latest(slug: str, reusable_only: bool = True) -> Measurement | None:
    """The most recent record for a slug.

    With ``reusable_only`` the search skips records that were marked as
    not reusable, so a caller that quotes a number quotes a number that
    was taken under valid conditions.
    """

    candidates = [m for m in load_all() if m.slug == slug]
    if reusable_only:
        candidates = [m for m in candidates if m.reusable]
    return candidates[-1] if candidates else None


def all_records() -> dict[str, list[Measurement]]:
    """The ledger grouped by slug, oldest first within each group."""
    grouped: dict[str, list[Measurement]] = {}
    for record in load_all():
        grouped.setdefault(record.slug, []).append(record)
    return grouped


def _index_row(record: Measurement) -> str:
    metrics = ", ".join(f"`{k}`={v}" for k, v in list(record.metrics.items())[:4])
    if len(record.metrics) > 4:
        metrics += f", +{len(record.metrics) - 4} more"
    flag = "✅" if record.reusable else "♻️"
    return (
        f"| `{record.slug}` | {record.measured_at[:16]} | {flag} | {metrics} | "
        f"{record.conclusion} |"
    )


def _rebuild_index() -> None:
    """Rewrite ``INDEX.md`` from the records on disk.

    The index is generated, never edited by hand: that way it cannot
    drift away from the records it describes.
    """

    grouped = all_records()
    lines: list[str] = [
        "# Chỉ mục phép đo",
        "",
        "_Sinh tự động từ `data/`. Đừng sửa tay — chạy "
        "`python -m novaprofile.scripts.measurements` để cập nhật._",
        "",
        "| Slug | Thời điểm | Dùng lại | Số liệu chính | Kết luận |",
        "| --- | --- | --- | --- | --- |",
    ]

    for slug in sorted(grouped):
        records = grouped[slug]
        current = next((m for m in reversed(records) if m.reusable), records[-1])
        lines.append(_index_row(current))

        history = [m for m in records if m is not current]
        if history:
            lines.append("")
            lines.append(
                f"<details><summary>Lịch sử {slug} "
                f"({len(history)} lần trước)</summary>"
            )
            lines.append("")
            for record in history:
                lines.append(f"- `{record.measured_at[:16]}` — {record.conclusion}")
            lines.append("")
            lines.append("</details>")

    lines.append("")
    MEASUREMENTS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


def verify() -> list[str]:
    """Check every record's checksum.

    Returns the list of records whose content no longer matches the
    checksum that was stored with it.
    """

    broken: list[str] = []
    if not DATA_DIR.exists():
        return broken
    for path in sorted(DATA_DIR.glob(f"{_RECORD_PREFIX}*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            broken.append(f"{path.name}: unreadable")
            continue
        if not isinstance(payload, dict):
            broken.append(f"{path.name}: not an object")
            continue
        stored = payload.get("sha256")
        if not stored:
            broken.append(f"{path.name}: no checksum")
            continue
        body = {k: v for k, v in payload.items() if k != "sha256"}
        if _payload_digest(body) != stored:
            broken.append(f"{path.name}: checksum mismatch")
    return broken


def ensure_ledger_exists() -> None:
    """Create the ledger directories if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        _rebuild_index()


__all__ = [
    "DATA_DIR",
    "INDEX_PATH",
    "MEASUREMENTS_DIR",
    "Measurement",
    "all_records",
    "ensure_ledger_exists",
    "latest",
    "load_all",
    "record_path",
    "save",
    "verify",
]


if __name__ == "__main__":  # pragma: no cover
    ensure_ledger_exists()
    broken = verify()
    if broken:
        print(f"{len(broken)} broken record(s):")
        for item in broken:
            print(f"  {item}")
        sys.exit(1)
    print(f"ledger ok: {len(load_all())} record(s)")
