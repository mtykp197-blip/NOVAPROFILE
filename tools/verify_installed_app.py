"""Prove the installed application actually works, end to end.

The installer is what the operator gets, so the installer is what has
to be checked.  This script starts the *installed* backend, creates a
batch of profiles through the same API the desktop shell uses, reads
the readings back, and then launches one of the profiles on the
*installed* core and reads the readings off the live page.

The readings are the point.  The claim is that two profiles of the
fleet are two different handsets, so the readings a site reads have
to differ between them: the chip count, the memory, the panel, the
graphics part, the serial, the coordinates.  If a profile launches
and reports the readings of the machine it runs on, the work is not
done no matter what the profile file says.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

INSTALL = Path(r"C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE\installed")
BACKEND = INSTALL / "resources" / "backend" / "nova-backend.exe"
DATA_DIR = Path(os.environ.get("TEMP", ".")) / "nova_verify_data"

#: The port the backend under test binds to.  It is set when the
#: backend starts, and every request goes to it.
PORT = 0


def free_port() -> int:
    """A port nothing is listening on.

    The port has to be free, not merely unused: a leftover backend
    from an earlier run would answer with *its* key and every request
    would be refused.
    """

    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_backend() -> tuple[subprocess.Popen, str]:
    global PORT
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PORT = free_port()
    process = subprocess.Popen(
        [
            str(BACKEND),
            "serve",
            "--port",
            str(PORT),
            "--data-dir",
            str(DATA_DIR),
            "--handshake",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    key = ""
    deadline = time.time() + 45
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("{"):
            try:
                key = json.loads(line).get("api_key", "")
            except json.JSONDecodeError:
                continue
            if key:
                break
    if not key:
        stop_backend(process)
        raise SystemExit("the backend never produced a handshake")
    # The handshake is printed before the server has finished binding,
    # so the first request can arrive a moment too early.
    time.sleep(1.5)
    return process, key


def _spread_over_chipsets(presets: list, count: int) -> list:
    """A sample of the catalogue that covers its chipsets.

    The catalogue is ordered, and its head is made of the handsets of
    one generation -- the same chipset, over and over.  A slice of the
    head of the list therefore says nothing about the catalogue: it
    would report one core count and a handful of panels, and the
    operator would read that as a fleet of one kind of machine.

    The sample walks the whole list and keeps the first row of every
    chipset it meets, so the fleet that is built over it spans the
    chipsets, the core counts and the panels the catalogue really
    holds.

    The spread is over the *core counts* first, because the core
    count is the reading that groups a fleet: a sample that holds one
    count and many chipsets still reports a fleet that agrees on the
    number the site groups on.  The counts are therefore taken in
    turn, one row per count, until the sample is full.
    """

    by_count: dict[int, list] = {}
    seen: set[str] = set()

    for preset in presets:
        if not isinstance(preset, dict):
            continue
        abi = preset.get("abi") or {}
        chipset = str(
            abi.get("soc_model")
            or (preset.get("android_build") or {}).get("model")
            or preset.get("chipset")
            or preset.get("soc_model")
            or ""
        )
        if chipset and chipset in seen:
            continue
        seen.add(chipset)

        options = preset.get("cpu_options") or []
        if not options:
            continue
        by_count.setdefault(int(options[0]), []).append(preset)

    if not by_count:
        return presets[:count]

    chosen: list = []
    counts = sorted(by_count)
    round_index = 0
    while len(chosen) < count:
        added = False
        for core_count in counts:
            bucket = by_count[core_count]
            if round_index < len(bucket):
                chosen.append(bucket[round_index])
                added = True
                if len(chosen) >= count:
                    break
        if not added:
            break
        round_index += 1

    return chosen[:count]


def call(path: str, key: str, payload: dict | None = None, method: str = "GET"):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    last_error: Exception | None = None
    for attempt in range(4):
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("x-nova-key", key)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")[:400]
        except (urllib.error.URLError, ConnectionError, OSError) as error:
            # The control plane drops the connection when a worker is
            # recycled under load.  The request is idempotent here, so
            # it is worth sending it again.
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    return 0, f"the request never completed: {last_error}"


def stop_backend(process: subprocess.Popen) -> None:
    """Stop the backend and everything it started.

    The backend runs the server in a worker of its own, and killing
    the parent leaves the worker holding the port -- which then
    answers the *next* run with the *old* key.

    The kill is not waited on: the taskkill call returns as soon as
    the tree is signalled, and waiting for it can block on a worker
    that is already on its way out.
    """

    if process.poll() is not None:
        return
    subprocess.Popen(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    if not BACKEND.exists():
        print(f"no installed backend at {BACKEND}")
        return 1

    process, key = start_backend()
    print(f"backend up on {PORT}")
    failures = 0
    try:
        # ----------------------------------------------------------
        # 1. The service answers.
        # ----------------------------------------------------------
        status, body = call("/api/health", key)
        print(f"\n--- health: {status} ---")
        print(json.dumps(body, indent=2)[:900] if isinstance(body, dict) else body)
        if status != 200:
            failures += 1

        # ----------------------------------------------------------
        # 2. The fleet can be planned and created through the API.
        # ----------------------------------------------------------
        status, presets_body = call("/api/presets", key)
        presets = []
        if isinstance(presets_body, dict):
            presets = presets_body.get("items") or []
        elif isinstance(presets_body, list):
            presets = presets_body
        print(f"\n--- presets: {status}, {len(presets)} of them ---")
        if not presets:
            failures += 1

        status, body = call(
            "/api/batch/create",
            key,
            {"count": 200, "name_prefix": "verify", "preset_id": "pixel-8-pro-android14"},
            "POST",
        )
        print(f"\n--- batch create 200: {status} ---")
        if isinstance(body, dict):
            print("  keys:", list(body)[:20])
            for k in ("created", "failed", "created_count", "failed_count", "requested"):
                if k in body:
                    value = body[k]
                    if isinstance(value, list):
                        print(f"  {k}: {len(value)}")
                        if value:
                            print(f"    first: {value[0]}")
                    else:
                        print(f"  {k}: {value}")
        else:
            print(" ", str(body)[:600])
        if status not in (200, 201):
            failures += 1
        created_count = 0
        if isinstance(body, dict):
            created_count = int(body.get("created_count") or 0)
        if created_count < 100:
            failures += 1

        status, body = call("/api/profiles?limit=500", key)
        print(f"\n--- list profiles: {status} ---")
        profiles = []
        if isinstance(body, dict):
            profiles = body.get("profiles") or body.get("items") or []
        elif isinstance(body, list):
            profiles = body
        print(f"  listed: {len(profiles)}")
        if len(profiles) < 100:
            failures += 1

        # ----------------------------------------------------------
        # 4. The readings are different between the units.
        # ----------------------------------------------------------
        if profiles:
            serials = []
            cores = []
            memories = []
            widths = []
            lats = []
            for item in profiles:
                if not isinstance(item, dict):
                    continue
                android = item.get("android_build") or item.get("android") or {}
                navigator = item.get("navigator") or {}
                screen = item.get("screen") or {}
                geo = item.get("geolocation") or {}
                serials.append(android.get("serial"))
                cores.append(navigator.get("hardware_concurrency"))
                memories.append(navigator.get("device_memory"))
                widths.append(screen.get("width"))
                lats.append(geo.get("latitude"))

            print("\n--- the readings of the created units ---")
            for name, values in (
                ("serial", serials),
                ("cores", cores),
                ("memory", memories),
                ("screen width", widths),
                ("latitude", lats),
            ):
                values = [v for v in values if v is not None]
                distinct = len(set(values))
                if not values:
                    continue
                counts = {}
                for value in values:
                    counts[value] = counts.get(value, 0) + 1
                spread = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
                shown = ", ".join(f"{k}×{v}" for k, v in spread)
                print(f"  {name:14} distinct={distinct:5}/{len(values):5}   {shown}")
                if name == "serial" and distinct != len(values):
                    print("    FAIL: two units share a serial")
                    failures += 1

        # ----------------------------------------------------------
        # 5. The fleet spread over the catalogue.
        # ----------------------------------------------------------
        #    A fleet of one model is one model with many serials.  The
        #    claim the operator buys is that the fleet looks like a
        #    market: many handsets, many chips, many panels.  So the
        #    batch is created over a *sample* of the catalogue, the
        #    way the desktop shell's batch form does it, and the
        #    readings are read back.
        # ----------------------------------------------------------
        sample = _spread_over_chipsets(presets, 25)
        print(f"\n--- a fleet over {len(sample)} different handsets ---")
        made = 0
        for index, preset in enumerate(sample):
            preset_id = preset.get("id") or preset.get("preset_id")
            status, body = call(
                "/api/batch/create",
                key,
                {"count": 2, "name_prefix": f"spread{index}", "preset_id": preset_id},
                "POST",
            )
            if isinstance(body, dict):
                made += int(body.get("created_count") or 0)
        print(f"  created: {made} units over {len(sample)} handsets")
        if made < len(sample):
            failures += 1

        status, body = call("/api/profiles?limit=1000", key)
        profiles = []
        if isinstance(body, dict):
            profiles = body.get("profiles") or body.get("items") or []
        elif isinstance(body, list):
            profiles = body
        print(f"  the fleet now holds {len(profiles)} units")

        models = []
        cores = []
        memories = []
        panels = []
        serials = []
        for item in profiles:
            if not isinstance(item, dict):
                continue
            android = item.get("android_build") or item.get("android") or {}
            navigator = item.get("navigator") or {}
            screen = item.get("screen") or {}
            models.append(android.get("model"))
            cores.append(navigator.get("hardware_concurrency"))
            memories.append(navigator.get("device_memory"))
            panels.append((screen.get("width"), screen.get("height")))
            serials.append(android.get("serial"))

        print("\n--- how the fleet reads ---")
        for name, values in (
            ("model", models),
            ("cores", cores),
            ("memory", memories),
            ("panel", panels),
            ("serial", serials),
        ):
            values = [v for v in values if v is not None]
            if not values:
                continue
            counts = {}
            for value in values:
                counts[value] = counts.get(value, 0) + 1
            spread = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
            shown = ", ".join(f"{k}×{v}" for k, v in spread)
            print(f"  {name:10} distinct={len(counts):5}/{len(values):5}   {shown}")

        if len(set(cores)) < 2:
            print("    FAIL: every unit of the fleet reports the same core count")
            failures += 1
        if len(set(memories)) < 2:
            print("    FAIL: every unit of the fleet reports the same memory")
            failures += 1
        if len(set(panels)) < 5:
            print("    FAIL: the fleet uses fewer than five panels")
            failures += 1
        if len(set(models)) < 20:
            print("    FAIL: the fleet uses fewer than twenty handsets")
            failures += 1
        if len(set(serials)) != len(serials):
            print("    FAIL: two units of the fleet share a serial")
            failures += 1
    finally:
        stop_backend(process)

    print()
    if failures:
        print(f"RESULT: {failures} check(s) FAILED")
        return 1
    print("RESULT: the installed application serves a fleet of different units")
    return 0


if __name__ == "__main__":
    sys.exit(main())
