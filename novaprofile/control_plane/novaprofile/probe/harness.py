"""Harness page generation for the NOVA runtime probe.

The harness page is a *static artefact*: :func:`novaprofile.probe.measurements.build_harness_html`
generates it and the very same content is checked in as
``novaprofile/probe/harness.html`` so that it can be served straight from the
filesystem (``file://`` URL) without a templating step.

This module owns the canonical location of that file and provides helpers to
read it back and to verify that the checked-in copy is still in sync with the
generator.
"""

from __future__ import annotations

from pathlib import Path

from .measurements import build_harness_html

#: Absolute path of the checked-in harness page.
HARNESS_HTML_PATH: Path = Path(__file__).resolve().parent / "harness.html"

#: Marker strings that MUST appear in a valid harness page.
REQUIRED_MARKERS: tuple[str, ...] = (
    "__NOVA_PROBE__",
    "__NOVA_PROBE_READY__",
    "nova.probe/1",
    "run: function",
)


def harness_path() -> Path:
    """Return the path of the checked-in harness page."""

    return HARNESS_HTML_PATH


def read_harness_html() -> str:
    """Read the checked-in harness page from disk."""

    return HARNESS_HTML_PATH.read_text(encoding="utf-8")


def is_self_contained(html: str) -> bool:
    """Return ``True`` when the harness page references no external resource.

    The harness must work from a ``file://`` URL inside an isolated profile
    with no network access, so any ``http://`` / ``https://`` / protocol
    relative reference is a hard failure.
    """

    lowered = html.lower()
    for marker in ("http://", "https://", "//cdn", 'src="//', 'href="//'):
        if marker in lowered:
            return False
    return True


def write_harness_html(path: Path | None = None) -> Path:
    """Regenerate the harness page and write it to ``path``.

    Returns the path that was written.
    """

    target = Path(path) if path is not None else HARNESS_HTML_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_harness_html(), encoding="utf-8")
    return target


__all__ = [
    "HARNESS_HTML_PATH",
    "REQUIRED_MARKERS",
    "build_harness_html",
    "harness_path",
    "is_self_contained",
    "read_harness_html",
    "write_harness_html",
]
