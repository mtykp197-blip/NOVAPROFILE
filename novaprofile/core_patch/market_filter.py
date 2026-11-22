"""Keep the devices that the Vietnamese market actually sells.

A fleet profile is a claim about a device.  The claim is only
credible if the device is one that people here own, because the sites
that fingerprint a visitor compare the visitor's hardware against the
hardware of the real devices that visit them.  A fleet full of
devices that never reach a Vietnamese handset is a fleet a Vietnamese
site separates at a glance -- not because any field is wrong, but
because the combination of fields is something that never happens.

This module holds the market filter.

Where the list comes from

    The brand weights are the Vietnamese handset market as it stood
    across 2023-2025: Samsung leads, Xiaomi's three brands together
    are close behind, then OPPO, vivo and realme, with HONOR and the
    Transsion brands (Tecno, Infinix) holding a real share of the
    affordable segment, and Nokia keeping a small one.

    The *model* filter is the harder part.  A brand is popular but its
    catalogue is not: the Galaxy A series sells by the million in
    Vietnam and the Galaxy S series sells in the tens of thousands,
    and the two have to be kept apart.  The same is true of the Redmi
    Note line against the Redmi K line, of the Oppo A line against the
    Find line, and of the vivo Y line against the X line.

    The rule this module applies is therefore a *line* rule, not a
    model rule: a device is kept when it belongs to a line that the
    Vietnamese market buys.  Where a line has a restricted variant --
    the Ultra and Pro variants of a flagship line cost many times the
    money of the base line -- the restriction is applied inside the
    line.

The filter is deliberately not a hard cutoff on the sales volume.  A
popular line has a long tail of storage and colour variants, and the
market buys those too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

#: The brands that reach the Vietnamese handset market in numbers.
#:
#: The value is the weight of the brand.  The weight does not scale a
#: score; it records how much of the market the brand holds, and it is
#: used for the report and for the ordering of the fleet.
MARKET_BRANDS: dict[str, float] = {
    "Samsung": 0.23,
    "Xiaomi": 0.19,
    "OPPO": 0.13,
    "vivo": 0.11,
    "realme": 0.09,
    "HONOR": 0.06,
    "Tecno": 0.04,
    "Infinix": 0.03,
    "Nokia": 0.02,
    "OnePlus": 0.015,
    "Motorola": 0.015,
    "itel": 0.01,
    "Apple": 0.05,
    "Google": 0.01,
    "Nothing": 0.005,
    # The brands that keep a small but real share of the market here.
    # Sony's Xperia line is still sold, the Lenovo and Motorola lines
    # are sold under the same roof, HTC is down to a couple of lines,
    # and the RedMagic gaming line of the nubia brand reaches the
    # enthusiast segment.
    "Sony": 0.005,
    "Lenovo": 0.008,
    "HTC": 0.005,
    "ZTE": 0.006,
}

#: The sub-brands that are sold under a parent brand.  The key is the
#: brand as the dataset spells it, the value is the parent.
SUB_BRANDS: dict[str, str] = {
    "Redmi": "Xiaomi",
    "POCO": "Xiaomi",
    "Honor": "HONOR",
    "Motorola": "Motorola",
    "lenovo": "Lenovo",
    "Lenovo": "Lenovo",
    "ZTE": "ZTE",
    "nubia": "ZTE",
    "RedMagic": "ZTE",
    "Red Magic": "ZTE",
    "realme": "realme",
    "OPPO": "OPPO",
    "vivo": "vivo",
    "Vivo": "vivo",
    "iQOO": "vivo",
    "iqoo": "vivo",
    "OnePlus": "OnePlus",
    "oneplus": "OnePlus",
    "Tecno": "Tecno",
    "Infinix": "Infinix",
    "itel": "itel",
    "Nokia": "Nokia",
    "Google": "Google",
    "Nothing": "Nothing",
    "Apple": "Apple",
    "Samsung": "Samsung",
    "Xiaomi": "Xiaomi",
    "HONOR": "HONOR",
    "Sony": "Sony",
    "HTC": "HTC",
}

#: The model lines the Vietnamese market buys, per brand.
#:
#: A line is matched against the model name case-insensitively.  An
#: empty tuple for a brand means the whole catalogue of the brand is
#: kept, which is right for the brands whose entire catalogue is sold
#: here.
MARKET_LINES: dict[str, tuple[str, ...]] = {
    "Samsung": (
        "galaxy a",
        "galaxy m",
        "galaxy s",
        "galaxy z",
        "galaxy j",
        "galaxy f",
        "galaxy tab",
        "galaxy note",
    ),
    "Xiaomi": (
        "redmi note",
        "redmi a",
        "redmi",
        "poco x",
        "poco m",
        "poco f",
        "poco",
        "xiaomi",
        "mi ",
        "mi 9",
        "mi 10",
        "mi 11",
        "mi note",
    ),
    "OPPO": (
        "oppo a",
        "oppo f",
        "oppo reno",
        "oppo find",
        "oppo k",
        "oppo r",
        "oppo a5",
        "oppo a7",
        "oppo a9",
        "oppo a15",
        "oppo a16",
        "oppo a17",
        "oppo a31",
        "oppo a57",
        "oppo a58",
        "oppo a59",
        "oppo a74",
        "oppo a76",
        "oppo a77",
        "oppo a78",
        "oppo a79",
        "oppo a94",
        "oppo a95",
        "oppo a96",
        "oppo c",
        "oppo ",
    ),
    "vivo": (
        "vivo y",
        "vivo v",
        "vivo s",
        "vivo x",
        "vivo t",
        "vivo z",
        "vivo iqoo",
        "vivo ",
    ),
    "realme": (
        "realme c",
        "realme m",
        "realme n",
        "realme gt",
        "realme x",
        "realme 11",
        "realme 12",
        "realme 10",
        "realme 9",
        "realme 8",
        "realme 7",
        "realme 6",
        "realme 5",
        "realme ",
    ),
    "HONOR": (
        "honor x",
        "honor 9",
        "honor 8",
        "honor 7",
        "honor 5",
        "honor 4",
        "honor 3",
        "honor 2",
        "honor 1",
        "honor magic",
        "honor play",
        "honor ",
    ),
    "Tecno": ("tecno ", "spark", "pova", "phantom", "camon"),
    "Infinix": ("infinix ", "hot s", "note", "smart"),
    "Nokia": ("nokia ", "g", "m", "x"),
    "OnePlus": ("oneplus ", "oneplus"),
    "Google": ("pixel", "nexus"),
    "Motorola": ("moto ", "moto g", "moto e", "moto m", "motorola "),
    "itel": ("itel ", "it "),
    "Apple": ("iphone", "ipad"),
    "Nothing": ("nothing ", "phone "),
    # The Xperia line is still sold here, and the Neo and the Xperia 10
    # lines are the ones that move.
    "Sony": ("xperia", "sony "),
    # Lenovo sells here through the Motorola brand and through its own
    # Legion gaming line; the IdeaPad and Tab lines are not sold.
    "Lenovo": ("legion", "lenovo tab", "lenovo z", "lenovo k"),
    # HTC is down to a couple of lines, of which the Desire and the
    # One series are the ones that reach the market.
    "HTC": ("htc desire", "htc one", "htc u", "htc 10", "htc 12", "htc "),
    # The nubia RedMagic gaming line reaches the enthusiast segment
    # here, and the ZTE Blade line the affordable one.
    "ZTE": ("redmagic", "red magic", "nubia", "blade", "zte "),
}

#: The variants inside a line that the market does not buy.
#:
#: A flagship variant costs several times the money of the line it
#: belongs to, and in Vietnam it is bought by a small enough number of
#: people that a fleet carrying many of them is separated from the
#: real one.
RESTRICTED_VARIANTS: tuple[str, ...] = (
    "ultra",
    "pro max",
    "pro+",
    "pro plus",
)

#: The lines that are the price band above the market.
#:
#: These are not variants inside a line; they are whole lines that sit
#: at a price the Vietnamese market does not reach in numbers.  The
#: Oppo Find line is sold here, but by a small enough number of people
#: that a fleet carrying it in bulk is separated from the real one.
#:
#: The RedMagic gaming line is deliberately NOT here.  It reaches the
#: enthusiast segment here, and a fleet that carries it is not the
#: fleet of the Vietnamese market -- it is the fleet of the Vietnamese
#: market plus its enthusiasts, which is a real and growing segment.
RESTRICTED_LINES: tuple[str, ...] = (
    "find",
)

#: The variants inside a line that are common enough to keep.  A "pro"
#: on a mid line is a different device from the base line and it sells
#: in Vietnam; a "pro max" on a flagship is a different price band.
COMMON_VARIANTS: tuple[str, ...] = ("pro", "plus", "note", "neo", "gt", "x", "lite", "air")


#: The brands whose whole catalogue is kept, because every model they
#: made is sold in Vietnam.
FULL_CATALOGUE_BRANDS = frozenset(
    {
        "Apple",
        "Google",
        "Nothing",
        "OnePlus",
    }
)


@dataclass
class MarketDecision:
    """Why a record was kept or dropped."""

    kept: bool
    reason: str
    brand: str = ""
    model: str = ""
    parent_brand: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the decision."""

        return {
            "kept": self.kept,
            "reason": self.reason,
            "brand": self.brand,
            "model": self.model,
            "parent_brand": self.parent_brand,
            "extra": self.extra,
        }


