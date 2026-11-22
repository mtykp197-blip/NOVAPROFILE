"""Normalise the public device-specification datasets into one record set.

Three public datasets describe the same thing -- a real phone -- in
three different shapes.  This module turns all of them into the single
flat record the catalogue builder consumes, and it deduplicates the
result.

Why three sources

    A single source is never enough.  The fleet has to reach ten
    thousand devices, and no public dataset carries ten thousand
    devices with a chipset, a GPU and a panel.  The sources overlap
    heavily, and their union covers more than any one of them does:

    * the ``gsm.csv`` dump -- the oldest devices, densely populated;
    * the ``KhalSCI`` dump -- the widest brand coverage, with the
      specification tree as nested dictionaries;
    * the ``ishumann`` dump -- the newest devices, with a flat column
      layout.

    Their union is larger than their sum of parts, because each source
    misses devices the others carry.

Why deduplicate

    The same phone appears in more than one source, usually with a
    slightly different name.  A fleet that carries the same phone twice
    is not twice as diverse -- a site that groups on the model string
    sees one bucket, and the second copy only weakens the guard that
    promises the two copies are different units.

    The deduplication key is the *specification*, not the name: the
    manufacturer, the model, the chipset and the panel.  Two entries
    with the same key are the same device described twice, and the
    first one wins.  Two entries with the same name but different
    hardware are two variants of one model, and both are kept -- that
    is a real distinction that the market makes.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

#: The values that mean "the source does not know".  They are treated
#: as absent, not as data.
_EMPTY_VALUES = frozenset({"", "-", "n/a", "na", "unknown", "none", "_", "not disclosed"})

#: The Android version to SDK level map.  The SDK level is what the
#: client hints and the WebView report, and it has to agree with the
#: release string.
ANDROID_SDK: dict[int, int] = {
    9: 28,
    10: 29,
    11: 30,
    12: 31,
    13: 33,
    14: 34,
    15: 35,
    16: 36,
}

#: The patterns that pull the RAM amounts out of a memory string.
#:
#: The sources write the memory in every order: ``"128GB 8GB RAM"``,
#: ``"8GB RAM 128GB"``, ``"256GB 12GB RAM, 512GB 16GB RAM"``.
_RAM_PATTERN = re.compile(r"(\d+)\s*gb\s*ram", re.IGNORECASE)

#: The pattern that pulls the announcement year and month.
_ANNOUNCED_PATTERN = re.compile(r"(?P<year>(19|20)\d{2})(?:\s*,\s*(?P<month>[A-Za-z]+))?")

#: The month names, with their numbers.
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

#: The chipset to SoC vendor map, keyed by a lowercase substring.
#:
#: The map is checked in order, so the more specific entries come
#: first: a "Tensor" entry must win over the generic "Samsung" one.
_SOC_VENDOR_RULES: tuple[tuple[str, str], ...] = (
    ("snapdragon", "Qualcomm"),
    ("qualcomm", "Qualcomm"),
    ("qcom", "Qualcomm"),
    ("tensor", "Google"),
    ("exynos", "Samsung"),
    ("dimensity", "MediaTek"),
    ("helio", "MediaTek"),
    ("mediatek", "MediaTek"),
    ("mt6", "MediaTek"),
    ("kirin", "HiSilicon"),
    ("hisilicon", "HiSilicon"),
    ("huawei", "HiSilicon"),
    ("unisoc", "Unisoc"),
    ("spreadtrum", "Unisoc"),
)


@dataclass
class DeviceRecord:
    """One real device, described by the specifications that were read."""

    brand: str = ""
    model: str = ""
    chipset: str = ""
    gpu_renderer: str = ""
    resolution: str = ""
    display_size: str = ""
    memory_internal: str = ""
    os_text: str = ""
    announced: str = ""
    body: str = ""
    source_id: str = ""
    #: The dataset the record came from.  It is kept for the provenance
    #: of the fleet, and for the report.
    source_dataset: str = ""
    #: Every field the source carried, for the fields the builder does
    #: not consume yet.
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the record."""

        return {
            "brand": self.brand,
            "model": self.model,
            "chipset": self.chipset,
            "gpu_renderer": self.gpu_renderer,
            "resolution": self.resolution,
            "display_size": self.display_size,
            "memory_internal": self.memory_internal,
            "os_text": self.os_text,
            "announced": self.announced,
            "body": self.body,
            "source_id": self.source_id,
            "source_dataset": self.source_dataset,
            "extra": self.extra,
        }

    @property
    def dedup_key(self) -> tuple[str, str, str, str]:
        """The key that decides whether two records are the same device.

        The key is the *specification* and not the name.  A source
        that spells a model differently -- ``"Galaxy S24"`` against
        ``"Samsung Galaxy S24"`` -- still describes the same hardware,
        and the hardware is what makes the device the same device.
        """

        return (
            self.brand.strip().lower(),
            _normalise_model(self.model),
            self.chipset.strip().lower(),
            self.resolution.strip().lower(),
        )


