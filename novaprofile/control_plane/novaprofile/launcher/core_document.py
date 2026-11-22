"""Turn a profile into the document the browser core reads.

The browser core parses the configuration with a strict JSON reader and
a strict schema: a field that is the wrong type is not skipped, it ends
the parse and the profile is refused.  Two things then happen, and both
are bad: every surface falls back to the host's real values, and the
failure is silent -- the browser starts and looks like a browser.

This module is the boundary.  It takes the profile as the control plane
knows it and produces the document the core accepts.

What it has to fix
------------------
**A number that does not fit in 32 bits.**  The core reads numbers
with a 32-bit reader, so a quota of twelve gigabytes is not an integer
to it.  The value is clamped rather than dropped: a quota of zero would
be a contradiction of its own, and the browser does report one.

**A field that is null where a string is expected.**  ``dnt`` -- the
Do Not Track header -- is optional in the profile and absent in the
core's schema, so a profile that does not track carries ``null`` and
the core refuses the whole document.  A null string becomes the empty
string, which is what the header means when it is absent.

**A number that is a float where an integer is expected.**  The
readers are exact about this.
"""

from __future__ import annotations

from typing import Any

#: The largest integer the core's number reader holds.
INT32_MAX = 2**31 - 1

#: The smallest integer it holds.
INT32_MIN = -(2**31)

#: The fields the core reads as a number.
#:
#: The profile holds some of them as an empty string when the device
#: does not know the value -- a handset that has no fix cannot report
#: a latitude, and the profile says so with ``""``.  The core reads
#: those fields as an optional number, and an optional number that is
#: absent is ``null``.  A string there is a type error, and a type
#: error ends the parse of the whole document.
#:
#: The names are the ones the core uses, and they are matched by their
#: last path segment: the same name means the same thing wherever it
#: appears, and a field of another kind with the same name would be a
#: coincidence the core would have to reject as well.
NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        # the optional geolocation readings
        "accuracy",
        "altitude",
        "altitude_accuracy",
        "heading",
        "latitude",
        "longitude",
        "speed",
        # the readings that are always numbers
        "base_latency",
        "device_pixel_ratio",
        "downlink",
        "noise_strength",
        "output_latency",
        "visual_viewport_scale",
    }
)


def coerce_document(value: Any, path: str = "") -> Any:
    """Make one value acceptable to the core.

    Returns the value itself when it is already acceptable, a clamped
    integer when it is a number that is too large, the empty string
    when it is a null string, ``null`` when it is an empty string in a
    field the core reads as a number, and an integer when it is a float
    that has to be whole.
    """

    if isinstance(value, dict):
        return {
            key: coerce_document(item, f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [coerce_document(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value > INT32_MAX:
            return INT32_MAX
        if value < INT32_MIN:
            return INT32_MIN
        return value
    if isinstance(value, float):
        # A float that has to be whole is truncated, not rounded: the
        # core reads it as an integer, and rounding would report a
        # value the profile never held.
        return int(value)
    if value is None:
        # A null string is the empty string, unless the field is one
        # the core reads as a number -- there an absent value is the
        # absent value the profile means.
        if path.rsplit(".", 1)[-1] in NUMERIC_FIELDS:
            return None
        # The core's readers are strict and have no notion of an
        # absent optional string.
        return ""
    if value == "" and path.rsplit(".", 1)[-1] in NUMERIC_FIELDS:
        # An empty string in a field the core reads as a number is the
        # profile saying "the device does not know", which the core
        # spells as an absent value.
        return None
    return value


def build_core_document(config: Any) -> dict[str, Any]:
    """The document the browser core reads.

    The profile is serialised first and then coerced, because the
    coercion walks plain JSON values: a pydantic model would need a
    second set of rules for every field it holds.
    """

    dump = getattr(config, "model_dump", None)
    if callable(dump):
        try:
            document = dict(dump(mode="json"))
        except TypeError:  # pragma: no cover - non-pydantic stand-ins
            document = dict(dump())
    elif isinstance(config, dict):
        document = dict(config)
    else:
        document = {"repr": repr(config)}

    coerced = coerce_document(document)
    return coerced if isinstance(coerced, dict) else {"repr": repr(config)}


def oversized_fields(config: Any) -> list[str]:
    """The fields of a profile that the core would refuse.

    The names are the paths in the document, so that the report says
    which value is at fault rather than that something, somewhere, is
    too large.
    """

    dump = getattr(config, "model_dump", None)
    if callable(dump):
        try:
            document = dict(dump(mode="json"))
        except TypeError:  # pragma: no cover - non-pydantic stand-ins
            document = dict(dump())
    elif isinstance(config, dict):
        document = dict(config)
    else:
        return []

    found: list[str] = []
    _walk(document, "", found)
    return found


def _walk(value: Any, path: str, found: list[str]) -> None:
    """Collect the paths of the values the core would refuse."""

    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), found)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", found)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int) and not (INT32_MIN <= value <= INT32_MAX):
        found.append(f"{path} = {value}")


__all__ = [
    "INT32_MAX",
    "INT32_MIN",
    "build_core_document",
    "coerce_document",
    "oversized_fields",
]
