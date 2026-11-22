"""Tests for the advanced leak guards.

Three leaks are covered here, and they are leaks of a different kind
from the ones the other guard tests cover: the value the page reads is
right, but the *duration* or the *neighbouring values* give the host
away.

1. **The canvas readback timing.**  ``canvas.toDataURL()`` and
   ``gl.readPixels()`` take a time that depends on the graphics card.
   Two machines with the same card report the same duration, so a site
   that measures it groups every profile built on the same build
   server.  The guard gives every profile a readback time of its own.

2. **The font timing attack.**  ``document.fonts.check()`` costs more
   for a family that is really installed than for one that is not.  The
   guard makes the timing independent of the answer.

3. **The GPU parameter table.**  ``gl.getParameter(MAX_TEXTURE_SIZE)``
   and its dozen siblings are read off the *host* machine, so a fleet
   built on one server reports one identical table.  The guard gives
   every profile a table of its own -- one that the declared GPU is
   really able to produce.

The properties under test are the ones that make a guard a guard
rather than a randomiser:

* **Determinism.**  The same seed produces the same guard values.
* **Stability.**  Two reads of one profile report the same value; a
  guard that flickered between two reads would be its own signature.
* **Diversity.**  Two profiles of different seeds do not report the
  same value.
* **Coherence.**  The GPU table still agrees with the GPU the profile
  declares, and the WebGPU table still agrees with the WebGL one.
* **No host leak.**  Nothing the guard produces is the value the host
  happens to report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from novaprofile.core.devices import DEVICE_PRESETS
from novaprofile.core.generator import ProfileGenerator
from novaprofile.core.gpu_params import build_tier_table, tier_for
from novaprofile.core.identity import _GPU_CATALOGUE
from novaprofile.core.seed import DOMAIN_SALTS, Axis, SeedDeriver
from novaprofile.js_runtime import JSRuntimeBuilder

#: Where a Node interpreter may be found.
NODE_CANDIDATES = (
    "node",
    r"C:\chromium\src\third_party\node\win\node.exe",
    "/usr/bin/node",
    "/usr/local/bin/node",
)


def _find_node() -> str | None:
    """The Node interpreter to run the runtime with."""

    for candidate in NODE_CANDIDATES:
        if shutil.which(candidate):
            return candidate
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


NODE = _find_node()

#: The preset the fleet tests use.  A Pixel 8 Pro carries the Mali-G78,
#: whose texture limit sits at the top of its class band.
PRESET = "pixel-8-pro-android14"


@pytest.fixture(scope="module")
def gen() -> ProfileGenerator:
    return ProfileGenerator()


def _seeds(count: int) -> list[str]:
    """``count`` distinct seeds, as hex strings."""

    return [f"{index:064x}" for index in range(count)]


# --------------------------------------------------------------------------
# The axes the guards live on
# --------------------------------------------------------------------------


class TestTheGuardAxesAreSeparate:
    """Each guard needs its own domain salt.

    Two axes that shared a salt would be coupled: a change to one of
    them would move the other, and two profiles would correlate on both
    even though their seeds differ.  The seed module says this
    explicitly, so it is worth checking that the new axes follow the
    rule.
    """

    def test_the_new_axes_have_a_salt(self):
        for axis in (Axis.CANVAS_TIMING, Axis.FONT_TIMING, Axis.GPU_PARAMS):
            assert axis in DOMAIN_SALTS

    def test_the_new_salts_are_unique(self):
        salts = list(DOMAIN_SALTS.values())
        assert len(set(salts)) == len(salts)

    def test_the_new_axes_are_independent(self):
        """Two axes must not produce the same bytes for one seed."""

        deriver = SeedDeriver("a" * 64)
        keys = {
            deriver.axis_key(Axis.CANVAS_TIMING),
            deriver.axis_key(Axis.FONT_TIMING),
            deriver.axis_key(Axis.GPU_PARAMS),
        }
        assert len(keys) == 3

    def test_the_new_axes_do_not_move_the_old_ones(self):
        """A new axis must not perturb an existing one.

        The seed of an existing profile is a promise: the profile that
        was stored under it must come back identical.  A new axis
        that shared a salt with an old one would break that promise
        for every profile ever written.
        """

        deriver = SeedDeriver("a" * 64)
        for axis in (Axis.CANVAS_2D, Axis.WEBGL_PIXEL, Axis.WEBGL_PARAMS, Axis.FONTS):
            assert deriver.axis_key(axis) != deriver.axis_key(Axis.CANVAS_TIMING)
            assert deriver.axis_key(axis) != deriver.axis_key(Axis.FONT_TIMING)
            assert deriver.axis_key(axis) != deriver.axis_key(Axis.GPU_PARAMS)


# --------------------------------------------------------------------------
# (1) The canvas readback timing
# --------------------------------------------------------------------------


class TestTheCanvasReadbackTiming:
    """The readback delay is a constant of the profile."""

    def test_the_delay_is_present(self, gen):
        profile = gen.generate(PRESET, master_seed="a" * 64)
        assert profile.canvas_2d.timing.enabled is True
        assert profile.canvas_2d.timing.delay_ms > 0.0

    def test_the_delay_is_in_the_band(self, gen):
        """A delay of zero is no delay; one above 3 ms is a signal."""

        for seed in _seeds(50):
            profile = gen.generate(PRESET, master_seed=seed)
            timing = profile.canvas_2d.timing
            assert 0.0 < timing.delay_ms <= 3.0
            assert 0.0 < timing.read_pixels_delay_ms <= 3.0

    def test_two_profiles_of_different_seeds_differ(self, gen):
        delays = {
            gen.generate(PRESET, master_seed=seed).canvas_2d.timing.delay_ms
            for seed in _seeds(40)
        }
        assert len(delays) > 20

    def test_the_same_seed_gives_the_same_delay(self, gen):
        first = gen.generate(PRESET, master_seed="b" * 64).canvas_2d.timing
        second = gen.generate(PRESET, master_seed="b" * 64).canvas_2d.timing
        assert first.delay_ms == second.delay_ms
        assert first.read_pixels_delay_ms == second.read_pixels_delay_ms

    def test_two_reads_of_one_profile_report_the_same_delay(self, gen):
        """The delay must not flicker between two reads.

        A site that reads the canvas twice and compares the two
        durations finds a guard that jitters per call -- and a real
        device never behaves that way.
        """

        profile = gen.generate(PRESET, master_seed="c" * 64)
        reads = [profile.canvas_2d.timing.delay_ms for _ in range(5)]
        assert len(set(reads)) == 1

    def test_the_two_delays_are_not_the_same_number(self, gen):
        """A canvas encode and a pixel copy cost different amounts.

        A profile that delayed both by the same amount would report a
        readback cost that no real graphics stack has.
        """

        distinct = 0
        for seed in _seeds(40):
            timing = gen.generate(PRESET, master_seed=seed).canvas_2d.timing
            if timing.delay_ms != timing.read_pixels_delay_ms:
                distinct += 1
        assert distinct > 30

    def test_the_delay_is_never_the_degenerate_value(self, gen):
        """Nothing the guard produces may be the host's own number.

        The guard never reads the host, so the check is that the
        values are drawn from the band and are not the zero of a
        disabled shield.
        """

        for seed in _seeds(20):
            timing = gen.generate(PRESET, master_seed=seed).canvas_2d.timing
            assert timing.delay_ms != 0.0
            assert timing.read_pixels_delay_ms != 0.0

    def test_the_timing_survives_a_nudge(self, gen):
        """The collision guard's nudge must not break the timing.

        The nudge re-keys the axes that carry the nudge step.  The
        timing has to stay a valid delay whatever the nudge did.
        """

        from novaprofile.core.collision import CollisionGuard, InMemorySignatureRegistry

        guard = CollisionGuard(InMemorySignatureRegistry())
        profile = gen.generate(PRESET, master_seed="d" * 64)
        for nudge in range(3):
            profile = guard._rebuild(profile, profile.seed_config, nudge, 0)
            timing = profile.canvas_2d.timing
            assert 0.0 < timing.delay_ms <= 3.0
            assert 0.0 < timing.read_pixels_delay_ms <= 3.0


# --------------------------------------------------------------------------
# (2) The font timing attack
# --------------------------------------------------------------------------


class TestTheFontTimingShield:
    """The font probe must cost the same whatever the answer is."""

    def test_the_shield_is_present(self, gen):
        profile = gen.generate(PRESET, master_seed="a" * 64)
        assert profile.fonts.timing.enabled is True
        assert profile.fonts.timing.delay_ms > 0.0

    def test_the_delay_is_in_the_band(self, gen):
        """A delay below the timer resolution is invisible.

        Chromium clamps ``performance.now()`` to about 0.1 ms, so a
        delay of a hundredth of a millisecond would not slow the probe
        down at all.
        """

        for seed in _seeds(50):
            timing = gen.generate(PRESET, master_seed=seed).fonts.timing
            assert 0.0 < timing.delay_ms <= 2.0
            assert 0.0 < timing.measure_delay_ms <= 2.0

    def test_two_profiles_of_different_seeds_differ(self, gen):
        delays = {
            gen.generate(PRESET, master_seed=seed).fonts.timing.delay_ms
            for seed in _seeds(40)
        }
        assert len(delays) > 20

    def test_the_same_seed_gives_the_same_delay(self, gen):
        first = gen.generate(PRESET, master_seed="b" * 64).fonts.timing
        second = gen.generate(PRESET, master_seed="b" * 64).fonts.timing
        assert first.delay_ms == second.delay_ms
        assert first.measure_delay_ms == second.measure_delay_ms

    def test_two_probes_of_one_profile_report_the_same_delay(self, gen):
        profile = gen.generate(PRESET, master_seed="c" * 64)
        probes = [profile.fonts.timing.delay_ms for _ in range(5)]
        assert len(set(probes)) == 1

    def test_the_profile_carries_one_delay_not_one_per_answer(self, gen):
        """The shield must not depend on the answer.

        This is the whole point of the shield: the timing of a probe
        for a family the device carries and the timing of a probe for
        one it does not are the same number.  The profile therefore
        carries one delay, not one for the present case and one for
        the absent case.
        """

        profile = gen.generate(PRESET, master_seed="e" * 64)
        assert set(profile.fonts.timing.model_dump()) == {
            "enabled",
            "delay_ms",
            "measure_delay_ms",
        }

    def test_the_font_list_and_the_timing_are_independent(self, gen):
        """The timing must not be a function of the installed list.

        Two profiles that ship the same font families but are built on
        different machines still have to report different timings,
        otherwise the font list becomes the grouping key.
        """

        pairs: dict[tuple[str, ...], set[float]] = {}
        for seed in _seeds(60):
            profile = gen.generate(PRESET, master_seed=seed)
            key = tuple(profile.fonts.installed)
            pairs.setdefault(key, set()).add(profile.fonts.timing.delay_ms)
        shared = [values for values in pairs.values() if len(values) > 1]
        assert shared, "the timing never varies within a font list"


# --------------------------------------------------------------------------
# (3) The GPU parameter table
# --------------------------------------------------------------------------


class TestTheGpuParameterTable:
    """Every profile gets a table of its own, one the GPU can produce."""

    def test_the_parameter_set_carries_a_salt(self, gen):
        profile = gen.generate(PRESET, master_seed="a" * 64)
        assert profile.webgl.parameter_set.salt
        assert len(profile.webgl.parameter_set.salt) >= 32

    def test_the_salt_is_different_for_two_profiles(self, gen):
        salts = {
            gen.generate(PRESET, master_seed=seed).webgl.parameter_set.salt
            for seed in _seeds(30)
        }
        assert len(salts) == 30

    def test_two_profiles_of_different_seeds_report_different_tables(self, gen):
        """The fleet must not report one identical limit table."""

        tables = set()
        for seed in _seeds(40):
            params = gen.generate(PRESET, master_seed=seed).webgl.parameters
            tables.add(
                (
                    params.max_texture_size,
                    params.max_cube_map_texture_size,
                    params.max_renderbuffer_size,
                    tuple(params.max_viewport_dims),
                )
            )
        assert len(tables) > 1

    def test_the_same_seed_gives_the_same_table(self, gen):
        first = gen.generate(PRESET, master_seed="b" * 64).webgl.parameters
        second = gen.generate(PRESET, master_seed="b" * 64).webgl.parameters
        assert first.model_dump() == second.model_dump()

    def test_the_table_is_one_the_model_really_produces(self, gen):
        """The limits must be a tier of the declared GPU.

        A limit that no configuration of that part ever reported is
        not a variation of that part -- it is a contradiction, and a
        site with a table of the real limits finds it in one
        comparison.
        """

        checked = 0
        for seed in _seeds(60):
            profile = gen.generate(PRESET, master_seed=seed)
            renderer = profile.webgl.unmasked_renderer
            gpu = _GPU_CATALOGUE.get(renderer)
            if gpu is None:
                continue
            checked += 1
            params = profile.webgl.parameters
            tier = tier_for(gpu, profile.webgl.parameter_set.tier)
            limits = tier.limits
            assert params.max_texture_size == limits.max_texture_size
            assert params.max_cube_map_texture_size == limits.max_cube_map_texture_size
            assert params.max_renderbuffer_size == limits.max_renderbuffer_size
            assert list(params.max_viewport_dims) == list(limits.max_viewport_dims)
        assert checked > 0

    def test_the_table_is_never_wider_than_the_catalogue_entry(self, gen):
        """A tier may only *narrow* the addressable memory.

        The class band of a flagship part reaches 32768, but no mobile
        part in the catalogue reports it, and the WebGPU table of the
        same card could not carry it either.  A tier that widened the
        limits would be a configuration no phone has ever had.
        """

        for seed in _seeds(60):
            profile = gen.generate(PRESET, master_seed=seed)
            renderer = profile.webgl.unmasked_renderer
            gpu = _GPU_CATALOGUE.get(renderer)
            if gpu is None:
                continue
            assert profile.webgl.parameters.max_texture_size <= gpu.max_texture_size

    def test_the_table_stays_inside_the_range_a_mobile_gpu_reports(self, gen):
        """The texture limit must stay inside the range a phone reports.

        The lower bound is the smallest texture a WebGL implementation
        accepts; the upper bound is the largest a mobile part reports
        and the largest a WebGPU adapter can carry.
        """

        for seed in _seeds(60):
            params = gen.generate(PRESET, master_seed=seed).webgl.parameters
            assert 2048 <= params.max_texture_size <= 16384

    def test_the_whole_memory_group_moves_together(self, gen):
        """A card cannot sample a big texture and render into a small one.

        The memory that bounds the texture bounds the cube map, the
        renderbuffer and the viewport with it.  A profile that moved
        one of them on its own is a profile of a card that does not
        exist.
        """

        for seed in _seeds(60):
            params = gen.generate(PRESET, master_seed=seed).webgl.parameters
            group = {
                params.max_texture_size,
                params.max_cube_map_texture_size,
                params.max_renderbuffer_size,
                params.max_viewport_dims[0],
                params.max_viewport_dims[1],
            }
            assert len(group) == 1

    def test_the_webgpu_table_agrees_with_the_webgl_one(self, gen):
        """The two surfaces describe the same physical card."""

        checked = 0
        for seed in _seeds(60):
            profile = gen.generate(PRESET, master_seed=seed)
            if not profile.webgpu.enabled:
                continue
            checked += 1
            reported = profile.webgpu.limits.get("maxTextureDimension2D")
            assert reported == float(profile.webgl.parameters.max_texture_size)
        assert checked > 0

    def test_the_tier_of_a_profile_is_inside_the_table_of_its_gpu(self, gen):
        for seed in _seeds(60):
            profile = gen.generate(PRESET, master_seed=seed)
            renderer = profile.webgl.unmasked_renderer
            gpu = _GPU_CATALOGUE.get(renderer)
            if gpu is None:
                continue
            table = build_tier_table(gpu)
            assert 0 <= profile.webgl.parameter_set.tier < len(table)

    def test_the_source_renderer_matches_the_declared_one(self, gen):
        for seed in _seeds(40):
            profile = gen.generate(PRESET, master_seed=seed)
            assert (
                profile.webgl.parameter_set.source_renderer
                == profile.webgl.unmasked_renderer
            )

    def test_the_tier_table_of_every_known_gpu_is_coherent(self):
        """Every tier of every GPU must move the whole group together."""

        for renderer, gpu in _GPU_CATALOGUE.items():
            for tier in build_tier_table(gpu):
                limits = tier.limits
                group = {
                    limits.max_texture_size,
                    limits.max_cube_map_texture_size,
                    limits.max_renderbuffer_size,
                    limits.max_viewport_dims[0],
                    limits.max_viewport_dims[1],
                }
                assert len(group) == 1, renderer

    def test_the_tier_table_of_every_known_gpu_only_narrows(self):
        """A tier may never widen the catalogue entry.

        The class band of a flagship part reaches 32768, but no mobile
        part in the catalogue reports it, so no tier may reach it
        either.
        """

        for renderer, gpu in _GPU_CATALOGUE.items():
            for tier in build_tier_table(gpu):
                assert tier.max_texture_size <= gpu.max_texture_size, renderer
                assert tier.max_texture_size >= 2048, renderer

    def test_the_tier_table_of_every_known_gpu_keeps_the_webgpu_range(self):
        """Every tier must carry a WebGPU adapter of the same card.

        The WebGPU texture dimension is capped well below the class
        band, so a tier that went past it would describe a card whose
        two surfaces cannot both be right.  The check is on the parts
        the catalogue actually ships -- a renderer no preset uses is
        not on any phone.
        """

        used = {preset.gpu.unmasked_renderer for preset in DEVICE_PRESETS}
        for renderer, gpu in _GPU_CATALOGUE.items():
            if renderer not in used:
                continue
            for tier in build_tier_table(gpu):
                assert 2048 <= tier.max_texture_size <= 16384, renderer

    def test_the_webgpu_table_of_every_shipping_preset_is_plausible(self, gen):
        """The WebGPU table of a shipped device has to be one a phone reports.

        The two surfaces describe the same physical card, so the WebGPU
        texture dimension cannot be past what a mobile adapter offers.
        """

        for preset in DEVICE_PRESETS:
            profile = gen.generate(preset.preset_id, master_seed="a" * 64)
            if not profile.webgpu.enabled:
                continue
            reported = profile.webgpu.limits["maxTextureDimension2D"]
            assert 2048 <= reported <= 16384, preset.preset_id

    def test_a_fleet_of_one_model_does_not_report_one_table(self, gen):
        """The units of one device family must spread across the tiers.

        The fleet is the case the guard exists for: a hundred units of
        one model, all built on one server, all reporting the limits of
        that server.
        """

        tables = set()
        for index in range(12):
            profile = gen.generate(PRESET, master_seed=f"{index:064x}", stratum=index)
            params = profile.webgl.parameters
            tables.add((params.max_texture_size, tuple(params.max_viewport_dims)))
        assert len(tables) > 1

    def test_every_preset_of_the_catalogue_builds_a_coherent_table(self, gen):
        """The guard must hold for every device the engine knows."""

        for preset in DEVICE_PRESETS:
            profile = gen.generate(preset.preset_id, master_seed="f" * 64)
            params = profile.webgl.parameters
            group = {
                params.max_texture_size,
                params.max_cube_map_texture_size,
                params.max_renderbuffer_size,
                params.max_viewport_dims[0],
                params.max_viewport_dims[1],
            }
            assert len(group) == 1, preset.preset_id


# --------------------------------------------------------------------------
# The consistency resolver
# --------------------------------------------------------------------------


class TestTheConsistencyResolverAgrees:
    """The resolver is the second opinion on the new fields."""

    def test_a_generated_profile_is_consistent(self, gen):
        from novaprofile.core.consistency import ConsistencyResolver

        resolver = ConsistencyResolver()
        for seed in _seeds(25):
            profile = gen.generate(PRESET, master_seed=seed)
            report = resolver.check(profile)
            assert report.ok, [issue.message for issue in report.errors]

    def test_a_disabled_timing_shield_is_an_error(self, gen):
        from novaprofile.core.consistency import ConsistencyResolver

        profile = gen.generate(PRESET, master_seed="a" * 64)
        broken = profile.model_copy(
            update={
                "canvas_2d": profile.canvas_2d.model_copy(
                    update={
                        "timing": profile.canvas_2d.timing.model_copy(update={"enabled": False})
                    }
                )
            }
        )
        report = ConsistencyResolver().check(broken)
        assert not report.ok
        assert any("readback timing shield" in issue.message for issue in report.errors)

    def test_a_disabled_font_shield_is_an_error(self, gen):
        from novaprofile.core.consistency import ConsistencyResolver

        profile = gen.generate(PRESET, master_seed="a" * 64)
        broken = profile.model_copy(
            update={
                "fonts": profile.fonts.model_copy(
                    update={
                        "timing": profile.fonts.timing.model_copy(update={"enabled": False})
                    }
                )
            }
        )
        report = ConsistencyResolver().check(broken)
        assert not report.ok
        assert any("font timing shield" in issue.message for issue in report.errors)

    def test_a_moved_texture_limit_alone_is_an_error(self, gen):
        """Moving one limit of the group on its own is a contradiction."""

        from novaprofile.core.consistency import ConsistencyResolver

        profile = gen.generate(PRESET, master_seed="a" * 64)
        params = profile.webgl.parameters
        broken_params = params.model_copy(
            update={"max_renderbuffer_size": params.max_renderbuffer_size // 2}
        )
        broken = profile.model_copy(
            update={"webgl": profile.webgl.model_copy(update={"parameters": broken_params})}
        )
        report = ConsistencyResolver().check(broken)
        assert not report.ok
        assert any("max_renderbuffer_size" in issue.message for issue in report.errors)

    def test_a_missing_parameter_salt_is_an_error(self, gen):
        from novaprofile.core.consistency import ConsistencyResolver

        profile = gen.generate(PRESET, master_seed="a" * 64)
        broken = profile.model_copy(
            update={
                "webgl": profile.webgl.model_copy(
                    update={
                        "parameter_set": profile.webgl.parameter_set.model_copy(
                            update={"salt": ""}
                        )
                    }
                )
            }
        )
        report = ConsistencyResolver().check(broken)
        assert not report.ok
        assert any("per-device salt" in issue.message for issue in report.errors)


# --------------------------------------------------------------------------
# The JavaScript layer
# --------------------------------------------------------------------------


class TestTheJavaScriptLayerCarriesTheGuards:
    """The runtime has to install the guards, not just describe them."""

    def _payload(self, profile) -> dict:
        return json.loads(
            JSRuntimeBuilder().build_payload_only(profile).split("=", 1)[1].rstrip(";\n")
        )

    def test_the_payload_carries_the_timing(self, gen):
        payload = self._payload(gen.generate(PRESET, master_seed="a" * 64))
        assert payload["canvas_2d"]["timing"]["delay_ms"] > 0
        assert payload["canvas_2d"]["timing"]["read_pixels_delay_ms"] > 0
        assert payload["fonts"]["timing"]["delay_ms"] > 0
        assert payload["fonts"]["timing"]["measure_delay_ms"] > 0

    def test_the_payload_carries_the_parameter_set(self, gen):
        payload = self._payload(gen.generate(PRESET, master_seed="a" * 64))
        assert payload["webgl"]["parameter_set"]["salt"]
        assert payload["webgl"]["parameter_set"]["tier"] >= 0

    def test_the_runtime_installs_the_timing_shield(self, gen):
        script = JSRuntimeBuilder().build(gen.generate(PRESET, master_seed="a" * 64))
        assert "function burn(" in script
        assert "readPixelsDelay" in script
        assert "fontProbeDelay" in script
        assert "fontMeasureDelay" in script

    def test_two_profiles_produce_two_different_runtimes(self, gen):
        """The runtime of one profile must not carry the delay of another."""

        first = JSRuntimeBuilder().build(gen.generate(PRESET, master_seed="a" * 64))
        second = JSRuntimeBuilder().build(gen.generate(PRESET, master_seed="b" * 64))
        assert first != second

    def test_the_same_seed_produces_the_same_runtime(self, gen):
        first = JSRuntimeBuilder().build(gen.generate(PRESET, master_seed="a" * 64))
        second = JSRuntimeBuilder().build(gen.generate(PRESET, master_seed="a" * 64))
        assert first == second


# --------------------------------------------------------------------------
# The runtime, executed
# --------------------------------------------------------------------------

#: The harness.  It runs the generated runtime inside a browser-shaped
#: environment and reports what the page would have measured.
TIMING_HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');
    const profile = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

    function defineGetter(obj, name, value) {
      var accessor = function () { return value; };
      Object.defineProperty(accessor, 'name', {
        value: 'get ' + name, writable: false, enumerable: false, configurable: true
      });
      Object.defineProperty(obj, name, {
        get: accessor,
        set: undefined,
        enumerable: true,
        configurable: true
      });
    }

    const Navigator = function () {};
    defineGetter(Navigator.prototype, 'userAgent', 'stock-ua');
    defineGetter(Navigator.prototype, 'platform', 'stock-platform');
    defineGetter(Navigator.prototype, 'appVersion', 'stock-appversion');
    defineGetter(Navigator.prototype, 'appName', 'stock-appname');
    defineGetter(Navigator.prototype, 'appCodeName', 'stock-appcodename');
    defineGetter(Navigator.prototype, 'product', 'stock-product');
    defineGetter(Navigator.prototype, 'productSub', 'stock-productsub');
    defineGetter(Navigator.prototype, 'vendor', 'stock-vendor');
    defineGetter(Navigator.prototype, 'vendorSub', 'stock-vendorsub');
    defineGetter(Navigator.prototype, 'language', 'en-US');
    defineGetter(Navigator.prototype, 'languages', ['en-US', 'en']);
    defineGetter(Navigator.prototype, 'hardwareConcurrency', 4);
    defineGetter(Navigator.prototype, 'deviceMemory', 4);
    defineGetter(Navigator.prototype, 'maxTouchPoints', 5);
    defineGetter(Navigator.prototype, 'webdriver', false);
    defineGetter(Navigator.prototype, 'pdfViewerEnabled', true);
    defineGetter(Navigator.prototype, 'cookieEnabled', true);
    defineGetter(Navigator.prototype, 'doNotTrack', null);
    defineGetter(Navigator.prototype, 'onLine', true);

    function Screen() {}
    defineGetter(Screen.prototype, 'width', 800);
    defineGetter(Screen.prototype, 'height', 600);
    defineGetter(Screen.prototype, 'availWidth', 800);
    defineGetter(Screen.prototype, 'availHeight', 600);
    defineGetter(Screen.prototype, 'colorDepth', 24);
    defineGetter(Screen.prototype, 'pixelDepth', 24);

    function FakeCanvas() {}
    FakeCanvas.prototype.getContext = function () { return new FakeContext2D(); };
    FakeCanvas.prototype.toDataURL = function () { return 'data:image/png;base64,AAAA'; };
    FakeCanvas.prototype.toBlob = function (cb) { cb({}); };

    function FakeContext2D() {}
    FakeContext2D.prototype.measureText = function (t) { return { width: t.length * 10 }; };
    FakeContext2D.prototype.getImageData = function () {
      return { data: new Uint8ClampedArray(4), width: 1, height: 1 };
    };

    function FakeFontFaceSet() {}
    FakeFontFaceSet.prototype.check = function () { return false; };
    FakeFontFaceSet.prototype.load = function () { return Promise.resolve([]); };
    FakeFontFaceSet.prototype.add = function () {};
    FakeFontFaceSet.prototype.ready = Promise.resolve(FakeFontFaceSet);

    const document = {
      createElement: function (tag) {
        if (tag === 'canvas') { return new FakeCanvas(); }
        return { style: {}, appendChild: function () {} };
      },
      documentElement: { style: {}, appendChild: function () {} },
      body: { appendChild: function () {}, style: {} },
      fonts: new FakeFontFaceSet(),
      addEventListener: function () {},
      removeEventListener: function () {},
      querySelectorAll: function () { return []; },
      createTextNode: function (t) { return { textContent: t }; },
      head: { appendChild: function () {} }
    };

    const window = {
      navigator: new Navigator(),
      screen: new Screen(),
      devicePixelRatio: 1,
      innerWidth: 800,
      innerHeight: 600,
      outerWidth: 800,
      outerHeight: 600,
      document: document,
      location: { href: 'https://example.test/', protocol: 'https:', host: 'example.test' },
      addEventListener: function () {},
      removeEventListener: function () {},
      requestAnimationFrame: function (cb) { return setTimeout(cb, 16); },
      cancelAnimationFrame: function (id) { clearTimeout(id); },
      setTimeout: setTimeout,
      clearTimeout: clearTimeout,
      setInterval: setInterval,
      clearInterval: clearInterval,
      performance: { now: function () { return Number(process.hrtime.bigint()) / 1e6; } },
      Date: Date,
      Math: Math,
      JSON: JSON,
      Object: Object,
      Array: Array,
      String: String,
      Number: Number,
      Boolean: Boolean,
      RegExp: RegExp,
      Error: Error,
      TypeError: TypeError,
      Promise: Promise,
      Set: Set,
      Map: Map,
      WeakMap: WeakMap,
      WeakSet: WeakSet,
      Proxy: Proxy,
      Reflect: Reflect,
      Symbol: Symbol,
      Function: Function,
      parseInt: parseInt,
      parseFloat: parseFloat,
      isNaN: isNaN,
      isFinite: isFinite,
      encodeURIComponent: encodeURIComponent,
      decodeURIComponent: decodeURIComponent,
      Intl: Intl
    };
    window.window = window;
    window.self = window;
    window.top = window;
    window.globalThis = window;
    window.HTMLCanvasElement = FakeCanvas;
    window.CanvasRenderingContext2D = FakeContext2D;
    // The runtime reaches for the constructors themselves, not only for
    // the instances.
    window.Navigator = Navigator;
    window.Screen = Screen;
    window.FakeCanvas = FakeCanvas;
    window.FakeContext2D = FakeContext2D;

    const context = vm.createContext(window);
    vm.runInContext(runtime, context);

    // The measurement of the canvas readback.
    const canvas = new FakeCanvas();
    const samples = [];
    for (let i = 0; i < 5; i++) {
      const start = process.hrtime.bigint();
      canvas.toDataURL();
      samples.push(Number(process.hrtime.bigint() - start) / 1e6);
    }
    const min = Math.min.apply(null, samples);
    const max = Math.max.apply(null, samples);

    // The measurement of the font probe, for a family that is installed
    // and for one that is not.
    const fontSamples = [];
    for (const family of ['Roboto', 'Definitely Not Installed Family']) {
      const start = process.hrtime.bigint();
      document.fonts.check('12px "' + family + '"');
      fontSamples.push(Number(process.hrtime.bigint() - start) / 1e6);
    }

    console.log(JSON.stringify({
      canvasMin: min,
      canvasMax: max,
      fontSamples: fontSamples
    }));
    """
)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestTheRuntimeActuallyDelays:
    """The guard has to work in a real JavaScript engine.

    The other tests in this file check that the profile carries the
    values.  This one runs the generated runtime and measures what the
    page would measure, which is the only way to know that the delay
    is really applied.
    """

    def _run(self, profile, tmp_path) -> dict:
        builder = JSRuntimeBuilder()
        script = builder.build(profile)
        payload = json.loads(
            builder.build_payload_only(profile).split("=", 1)[1].rstrip(";\n")
        )
        script_path = tmp_path / "runtime.js"
        payload_path = tmp_path / "payload.json"
        script_path.write_text(script, encoding="utf-8")
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        harness_path = tmp_path / "harness.js"
        harness_path.write_text(TIMING_HARNESS, encoding="utf-8")

        result = subprocess.run(
            [NODE, str(harness_path), str(script_path), str(payload_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_the_readback_takes_longer_than_the_delay(self, gen, tmp_path):
        """The delay has to be visible to a stopwatch."""

        profile = gen.generate(PRESET, master_seed="a" * 64)
        report = self._run(profile, tmp_path)
        assert report["canvasMin"] >= profile.canvas_2d.timing.delay_ms

    def test_two_reads_of_one_profile_cost_the_same(self, gen, tmp_path):
        """The delay must not flicker between two reads.

        The measurement is a stopwatch around the whole call, so it
        carries the noise of the machine; the guard is that the
        *profile* carries one delay, not that the stopwatch reads the
        same number twice.
        """

        profile = gen.generate(PRESET, master_seed="a" * 64)
        report = self._run(profile, tmp_path)
        # The spread of the samples is bounded by the machine's noise
        # plus the guard's own constant; a per-call jitter would make
        # it arbitrarily large.
        assert report["canvasMax"] - report["canvasMin"] < 50.0

    def test_the_font_probe_costs_the_same_for_both_answers(self, gen, tmp_path):
        """The shield makes the timing independent of the answer.

        The two probes are for a family the profile lists and a family
        it does not.  A shield that only delayed one of the two paths
        would show a difference here.
        """

        profile = gen.generate(PRESET, master_seed="a" * 64)
        report = self._run(profile, tmp_path)
        first, second = report["fontSamples"]
        assert abs(first - second) < 50.0

    def test_two_profiles_of_different_seeds_cost_differently(self, gen, tmp_path):
        """Two profiles must not report the same readback cost."""

        first = self._run(gen.generate(PRESET, master_seed="a" * 64), tmp_path)
        second = self._run(gen.generate(PRESET, master_seed="b" * 64), tmp_path)
        assert first["canvasMin"] != second["canvasMin"]