def _normalise_model(model: str) -> str:
    """Reduce a model name to its comparable form.

    The sources differ in the manufacturer prefix (``"Samsung Galaxy
    S24"`` against ``"Galaxy S24"``), in the punctuation and in the
    case.  None of those differences describe a different device.
    """

    text = model.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s+]", "", text)
    return text.strip()


def _clean(value: Any) -> str:
    """Return the trimmed text of a source cell, or an empty string."""

    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _EMPTY_VALUES:
        return ""
    return text


def ram_amounts(memory: str) -> tuple[int, ...]:
    """Return the RAM amounts, in gigabytes, that the memory string lists."""

    found = {int(match) for match in _RAM_PATTERN.findall(memory or "")}
    return tuple(sorted(found))


def announcement(announced: str) -> tuple[int | None, int | None]:
    """Return the ``(year, month)`` of the announcement.

    The month is ``None`` when the source gives only the year, and the
    year is ``None`` when the source gives nothing usable.
    """

    match = _ANNOUNCED_PATTERN.search(announced or "")
    if match is None:
        return None, None
    year = int(match.group("year"))
    month_text = match.group("month")
    month = _MONTHS.get(month_text.lower()) if month_text else None
    return year, month


def soc_vendor_of(chipset: str) -> str:
    """Return the SoC vendor of a chipset string.

    The rules are checked in order, so the specific ones win.  The
    fallback is ``Qualcomm`` because the overwhelming majority of the
    Android devices in the wild carry a Snapdragon, and a wrong guess
    there is caught by the coherence check rather than shipped.
    """

    text = (chipset or "").lower()
    for needle, vendor in _SOC_VENDOR_RULES:
        if needle in text:
            return vendor
    return "Qualcomm"


#: The canonical spelling of every manufacturer the sources spell in
#: more than one way.
#:
#: The sources are inconsistent about the case and about the spelling:
#: one writes ``samsung``, another writes ``Samsung``, a third writes
#: ``Xiaomi`` where the first wrote ``xiaomi``.  A fleet that keeps
#: both spellings carries two buckets for one manufacturer, and a vendor
#: that groups on the manufacturer sees the fleet as twice as small as
#: it is.
#:
#: The map is applied to the lowercased name, and the value is the
#: canonical spelling.
_CANONICAL_BRANDS: dict[str, str] = {
    "samsung": "Samsung",
    "google": "Google",
    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi",
    "huawei": "Huawei",
    "honor": "HONOR",
    "motorola": "motorola",
    "oppo": "OPPO",
    "oneplus": "OnePlus",
    "realme": "realme",
    "vivo": "vivo",
    "iqoo": "vivo",
    "zte": "ZTE",
    "nokia": "Nokia",
    "lg": "LG",
    "sony": "Sony",
    "asus": "ASUS",
    "nothing": "Nothing",
    "alcatel": "Alcatel",
    "tecno": "Tecno",
    "infinix": "Infinix",
    "itel": "itel",
    "lenovo": "Lenovo",
    "meizu": "Meizu",
    "htc": "HTC",
    "sonim": "Sonim",
    "cat": "Cat",
    "sharp": "Sharp",
    "panasonic": "Panasonic",
    "micromax": "Micromax",
    "coolpad": "Coolpad",
    "gionee": "Gionee",
    "lava": "Lava",
    "tecno mobile": "Tecno",
    "bq": "BQ",
    "philips": "Philips",
    "prestigio": "Prestigio",
    "myos": "Myos",
    "maxwest": "Maxwest",
    "iHunt": "iHunt",
    "o+": "oplus",
    "crosscall": "Crosscall",
    "doro": "Doro",
    "wiko": "Wiko",
    "teclast": "Teclast",
    "cube": "Cube",
    "i-mobile": "i-mobile",
    "koobee": "Koobee",
    "itel mobile": "itel",
    "leagoo": "Leagoo",
    "b-mobile": "B-Mobile",
    "bluboo": "Bluboo",
    "bq mobiles": "BQ",
    "coby": "Coby",
    "eurostar": "Eurostar",
}


