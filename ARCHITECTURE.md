# How the profile reaches the browser

This document describes the path a profile takes from the control plane
to the pixels on the screen, and the reasons for each step. It is the
short version of `CHROMIUM_HOOKS.md`, which describes the individual
hooks.

## The shape of the problem

A browser fingerprint is only useful if a site cannot tell it from a
real device. That rules out the obvious approach: a JavaScript shim
that overwrites `navigator.userAgent` and the rest. A shim is visible
in four ways, all of them trivial to check:

- `Function.prototype.toString()` returns the shim's source
- `Object.getOwnPropertyDescriptor()` reveals an accessor
- a `Proxy` traps every property access
- `navigator.webdriver` is `true`

So the values have to be installed **below** Blink, in the C++
accessors themselves, where the page sees ordinary native properties.

## The three documents

| Document | What it holds | Who reads it |
|---|---|---|
| the profile | the values: the user agent, the screen, the device catalogue, the GPU parameters | the browser process |
| the payload | the same values, in the form the JavaScript layer reads | the renderer |
| the layer's code | the code that installs the values the native core does not own | the renderer, from the binary |

The profile is one JSON document of about nineteen kilobytes. The
payload is about nine. The code is about eighty-four.

## The path

```
control plane
    |
    |  nova_core.json
    v
browser process  --reads the file on the main thread-->
    |
    |  base64(deflate(profile))   on the renderer's command line
    |  base64(deflate(payload))   on the renderer's command line
    v
renderer         --inflates both, installs the values-->
    |
    v
page
```

Three decisions shape this diagram, and each of them was forced by a
failure that is worth recording.

### The browser reads the file, not the renderer

The renderer is sandboxed. On Windows it cannot open a file outside a
short list of directories, and the user data directory is not on it.
A renderer that is given a path reports the host's values for every
axis, which is the one thing the engine exists to prevent.

The browser process is not sandboxed. It reads the profile and hands it
on.

### The hand-over rides on the command line, not on a file

A shared file in the user data directory would have the same problem as
the path: the renderer cannot open it.

A Mojo channel would have no length limit, but the channel arrives too
late. The renderer has to connect before it can deliver anything, and a
navigation can begin before it does. A renderer whose first navigation
has no payload reports the host's values for every axis.

The command line is built when the process is created, so the document
is there before the renderer runs.

### The documents are deflated

A Windows command line holds at most 32,767 characters. Base64 of the
profile and the payload together is more than that, and the renderer
was not started at all. Windows said so with

```
The filename or extension is too long. (0xCE)
```

which arrives as a launch failure rather than as a browser that starts
and then behaves oddly.

Deflated, the profile is six kilobytes and the payload is four. Base64
of that is thirteen in all, a third of what the command line holds.

## The layer's code is compiled into the browser

The JavaScript layer owns the surfaces the native core does not: the
canvas hash, the font list, the geometry of the elements, the media
devices, the WebGPU adapter.

Its code is the same for every profile, and it is eighty-four
kilobytes, which does not fit on a command line next to the payload.
So the code is compiled into the browser (`nova_js_runtime_code.cc`,
generated from the control plane's copy) and only the payload travels.

The payload is a JSON document of the profile's values, and it is eight
kilobytes -- the only part of the layer that is secret.

## The order of the startup

The browser process does two things, and the order matters:

1. **Prime the hand-over cache.** The profile and the payload are read
   from the disk, deflated and encoded. This has to happen on the main
   thread during startup, before any child process exists: the
   renderer's command line is built on the IO thread, and a blocking
   read on a thread that is marked as non-blocking trips the
   `tls_blocking_disallowed` DCHECK and takes the browser down.

2. **Install the configuration.** The browser reads its own copy of the
   profile out of the cache that step one filled.

The order is not a detail. The browser process is given a path and the
children are given the document, so the browser cannot read the file
twice: the second read happens on a thread that is not allowed to wait
on the disk, and the browser is sandboxed against the file anyway.

## What each surface is owned by

The layer checks a marker before it touches anything. The marker names
the surfaces the native core owns, and the layer leaves those alone.

The marker is only published when the browser is started with
`--nova-expose-surfaces`. Without it, the page cannot tell which
surfaces are native, and the layer cannot either -- it simply takes
everything that is not marked.

| Surface | Owner |
|---|---|
| user agent, client hints | native |
| screen, viewport, media queries | native |
| WebGL identity and parameters | native |
| audio | native |
| TLS (JA3, JA4) | native |
| storage quota | native |
| canvas 2D hash | native |
| fonts | native |
| geometry | native |
| media devices | native |
| WebGPU adapter | native |
| navigator values the core does not own | layer |
| codecs, heap limit, transcendentals | layer |

## Building it

The Chromium side lives in `third_party/blink/renderer/core/nova/`. The
patch series in `novaprofile/core_patch/patches/` applies to a stock
checkout:

```bash
git apply novaprofile/core_patch/patches/0001-nova-profile-core.patch
git apply novaprofile/core_patch/patches/0002-nova-profile-hardware-surfaces.patch
git apply novaprofile/core_patch/patches/0003-nova-profile-independent-surfaces-and-tls.patch
```

The series is regenerated from the checkout with

```bash
python tools/regenerate_patch_series.py
```

and it is diffed against the **upstream release**, not against the head
of the checkout: the checkout holds the NOVA changes and they are
committed there, so diffing against its head produces an empty patch
for every file the series touches.
