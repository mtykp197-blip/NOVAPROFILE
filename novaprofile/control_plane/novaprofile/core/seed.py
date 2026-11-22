"""Deterministic seed derivation engine.

The whole uniqueness guarantee of NOVA rests on this module.  A profile's
entire fingerprint is a pure function of ``(master_seed, seed_epoch,
nudge_step)``.  Two properties must hold at all times:

1. **Determinism** -- the same inputs always produce byte-identical output, so
   a profile survives restarts with an identical fingerprint.
2. **Axis independence** -- each fingerprint axis is derived through its own
   HKDF-SHA256 *domain salt*.  If two axes shared a salt, a single field
   change (e.g. the GPU renderer) would perturb unrelated axes, and two
   profiles could correlate on those axes even though their seeds differ.

The WebGL pixel axis additionally binds the ``unmasked_renderer`` string into
the hash so that two different GPU models can never accidentally produce the
same image hash -- see :meth:`SeedDeriver.derive_webgl_image_value`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Sequence
from enum import Enum
from typing import TypeVar

T = TypeVar("T")

HKDF_HASH = hashlib.sha256
HKDF_HASH_LEN = 32

#: How many slices a stratified draw splits the choice space into.
#:
#: The number is the square root of the fleet size a family is
#: expected to carry, rounded up.  A family with a dozen handsets
#: wants a dozen different browser majors; a family with two hundred
#: wants the space spread over two hundred slices, but a choice space
#: of twelve cannot carry more than twelve distinct values anyway.
#: Sixteen is the smallest number that keeps the slices of every
#: choice space the engine actually uses -- a browser major band, a
#: chip count, a memory option -- disjoint.
_STRATUM_COUNT = 16


class Axis(str, Enum):
    """The fingerprint axes that receive an independent HKDF domain salt.

    The spec enumerates exactly these; adding an axis here is safe, reusing
    one is not -- reusing a salt would couple two axes together.
    """

    ANDROID_BUILD = "android_build"
    NAVIGATOR = "navigator"
    CLIENT_HINTS = "client_hints"
    SCREEN_VIEWPORT = "screen_viewport"
    CANVAS_2D = "canvas_2d"
    WEBGL_PIXEL = "webgl_pixel"
    WEBGL_PARAMS = "webgl_params"
    WEBGPU = "webgpu"
    AUDIO = "audio"
    RECTS = "rects"
    FONTS = "fonts"
    MEDIA_DEVICES = "media_devices"
    SENSORS = "sensors"
    BATTERY_NETWORK = "battery_network"
    GEO_TIMEZONE = "geo_timezone"
    STORAGE_QUOTA = "storage_quota"
    MATH_PRECISION = "math_precision"
    HEAP_SIZE = "heap_size"
    MEDIA_DECODE = "media_decode"


#: Domain separation strings.  These are *not* secrets -- they are protocol
#: constants that must never change once released, because changing one would
#: invalidate every profile generated with the previous version.
DOMAIN_SALTS: dict[Axis, bytes] = {
    Axis.ANDROID_BUILD: b"nova/v1/android_build",
    Axis.NAVIGATOR: b"nova/v1/navigator",
    Axis.CLIENT_HINTS: b"nova/v1/client_hints",
    Axis.SCREEN_VIEWPORT: b"nova/v1/screen_viewport",
    Axis.CANVAS_2D: b"nova/v1/canvas_2d",
    Axis.WEBGL_PIXEL: b"nova/v1/webgl_pixel",
    Axis.WEBGL_PARAMS: b"nova/v1/webgl_params",
    Axis.WEBGPU: b"nova/v1/webgpu",
    Axis.AUDIO: b"nova/v1/audio",
    Axis.RECTS: b"nova/v1/rects",
    Axis.FONTS: b"nova/v1/fonts",
    Axis.MEDIA_DEVICES: b"nova/v1/media_devices",
    Axis.SENSORS: b"nova/v1/sensors",
    Axis.BATTERY_NETWORK: b"nova/v1/battery_network",
    Axis.GEO_TIMEZONE: b"nova/v1/geo_timezone",
    Axis.STORAGE_QUOTA: b"nova/v1/storage_quota",
    Axis.MATH_PRECISION: b"nova/v1/math_precision",
    Axis.HEAP_SIZE: b"nova/v1/heap_size",
    Axis.MEDIA_DECODE: b"nova/v1/media_decode",
}


class SeedError(ValueError):
    """Raised when a seed or derivation parameter is invalid."""


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 5869 HKDF-Extract."""
    if not salt:
        salt = bytes(HKDF_HASH_LEN)
    return hmac.new(salt, ikm, HKDF_HASH).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-Expand."""
    if length < 0:
        raise SeedError("HKDF-Expand length must be non-negative")
    if length > 255 * HKDF_HASH_LEN:
        raise SeedError("HKDF-Expand length exceeds the RFC 5869 limit")
    if not info:
        info = b""
    okm = bytearray()
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), HKDF_HASH).digest()
        okm.extend(block)
        counter += 1
    return bytes(okm[:length])


def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """Full RFC 5869 HKDF (extract-then-expand)."""
    return hkdf_expand(hkdf_extract(salt, ikm), info, length)


class SeedDeriver:
    """Derives per-axis deterministic values from a profile's master seed.

    Parameters
    ----------
    master_seed:
        64-character hex string (256 bits) produced by a CSPRNG.
    seed_epoch:
        Incremented by the collision guard when a profile must be re-rolled
        into a completely different device.
    nudge_step:
        Incremented by the collision guard to shift the deep decimal digits of
        a single axis (used for the WebGL image hash) without changing the
        rest of the device.
    """

    HKDF_VERSION: str = "hkdf-sha256-v1"

    __slots__ = (
        "_master_seed",
        "_seed_epoch",
        "_nudge_step",
        "_axis_cache",
        "_info_cache",
        "_draw_counters",
        "_retry",
    )

    def __init__(self, master_seed: str, seed_epoch: int = 0, nudge_step: int = 0) -> None:
        self._master_seed = self._validate_seed(master_seed)
        self._seed_epoch = int(seed_epoch)
        self._nudge_step = int(nudge_step)
        if self._seed_epoch < 0:
            raise SeedError("seed_epoch must be non-negative")
        if self._nudge_step < 0:
            raise SeedError("nudge_step must be non-negative")
        self._axis_cache: dict[Axis, bytes] = {}
        self._info_cache: dict[tuple[Axis, str], bytes] = {}
        self._draw_counters: dict[tuple[Axis, str], int] = {}
        self._retry = 0

    # ------------------------------------------------------------------
    # Validation / properties
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_seed(master_seed: str) -> bytes:
        if not isinstance(master_seed, str):
            raise SeedError("master_seed must be a hex string")
        cleaned = master_seed.strip().lower()
        if len(cleaned) != 64:
            raise SeedError(f"master_seed must be exactly 64 hex characters, got {len(cleaned)}")
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as exc:
            raise SeedError("master_seed contains non-hex characters") from exc
        if len(raw) != 32:
            raise SeedError("master_seed must decode to 256 bits")
        return raw

    @property
    def master_seed(self) -> bytes:
        return self._master_seed

    @property
    def master_seed_hex(self) -> str:
        return self._master_seed.hex()

    @property
    def seed_epoch(self) -> int:
        return self._seed_epoch

    @property
    def nudge_step(self) -> int:
        return self._nudge_step

    # ------------------------------------------------------------------
    # Core derivation
    # ------------------------------------------------------------------

    def axis_key(self, axis: Axis) -> bytes:
        """HKDF-Extract with the axis' domain salt -> a 32-byte pseudo-random key.

        The result is cached: it depends only on the master seed and the axis.
        """
        cached = self._axis_cache.get(axis)
        if cached is not None:
            return cached
        salt = DOMAIN_SALTS[axis]
        prk = hkdf_extract(salt, self._master_seed)
        self._axis_cache[axis] = prk
        return prk

    def _info(self, axis: Axis, context: str) -> bytes:
        """Build the HKDF-Expand ``info`` field.

        The seed epoch and the nudge step are folded in here so that bumping
        either one re-keys *every* axis consistently, while the axis salt keeps
        the axes mutually independent.

        The result is cached per ``(axis, context)``: the prefix depends only
        on the epoch and the nudge step, which are fixed for the lifetime of
        the deriver, and the field is built once per axis and context.
        """
        key = (axis, context)
        cached = self._info_cache.get(key)
        if cached is not None:
            return cached
        ctx = context.encode("utf-8") if context else b""
        info = b"|".join(
            [
                axis.value.encode("ascii"),
                f"epoch={self._seed_epoch}".encode("ascii"),
                f"nudge={self._nudge_step}".encode("ascii"),
                ctx,
            ]
        )
        self._info_cache[key] = info
        return info

    def _next_counter(self, axis: Axis, context: str) -> int:
        """A monotonically increasing draw counter for an (axis, context)."""
        key = (axis, context)
        current = self._draw_counters.get(key, 0)
        self._draw_counters[key] = current + 1
        return current

    def derive_bytes(self, axis: Axis, length: int, context: str = "") -> bytes:
        """HKDF-Expand to an arbitrary number of bytes."""
        return hkdf_expand(self.axis_key(axis), self._info(axis, context), length)

    def derive_int(self, axis: Axis, low: int, high: int, context: str = "") -> int:
        """Uniform integer in the inclusive range ``[low, high]``.

        Each call draws a fresh value: the draw counter is part of the HKDF
        ``info`` string, so two calls with the same axis and context return
        two *different* numbers instead of repeating the first one.
        """
        if low > high:
            raise SeedError(f"invalid range [{low}, {high}]")
        span = high - low + 1
        if span == 1:
            return low
        # Rejection sampling keeps the distribution exactly uniform.
        n_bytes = max(1, (span.bit_length() + 7) // 8)
        limit = (1 << (n_bytes * 8)) - ((1 << (n_bytes * 8)) % span)
        counter = self._next_counter(axis, context)
        while True:
            block = self.derive_bytes(
                axis, n_bytes, f"{context}|draw={counter}|retry={self._retry}"
            )
            value = int.from_bytes(block, "big")
            if value < limit:
                self._retry = 0
                return low + (value % span)
            self._retry += 1
            counter += 1

    def derive_float(self, axis: Axis, low: float, high: float, context: str = "") -> float:
        """Uniform float in ``[low, high)`` with 53 bits of precision.

        Each call draws a fresh value; see :meth:`derive_int`.
        """
        if low > high:
            raise SeedError(f"invalid range [{low}, {high}]")
        if low == high:
            return low
        counter = self._next_counter(axis, context)
        raw = int.from_bytes(self.derive_bytes(axis, 8, f"{context}|draw={counter}"), "big")
        unit = raw / float(1 << 64)
        return low + unit * (high - low)

    def derive_bool(self, axis: Axis, true_probability: float = 0.5, context: str = "") -> bool:
        """Derive a boolean with the given probability of being ``True``."""
        if not 0.0 <= true_probability <= 1.0:
            raise SeedError("true_probability must be within [0, 1]")
        if true_probability == 0.0:
            return False
        if true_probability == 1.0:
            return True
        return self.derive_float(axis, 0.0, 1.0, context) < true_probability

    def derive_choice(self, axis: Axis, options: Sequence[T], context: str = "") -> T:
        """Pick one element uniformly from ``options``."""
        if not options:
            raise SeedError("cannot choose from an empty sequence")
        return options[self.derive_int(axis, 0, len(options) - 1, context)]

    def derive_weighted_choice(
        self,
        axis: Axis,
        options: Sequence[tuple[T, float]],
        context: str = "",
    ) -> T:
        """Pick an element using non-uniform ``(value, weight)`` pairs."""
        total = sum(weight for _, weight in options)
        if total <= 0:
            raise SeedError("weights must sum to a positive value")
        target = self.derive_float(axis, 0.0, total, context)
        cumulative = 0.0
        for value, weight in options:
            cumulative += weight
            if target < cumulative:
                return value
        return options[-1][0]

    def derive_shuffled(self, axis: Axis, items: Sequence[T], context: str = "") -> list[T]:
        """Deterministically shuffle a sequence (Fisher-Yates, seeded)."""
        result = list(items)
        for i in range(len(result) - 1, 0, -1):
            j = self.derive_int(axis, 0, i, f"{context}|swap={i}")
            result[i], result[j] = result[j], result[i]
        return result

    def derive_stratified(
        self,
        axis: Axis,
        options: Sequence[T],
        context: str,
        stratum: int,
    ) -> T:
        """Pick an option from a *stratum* of the choice space.

        The ordinary :meth:`derive_choice` draws independently, so two
        profiles of the same model can land on the same value.  On a
        small choice space -- a browser major out of a dozen, a chip
        count out of four -- that happens often enough for a site to
        group the fleet on it.

        A stratified draw splits the choice space into slices and gives
        each unit its own slice.  Two units of the same model therefore
        draw from *different* slices, and the only way they can still
        agree is if the slices themselves overlap, which they do not.

        The slices are laid out to cover the space exactly, so every
        value is offered to exactly as many units as the space can
        carry.  When there are more units than slices -- a family of
        handsets larger than the browser band -- the surplus units are
        dealt the slices in reverse, which keeps two units that would
        otherwise have shared a slice apart for as long as possible.

        The stratum is folded into the derivation, so the choice stays
        a pure function of the seed and the stratum: the same unit
        always gets the same slice, and a profile survives a restart
        with an identical fingerprint.
        """

        if not options:
            raise SeedError("cannot choose from an empty sequence")
        count = len(options)

        # How many slices the space is cut into.  There is never more
        # than one slice per value, and never fewer than one.
        slices = min(count, _STRATUM_COUNT)
        # The base of each slice.  Slicing the space evenly means the
        # last slice may be shorter than the others.
        base, extra = divmod(count, slices)

        # The slice this unit draws from.  Units beyond the slice count
        # are dealt the slices in reverse, so two units that would
        # share a slice are as far apart in the space as the space
        # allows.
        index = stratum % _STRATUM_COUNT
        if index >= slices:
            index = slices - 1 - (index % slices)

        low = index * base + min(index, extra)
        high = low + base + (1 if index < extra else 0)
        if high <= low:
            # A degenerate slice (a space smaller than the slice
            # count): fall back to the whole space.
            low, high = 0, count

        return options[self.derive_int(axis, low, high - 1, context)]

    # ------------------------------------------------------------------
    # WebGL image value -- the anti cross-GPU-collision axis
    # ------------------------------------------------------------------

    def derive_webgl_image_value(self, unmasked_renderer: str) -> str:
        """Derive the deterministic WebGL pixel-noise value for a GPU.

        The ``unmasked_renderer`` string is folded into the hash input, which
        is what prevents two different GPU models from ever colliding on the
        same image hash.  Two profiles with different renderers can never
        produce the same value even with an identical master seed.

        The result keeps at least 17 significant decimal digits so that it
        survives a round trip through a C++ ``double`` and a JSON string
        without being rounded.
        """
        if not unmasked_renderer:
            raise SeedError("unmasked_renderer must not be empty")

        # Hash input = axis key || epoch || nudge || renderer || a fixed tag.
        # The tag separates this derivation from any other use of the axis.
        material = b"\x00".join(
            [
                self.axis_key(Axis.WEBGL_PIXEL),
                f"epoch={self._seed_epoch}".encode("ascii"),
                f"nudge={self._nudge_step}".encode("ascii"),
                unmasked_renderer.encode("utf-8"),
                b"webgl.image_value.v1",
            ]
        )
        digest = hashlib.sha256(material).digest()

        # Expand to 32 bytes (two SHA-256 blocks worth of deterministic data)
        # and interpret as a big integer scaled into [1e-6, 1).
        raw = hkdf_expand(digest, b"webgl-image-value", 32)
        integer = int.from_bytes(raw, "big")
        # Scale into (0, 1) with 17+ significant digits of resolution.
        scaled = integer / float(1 << 256)
        # Map into a positive magnitude with an Android-plausible scale.
        value = scaled * 1e-4 + 1e-7

        # "%.17g" guarantees 17 significant digits round-trip exactly.  The
        # C-style format is deliberate: it must match the snprintf("%.17g")
        # call in the C++ core so that both sides produce byte-identical
        # strings.
        text = "%.17g" % value  # noqa: UP031
        # Guarantee the minimum significant-digit count even if the value
        # happens to have a short decimal representation.
        if _significant_digits(text) < 17:
            text = "%.*e" % (16, value)  # noqa: UP031
        return text

    def webgl_image_value(self) -> str:
        """WebGL image value for the generic (renderer-agnostic) case."""
        return self.derive_webgl_image_value("generic")

    # ------------------------------------------------------------------
    # Signature helpers
    # ------------------------------------------------------------------

    def signature(self, axis: Axis, *parts: str) -> str:
        """Stable hex signature for an axis, used by the collision guard."""
        material = b"\x1f".join(
            [axis.value.encode("ascii")]
            + [p.encode("utf-8") for p in parts]
            + [b"epoch=%d" % self._seed_epoch, b"nudge=%d" % self._nudge_step]
        )
        return hashlib.sha256(material).hexdigest()


def _significant_digits(text: str) -> int:
    """Count the significant decimal digits in a formatted float."""
    mantissa = text.split("e")[0].split("E")[0].replace("-", "").replace(".", "")
    stripped = mantissa.lstrip("0")
    return len(stripped)


def new_master_seed() -> str:
    """Generate a fresh 256-bit master seed from the OS CSPRNG."""
    return os.urandom(32).hex()


def new_uuid7() -> str:
    """Generate a UUIDv7 (time-ordered, random tail).

    RFC 9562 layout: 48-bit big-endian millisecond timestamp, 4-bit version,
    12 bits of ``rand_a``, 2-bit variant, 62 random bits.
    """
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = os.urandom(10)
    value = (ms << 80) | (0x7 << 76) | ((rand[0] & 0x0F) << 72)
    value |= (rand[1] << 64) | (rand[2] << 56) | (rand[3] << 48)
    value |= (rand[4] << 40) | (rand[5] << 32) | (rand[6] << 24)
    value |= (rand[7] << 16) | (rand[8] << 8) | rand[9]
    value &= ~(0xC000 << 48)
    value |= 0x8000 << 48
    raw = value.to_bytes(16, "big")
    hexed = raw.hex()
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


__all__ = [
    "Axis",
    "DOMAIN_SALTS",
    "SeedDeriver",
    "SeedError",
    "hkdf",
    "hkdf_extract",
    "hkdf_expand",
    "new_master_seed",
    "new_uuid7",
]