def canonical_brand(brand: str) -> str:
    """Return the canonical spelling of a manufacturer name.

    A name the map does not know keeps its own spelling, with the first
    letter capitalised.  Guessing a manufacturer that does not exist
    would be worse than leaving the name alone.
    """

    text = brand.strip()
    if not text:
        return ""
    key = text.lower()
    if key in _CANONICAL_BRANDS:
        return _CANONICAL_BRANDS[key]
    return text[0].upper() + text[1:]


# --------------------------------------------------------------------------
# The readers
# --------------------------------------------------------------------------


def read_gsm_csv(path: Path) -> Iterator[DeviceRecord]:
    """Read the ``gsm.csv`` dump.

    The dump is a flat table: one row per device, one column per
    specification.  The columns that matter are the manufacturer, the
    model, the chipset, the GPU, the resolution, the memory, the OS and
    the announcement date.
    """

    csv.field_size_limit(10**9)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            record = DeviceRecord(
                brand=canonical_brand(_clean(row.get("oem"))),
                model=_clean(row.get("model")),
                chipset=_clean(row.get("platform_chipset")),
                gpu_renderer=_clean(row.get("platform_gpu")),
                resolution=_clean(row.get("display_resolution")),
                display_size=_clean(row.get("display_size")),
                memory_internal=_clean(row.get("memory_internal")),
                os_text=_clean(row.get("platform_os")),
                announced=_clean(row.get("launch_announced")),
                body=_clean(row.get("body")),
                source_id=f"gsm:{_clean(row.get('misc_models')) or index}",
                source_dataset="gsm",
            )
            if record.brand and record.model:
                yield record


def read_khal_csv(path: Path) -> Iterator[DeviceRecord]:
    """Read the ``KhalSCI`` dump.

    The dump carries the specification as a Python dictionary literal in
    one column.  The literal is parsed rather than scraped with a
    regular expression, because a dictionary literal has a structure
    and a regular expression over one would have to guess at it.
    """

    csv.field_size_limit(10**9)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            raw = row.get("specs") or ""
            if not raw:
                continue
            try:
                specs = ast.literal_eval(raw)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if not isinstance(specs, dict):
                continue

            platform = specs.get("Platform") or {}
            display = specs.get("Display") or {}
            memory = specs.get("Memory") or {}
            launch = specs.get("Launch") or {}
            body = specs.get("Body") or {}

            record = DeviceRecord(
                brand=canonical_brand(_clean(row.get("phone_brand"))),
                model=_clean(row.get("phone_model")),
                chipset=_clean(platform.get("Chipset")),
                gpu_renderer=_clean(platform.get("GPU")),
                resolution=_clean(display.get("Resolution")),
                display_size=_clean(display.get("Size")),
                memory_internal=_clean(memory.get("Internal")),
                os_text=_clean(platform.get("OS")),
                announced=_clean(launch.get("Announced")),
                body=_clean(body.get("Build")),
                source_id=f"khal:{index}",
                source_dataset="khal",
            )
            if record.brand and record.model:
                yield record


def read_ishumann_csv(path: Path) -> Iterator[DeviceRecord]:
    """Read the ``ishumann`` dump.

    The dump is a flat table with duplicated column names -- the header
    carries several columns called ``Untitled``, which the ``csv``
    module disambiguates by appending ``.N``.  Only the columns with a
    real name are read.
    """

    csv.field_size_limit(10**9)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            name = _clean(row.get("Product Name"))
            if not name:
                continue

            brand, model = _split_brand_model(name)

            record = DeviceRecord(
                brand=canonical_brand(brand),
                model=model,
                chipset=_clean(row.get("Chipset")),
                gpu_renderer=_clean(row.get("GPU")),
                resolution=_clean(row.get("Resolution")),
                display_size=_clean(row.get("Size")),
                memory_internal=_clean(row.get("Internal")),
                os_text=_clean(row.get("OS")),
                announced=_clean(row.get("Announced")),
                body=_clean(row.get("Build")),
                source_id=f"ishumann:{index}",
                source_dataset="ishumann",
            )
            if record.brand and record.model:
                yield record


