"""The runtime bundle: a browser, a profile and the script that applies it.

A profile is a description of a device.  On its own it does nothing --
it is a set of numbers that says "this machine has eight gigabytes and
twelve cores and a screen of this size".  Something has to carry those
numbers into the browser and put them where the page will read them.

That something is the runtime bundle.  It has three parts:

``browser/``
    The browser itself, unpacked and ready to run.

``runtime.js``
    The script that installs the profile's values into the browser's
    own objects.  It is the same script the native core reads, so a
    profile behaves the same whether it is applied by the patched
    Chromium or by the script.

``profile.json``
    The profile, written out in the format the runtime reads.

The bundle is deliberately self-contained.  It has no dependencies
outside itself, it can be copied to another machine and run there, and
it does not need a network connection to work.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novaprofile.core.generator import ProfileGenerator
from novaprofile.js_runtime.builder import JSRuntimeBuilder

#: The browser to bundle, when the caller does not name one.  The
#: Chrome for Testing builds are used because they are the same browser
#: the native core is built from, at the same version.
DEFAULT_BROWSER_VERSION = "160.0.8176.2"

#: Where the browser archives are kept between runs.
BROWSER_CACHE = Path.home() / ".cache" / "novaprofile" / "browsers"


@dataclass(frozen=True)
class BrowserArtifact:
    """A browser that has been downloaded and unpacked.

    ``platform`` is the name the download service uses.  The mapping
    from a device profile to a platform is not a guess: a profile that
    says it is an Android phone has to run on an Android phone, and a
    profile that says it is a desktop has to run on a desktop.
    """

    version: str
    platform: str
    root: Path

    @property
    def executable(self) -> Path:
        """The binary that starts the browser."""

        names = {
            "linux-arm64": "chrome",
            "linux-x64": "chrome",
            "win64": "chrome.exe",
            "mac-arm64": "Google Chrome for Testing.app",
            "mac-x64": "Google Chrome for Testing.app",
        }
        name = names.get(self.platform, "chrome")
        candidate = self.root / name
        if candidate.exists():
            return candidate
        # The archive unpacks into a directory named after the platform.
        for child in self.root.rglob(name):
            if child.is_file():
                return child
        return candidate


@dataclass
class BundleReport:
    """What the bundle turned out to contain."""

    root: Path
    files: list[str] = field(default_factory=list)
    total_bytes: int = 0
    runtime_bytes: int = 0
    profile_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "runtime_bytes": self.runtime_bytes,
            "profile_bytes": self.profile_bytes,
        }


def platform_for_profile(profile: Any) -> str:
    """The browser platform a profile belongs on.

    A profile that describes an Android device needs the Android build
    of the browser; anything else needs the desktop build.  The
    distinction is read off the profile rather than guessed from the
    host, because the profile is the thing that has to be consistent.

    The user agent is what carries the answer.  Its platform token is
    the one a page reads, so it is the one the bundle has to agree
    with -- and it is the one that survives the whole round trip
    through the schema.
    """

    navigator = getattr(profile, "navigator", None)
    agent = str(getattr(navigator, "user_agent", "") or "")
    if "android" in agent.lower():
        return "linux-arm64"
    return "win64" if sys.platform.startswith("win") else "linux-x64"


def fetch_browser(
    version: str = DEFAULT_BROWSER_VERSION,
    platform: str = "win64",
    cache: Path | None = None,
) -> BrowserArtifact:
    """Download and unpack a browser build.

    The download comes from the Chrome for Testing service, which
    publishes the exact same version numbers the native core is built
    from.  A bundle therefore runs the same browser the native core
    would have produced.
    """

    cache = cache or BROWSER_CACHE
    target = cache / f"{version}-{platform}"
    executable_names = {
        "linux-arm64": "chrome",
        "linux-x64": "chrome",
        "win64": "chrome.exe",
    }
    name = executable_names.get(platform, "chrome")
    if (target / name).exists():
        return BrowserArtifact(version=version, platform=platform, root=target)

    target.mkdir(parents=True, exist_ok=True)
    archive = cache / f"chrome-{platform}.zip"
    url = (
        "https://storage.googleapis.com/chrome-for-testing-public/"
        f"{version}/{platform}/chrome-{platform}.zip"
    )

    if not archive.exists():
        import urllib.request

        with urllib.request.urlopen(url, timeout=900) as response, archive.open(
            "wb"
        ) as out:
            shutil.copyfileobj(response, out)

    shutil.unpack_archive(archive, target, format="zip")
    return BrowserArtifact(version=version, platform=platform, root=target)


def write_runtime(profile: Any, path: Path) -> int:
    """Write the script that installs the profile, and return its size."""

    script = JSRuntimeBuilder().build(profile)
    path.write_text(script, encoding="utf-8")
    return len(script.encode("utf-8"))


#: The identity fields that record when something happened.  They are
#: not part of the profile's identity -- the seed is -- and they change
#: on every run, so they are left out of the written profile.  A bundle
#: built twice from one seed has to be the same bundle.
VOLATILE_IDENTITY_FIELDS = (
    "created_at",
    "updated_at",
    "last_started_at",
    "last_closed_at",
    "total_runtime_ms",
)

#: The fields that record when a check was made.  Same reason: they
#: are a record of an event, not a property of the device.
VOLATILE_CHECK_FIELDS = ("checked_at", "fetched_at", "observed_at")

#: The identity of the profile *in this installation*.  It names the
#: row in the local database, so it changes every time a profile is
#: made.  The identity of the *device* is the preset and the seed,
#: and those stay.
VOLATILE_ID_FIELDS = ("id", "group_id", "folder_id")


def _strip_volatile(value: Any) -> Any:
    """Take the record of an event out of the profile.

    The profile describes a device.  A timestamp records that something
    happened at a moment in time, and that moment is not a property of
    the device -- it is a property of when the profile was made.  Two
    bundles made from one seed have to be identical, so these fields
    do not go into the written profile.
    """

    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_IDENTITY_FIELDS
            and key not in VOLATILE_CHECK_FIELDS
            and key not in VOLATILE_ID_FIELDS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def write_profile(profile: Any, path: Path) -> int:
    """Write the profile in the format the runtime reads."""

    payload = _strip_volatile(_as_dict(profile))
    text = _dump(payload)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def _as_dict(value: Any) -> Any:
    """Turn the profile into plain data.

    The profile is a Pydantic model, so it knows how to describe
    itself; anything else that comes along is asked the same way.
    """

    for attribute in ("model_dump", "dict"):
        method = getattr(value, attribute, None)
        if callable(method):
            return method()
    raise TypeError(f"cannot describe {type(value).__name__} as plain data")


def _json_default(value: Any) -> Any:
    """The last resort for a value JSON does not know.

    Timestamps are the only thing that reaches this in practice, and
    the profile's own format writes them as ISO 8601.
    """

    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    raise TypeError(f"cannot write {type(value).__name__} as JSON")


def _dump(payload: Any) -> str:
    return json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default
    )


def build_bundle(
    root: Path,
    preset: str,
    master_seed: str,
    browser_version: str = DEFAULT_BROWSER_VERSION,
    platform: str | None = None,
    include_browser: bool = True,
) -> BundleReport:
    """Assemble a runnable bundle.

    The browser is optional because the bundle is also useful without
    one -- a profile plus its script is enough to drive a browser that
    is already installed.
    """

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    generator = ProfileGenerator()
    profile = generator.generate(preset, master_seed=master_seed)
    chosen = platform or platform_for_profile(profile)

    report = BundleReport(root=root)

    if include_browser:
        artifact = fetch_browser(browser_version, chosen)
        browser_dir = root / "browser"
        if browser_dir.exists():
            shutil.rmtree(browser_dir)
        shutil.copytree(artifact.root, browser_dir, dirs_exist_ok=True)
        for path in browser_dir.rglob("*"):
            if path.is_file():
                report.files.append(str(path.relative_to(root)))
                report.total_bytes += path.stat().st_size

    report.runtime_bytes = write_runtime(profile, root / "runtime.js")
    report.profile_bytes = write_profile(profile, root / "profile.json")

    for name in ("runtime.js", "profile.json"):
        report.files.append(name)
        report.total_bytes += (root / name).stat().st_size

    report.files.append("bundle.json")
    manifest = {
        "preset": preset,
        "master_seed": master_seed,
        "browser_version": browser_version if include_browser else None,
        "platform": chosen,
        "profile": json.loads((root / "profile.json").read_text(encoding="utf-8")),
    }
    (root / "bundle.json").write_text(_dump(manifest), encoding="utf-8")
    report.total_bytes += (root / "bundle.json").stat().st_size

    return report
