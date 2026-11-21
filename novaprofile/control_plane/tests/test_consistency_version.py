"""Tests for the version-aware consistency checks.

A profile declares the Chromium major it runs on.  The client hints it
sends have to agree with that declaration: a hint the declared core cannot
produce is a contradiction that a site can read, and a hint the core would
produce but the profile omits is a gap in the disguise.
"""

from __future__ import annotations

import pytest

from novaprofile.core.consistency import ConsistencyResolver
from novaprofile.core.generator import ProfileGenerator
from novaprofile.schema.version import BrowserVersion


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _profile(gen: ProfileGenerator, major: int, seed: str = "a" * 64):
    return gen.generate(
        "pixel-8-pro-android14",
        master_seed=seed,
        browser_version=BrowserVersion(major=major),
    )


def test_a_generated_profile_is_consistent_for_every_milestone(gen):
    """A profile the generator produced must pass its own consistency check."""

    resolver = ConsistencyResolver()
    for major in (100, 112, 116, 119, 124, 136, 155):
        report = resolver.check(_profile(gen, major))
        assert report.errors == [], f"Chromium {major}: {report.errors}"


def test_a_hint_the_core_cannot_send_is_an_error(gen):
    """Declaring a Chromium 119 hint on a Chromium 116 profile is an error."""

    resolver = ConsistencyResolver()
    config = _profile(gen, 116)

    # Force the hint on, as a hand-edited profile would.
    broken = config.model_copy(
        update={
            "http_headers": config.http_headers.model_copy(
                update={"sec_ch_ua_arch": "arm", "sec_ch_ua_bitness": "64"}
            )
        }
    )

    report = resolver.check(broken)
    assert not report.ok
    assert any(issue.axis == "client_hints" for issue in report.errors)


def test_an_omitted_hint_the_core_would_send_is_a_warning(gen):
    """A Chromium 125 profile without the architecture hint is suspicious."""

    resolver = ConsistencyResolver()
    config = _profile(gen, 125)

    stripped = config.model_copy(
        update={
            "http_headers": config.http_headers.model_copy(
                update={"sec_ch_ua_arch": "", "sec_ch_ua_bitness": ""}
            )
        }
    )

    report = resolver.check(stripped)
    assert report.ok
    assert any(issue.axis == "client_hints" for issue in report.warnings)


def test_a_brand_that_contradicts_the_major_is_an_error(gen):
    """The Chromium brand must report the declared major."""

    resolver = ConsistencyResolver()
    config = _profile(gen, 125)

    brands = list(config.user_agent_data.brands)
    brands[1] = brands[1].model_copy(update={"version": "99"})
    broken = config.model_copy(
        update={"user_agent_data": config.user_agent_data.model_copy(update={"brands": brands})}
    )

    report = resolver.check(broken)
    assert not report.ok
    assert any("declares Chromium 125" in issue.message for issue in report.errors)
