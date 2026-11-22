"""Run the profile verification against the real Chrome on this machine.

The simulator reasons about the configuration; this measures what the page
actually sees in a browser.  Running both is what makes a mistake in one of
them visible.

Usage:
    python verify_live.py [preset_id]
"""

from __future__ import annotations

import sys

from novaprofile.core.devices import DEVICE_PRESETS, get_preset
from novaprofile.core.generator import ProfileGenerator
from novaprofile.probe.verify import VerifyRunner

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def main() -> int:
    preset_id = sys.argv[1] if len(sys.argv) > 1 else DEVICE_PRESETS[0].preset_id
    preset = get_preset(preset_id)
    if preset is None:
        print(f"unknown preset: {preset_id}")
        return 2

    gen = ProfileGenerator()
    config = gen.generate(preset_id, master_seed="a" * 64, stratum=0)

    print(f"preset   : {preset_id}")
    print(f"model    : {preset.model}")
    print(f"UA       : {config.navigator.user_agent}")
    print(f"platform : {config.navigator.platform}")
    print(f"touch    : {config.navigator.max_touch_points}")
    print("-" * 70)

    runner = VerifyRunner(config, browser_path=CHROME, headless=True, timeout=120.0)
    report = runner.run_sync()

    print(report.to_table())
    print("-" * 70)
    print("counts:", report.counts())
    print("passed:", report.passed)
    print("browser:", report.browser)

    for axis in report.axes:
        for mismatch in axis.mismatches:
            print(
                f"  ! {axis.axis}.{mismatch.field}: "
                f"expected {mismatch.expected!r}, got {mismatch.actual!r}"
            )

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