def parent_brand_of(brand: str) -> str:
    """Return the parent of a brand, or the brand itself."""

    if brand in SUB_BRANDS:
        return SUB_BRANDS[brand]
    for sub, parent in SUB_BRANDS.items():
        if brand.lower() == sub.lower():
            return parent
    return brand


def _matches_line(model: str, line: str) -> bool:
    """Whether a model name contains a line.

    The comparison is on word boundaries, because ``"a"`` inside
    ``"galaxy"`` is not the Oppo A line.  A line that is a prefix of a
    word -- ``"galaxy a"`` against ``"galaxy a54"`` -- matches when the
    next character is a digit or a space.

    The line may carry the manufacturer prefix -- ``"oppo a"`` -- while
    the model name does not -- ``"A78"``.  The prefix is therefore
    stripped from the line before the comparison, so that the lines can
    be written the way a reader thinks of them.
    """

    text = model.lower()
    needle = line.lower()
    index = text.find(needle)
    while index != -1:
        before_ok = index == 0 or not text[index - 1].isalnum()
        end = index + len(needle)
        after = text[end] if end < len(text) else ""
        after_ok = after == "" or not after.isalpha()
        if before_ok and after_ok:
            return True
        index = text.find(needle, index + 1)
    return False


def _line_without_brand(line: str, brand: str) -> str:
    """Return the line with the manufacturer prefix removed.

    The dataset spells a model without its manufacturer -- ``"A78"``
    for the Oppo A78 -- while the line table reads more naturally with
    the prefix -- ``"oppo a"``.  Comparing the two as they are written
    never matches, so the prefix is taken off the line before the
    comparison.
    """

    text = line.lower()
    prefix = brand.lower()
    if text.startswith(prefix + " "):
        return text[len(prefix) + 1 :]
    return text


