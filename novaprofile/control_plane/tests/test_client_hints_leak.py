"""A profile must not answer a question the browser answers differently.

The verification harness compares what the browser reports against
what the profile carries.  When the two disagree on an axis that the
profile *does* settle, that is a leak.

The client hints are the sharpest case, because the browser answers
the high-entropy question over the *native* channel, where the
JavaScript runtime's overridden accessors do not reach.  A profile
that claims to be a browser old enough not to send the full version
list has to end up with an empty list, not with the host's list --
the host's list names a browser major the profile does not claim to
be, and a page that reads both sees the contradiction.
"""

from __future__ import annotations

from novaprofile.core.generator import ProfileGenerator
from novaprofile.probe.verify import VerifyRunner


def _verify(preset_id: str, seed: str):
    config = ProfileGenerator().generate(preset_id, master_seed=seed)
    return VerifyRunner(config, headless=True, timeout=180.0).run_sync()


class TestTheHighEntropyChannelIsClosed:
    """The native channel has to answer with the profile's values."""

    def test_a_modern_profile_reports_its_own_version_list(self) -> None:
        """A profile on a browser that has the list reports the list.

        The list has to name the browser the profile claims to be.  A
        list naming the host's browser is the single loudest way for
        a profile to give itself away.
        """

        report = _verify("pixel-8-pro-android14", "c" * 64)
        hints = next(item for item in report.axes if item.axis == "client_hints")

        versions = next(f for f in hints.fields if f.field == "fullVersionList")
        assert versions.verdict == "match", (
            f"the browser reported {versions.actual}, the profile carries {versions.expected}"
        )

    def test_an_old_profile_reports_no_version_list(self) -> None:
        """A profile on a browser that has no list must report none.

        Chromium only started sending the full version list in major
        116.  Before that the accessor does not exist at all, and a
        profile that carries a list is claiming a capability the
        browser it runs in does not have.
        """

        report = _verify("galaxy-a54-android13", "3" * 64)
        hints = next(item for item in report.axes if item.axis == "client_hints")

        versions = next(f for f in hints.fields if f.field == "fullVersionList")
        assert versions.verdict == "match", (
            f"the browser reported {versions.actual}, the profile carries {versions.expected}"
        )

    def test_the_brands_name_the_profiles_browser(self) -> None:
        """The brands are the loudest hint of all.

        Every brand in the list has to name the browser major the
        profile claims to be.  A host's brand list would name a
        browser that the profile's own user agent contradicts.
        """

        report = _verify("redmi-note-12-android13", "7" * 64)
        hints = next(item for item in report.axes if item.axis == "client_hints")

        brands = next(f for f in hints.fields if f.field == "brands")
        assert brands.verdict == "match", (
            f"the browser reported {brands.actual}, the profile carries {brands.expected}"
        )

        reported = brands.actual or []
        assert reported, "no brands were reported"
        for entry in reported:
            version = str(entry.get("version"))
            # The greased brand carries a random number; the two real
            # brands carry the browser major.
            if entry.get("brand") in {"Chromium", "Google Chrome"}:
                assert version.isdigit()
                assert 100 <= int(version) <= 200, (
                    f"the brand list names browser {version}, which the "
                    "profile does not claim to be"
                )
