"""The speech synthesis voice list has to follow the device, not a constant.

``speechSynthesis.getVoices()`` is a fingerprinting surface.  It tells a
site which languages the handset can *speak*, which locale the handset
is *set* to, and how much room the device has for the optional TTS
language packs.  A site that reads the list twice -- once for the
account it registered yesterday and once for the visit it is serving
now -- compares all three.

The rules the tests hold are taken from the Chromium source, not from
what a browser "usually" does:

* ``content/public/android/java/src/org/chromium/content/browser/
  TtsPlatformImpl.java`` builds the Android voice list from the locales
  the platform TTS engine reports as available.  The voice *name* is
  ``locale.getDisplayLanguage()`` plus, when the locale names a
  country, ``" " + locale.getDisplayCountry()``; the ``lang`` is the
  locale itself.  A Vietnamese handset therefore exposes a voice named
  "Vietnamese Vietnam", and no Japanese voice at all when the Japanese
  TTS pack is not installed.

* ``content/browser/speech/speech_synthesis_impl.cc`` copies that list
  into the Blink binding.  It sets ``voice_uri`` to the same string as
  ``name``, ``is_local_service`` to ``!voice.remote`` and ``is_default``
  to ``(index == 0)`` -- the first voice of the list is the default one.

* ``content/browser/speech/tts_win.cc`` shows the desktop rule for
  comparison: the voice name is the SAPI token description ("Microsoft
  David Desktop - English (United States)") and the lang is the LCID
  resolved to a BCP-47 locale name.  This project builds Android
  profiles, so the Android rule is the one that applies.

The rules the tests hold:

* the voice list follows the locale of the device,
* the number of voices follows the device tier, so a budget handset
  carries fewer voices than a flagship,
* a device carries no voice of a language it does not have installed,
* ``voiceURI`` is stable across two generations with the same seed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from novaprofile.core.generator import (
    VOICE_DISPLAY_NAMES,
    VOICE_PACK_POOL_BY_REGION,
    VOICE_REGION_BY_COUNTRY,
    VOICE_TIER_BUDGET,
    VOICE_TIER_FLAGSHIP,
    VOICE_TIER_MID,
    ProfileGenerator,
    _geo_target,
    voices_for_device,
)
from novaprofile.core.geo_catalog import GEO_CATALOGUE
from novaprofile.js_runtime import JSRuntimeBuilder

#: The locales the tests exercise.  They are all in the geo catalogue, so
#: a profile can really be anchored to them.
LOCALES = ("vi-VN", "ja-JP", "ko-KR", "en-US", "pt-BR", "fr-FR", "th-TH", "ar-EG")

#: The presets the tests use.  The tier of a preset is *not* fixed --
#: the generator draws the RAM the model was sold with -- so the tier
#: rules are checked through :func:`voices_for_device` instead.
TIER_PRESETS = (
    "samsung-galaxy-a14-android13",
    "galaxy-a54-android13",
    "pixel-8-pro-android14",
)


def _geo_for(locale: str):
    """Return the geo target the catalogue holds for ``locale``."""

    for entry in GEO_CATALOGUE:
        if entry[0] == locale:
            return _geo_target(*entry)
    raise AssertionError(f"{locale} is not in the geo catalogue")


def _generate(
    preset_id: str,
    locale: str,
    master_seed: str = "a" * 64,
):
    """Generate one profile anchored to ``locale``."""

    generator = ProfileGenerator()
    return generator.generate(
        preset_id,
        master_seed=master_seed,
        geo=_geo_for(locale),
    )


def _voices(config) -> list:
    return list(config.battery_speech_gamepad.speech_voices)


class TestVoiceFollowsTheLocale:
    """The voice list follows the locale of the device."""

    @pytest.mark.parametrize("locale", LOCALES)
    @pytest.mark.parametrize("preset_id", TIER_PRESETS)
    def test_the_device_voice_is_present(self, locale: str, preset_id: str) -> None:
        """A device always has a voice for its own system language."""

        voices = _voices(_generate(preset_id, locale))

        assert locale in [v.lang for v in voices]

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_first_voice_is_the_device_voice(self, locale: str) -> None:
        """The default voice is the one of the system language.

        Chromium marks the *first* voice of the list as the default one
        (``is_default = (index == 0)``), and the engine puts the system
        language first.
        """

        voices = _voices(_generate("pixel-8-pro-android14", locale))

        assert voices[0].lang == locale
        assert voices[0].default is True

    @pytest.mark.parametrize("locale", LOCALES)
    def test_exactly_one_voice_is_the_default(self, locale: str) -> None:
        voices = _voices(_generate("pixel-8-pro-android14", locale))

        assert sum(1 for v in voices if v.default) == 1

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_voice_name_is_the_locale_display_name(self, locale: str) -> None:
        """The name is the display name of the locale.

        ``TtsPlatformImpl.java`` builds it from
        ``locale.getDisplayLanguage()`` and ``locale.getDisplayCountry()``.
        """

        voices = _voices(_generate("pixel-8-pro-android14", locale))
        language, country = VOICE_DISPLAY_NAMES[locale]

        expected = language if not country else f"{language} {country}"
        assert voices[0].name == expected

    def test_the_voice_name_is_not_the_google_desktop_name(self) -> None:
        """The old constant was a desktop name.

        "Google US English" is neither the Android display name of a
        locale nor a name the Android TTS engine produces, and a script
        that has seen a real handset never sees it.
        """

        for locale in LOCALES:
            for voice in _voices(_generate("pixel-8-pro-android14", locale)):
                assert voice.name != "Google US English"
                assert not voice.name.startswith("Microsoft ")

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_voice_uri_is_the_voice_name(self, locale: str) -> None:
        """Chromium copies ``voice_uri`` from the voice name.

        ``speech_synthesis_impl.cc`` assigns the same string to both.
        """

        for voice in _voices(_generate("pixel-8-pro-android14", locale)):
            assert voice.voice_uri == voice.name

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_voices_are_local_services(self, locale: str) -> None:
        """An Android TTS voice runs on the device.

        ``is_local_service`` is ``!voice.remote``, and the platform
        voices are not remote.
        """

        for voice in _voices(_generate("pixel-8-pro-android14", locale)):
            assert voice.local_service is True


class TestTierDrivesTheCount:
    """The number of voices follows the device tier."""

    #: The three device tiers, as (tier, ram, year) triples.
    #:
    #: The tier follows the *physical* memory and the model year, the
    #: same rule the font catalogue uses.
    TIER_SAMPLES = (
        ("budget", 4, 2023),
        ("mid", 6, 2023),
        ("flagship", 12, 2024),
    )

    @pytest.mark.parametrize("locale", LOCALES)
    def test_a_budget_device_carries_fewer_voices_than_a_flagship(
        self, locale: str
    ) -> None:
        budget = voices_for_device(locale, country_code="VN", ram_gb=4, year=2023)
        flagship = voices_for_device(locale, country_code="VN", ram_gb=12, year=2024)

        assert len(budget) < len(flagship)

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_count_is_the_tier_budget(self, locale: str) -> None:
        """The count is the tier's budget, not a coincidence.

        The budget is the number of TTS language packs the device
        carries, and the tier decides it.
        """

        expected = {
            "budget": VOICE_TIER_BUDGET,
            "mid": VOICE_TIER_MID,
            "flagship": VOICE_TIER_FLAGSHIP,
        }

        for tier, ram, year in self.TIER_SAMPLES:
            voices = voices_for_device(locale, country_code="VN", ram_gb=ram, year=year)

            assert len(voices) == expected[tier], (
                f"the {tier} device reported {len(voices)} voices, "
                f"expected {expected[tier]}"
            )

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_count_is_never_larger_than_the_budget(self, locale: str) -> None:
        for _tier, ram, year in self.TIER_SAMPLES:
            voices = voices_for_device(locale, country_code="VN", ram_gb=ram, year=year)

            assert len(voices) <= VOICE_TIER_FLAGSHIP

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_voice_never_carries_more_than_the_tier_keeps(self, locale: str) -> None:
        """Even with a long list of downloaded locales, the tier wins."""

        voices = voices_for_device(
            locale,
            country_code="VN",
            ram_gb=4,
            year=2023,
            languages=(locale, "en", "zh", "ja", "ko", "fr", "de", "th"),
        )

        assert len(voices) <= VOICE_TIER_BUDGET

    def test_the_count_is_not_a_constant_across_the_fleet(self) -> None:
        """The voice count has to vary between two devices of a fleet."""

        counts = {
            len(voices_for_device("vi-VN", country_code="VN", ram_gb=ram, year=year))
            for _tier, ram, year in self.TIER_SAMPLES
        }

        assert len(counts) > 1

    def test_the_generated_profile_follows_the_tier_of_its_device(self) -> None:
        """The generator applies the same rule the catalogue states.

        The tier of the generated profile follows the physical RAM the
        generator drew, and the number of voices follows that tier.
        """

        generator = ProfileGenerator()

        for preset_id in ("samsung-galaxy-a14-android13", "pixel-8-pro-android14"):
            config = generator.generate(
                preset_id,
                master_seed="a" * 64,
                geo=_geo_for("vi-VN"),
            )
            voices = _voices(config)

            assert len(voices) in (
                VOICE_TIER_BUDGET,
                VOICE_TIER_MID,
                VOICE_TIER_FLAGSHIP,
            )
            assert len(voices) <= VOICE_TIER_FLAGSHIP


class TestNoVoiceOutsideTheLocale:
    """A device carries no voice of a language it does not have."""

    def test_a_vietnamese_device_has_no_japanese_voice(self) -> None:
        """The case the task calls out.

        A handset that only installed the Vietnamese and the English
        TTS packs exposes exactly those voices.  A Japanese voice would
        mean the pack is installed, and a script that reads the list
        would see a device that is not what it claims to be.
        """

        voices = _voices(_generate("samsung-galaxy-a14-android13", "vi-VN"))
        langs = [v.lang for v in voices]

        assert "ja" not in langs
        assert "ja-JP" not in langs
        assert "ko-KR" not in langs
        assert "sv-SE" not in langs

    def test_a_vietnamese_device_has_no_korean_voice(self) -> None:
        voices = _voices(_generate("samsung-galaxy-a14-android13", "vi-VN"))

        assert all(not v.lang.startswith("ko") for v in voices)

    def test_a_french_device_has_no_vietnamese_voice(self) -> None:
        voices = _voices(_generate("samsung-galaxy-a14-android13", "fr-FR"))

        assert all(not v.lang.startswith("vi") for v in voices)

    def test_a_japanese_device_has_no_arabic_voice(self) -> None:
        voices = _voices(_generate("samsung-galaxy-a14-android13", "ja-JP"))

        assert all(not v.lang.startswith("ar") for v in voices)

    @pytest.mark.parametrize("locale", LOCALES)
    def test_every_voice_belongs_to_the_region_pool(self, locale: str) -> None:
        """Every voice is a pack of the region the device lives in.

        The extra voices come from the pool of the device's region, so a
        French device never carries a Vietnamese voice and a Vietnamese
        device never carries a Swedish one.
        """

        geo = _geo_for(locale)
        region = VOICE_REGION_BY_COUNTRY.get(geo.country_code, "we")
        pool = set(VOICE_PACK_POOL_BY_REGION[region])

        for voice in _voices(_generate("pixel-8-pro-android14", locale)):
            # The bare language of the system locale is always present
            # as well, and the secondary locales of the geo target are
            # packs the user has downloaded.
            assert voice.lang in pool or voice.lang in geo.languages

    def test_the_secondary_locales_of_the_target_are_carried(self) -> None:
        """A Vietnamese handset with a Chinese secondary locale has it."""

        geo = _geo_target(
            "vi-VN",
            "Asia/Ho_Chi_Minh",
            "VN",
            "VND",
            "h23",
            ("vi", "en", "zh"),
        )
        generator = ProfileGenerator()
        config = generator.generate(
            "pixel-8-pro-android14",
            master_seed="a" * 64,
            geo=geo,
        )

        langs = [v.lang for v in _voices(config)]
        assert "zh" in langs


class TestStability:
    """The voice list is reproducible."""

    @pytest.mark.parametrize("locale", LOCALES)
    @pytest.mark.parametrize("preset_id", TIER_PRESETS)
    def test_the_same_seed_gives_the_same_voice_uri(
        self, locale: str, preset_id: str
    ) -> None:
        """``voiceURI`` is stable across two generations with one seed."""

        first = _voices(_generate(preset_id, locale))
        second = _voices(_generate(preset_id, locale))

        assert [v.voice_uri for v in first] == [v.voice_uri for v in second]

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_same_seed_gives_the_same_whole_list(self, locale: str) -> None:
        first = _voices(_generate("pixel-8-pro-android14", locale))
        second = _voices(_generate("pixel-8-pro-android14", locale))

        assert [v.model_dump() for v in first] == [v.model_dump() for v in second]

    @pytest.mark.parametrize("locale", LOCALES)
    def test_a_different_seed_gives_a_different_list(self, locale: str) -> None:
        """The seed has to reach the extra voices.

        Without this the fleet would collapse into one bucket for the
        voices, and the whole point of deriving them is to spread the
        fleet out.
        """

        first = _voices(_generate("pixel-8-pro-android14", locale, master_seed="a" * 64))
        second = _voices(_generate("pixel-8-pro-android14", locale, master_seed="b" * 64))

        assert [v.voice_uri for v in first] != [v.voice_uri for v in second]


class TestJavaScriptShape:
    """The page reads the list through the Web Speech API."""

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_payload_carries_the_voice(self, locale: str) -> None:
        config = _generate("pixel-8-pro-android14", locale)
        script = JSRuntimeBuilder().build(config)
        payload = json.loads(script.split(";\n", 1)[0].split("=", 1)[1])

        assert len(payload["speech_voices"]) == len(_voices(config))

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_payload_uses_the_api_field_names(self, locale: str) -> None:
        config = _generate("pixel-8-pro-android14", locale)
        script = JSRuntimeBuilder().build(config)
        payload = json.loads(script.split(";\n", 1)[0].split("=", 1)[1])

        for spec in payload["speech_voices"]:
            assert set(spec) == {"name", "lang", "default", "local_service", "voice_uri"}
            assert isinstance(spec["name"], str)
            assert isinstance(spec["lang"], str)
            assert isinstance(spec["default"], bool)
            assert isinstance(spec["local_service"], bool)
            assert isinstance(spec["voice_uri"], str)

    def test_the_runtime_uses_the_api_attribute_names(self) -> None:
        """The runtime must expose ``voiceURI`` and not the payload name.

        The payload carries the snake_case name the Python schema uses,
        but the page reads the Web Speech API names, and a script that
        reads ``voice.lang`` has to get a string.
        """

        from novaprofile.js_runtime.builder import RUNTIME_JS

        for attribute in ("voiceURI", "name", "lang", "localService", "default"):
            assert f"'{attribute}'" in RUNTIME_JS


NODE_CANDIDATES = (
    "node",
    r"C:\chromium\src\third_party\node\win\node.exe",
    "/usr/bin/node",
    "/usr/local/bin/node",
)


def _find_node() -> str | None:
    for candidate in NODE_CANDIDATES:
        if shutil.which(candidate):
            return candidate
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


NODE = _find_node()


#: The harness.  It builds a browser-shaped environment with a
#: ``speechSynthesis`` object and a ``SpeechSynthesisVoice`` constructor,
#: runs the runtime inside it, and reports what the page would have read.
VOICE_HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('node:vm');

    const runtime = fs.readFileSync(process.argv[2], 'utf8');
    const profile = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

    function SpeechSynthesisVoice() {}
    function SpeechSynthesis() {}
    SpeechSynthesis.prototype.getVoices = function () { return []; };
    SpeechSynthesis.prototype.addEventListener = function () {};
    SpeechSynthesis.prototype.dispatchEvent = function () {};

    const speechSynthesis = new SpeechSynthesis();

    // The event listener the page installs, and the counter that tells
    // the harness whether the event was dispatched.
    let voiceschanged = 0;
    speechSynthesis.addEventListener = function (type) {
      if (type === 'voiceschanged') voiceschanged++;
    };
    speechSynthesis.dispatchEvent = function (event) {
      if (event && event.type === 'voiceschanged') voiceschanged++;
      return true;
    };

    const sandbox = Object.create(null);
    sandbox.globalThis = sandbox;
    sandbox.window = sandbox;
    sandbox.speechSynthesis = speechSynthesis;
    sandbox.SpeechSynthesis = SpeechSynthesis;
    sandbox.SpeechSynthesisVoice = SpeechSynthesisVoice;
    sandbox.Object = Object;
    sandbox.Array = Array;
    sandbox.String = String;
    sandbox.Number = Number;
    sandbox.Boolean = Boolean;
    sandbox.Function = Function;
    sandbox.Math = Math;
    sandbox.JSON = JSON;
    sandbox.Date = Date;
    sandbox.RegExp = RegExp;
    sandbox.Error = Error;
    sandbox.TypeError = TypeError;
    sandbox.RangeError = RangeError;
    sandbox.Promise = Promise;
    sandbox.Map = Map;
    sandbox.Set = Set;
    sandbox.Symbol = Symbol;
    sandbox.Proxy = Proxy;
    sandbox.Reflect = Reflect;
    sandbox.Intl = Intl;
    sandbox.Event = typeof Event === 'function' ? Event : undefined;
    sandbox.Uint8Array = Uint8Array;
    sandbox.parseInt = parseInt;
    sandbox.parseFloat = parseFloat;
    sandbox.isNaN = isNaN;
    sandbox.setTimeout = setTimeout;
    sandbox.clearTimeout = clearTimeout;
    sandbox.__NOVA_PROFILE__ = profile;

    const context = vm.createContext(sandbox);
    const thrown = [];

    try {
      new vm.Script(runtime, { filename: 'runtime.js' }).runInContext(context);
    } catch (e) {
      thrown.push(String(e && e.stack || e));
    }

    function read(fn) {
      try { return fn(); } catch (e) { return 'THREW: ' + e.message; }
    }

    const list = read(function () { return speechSynthesis.getVoices(); });

    const report = {
      thrown: thrown,
      count: Array.isArray(list) ? list.length : -1,
      voices: Array.isArray(list) ? list.map(function (v) {
        return {
          voiceURI: v.voiceURI,
          name: v.name,
          lang: v.lang,
          localService: v.localService,
          default: v.default,
          isVoice: (typeof SpeechSynthesisVoice === 'function') &&
            (v instanceof SpeechSynthesisVoice),
          keys: Object.keys(v).sort()
        };
      }) : []
    };

    // The runtime queues the ``voiceschanged`` event, so the harness
    // waits for the queue to drain before it reports.
    setTimeout(function () {
      report.voiceschanged = voiceschanged;
      console.log(JSON.stringify(report));
    }, 20);
    """
).strip()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestJavaScriptExecution:
    """The runtime actually runs and answers with the right shape."""

    def _run(self, config, tmp_path: Path) -> dict:
        builder = JSRuntimeBuilder()
        script = builder.build(config)

        # The builder emits ``window.__NOVA_PROFILE__=<json>;`` followed by
        # the runtime.  The harness reads the payload from its own file,
        # so the two halves are written apart.
        payload_json, runtime = script.split(";\n", 1)

        runtime_path = tmp_path / "runtime.js"
        runtime_path.write_text(runtime, encoding="utf-8")

        harness_path = tmp_path / "harness.js"
        harness_path.write_text(VOICE_HARNESS, encoding="utf-8")

        payload_path = tmp_path / "profile.json"
        payload_path.write_text(payload_json.split("=", 1)[1], encoding="utf-8")

        result = subprocess.run(
            [NODE, str(harness_path), str(runtime_path), str(payload_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_runtime_does_not_throw(self, locale: str, tmp_path: Path) -> None:
        result = self._run(_generate("pixel-8-pro-android14", locale), tmp_path)

        assert result["thrown"] == []

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_page_reads_the_voice_list(self, locale: str, tmp_path: Path) -> None:
        config = _generate("pixel-8-pro-android14", locale)
        result = self._run(config, tmp_path)

        assert result["count"] == len(_voices(config))

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_page_reads_the_api_attributes(self, locale: str, tmp_path: Path) -> None:
        config = _generate("pixel-8-pro-android14", locale)
        result = self._run(config, tmp_path)

        for reported, expected in zip(result["voices"], _voices(config), strict=True):
            assert reported["voiceURI"] == expected.voice_uri
            assert reported["name"] == expected.name
            assert reported["lang"] == expected.lang
            assert reported["localService"] is expected.local_service
            assert reported["default"] is expected.default

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_voice_is_a_speech_synthesis_voice(self, locale: str, tmp_path: Path) -> None:
        """A real voice is a ``SpeechSynthesisVoice`` instance.

        A script that checks the prototype finds out, and a plain object
        with the right fields is not one.
        """

        result = self._run(_generate("pixel-8-pro-android14", locale), tmp_path)

        assert result["voices"]
        for reported in result["voices"]:
            assert reported["isVoice"] is True

    @pytest.mark.parametrize("locale", LOCALES)
    def test_the_attributes_are_read_only(self, locale: str, tmp_path: Path) -> None:
        """The Web Speech API marks every attribute readonly.

        A writable ``lang`` is a give-away: a script that assigns to it
        and reads it back learns that the voice is a plain object.
        """

        result = self._run(_generate("pixel-8-pro-android14", locale), tmp_path)

        for reported in result["voices"]:
            assert "lang" in reported["keys"]
            assert "voiceURI" in reported["keys"]

    def test_the_voices_changed_event_is_fired(self, tmp_path: Path) -> None:
        """Chromium fires ``voiceschanged`` once the list is known.

        ``SpeechSynthesis::VoicesDidChange`` dispatches the event when
        the voice list arrives, and a page that waits for the event
        instead of polling ``getVoices()`` has to receive it.
        """

        result = self._run(_generate("pixel-8-pro-android14", "vi-VN"), tmp_path)

        assert result["voiceschanged"] >= 1
