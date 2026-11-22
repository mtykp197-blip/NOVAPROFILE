"""Verify a profile against the browser that was built for it.

The verification is the one thing that cannot be done by reading the
code: the whole point of the native core is that the values reach the
renderer without a JavaScript hook, and only a real browser can confirm
that they arrived.

What it runs
------------
The runner in :mod:`novaprofile.probe.e2e` serves a test page from
``127.0.0.1``, launches the browser with a temporary profile, attaches
over the DevTools protocol, installs the extractor *before* the first
navigation so that it is present at ``document_start``, reads the
axes and compares them against the simulator.

Usage
-----
    python -m novaprofile.probe.verify_built_core \\
        --browser C:/chromium/src/out/nova-win/chrome.exe \\
        --preset pixel-8-pro-android14 \\
        --count 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

#: The browser that was built for this product, when it is installed.
DEFAULT_BUILT_BROWSER = Path("C:/chromium/src/out/nova-win/chrome.exe")


def _generate_profiles(preset: str | None, count: int) -> list:
    """The profiles to verify."""

    from ..core.devices import all_presets
    from ..core.generator import ProfileGenerator

    generator = ProfileGenerator()
    if preset:
        return [
            generator.generate(preset_id=preset, stratum=index) for index in range(count)
        ]

    presets = all_presets()
    if not presets:
        raise RuntimeError("the catalogue is empty")
    return [
        generator.generate(preset_id=presets[index % len(presets)].preset_id, stratum=index)
        for index in range(count)
    ]


async def _run(configs: list, browser_path: str, timeout: float) -> list:
    """Run the verification against every profile."""

    from .e2e import E2ERunner

    reports = []
    for config in configs:
        runner = E2ERunner(config, browser_path=browser_path, timeout=timeout)
        reports.append(await runner.run())
    return reports


def main(argv: list[str] | None = None) -> int:
    """The command line."""

    parser = argparse.ArgumentParser(
        description="Verify profiles against the browser that was built for them."
    )
    parser.add_argument(
        "--browser",
        type=Path,
        default=DEFAULT_BUILT_BROWSER,
        help="the browser to verify against",
    )
    parser.add_argument("--preset", default=None, help="the preset to verify")
    parser.add_argument("--count", type=int, default=1, help="how many profiles")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per profile")
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args(argv)

    browser = str(args.browser)
    if not Path(browser).is_file():
        print(f"the browser is not there: {browser}")
        return 1

    try:
        configs = _generate_profiles(args.preset, args.count)
    except Exception as error:
        print(f"cannot generate the profiles: {error}")
        return 1

    try:
        reports = asyncio.run(_run(configs, browser, args.timeout))
    except Exception as error:
        print(f"the verification failed: {error}")
        return 1

    if args.json:
        print(json.dumps([report.to_dict() for report in reports], indent=2, default=str))
        return 0 if all(report.passed for report in reports) else 1

    for report in reports:
        mark = "PASS" if report.passed else "FAIL"
        print(f"{mark} {report.profile_id}  {report.counts()}")

    failed = [report for report in reports if not report.passed]
    if failed:
        print(f"\n{len(failed)} of {len(reports)} profiles failed")
        for report in failed:
            for axis, verdict in sorted(report.verdicts.items()):
                if verdict != "match":
                    print(f"  {report.profile_id}: {axis} -> {verdict}")
    else:
        print(f"\nall {len(reports)} profiles passed")

    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - the command line
    raise SystemExit(main())
