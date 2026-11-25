"""The defaults of the desktop application.

The defaults are the values every *new* profile starts from: the
timezone, the locale, the proxy and the starting page.  They live in a
file beside the product's other state so that a person can set them
once and forget about them.

The module is deliberately small.  It stores a dictionary and nothing
else -- the defaults are a convenience, not a second source of truth
for the fingerprint, which always comes from the generator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULTS_FILENAME",
    "default_settings_path",
    "load_defaults",
    "save_defaults",
]

DEFAULTS_FILENAME = "defaults.json"

BUILTIN_DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Ho_Chi_Minh",
    "locale": "vi-VN",
    # The proxy of a new profile is HTTP by default.  It is the protocol
    # the overwhelming majority of the proxies in the market speak, and
    # the one the vendor lists are written in: a list of "host:port"
    # lines *is* an HTTP list.  The proxy stays switched off until a
    # host is filled in, so a new profile with no proxy still runs
    # direct.
    "proxy": {
        "mode": "http",
        "host": "",
        "port": 0,
        "username": "",
        "password": "",
    },
    "startup_url": "about:blank",
    "notes": "",
}


def default_settings_path(home: Path | None = None) -> Path:
    """Where the defaults file lives.

    Resolution order:

    1. ``$NOVA_PROFILE_HOME`` if it is set -- the tests use it.
    2. ``%LOCALAPPDATA%\\NovaProfile`` on Windows.
    3. ``~/.local/share/NovaProfile`` elsewhere.
    """

    if home is not None:
        return Path(home) / DEFAULTS_FILENAME

    override = os.environ.get("NOVA_PROFILE_HOME")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NovaProfile"
    else:
        base = Path.home() / ".local" / "share" / "NovaProfile"

    return base / DEFAULTS_FILENAME


def load_defaults(home: Path | None = None) -> dict[str, Any]:
    """Read the defaults, falling back to the built-in ones.

    A damaged file is not an error: the built-in defaults are always
    there, and a person who broke the file would rather have the
    defaults than an empty window.
    """

    settings = dict(BUILTIN_DEFAULTS)
    settings["proxy"] = dict(BUILTIN_DEFAULTS["proxy"])

    path = default_settings_path(home)
    if not path.is_file():
        return settings

    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            return settings
        for key, value in stored.items():
            if key == "proxy" and isinstance(value, dict):
                settings["proxy"].update(value)
                continue
            settings[key] = value
        return settings
    except (OSError, ValueError):
        return settings


def save_defaults(
    values: dict[str, Any],
    home: Path | None = None,
) -> dict[str, Any]:
    """Write the defaults and return what was stored."""

    path = default_settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = load_defaults(home)
    for key, value in values.items():
        if key == "proxy" and isinstance(value, dict):
            merged["proxy"].update(value)
            continue
        merged[key] = value

    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged
