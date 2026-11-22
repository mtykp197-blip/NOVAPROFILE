"""Fetch the GSMArena device specifications.

The fleet catalogue is built from *real* devices, and the only honest
source of a real device's SoC, GPU, panel and memory is the
manufacturer's own specification sheet.  This module reads the
specification tables of the GSMArena device pages and turns them into
the flat records the catalogue builder consumes.

Why the HTML is parsed here rather than fetched with a scraper
library: the specification table has a stable shape -- every row is a
``data-spec`` attribute on the value cell -- and the parse has to
survive the *absence* of a value without inventing one.  A scraper
library that raises on a missing field would take the whole run down;
here a missing field is a missing key, and the caller decides what a
device without a chipset is worth.

The output is a JSON Lines file: one JSON object per device, one file
per run.  The file is the evidence, and the catalogue builder reads it
rather than the web, so the whole fleet can be rebuilt without touching
the network again.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The user agent.  GSMArena serves the specification table to any
#: client that identifies itself as a browser; an empty or bot-like
#: agent is refused with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: The specification keys this module reads.  The key is the
#: ``data-spec`` attribute of the value cell; the value is the cell text
#: with the HTML stripped.
WANTED_SPECS = (
    "os",
    "chipset",
    "cpu",
    "gpu",
    "resolution",
    "displaytype",
    "size",
    "internal",
    "cardslot",
    "battery",
    "body",
    "releasedate",
)

#: The regular expression that pulls one specification out of the page.
#:
#: The pattern matches the value cell by its ``data-spec`` attribute, so
#: it does not depend on the row order, on the number of rows before it,
#: or on whether the specification is present at all.  A specification
#: the page does not have simply produces no match.
_SPEC_TEMPLATE = (
    r'<td\s+class="nfo"\s+data-spec="{key}"[^>]*>(?P<value>.*?)</td>'
)

#: The compiled form of :data:`_SPEC_TEMPLATE` for the common case.
_SPEC_PATTERN = re.compile(_SPEC_TEMPLATE.format(key="chipset"), re.DOTALL)

#: The title pattern.  GSMArena titles the page
#: ``<manufacturer> <model> - Full phone specifications``.
_TITLE_PATTERN = re.compile(r"<title>(?P<title>.*?)</title>", re.DOTALL)

#: The manufacturer pattern.  The page names it in the heading, and the
#: first cell of the specification table repeats it.
_MANUFACTURER_PATTERN = re.compile(
    r'<td\s+class="nfo"\s+data-spec="brand"[^>]*>(?P<value>.*?)</td>', re.DOTALL
)

#: A tag stripper.  The cells carry links and ``<br>`` separators; the
#: text is what matters, and the links are noise.
_TAG_PATTERN = re.compile(r"<[^>]+>")

#: The whitespace normaliser.  GSMArena indents its HTML, so the raw
#: cell text carries newlines and runs of spaces.
_SPACE_PATTERN = re.compile(r"\s+")

#: The URL pattern of a device page.  The identifier at the end is the
#: GSMArena device number.
_DEVICE_URL_PATTERN = re.compile(r"-(?P<id>\d+)\.php$")


@dataclass
class DeviceSpec:
    """The specifications of one device, as read from the page."""

    gsmarena_id: str = ""
    url: str = ""
    manufacturer: str = ""
    model: str = ""
    title: str = ""
    os: str = ""
    chipset: str = ""
    cpu: str = ""
    gpu: str = ""
    resolution: str = ""
    display_type: str = ""
    display_size: str = ""
    internal: str = ""
    card_slot: str = ""
    body: str = ""
    releasedate: str = ""
    #: Every specification the page carried, for the fields the builder
    #: does not consume yet.
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Whether the device carries the four fields a preset needs.

        A preset without a chipset has no SoC to pair a GPU with, and a
        preset without a resolution has no panel.  Such a device cannot
        become a physically coherent profile, so it is not a preset.
        """

        return bool(self.chipset and self.gpu and self.resolution and self.manufacturer)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the record."""

        return {
            "gsmarena_id": self.gsmarena_id,
            "url": self.url,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "title": self.title,
            "os": self.os,
            "chipset": self.chipset,
            "cpu": self.cpu,
            "gpu": self.gpu,
            "resolution": self.resolution,
            "display_type": self.display_type,
            "display_size": self.display_size,
            "internal": self.internal,
            "card_slot": self.card_slot,
            "body": self.body,
            "releasedate": self.releasedate,
            "raw": self.raw,
        }


def _clean(text: str) -> str:
    """Strip the HTML from a cell and normalise its whitespace."""

    stripped = _TAG_PATTERN.sub(" ", text)
    stripped = stripped.replace("&nbsp;", " ").replace("&amp;", "&")
    stripped = stripped.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return _SPACE_PATTERN.sub(" ", stripped).strip()


def _spec(html: str, key: str) -> str:
    """Return the cleaned text of one specification, or an empty string."""

    pattern = re.compile(_SPEC_TEMPLATE.format(key=re.escape(key)), re.DOTALL)
    match = pattern.search(html)
    if match is None:
        return ""
    return _clean(match.group("value"))


def parse_device_page(html: str, url: str = "") -> DeviceSpec:
    """Parse one GSMArena device page.

    The function never raises for a missing specification: a page that
    does not carry a field produces an empty string for it.  That is the
    point -- a device page is a *description*, and a description may be
    incomplete.
    """

    spec = DeviceSpec(url=url)

    id_match = _DEVICE_URL_PATTERN.search(url)
    if id_match is not None:
        spec.gsmarena_id = id_match.group("id")

    title_match = _TITLE_PATTERN.search(html)
    if title_match is not None:
        spec.title = _clean(title_match.group("title"))

    manufacturer = _spec(html, "brand")
    if manufacturer:
        spec.manufacturer = manufacturer
    elif spec.title:
        # The title is ``<manufacturer> <model> - Full phone
        # specifications``; the manufacturer is the part before the
        # model, and the model is the part after it.  The split is at
        # the last space of the first group, which is where the
        # manufacturer ends and the model begins.
        head = spec.title.split(" - Full phone specifications")[0].strip()
        if head:
            parts = head.split(" ", 1)
            spec.manufacturer = parts[0]
            spec.model = parts[1] if len(parts) > 1 else parts[0]

    spec.os = _spec(html, "os")
    spec.chipset = _spec(html, "chipset")
    spec.cpu = _spec(html, "cpu")
    spec.gpu = _spec(html, "gpu")
    spec.resolution = _spec(html, "resolution")
    spec.display_type = _spec(html, "displaytype")
    spec.display_size = _spec(html, "size")
    spec.internal = _spec(html, "internal")
    spec.card_slot = _spec(html, "cardslot")
    spec.body = _spec(html, "body")
    spec.releasedate = _spec(html, "releasedate")

    for key in WANTED_SPECS:
        value = _spec(html, key)
        if value:
            spec.raw[key] = value

    return spec


def fetch(url: str, *, timeout: float = 30.0, retries: int = 2) -> str | None:
    """Fetch one page, or return ``None`` when it cannot be read.

    The retries exist because the server refuses a burst of requests
    with a 403 now and then.  A refused request is not a permanent
    failure, so the function waits and asks again rather than dropping
    the device.
    """

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def read_url_list(path: Path) -> list[str]:
    """Read the device page URLs from a text file, one per line."""

    urls: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and stripped.startswith("http"):
                urls.append(stripped)
    return urls


def scrape(
    urls: list[str],
    output: Path,
    *,
    workers: int = 8,
    delay: float = 0.0,
    limit: int | None = None,
) -> dict[str, int]:
    """Fetch every URL and write the records to a JSON Lines file.

    The file is written as the run proceeds, so an interrupted run keeps
    what it has already read.  The function returns the counters the
    caller reports.
    """

    if limit is not None:
        urls = urls[:limit]

    counters = {"fetched": 0, "complete": 0, "failed": 0}
    output.parent.mkdir(parents=True, exist_ok=True)

    def work(url: str) -> tuple[str, DeviceSpec | None]:
        if delay:
            time.sleep(delay)
        html = fetch(url)
        if html is None:
            return url, None
        return url, parse_device_page(html, url)

    with output.open("w", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, spec in pool.map(work, urls):
                if spec is None:
                    counters["failed"] += 1
                    continue
                counters["fetched"] += 1
                if spec.complete:
                    counters["complete"] += 1
                handle.write(json.dumps(spec.to_dict(), ensure_ascii=False) + "\n")

    return counters


def main(argv: list[str] | None = None) -> int:
    """The command line entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--urls",
        type=Path,
        required=True,
        help="the text file that holds one device page URL per line",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="the JSON Lines file to write",
    )
    parser.add_argument("--workers", type=int, default=8, help="the number of parallel fetches")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="the seconds to wait before each fetch (use it to be gentle)",
    )
    parser.add_argument("--limit", type=int, default=None, help="fetch at most this many URLs")
    args = parser.parse_args(argv)

    urls = read_url_list(args.urls)
    if not urls:
        print("the URL list is empty", file=sys.stderr)
        return 1

    counters = scrape(
        urls,
        args.output,
        workers=args.workers,
        delay=args.delay,
        limit=args.limit,
    )
    print(
        f"fetched={counters['fetched']} complete={counters['complete']} "
        f"failed={counters['failed']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