#: The brands whose product name starts with two words.
_TWO_WORD_BRANDS = frozenset(
    {
        "Apple iPhone",
        "Apple iPad",
        "Apple Watch",
        "Honor",
        "Xiaomi",
        "Motorola",
        "Nokia",
        "Realme",
        "Samsung",
        "Google",
        "Sony",
        "Asus",
        "Huawei",
        "Oppo",
        "Vivo",
        "Oneplus",
        "Nothing",
        "Poco",
        "Redmi",
        "Infinix",
        "Tecno",
        "Itel",
        "Zte",
        "Alcatel",
        "HTC",
        "Lenovo",
        "Meizu",
        "Coolpad",
        "Wiko",
        "Micromax",
        "Panasonic",
        "Sharp",
        "Umidigi",
        "Cubot",
        "Leagoo",
        "Doogee",
        "Elephone",
        "Ulefone",
        "Vernee",
        "Intex",
        "Oukitel",
        "Allview",
        "Archos",
        "BLU",
        "HMD",
    }
)


def _split_brand_model(name: str) -> tuple[str, str]:
    """Split a product name into its manufacturer and its model."""

    text = name.strip()
    for brand in sorted(_TWO_WORD_BRANDS, key=len, reverse=True):
        if text.lower().startswith(brand.lower() + " "):
            return brand, text[len(brand) :].strip()
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[1].strip()


#: The readers, keyed by the name of the dataset.
READERS = {
    "gsm": read_gsm_csv,
    "khal": read_khal_csv,
    "ishumann": read_ishumann_csv,
}


# --------------------------------------------------------------------------
# The merge
# --------------------------------------------------------------------------


def deduplicate(records: Iterable[DeviceRecord]) -> list[DeviceRecord]:
    """Return the records with the duplicates removed.

    The first record of a key wins.  The sources are read in a fixed
    order -- the widest dataset first -- so the winner is the record
    with the richest specification.
    """

    seen: set[tuple[str, str, str, str]] = set()
    unique: list[DeviceRecord] = []
    for record in records:
        key = record.dedup_key
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def usable(record: DeviceRecord) -> bool:
    """Whether the record carries the three things a preset needs.

    A record without a chipset has no SoC to pair a GPU with, and a
    record without a resolution has no panel.  Such a device cannot
    become a physically coherent profile, so it is not a candidate.
    """

    return bool(
        record.brand
        and record.model
        and record.chipset
        and record.gpu_renderer
        and record.resolution
    )


def load_all(paths: list[Path], *, dedupe: bool = True) -> list[DeviceRecord]:
    """Read every dataset and return the merged record set."""

    records: list[DeviceRecord] = []
    for path in paths:
        dataset = path.stem.split("_")[0].lower()
        reader = READERS.get(dataset)
        if reader is None:
            continue
        records.extend(reader(path))
    if dedupe:
        records = deduplicate(records)
    return records


def write_jsonl(records: Iterable[DeviceRecord], output: Path) -> int:
    """Write the records to a JSON Lines file and return the count."""

    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    """The command line entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gsm", type=Path, default=None, help="the gsm.csv dump")
    parser.add_argument("--khal", type=Path, default=None, help="the KhalSCI dump")
    parser.add_argument("--ishumann", type=Path, default=None, help="the ishumann dump")
    parser.add_argument("--output", type=Path, required=True, help="the JSON Lines file to write")
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="keep the duplicates (for measuring the overlap)",
    )
    args = parser.parse_args(argv)

    paths = [p for p in (args.gsm, args.khal, args.ishumann) if p is not None]
    if not paths:
        print("no dataset was given", file=sys.stderr)
        return 1

    records = load_all(paths, dedupe=not args.no_dedupe)
    usable_records = [r for r in records if usable(r)]
    written = write_jsonl(usable_records, args.output)

    by_dataset: dict[str, int] = {}
    for record in usable_records:
        by_dataset[record.source_dataset] = by_dataset.get(record.source_dataset, 0) + 1

    print(f"usable={written} -> {args.output}")
    for name, count in sorted(by_dataset.items()):
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