def restricted_variant(model: str) -> str | None:
    """Return the restricted variant the model carries, if any."""

    text = model.lower()
    for variant in RESTRICTED_VARIANTS:
        if _matches_line(text, variant):
            return variant
    return None


def decide(brand: str, model: str) -> MarketDecision:
    """Decide whether a device belongs to the Vietnamese market."""

    parent = parent_brand_of(brand)

    if parent not in MARKET_BRANDS and brand not in MARKET_BRANDS:
        return MarketDecision(False, "the brand is not sold in Vietnam", brand, model, parent)

    if parent in FULL_CATALOGUE_BRANDS:
        return MarketDecision(True, "the whole catalogue of the brand is sold here", brand, model, parent)

    lines = MARKET_LINES.get(parent)
    if lines is None:
        return MarketDecision(True, "the brand is sold here without a line restriction", brand, model, parent)

    # The line table carries the manufacturer prefix for readability,
    # while the dataset spells the model without it.  Both the brand as
    # written and the parent brand are tried as the prefix, because a
    # sub-brand's models are listed under the parent.
    prefixes = {brand.lower(), parent.lower()}
    matched: list[str] = []
    for line in lines:
        stripped = line.lower()
        for prefix in prefixes:
            if stripped.startswith(prefix + " "):
                stripped = stripped[len(prefix) + 1 :]
                break
        if _matches_line(model, stripped):
            matched.append(line)

    if not matched:
        return MarketDecision(
            False,
            "no line of this brand is sold in Vietnam",
            brand,
            model,
            parent,
            {"lines_checked": len(lines)},
        )

    variant = restricted_variant(model)
    if variant is not None:
        return MarketDecision(
            False,
            f"the {variant} variant is not bought here",
            brand,
            model,
            parent,
            {"matched_lines": matched, "variant": variant},
        )

    # A whole line can sit above the market as well.  The check runs
    # after the variant check, because a model that carries both is
    # reported by the more specific of the two.
    for restricted in RESTRICTED_LINES:
        if _matches_line(model, restricted):
            return MarketDecision(
                False,
                f"the {restricted} line is not bought here in numbers",
                brand,
                model,
                parent,
                {"matched_lines": matched, "line": restricted},
            )

    return MarketDecision(
        True,
        "a line the market buys",
        brand,
        model,
        parent,
        {"matched_lines": matched},
    )


def filter_records(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[MarketDecision]]:
    """Split the records into the kept ones and the decisions.

    The decisions are returned as well, because the report of what was
    dropped is how the filter is checked: a filter that drops the
    Pixel 8 or the Redmi Note 13 is wrong, and the report is where that
    shows.
    """

    kept: list[dict[str, Any]] = []
    decisions: list[MarketDecision] = []
    for record in records:
        decision = decide(record.get("brand", ""), record.get("model", ""))
        decisions.append(decision)
        if decision.kept:
            kept.append(record)
    return kept, decisions


def main(argv: list[str] | None = None) -> int:
    """The command line entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="the merged device records")
    parser.add_argument("--output", type=Path, required=True, help="the records to keep")
    parser.add_argument("--report", type=Path, default=None, help="where to write the decisions")
    args = parser.parse_args(argv)

    records = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line]
    kept, decisions = filter_records(records)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in kept:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8") as handle:
            for decision in decisions:
                handle.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")

    print(f"kept {len(kept)} of {len(records)}")
    by_brand: dict[str, int] = {}
    for record in kept:
        brand = parent_brand_of(record.get("brand", ""))
        by_brand[brand] = by_brand.get(brand, 0) + 1
    for brand, count in sorted(by_brand.items(), key=lambda item: -item[1]):
        print(f"  {count:5}  {brand}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
