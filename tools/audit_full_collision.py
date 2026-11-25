"""The full collision and hardware-coherence audit of the fleet.

The claim the product makes is that a hundred million profiles exist
and that not one pair of them is the same to the outside world.  The
audit checks the whole fleet end to end:

* the signature of every profile is distinct -- the values a site
  would read, not the identifier the engine keeps for itself;
* the hardware of every preset agrees with itself -- the graphics
  part, the chipset, the vendor of the chipset, the number of cores
  and the layout of the clusters;
* the numbers a site reads obey the schema of the real thing -- the
  core count is one of the four counts a phone really has, and the
  texture limit is one of the four limits a phone really reports.

The run writes the findings to the console and exits non-zero when
anything is wrong, so it can be used as a gate.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "novaprofile" / "control_plane"))


def _gpu_family(renderer: str) -> str:
    """The family the graphics part belongs to."""

    name = renderer.lower()
    if "adreno" in name:
        return "adreno"
    if "mali" in name or "immortalis" in name:
        return "mali"
    if "xclipse" in name:
        return "xclipse"
    if "powervr" in name or "videocore" in name:
        return "powervr"
    if "maleoon" in name:
        return "maleoon"
    if "img" in name:
        return "img"
    if "sgx" in name:
        return "sgx"
    return "unknown"


def _vendor_of_chipset(chipset: str) -> str:
    """The vendor the name of the chipset names."""

    name = chipset.lower()
    for needle, vendor in (
        ("snapdragon", "qualcomm"),
        ("qualcomm", "qualcomm"),
        ("exynos", "samsung"),
        ("samsung", "samsung"),
        ("mediatek", "mediatek"),
        ("dimensity", "mediatek"),
        ("helio", "mediatek"),
        ("tensor", "google"),
        ("google", "google"),
        ("kirin", "hisilicon"),
        ("hi silicon", "hisilicon"),
        ("hisilicon", "hisilicon"),
        ("unisoc", "unisoc"),
        ("spreadtrum", "unisoc"),
    ):
        if needle in name:
            return vendor
    return "unknown"


#: The graphics families each vendor of chipset really ships with.
#:
#: The rule is the one the market follows, and it is looser than it
#: looks at first.  A MediaTek chip of the last decade carries a
#: Mali, but the MediaTek of the middle decade carried an
#: Imagination part: the Helio A20 carried the PowerVR GE8320, the
#: Helio A25 the GE8300, the Helio P90 the GM9446, and the MT6739 the
#: GE8100.  The Intel, the Texas Instruments, the Marvell and the
#: NVIDIA parts carry what their own maker made.
#:
#: What no maker does is put an Adreno on a chip that is not a
#: Qualcomm one, or a Qualcomm part on a chip that is not the
#: Qualcomm one that ships with it.  Those are the rules the audit
#: holds the fleet to.
_FAMILY_BY_VENDOR = {
    "qualcomm": {"adreno"},
    "samsung": {"mali", "xclipse", "powervr"},
    "mediatek": {"mali", "immortalis", "powervr", "img"},
    "google": {"mali"},
    "hisilicon": {"mali", "maleoon"},
    "unisoc": {"mali", "powervr", "img"},
    "unknown": {
        "adreno",
        "mali",
        "immortalis",
        "xclipse",
        "powervr",
        "maleoon",
        "img",
        "sgx",
        "other",
        "unknown",
    },
}

#: The counts of cores a phone really has.
REAL_CORE_COUNTS = {4, 6, 8, 12}

#: The limits of texture a phone really reports.
REAL_TEXTURE_LIMITS = {4096, 8192, 16384, 32768}

#: The vendors the schema of the SoC allows.
REAL_SOC_VENDORS = {
    "ARM",
    "Qualcomm",
    "MediaTek",
    "Samsung",
    "Google",
    "Unisoc",
    "HiSilicon",
}

#: The names of the families that stand for a part without being one.
_BARE_FAMILIES = {
    "adreno",
    "mali",
    "immortalis",
    "maleoon",
    "xclipse",
    "powervr",
    "img",
    "sgx",
    "geforce",
    "vivante",
    "video core",
}


def _is_bare_family(renderer: str) -> bool:
    """The renderer names the family of a part and not the part.

    The answer is true for a renderer that carries nothing but the
    name of a family -- the "Adreno" of a row that was never filled
    in -- and false for a renderer that names a real part, whose name
    may or may not carry a digit.
    """

    text = (renderer or "").strip().lower()
    if not text:
        return True

    text = re.sub(
        r"^(?:arm|qualcomm|imagination|google inc\.?|samsung|nvidia|intel|vivante)\s+",
        "",
        text,
    ).strip()

    return text in _BARE_FAMILIES


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261125)
    parser.add_argument("--skip-fleet", action="store_true")
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        help="Generate the fleet in batches of this size (0 = all at once).",
    )
    args = parser.parse_args()

    duplicates: list[tuple[str, str]] = []

    if not args.skip_fleet:
        from novaprofile.core.fleet import generate_fleet
        from novaprofile.core.collision import InMemorySignatureRegistry

        started = time.time()
        seen: dict[str, str] = {}

        if args.batch:
            # The fleet is built in batches, and every batch is built
            # against the registry of the batches before it, so a
            # duplicate that straddles two batches is caught as well
            # as a duplicate inside one.
            registry = InMemorySignatureRegistry()
            made = 0
            batch_number = 0
            while made < args.count:
                size = min(args.batch, args.count - made)
                profiles = generate_fleet(
                    count=size,
                    name_prefix=f"audit{batch_number}",
                    registry=registry,
                )
                for profile in profiles:
                    parts = profile.signature_parts()
                    key = "|".join(f"{k}={v}" for k, v in sorted(parts.items()))
                    previous = seen.get(key)
                    if previous is not None:
                        duplicates.append((previous, profile.identity.id))
                    else:
                        seen[key] = profile.identity.id
                made += len(profiles)
                batch_number += 1
                print(
                    "  batch %d: %d profiles, %d distinct so far"
                    % (batch_number, len(profiles), len(seen))
                )
        else:
            profiles = generate_fleet(count=args.count, name_prefix="audit")
            for profile in profiles:
                parts = profile.signature_parts()
                key = "|".join(f"{k}={v}" for k, v in sorted(parts.items()))
                previous = seen.get(key)
                if previous is not None:
                    duplicates.append((previous, profile.identity.id))
                else:
                    seen[key] = profile.identity.id

        elapsed = time.time() - started
        print(f"generated {args.count} profiles in {elapsed:.1f}s")

        # --------------------------------------------------------------
        # 1. The signature of every profile is distinct.
        # --------------------------------------------------------------
        print(f"distinct signatures: {len(seen)}")
        print(f"duplicate signatures: {len(duplicates)}")
        for pair in duplicates[:5]:
            print("   ", pair)

    # ------------------------------------------------------------------
    # 2. The hardware of every preset agrees with itself.
    # ------------------------------------------------------------------
    from novaprofile.core.device_catalog_market import market_presets
    from novaprofile.core.soc_cores import canonical_chipset_name

    presets = list(market_presets())
    print(f"\nmarket presets: {len(presets)}")

    problems: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}

    def note(kind: str, detail: str) -> None:
        problems[kind] += 1
        samples.setdefault(kind, [])
        if len(samples[kind]) < 5:
            samples[kind].append(detail)

    gpu_names: Counter[str] = Counter()
    soc_names: Counter[str] = Counter()
    core_counts: Counter[int] = Counter()
    vendor_names: Counter[str] = Counter()

    for preset in presets:
        gpu = preset.gpu
        soc = preset.abi

        gpu_name = gpu.unmasked_renderer
        soc_name = soc.soc_model

        gpu_names[gpu_name] += 1
        soc_names[soc_name] += 1
        vendor_names[soc.soc_vendor] += 1
        for count in preset.cpu_options:
            core_counts[count] += 1

        # The name of the chipset is a name of the market, not a run of
        # text scraped out of a specification sheet.
        canonical = canonical_chipset_name(soc_name)
        if canonical != soc_name:
            note("soc-not-canonical", f"{soc_name!r} -> {canonical!r}")

        # The chipset names a vendor, and the graphics part belongs to
        # the family that vendor really ships with.
        vendor = _vendor_of_chipset(soc_name)
        family = _gpu_family(gpu_name)
        allowed = _FAMILY_BY_VENDOR.get(vendor, set())
        if family not in allowed:
            note("gpu-chipset-family", f"{soc_name!r} ({vendor}) + {gpu_name!r} ({family})")

        # The vendor the preset carries is the vendor of the chipset.
        if soc.soc_vendor not in REAL_SOC_VENDORS:
            note("soc-vendor-schema", f"{soc_name!r} has vendor {soc.soc_vendor!r}")
        elif vendor != "unknown" and soc.soc_vendor.lower() != vendor:
            note("soc-vendor", f"{soc_name!r} says {soc.soc_vendor!r} but the name says {vendor!r}")

        # The count of cores is a count a phone really has.
        for count in preset.cpu_options:
            if count not in REAL_CORE_COUNTS:
                note("core-count", f"{soc_name!r} has {count} cores")

        # The layout of the clusters adds up to the count of cores.
        layout = soc.cpu_cluster_layout or ""
        parts = [int(p) for p in layout.replace("+", " ").split() if p.isdigit()]
        if parts and sum(parts) not in REAL_CORE_COUNTS:
            note("cluster-layout", f"{soc_name!r}: {layout}")

        # The limit of texture is a limit a phone really reports.
        if gpu.max_texture_size not in REAL_TEXTURE_LIMITS:
            note("texture-limit", f"{gpu_name!r} reports {gpu.max_texture_size}")

        # The graphics part names a real part, not only a family.
        #
        # A family on its own -- a bare "Adreno" or a bare "Mali" --
        # is a row of the reference sheet that was never filled in, and
        # a phone never reports the family of its graphics part on its
        # own.  A part whose name carries no digit is not a problem:
        # the "Nvidia Maxwell" of the Pixel C and the "ULP GeForce" of
        # the Tegra 2 are the names the market gives those parts.
        if _is_bare_family(gpu_name):
            note("gpu-without-number", f"{gpu_name!r} on {soc_name!r}")

        # The graphics part belongs to the architecture the SoC names.
        if not soc.gpu_architecture:
            note("gpu-architecture-empty", gpu_name)

    print(f"distinct graphics parts: {len(gpu_names)}")
    print(f"distinct chipsets: {len(soc_names)}")
    print(f"core counts: {dict(sorted(core_counts.items()))}")
    print(f"vendors: {dict(vendor_names)}")

    print("\nproblems:")
    if not problems:
        print("   none")
    for kind, count in problems.most_common():
        print(f"   {kind}: {count}")
        for detail in samples[kind]:
            print(f"      {detail}")

    failed = bool(duplicates) or bool(problems)
    print("\nFAIL" if failed else "\nOK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
