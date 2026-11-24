"""Replace the duplicated hardware derivation in the specification source.

``device_spec_source`` and ``device_catalog_market`` read the same
handset specifications and had grown the same copy of the hardware
rules: the same table of real memory sizes, the same table of real core
counts, and the same arithmetic over them.  Two copies of a rule is
one copy too many -- they drift, and the drift shows up as two
handbooks that disagree with each other.

This script replaces the two bodies in ``device_spec_source`` with
calls into ``device_catalog_market``, which is the module the fleet
reads its presets from.  The rules now live in one place.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TARGET = HERE / "novaprofile" / "core" / "device_spec_source.py"

RAM_BODY = '''def derive_ram_options(memory: str) -> tuple[int, ...]:
    """The RAM configurations a handset ships with.

    The rule is the one the fleet reads its presets from, and it lives
    in :mod:`novaprofile.core.device_catalog_market`: the amounts the
    memory text names, and the smaller ones the line was also sold in.
    """

    return _derive_ram_options(memory)
'''

CPU_BODY = '''def derive_cpu_options(chipset: str) -> tuple[int, ...]:
    """The core counts a chipset of this model can report.

    The rule is the one the fleet reads its presets from, and it lives
    in :mod:`novaprofile.core.device_catalog_market`: the core counts
    of the chipsets the record actually names.  A chipset has one core
    count, so a model that shipped with one chipset has one count.
    """

    return _derive_cpu_options(chipset)
'''


def _replace(text: str, header: str, body: str) -> str:
    """Replace the function whose header starts with ``header``."""

    start = text.index(header)
    # The function ends at the next top-level definition.
    end = text.index("\n\ndef ", start + len(header))
    return text[:start] + body + text[end:]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = _replace(text, "def derive_ram_options(memory: str)", RAM_BODY)
    text = _replace(text, "def derive_cpu_options(chipset: str)", CPU_BODY)
    TARGET.write_text(text, encoding="utf-8")
    print("the specification source now shares the hardware rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
