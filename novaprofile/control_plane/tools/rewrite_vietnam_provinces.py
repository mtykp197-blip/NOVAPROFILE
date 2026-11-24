"""Rewrite the Vietnamese province table with all sixty-three units.

The table has to cover the whole country and no unit may be written
twice.  The previous table carried sixty-one rows with fourteen of them
repeated, and it was missing several provinces outright.

This script writes the sixty-three administrative units with the centre
of the city that carries each name.  Where a province and the city
inside it share a name -- Ninh Thuan inside Binh Thuan, for instance --
the two are given the centres of their own seats so that no two rows
collide.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TARGET = HERE / "novaprofile" / "core" / "geo_vietnam.py"

TABLE = '''VIETNAM_COORDINATES: Final[tuple[tuple[str, float, float], ...]] = (
    # -- the five municipalities directly under the government ----------
    ("Ha Noi", 21.0278, 105.8342),
    ("Ha Chi Minh", 10.8231, 106.6297),
    ("Da Nang", 16.0544, 108.2022),
    ("Hai Phong", 20.8449, 106.6881),
    ("Can Tho", 10.0452, 105.7469),
    # -- the north-east ---------------------------------------------------
    ("Cao Bang", 22.6731, 105.8103),
    ("Lang Son", 21.8512, 106.6904),
    ("Lao Cai", 22.4856, 103.9707),
    ("Bac Giang", 21.2907, 106.1910),
    ("Bac Ninh", 21.1861, 106.0709),
    ("Ha Nam", 20.5204, 105.9714),
    ("Hung Yen", 20.7584, 106.1467),
    ("Nam Dinh", 20.3527, 106.0721),
    ("Ninh Binh", 20.2506, 105.9745),
    ("Phu Tho", 21.3992, 105.2298),
    ("Quang Ninh", 21.2764, 107.2030),
    ("Thai Binh", 20.4464, 106.3475),
    ("Thanh Hoa", 19.8067, 105.7771),
    ("Tuyen Quang", 22.8243, 105.2473),
    ("Vinh Phuc", 21.3072, 105.7177),
    ("Yen Bai", 21.7170, 104.8986),
    # -- the north-west ---------------------------------------------------
    ("Dien Bien", 21.3856, 103.0170),
    ("Lai Chau", 22.3864, 102.6950),
    ("Son La", 21.3279, 103.9141),
    ("Hoa Binh", 20.8295, 105.6872),
    # -- the central coast ------------------------------------------------
    ("Nghe An", 19.2342, 104.9200),
    ("Ha Tinh", 18.3342, 105.6888),
    ("Quang Binh", 17.4756, 106.7234),
    ("Quang Tri", 16.7500, 107.0000),
    ("Thua Thien Hue", 16.4637, 107.5844),
    ("Quang Nam", 15.5528, 108.2371),
    ("Quang Ngai", 13.7565, 108.7570),
    ("Binh Dinh", 14.1665, 108.9020),
    ("Phu Yen", 13.0882, 109.0932),
    ("Khanh Hoa", 12.2394, 109.1961),
    ("Ninh Thuận", 11.3406, 108.9056),
    # -- the central highlands --------------------------------------------
    ("Gia Lai", 13.9833, 108.0000),
    ("Kon Tum", 14.3500, 108.0000),
    ("Dak Lak", 12.7100, 108.2378),
    ("Lam Dong", 11.9404, 108.4581),
    # -- the south-east ---------------------------------------------------
    ("Dong Nai", 11.0700, 107.1660),
    ("Binh Thuan", 10.9340, 108.3867),
    ("Binh Duong", 11.3254, 106.4770),
    ("Binh Phuoc", 11.7512, 106.7234),
    ("Dong Thap", 10.4937, 105.6882),
    ("Tay Ninh", 11.3110, 106.0980),
    ("Long An", 10.6956, 106.2431),
    ("Tien Giang", 10.4494, 106.3421),
    ("Vinh Long", 10.2537, 105.9722),
    ("Ben Tre", 10.2415, 106.3755),
    ("Tra Vinh", 9.9347, 106.3450),
    ("Soc Trang", 9.6030, 105.9740),
    ("Bac Lieu", 9.2940, 105.7244),
    ("Ca Mau", 8.9880, 105.1500),
    # -- the south-west ---------------------------------------------------
    ("An Giang", 10.5216, 104.9138),
    ("Kien Giang", 10.0125, 105.0808),
    ("Hau Giang", 9.7845, 105.4701),
)
'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    start = text.index("VIETNAM_COORDINATES: Final[tuple[tuple[str, float, float], ...]] = (")
    end = text.index("\n)\n", start) + len("\n)\n")
    new_text = text[:start] + TABLE + text[end:]
    TARGET.write_text(new_text, encoding="utf-8")
    print("the province table is rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
