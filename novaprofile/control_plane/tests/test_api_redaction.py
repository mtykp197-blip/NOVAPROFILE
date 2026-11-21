"""Tests for the redaction of sensitive profile fields.

The master seed regenerates the profile's entire fingerprint, and the
encrypted proxy password is protected by a key that lives on the same
machine.  Neither belongs in an ordinary API response, where it ends up in
a log, a screenshot or a bug report.
"""

from __future__ import annotations

from novaprofile.api.deps import REDACTED, redact_secrets, serialize_profile
from novaprofile.core.generator import ProfileGenerator


def _profile():
    config = ProfileGenerator().generate("pixel-8-pro-android14", master_seed="a" * 64)
    return config.model_copy(
        update={
            "proxy": config.proxy.model_copy(
                update={"enabled": True, "host": "1.2.3.4", "password": "c2VjcmV0"}
            )
        }
    )


def test_the_master_seed_is_redacted():
    """The seed must not appear in a serialised profile."""

    payload = serialize_profile(_profile())
    assert payload["seed_config"]["master_seed"] == REDACTED
    assert "a" * 64 not in str(payload)


def test_the_proxy_password_is_redacted():
    """The encrypted password must not appear either."""

    payload = serialize_profile(_profile())
    assert payload["proxy"]["password"] == REDACTED
    assert "c2VjcmV0" not in str(payload)


def test_the_secrets_can_be_revealed_on_request():
    """An operator who needs them can ask for them."""

    config = _profile()
    payload = serialize_profile(config, reveal_secrets=True)
    assert payload["seed_config"]["master_seed"] == config.seed_config.master_seed
    assert payload["proxy"]["password"] == "c2VjcmV0"


def test_the_other_fields_are_untouched():
    """Only the sensitive fields are affected."""

    config = _profile()
    payload = serialize_profile(config)
    assert payload["proxy"]["host"] == "1.2.3.4"
    assert payload["navigator"]["user_agent"] == config.navigator.user_agent
    assert payload["android_build"]["serial"] == config.android_build.serial


def test_redaction_works_on_a_plain_dictionary():
    """The redaction is by field name, so it works on plain data too."""

    payload = redact_secrets({"seed_config": {"master_seed": "x"}, "keep": "y"})
    assert payload["seed_config"]["master_seed"] == REDACTED
    assert payload["keep"] == "y"


def test_an_empty_secret_is_left_alone():
    """An empty password is not a secret."""

    payload = redact_secrets({"password": ""})
    assert payload["password"] == ""


def test_the_proxy_credentials_are_masked_in_the_command_line():
    """The debugging endpoint must not hand out the password in clear text."""

    from novaprofile.api.routes_profiles import _redact_args

    args = [
        "chrome.exe",
        "--proxy-server=http://1.2.3.4:8080",
        "--proxy-server-credentials=alice:hunter2",
        "--user-data-dir=C:/profiles/abc",
    ]
    masked = _redact_args(args)

    assert "alice:hunter2" not in " ".join(masked)
    assert "--proxy-server-credentials=***redacted***" in masked
    # Everything else stays readable: the endpoint is a debugging aid.
    assert "--proxy-server=http://1.2.3.4:8080" in masked
    assert "--user-data-dir=C:/profiles/abc" in masked
