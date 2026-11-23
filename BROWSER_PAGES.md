# What the browser's own pages must not see

The profile describes a device on the web. Chromium's own pages are not
the web: they are part of the browser, and a fingerprint that changes
them changes what the browser does.

## The crash that found it

The browser died on startup, about a second in, with

```
FATAL:chrome\browser\speech\extension_api\tts_engine_extension_api.cc:280
Check failed: event_router.
```

and nothing else. The dump had a stack but no symbols.

`event_router` is null because the extension system was still coming
up. `EventRouter::Get(profile)` returns null until it is ready, and
the TTS engine's `GetVoices` checks it:

```cpp
EventRouter* event_router = EventRouter::Get(profile);
CHECK(event_router, base::NotFatalUntil::M162);
```

## Why the profile made it happen

The layer overrides `speechSynthesis.getVoices()`:

```javascript
if (typeof speechSynthesis !== 'undefined' && P.speech_voices) {
  // ...
}
```

In a `chrome-extension://` page -- one of Chromium's own component
extensions -- that override asks the browser for the voices of the
speech engine. The browser answers the question by looking for an
installed extension that provides them, and it looks them up through the
event router, which is null at that moment.

The extension asked because the page it ran in had been given a
fingerprint that says it is an Android phone, and the phone it claims to
be has a speech engine. Without the fingerprint the extension would not
have asked, and the browser would not have died.

So the profile did not break the browser by being wrong. It broke it by
being *right about the wrong thing*: it told Chromium's own page that
it was a phone, and the page then behaved like one.

## The fix

The profile applies to `http`, `https` and `file`, and to nothing else.

```cpp
bool ProfileAppliesTo(const Document* document) {
  // ...
  return scheme == "http" || scheme == "https" || scheme == "file";
}
```

## The other half of it

The settings page measures the screen to lay itself out, and the
geometry perturbation moves what it measures. The new tab page reads
the fonts to pick a theme. A fingerprint that changes the browser's own
layout is a fingerprint that is visible without a page.

The native core is process-wide, so its values reach the browser's own
pages as well. That is acceptable for the values that describe the
device -- the user agent in the settings page is not something a site
can see -- but the geometry and the fonts are read by the browser's own
layout, and they are not.

## The rule

A fingerprint belongs to the web. The browser is the instrument, and an
instrument that reports the wrong reading of itself is broken.
