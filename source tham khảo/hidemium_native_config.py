"""Native line-format adapter derived from the installed core's config reader.

This handles configuration serialization, not engine licensing or entitlement.
Observed core reader: reverse the first seven hex characters, move them to the
end, decode hex, trim and split on ':'. Runtime coverage is recorded separately.
"""
import math


# The core reads every key of the config that starts with `hidemium.`, plus
# the key `uuid` that the genuine Hidemium app writes next to them: the
# identifier of the profile, which differs in every profile.
NATIVE_KEY_PREFIX = 'hidemium.'
NATIVE_EXTRA_KEYS = ('uuid',)


def _valid_key(key):
    if not isinstance(key, str) or any(c in key for c in ':\r\n\0'):
        return False
    return key.startswith(NATIVE_KEY_PREFIX) or key in NATIVE_EXTRA_KEYS


def encode_config(config):
    lines = []
    for key, value in config.items():
        if not _valid_key(key):
            raise ValueError('Invalid native configuration key')
        if isinstance(value, bool):
            value = '1' if value else '0'
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                raise ValueError('Non-finite configuration value')
            value = str(value)
        elif not isinstance(value, str):
            raise ValueError('Native configuration values must be scalars')
        if any(c in value for c in '\r\n\0'):
            raise ValueError('Configuration value contains a line separator')
        text = (key + ':' + value).encode('utf8').hex()
        lines.append(text[-7:][::-1] + text[:-7])
    return '\n'.join(lines) + ('\n' if lines else '')


def decode_config(text):
    result = {}
    for line in text.splitlines():
        if not line:
            continue
        if len(line) < 7 or len(line) % 2:
            raise ValueError('Invalid native configuration line length')
        decoded = bytes.fromhex(line[7:] + line[:7][::-1]).decode('utf8').strip()
        key, separator, value = decoded.partition(':')
        key, value = key.strip(), value.strip()
        if not separator or not _valid_key(key) or key in result:
            raise ValueError('Invalid or duplicate native configuration field')
        result[key] = value
    return result
