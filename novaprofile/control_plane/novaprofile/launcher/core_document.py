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
        # The stratum is optional: a profile that stands alone has
        # none, and the core reads an absent stratum as "this profile
        # does not belong to a family".  A null there has to stay a
        # null -- the empty string would be a type error, and the core
        # refuses the whole document over a type error.
        "stratum",
    }
)

#: The fields the core reads as a *double* rather than as an integer.
#:
#: The coercion above turns every float into a whole number, because the
#: core's number reader is a 32-bit integer reader and a float where an
#: integer is expected is a type error.  That rule is wrong for the
#: fields the core reads with ``ReadDouble``: a pixel ratio of 2.625 is
#: a real value, a network latency of 22.5 milliseconds is a real
#: value, and truncating them changes what the browser reports -- a
#: pixel ratio of 2.625 becomes 2, which is a different device.
#:
#: The names are the ones the core uses, matched by their last path
#: segment, exactly like :data:`NUMERIC_FIELDS`.
FRACTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        # the screen and the viewport
        "device_pixel_ratio",
        # The ratios the panels of a model are able to report.  The
        # core draws the ratio of a unit from this list when the
        # profile leaves the ratio out, and a list of ratios that was
        # truncated to whole numbers is a list of different ratios.
        "device_pixel_ratio_options",
        "visual_viewport_scale",
        # the audio timings
        "base_latency",
        "output_latency",
        # the canvas and the audio noise strengths
        "noise_strength",
        # the network timings
        "downlink",
        # the battery level
        "level",
        # the motion sensors, in the accelerometer, the gyroscope, the
        # gravity vector and the orientations
        "x",
        "y",
        "z",
        "alpha",
        "beta",
        "gamma",
    }
)


def coerce_document(value: Any, path: str = "", field: str | None = None) -> Any:
    """Make one value acceptable to the core.

    Returns the value itself when it is already acceptable, a clamped
    integer when it is a number that is too large, the empty string
    when it is a null string, ``null`` when it is an empty string in a
    field the core reads as a number, and an integer when it is a float
    that has to be whole.

    |field| is the name of the field the value belongs to, when it is
    known.  It is the name the core knows, and for an item of a list it
    is the name of the list itself rather than the index: the core
    reads a list of pixel ratios as a list of doubles, and an index is
    not a field name it knows.
    """

    if field is None:
        field = path.rsplit(".", 1)[-1] if path else ""

    if isinstance(value, dict):
        return {
            key: coerce_document(item, f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        # A list of numbers keeps the type of the field it belongs to.
        #
        # The path of an item in a list ends in its index, so a list of
        # pixel ratios would have every one of its items truncated to an
        # integer -- 2.625 becoming 2 -- because the *index* is not a
        # field name the core knows.  The field the list belongs to is
        # the name before the bracket, and that is the name the
        # coercion has to look at.
        field = path.rsplit("[", 1)[0].rsplit(".", 1)[-1] if "[" in path else path.rsplit(".", 1)[-1]
        return [coerce_document(item, path, field=field) for index, item in enumerate(value)]
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
        #
        # The fields the core reads as a double are the exception: a
        # pixel ratio of 2.625 is a real value, and truncating it to 2
        # would report a device the profile never described.
        if field in FRACTIONAL_FIELDS:
            return value
        return int(value)
    if value is None:
        # A null string is the empty string, unless the field is one
        # the core reads as a number -- there an absent value is the
        # absent value the profile means.
        if field in NUMERIC_FIELDS:
            return None
        # The core's readers are strict and have no notion of an
        # absent optional string.
        return ""
    if value == "" and field in NUMERIC_FIELDS:
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
