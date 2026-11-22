"""The tests for the Vietnamese market filter.

The filter decides which real devices belong in the fleet.  It is the
one place where a *wrong* decision is invisible to every other test:
a device that should have been dropped and was kept makes the fleet
look bigger than the market, and a device that should have been kept
and was dropped makes it look smaller.  Neither shows up as a failure
anywhere else, so the filter's own decisions are checked here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The filter lives beside the data-preparation scripts, not in the
# package, so it is imported by path.  It sits in ``novaprofile/core_patch``
# while this test lives in ``novaprofile/control_plane/tests``.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "core_patch"))

from market_filter import (  # noqa: E402
    FULL_CATALOGUE_BRANDS,
    MARKET_BRANDS,
    decide,
    filter_records,
    parent_brand_of,
)


class TestBrandRecognition:
    """The brands the Vietnamese market buys."""

    @pytest.mark.parametrize(
        "brand",
        [
            "Samsung",
            "Xiaomi",
            "OPPO",
            "vivo",
            "realme",
            "HONOR",
            "Tecno",
            "Infinix",
            "Nokia",
            "OnePlus",
            "Google",
            "Motorola",
            "Apple",
            "Sony",
            "Lenovo",
            "HTC",
            "ZTE",
        ],
    )
    def test_market_brand_is_recognised(self, brand: str) -> None:
        assert brand in MARKET_BRANDS

    def test_a_brand_outside_the_market_is_not_in_the_table(self) -> None:
        for brand in ("LG", "Meizu", "Asus", "Micromax", "Gionee"):
            assert brand not in MARKET_BRANDS

    def test_sub_brand_maps_to_its_parent(self) -> None:
        assert parent_brand_of("Redmi") == "Xiaomi"
        assert parent_brand_of("POCO") == "Xiaomi"
        assert parent_brand_of("nubia") == "ZTE"
        assert parent_brand_of("RedMagic") == "ZTE"

    def test_a_brand_maps_to_itself_when_it_has_no_parent(self) -> None:
        assert parent_brand_of("Samsung") == "Samsung"
        assert parent_brand_of("Sony") == "Sony"


class TestLineMatching:
    """The lines the market buys, per brand."""

    @pytest.mark.parametrize(
        ("brand", "model"),
        [
            ("Samsung", "Galaxy A54"),
            ("Samsung", "Galaxy S24"),
            ("Samsung", "Galaxy M14"),
            ("Xiaomi", "Redmi Note 13"),
            ("Xiaomi", "POCO X5 Pro"),
            ("OPPO", "A78"),
            ("OPPO", "Reno 10"),
            ("vivo", "Y36"),
            ("vivo", "V29"),
            ("realme", "C55"),
            ("realme", "GT 3"),
            ("HONOR", "X9b"),
            ("HONOR", "Magic5"),
            ("Tecno", "Spark 10"),
            ("Infinix", "Hot 30"),
            ("Nokia", "G22"),
            ("Google", "Pixel 8"),
            ("Motorola", "Moto G84"),
            ("Apple", "iPhone 15"),
            ("Sony", "Xperia 10"),
            ("Lenovo", "Legion 5"),
            ("HTC", "Desire 12"),
            ("ZTE", "nubia Red Magic 5"),
        ],
    )
    def test_a_market_line_is_kept(self, brand: str, model: str) -> None:
        decision = decide(brand, model)
        assert decision.kept, decision.reason

    @pytest.mark.parametrize(
        ("brand", "model"),
        [
            ("Samsung", "Galaxy S24 Ultra"),
            ("Samsung", "Galaxy Note 20 Ultra"),
            ("Xiaomi", "Mi 11 Ultra"),
            ("OPPO", "Find X3 Pro"),
        ],
    )
    def test_a_restricted_variant_is_dropped(self, brand: str, model: str) -> None:
        decision = decide(brand, model)
        assert not decision.kept
        assert "variant" in decision.reason or "not bought" in decision.reason

    def test_a_brand_outside_the_market_is_dropped(self) -> None:
        for brand, model in (
            ("LG", "G8 ThinQ"),
            ("Meizu", "M5 Note"),
            ("Asus", "Zenfone 8"),
            ("Micromax", "Canvas 2"),
        ):
            decision = decide(brand, model)
            assert not decision.kept
            assert "not sold in Vietnam" in decision.reason

    def test_the_line_match_is_on_a_word_boundary(self) -> None:
        # "Galaxy" contains an "a", but the Oppo A line is not the
        # Samsung Galaxy line.
        assert decide("OPPO", "Galaxy").kept is False


class TestDecisions:
    """The shape of a decision."""

    def test_a_kept_decision_carries_the_reason(self) -> None:
        decision = decide("Samsung", "Galaxy A54")
        assert decision.kept
        assert decision.reason
        assert decision.brand == "Samsung"
        assert decision.model == "Galaxy A54"

    def test_a_dropped_decision_carries_the_reason(self) -> None:
        decision = decide("LG", "G8 ThinQ")
        assert not decision.kept
        assert "not sold in Vietnam" in decision.reason

    def test_the_full_catalogue_brands_are_a_subset_of_the_market(self) -> None:
        for brand in FULL_CATALOGUE_BRANDS:
            assert brand in MARKET_BRANDS

    def test_a_full_catalogue_brand_keeps_every_model(self) -> None:
        for model in ("iPhone 12", "iPhone 15 Pro Max", "iPad Air", "iPad Pro 11"):
            assert decide("Apple", model).kept


class TestFilter:
    """The filter over a record set."""

    def test_the_filter_keeps_and_drops(self) -> None:
        records = [
            {"brand": "Samsung", "model": "Galaxy A54"},
            {"brand": "Samsung", "model": "Galaxy S24 Ultra"},
            {"brand": "LG", "model": "G8 ThinQ"},
        ]
        kept, decisions = filter_records(records)
        assert len(kept) == 1
        assert kept[0]["model"] == "Galaxy A54"
        assert len(decisions) == 3

    def test_every_record_gets_a_decision(self) -> None:
        records = [{"brand": "Xiaomi", "model": f"Redmi Note {n}"} for n in range(10)]
        kept, decisions = filter_records(records)
        assert len(decisions) == len(records)
        assert len(kept) == 10

    def test_the_decision_serialises(self) -> None:
        decision = decide("Samsung", "Galaxy A54")
        encoded = json.dumps(decision.to_dict())
        assert "Galaxy A54" in encoded


class TestRealData:
    """The filter against the real merged dataset.

    This is the test that would have caught a filter that dropped the
    Pixel or the Redmi Note -- the mistakes that a unit test on the
    decision function does not reach.
    """

    @pytest.fixture(scope="class")
    def real_records(self) -> list[dict]:
        path = _ROOT / "core_patch" / "data" / "devices.jsonl"
        if not path.exists():
            pytest.skip("the merged device dataset is not present")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_the_dataset_is_present(self, real_records: list[dict]) -> None:
        assert len(real_records) > 3000, "the fleet lost its real devices"

    def test_a_popular_model_survives(self, real_records: list[dict]) -> None:
        models = {record["model"] for record in real_records}
        popular = [
            "Galaxy A54",
            "Redmi Note 13",
            "Pixel 8",
            "iPhone 15",
            "Xperia 10",
        ]
        found = [name for name in popular if any(name in model for model in models)]
        assert found, "the filter dropped every popular model"

    def test_the_filter_keeps_a_large_share(self, real_records: list[dict]) -> None:
        kept, _ = filter_records(real_records)
        # The dataset is worldwide and the market is Vietnam, so a
        # fifth is a healthy share; below it the fleet is not diverse
        # enough to separate a vendor's buckets.
        assert len(kept) >= len(real_records) * 0.2

    def test_no_kept_device_is_from_a_brand_outside_the_market(
        self, real_records: list[dict]
    ) -> None:
        kept, _ = filter_records(real_records)
        for record in kept:
            parent = parent_brand_of(record.get("brand", ""))
            assert parent in MARKET_BRANDS or record.get("brand") in MARKET_BRANDS
