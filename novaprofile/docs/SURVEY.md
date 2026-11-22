# Survey of anti-bot mechanisms on six registration sites

This document records what was measured on the six registration sites NOVA
PROFILE targets. Everything here is an observation, not a guess: every entry
carries the place it was taken from.

## Verification pass — 2026-11-22

Every claim in this document was re-measured on **2026-11-22** (14:35–15:05
UTC+7). Each row in the tables below carries a verification marker:

| Marker | Meaning |
|---|---|
| ✅ | Claim re-confirmed; the cited evidence was observed again |
| ❌ | Claim is wrong or outdated; the corrected value is given |
| ⚠️ | Could not be verified from this machine |

### How the verification was done

Two independent channels were used, because neither alone is sufficient:

* **Browser** (`cursor-ide-browser`): `browser_navigate` to each URL, then
  `browser_snapshot` / `browser_take_screenshot` to read the rendered DOM.
  `browser_cdp` (`Runtime.evaluate`) was **broken for the whole session** —
  every call returned `Error invoking remote method
  'vscode:webviewSendCDPCommand': Error: Invalid parameters`, for every tab,
  every method, including `DOM.getDocument` and a trivial `Runtime.evaluate`
  with `expression: "1+1"` on a fresh `about:blank` tab. This is a tool-side
  failure, not a page-side one.
* **Direct HTTP** (Python `urllib` from the shell): full page HTML, response
  headers, and every referenced JavaScript bundle fetched and grepped.

The second channel was the one that produced most of the evidence below,
because it reaches the bundles and the headers that the rendered DOM does
not expose. Anything that could only be observed in the live DOM (the
Lobby register form, the linkantoan front page, the Jun88 home page) is
marked as such.

Two measurement notes that matter for reading the counts:

* The three Lobby sites serve **gzip-compressed** HTML. A naive fetch that
  does not decompress returns a ~49 KB binary blob for `yaccm8h.cc` and
  finds nothing. All counts below are from decompressed bodies.
* `c168j.vip` and `yaccm8h.cc` return different bundle hashes than
  `m.ssc8111.vip`, but the browser-API surface counts inside them are
  identical (see §1.2).

## Summary table

| Site | Platform | CDN (verified) | Fingerprint | Device-risk signal |
|---|---|---|---|---|
| `m.ssc8111.vip` | Lobby (Cocos) | AWS CloudFront (`3.162.58.x`) + Aliyun OSS | FingerprintJS (server-side) + Geetest | `lobby@device@score@v2` |
| `c168j.vip` | Lobby (Cocos) | Cloudflare (`104.18.26.248`) | same set as SC88 | same as SC88 |
| `yaccm8h.cc` | Lobby (Cocos) | Alibaba CDN / Tengine (`107.155.62.x`) | same set as SC88 | same as SC88 |
| `8jjunsyj.cc` | Vue SPA (`vi5prod`-style) | Cloudflare (`104.21.15.157`) | Cloudflare Web Analytics only | none |
| `linkantoan.vip` | Vue SPA (78win) | Cloudflare (`104.18.4.14`) | Cloudflare Web Analytics only | none |
| `oon6a1.ink` | WPS / uni-app | Cloudflare (`104.21.19.149`) | **ThumbmarkJS + ClientJS** + `encrypt.js` | `SHELL_deviceId` |

### Corrections to the previous summary table

| Column | Was | Now | Why |
|---|---|---|---|
| `m.ssc8111.vip` CDN | CloudFront | ✅ CloudFront, **plus Aliyun OSS** for the API bucket | `Server: AliyunOSS`, `x-oss-request-id` header; DNS `3.162.58.x` is AWS CloudFront. Both are used. |
| `c168j.vip` CDN | Cloudflare | ✅ Cloudflare | `Server: cloudflare`; DNS `104.18.26.248`. It also references the asset host `pubsgppp.c1oudfront.com`, but that is an asset CDN, not the page host. |
| `yaccm8h.cc` CDN | Aliyun | ✅ Alibaba CDN | `Server: Tengine`, `Ali-Swift-Global-Savetime`, `x-oss-*` headers; DNS `107.155.62.x`. The label "Aliyun" was right in substance. |
| `linkantoan.vip` | "Cannot be opened" | ❌ **Opens fine in a browser** | The front page loads and renders (branded *78win*). Only non-browser TLS clients fail. See §4. |
| `8jjunsyj.cc` fingerprint | "Cloudflare Bot Management" | ⚠️ **Correct at the network layer, not verifiable from the page** | The page loads only the Cloudflare analytics beacon. Bot Management is a network-layer product and leaves no marker in the HTML. |
| `oon6a1.ink` fingerprint | "ThumbmarkJS + `encrypt.js`" | ❌ **ThumbmarkJS + ClientJS + `encrypt.js`** | ClientJS 0.2.1 is loaded as a failover with two CDN mirrors. See §2.2. |

---

## 1. Lobby group: SC88, C168, CM88

All three sites run the same application, so the mechanisms are the same and
were measured on all three.

### 1.1 Registration form

✅ Re-confirmed on **2026-11-22** from the live DOM of `m.ssc8111.vip`
(`browser_snapshot`, tab `fe63b0`), and confirmed byte-identical in the HTML
of `c168j.vip` and `yaccm8h.cc`.

```
Tài khoản            (placeholder: "Nhập Tên tài khoản")
Mật khẩu             (placeholder: "Nhập mật khẩu")
Họ và tên thật        (placeholder: "Vui lòng nhập Họ và Tên thật của bạn")
[X] Tôi trên 18 tuổi, đã đọc và đồng ý với 《 Thỏa thuận đăng ký người dùng
```

There are exactly three text inputs and two checkboxes. Every `<input>`
carries **`type="text"` even for the password field** — the password box is
`type="text"`, not `type="password"`. All inputs have `class="ui-input__input"`
or `class="ui-checkbox__input"`, and **no `name` and no `id` attributes at
all**:

```html
<input type="checkbox" class="ui-checkbox__input">
<input type="text" class="ui-input__input" placeholder="Nhập Tên tài khoản">
<input type="text" class="ui-input__input" placeholder="Nhập mật khẩu">
<input type="text" class="ui-input__input" placeholder="Vui lòng nhập Họ và Tên thật của bạn">
<input type="checkbox" class="ui-checkbox__input">
```

This is worth flagging for NOVA: because there is no `name`/`id`, a
fill strategy cannot key off DOM attributes. It has to key off the
placeholder or the DOM order.

The API that receives the registration is `POST /api/member/register`, and
the pre-check is `POST /api/member/check/register`. Both were located in
`chunk-closure-startup.js`:

```javascript
nL = '/api/member/register',
rL = (e, t) => {
  let { systemInfos: n } = Z();
  return J.request({ ...e, url: nL, method: 'post',
    data: { ...R0().isP2aParamsByChannel, ...Y().userLoginBasePayload,
             token: n?.token, version: 9, ...e.data } }, ...);
}
```

`version: 9` is sent on every registration.

### 1.2 One build, three domains — and one FingerprintJS tenant

✅ Re-confirmed on **2026-11-22**. All three pages carry the same
configuration blob, and all three carry the **same FingerprintJS public
key**:

| Site | `fingerprintJS.publicKey` | `render-init` | Result |
|---|---|---|---|
| `m.ssc8111.vip` | `cIMrDd2qJKZFByajXD7O` | `v7.6.9` | ✅ |
| `c168j.vip` | `cIMrDd2qJKZFByajXD7O` | `v7.6.9` | ✅ |
| `yaccm8h.cc` | `cIMrDd2qJKZFByajXD7O` | `v7.6.9` | ✅ |

The three pages have different lengths (184041 / 183425 / 206998 bytes),
because each embeds its own brand, phone list, banner and jackpot data.

All three also carry the same Geetest captcha id for every flow
(✅ confirmed, identical string on all three):

```json
"geetest_captcha_id": {
    "feedback":      "62c528ead784206de7e6db17765b9ac0",
    "force_geetest": "62c528ead784206de7e6db17765b9ac0",
    "login":         "62c528ead784206de7e6db17765b9ac0",
    "recharge":      "62c528ead784206de7e6db17765b9ac0",
    "register":      "62c528ead784206de7e6db17765b9ac0"
}
```

`force_geetest` equals `register`, so registration **always** shows a
Geetest challenge rather than only on suspicion.

#### The bundle counts are identical across the three sites

✅ Measured on **2026-11-22** by fetching each site's own startup chunk and
grepping it. The three use different content hashes
(`CUpFIxPM` / `DTb7eq1w` / `Dzm0__Ey`) but the browser-API surface counts
are **byte-for-byte identical**, which is strong evidence they are the same
build:

| Surface | SC88 | C168 | CM88 |
|---|---|---|---|
| `navigator.userAgent` | 16 | 16 | 16 |
| `userAgentData` | 4 | 4 | 4 |
| `navigator.platform` | 1 | 1 | 1 |
| `hardwareConcurrency` | 2 | 2 | 2 |
| `maxTouchPoints` | 7 | 7 | 7 |
| `screen.width` | 3 | 3 | 3 |
| `screen.availWidth` | 1 | 1 | 1 |
| `screen.colorDepth` | 1 | 1 | 1 |
| `devicePixelRatio` | 1 | 1 | 1 |
| `window.innerWidth` | 8 | 8 | 8 |
| `getParameter` (WebGL) | 1 | 1 | 1 |
| `getTimezoneOffset` | 5 | 5 | 5 |
| `permissions.query` | 1 | 1 | 1 |
| `indexedDB` | 4 | 4 | 4 |
| `navigator.connection` | 4 | 4 | 4 |
| `mimeTypes` | 2 | 2 | 2 |
| `localStorage` | 103 | 103 | 103 |
| `sessionStorage` | 25 | 23 | 23 |
| `document.cookie` | 10 | 10 | 10 |
| `performance.now` | 12 | 12 | 12 |

Startup chunk sizes: 1579637 / 1508959 / 1508513 bytes.

### 1.3 FingerprintJS is server-side — the library is never shipped

✅ **Confirmed, and this is the most important correction in the Lobby
section.** The survey's claim "FingerprintJS is called on the server after
the form is submitted, not at page load" is correct, and it is now
mechanically proven rather than inferred:

* The string `fingerprintJS` appears **0 times** in
  `chunk-closure-startup.CUpFIxPM.js` (1579637 bytes), and `FingerprintJS`,
  `fingerprintjs`, `@fingerprintjs/fingerprintjs` and `fpjs` also appear
  **0 times**. There is no FingerprintJS bundle on the page at all.
* The key lives only in the server-rendered config: `fingerprintJS:{publicKey:"cIMrDd2qJKZFByajXD7O"}`.
* The client calls two endpoints for it, both found in the startup chunk:
  `/api/member/getFingerprint` and `/api/member/noauth/getFingerprint`.
* `fingerId` is populated from the **server response**, not computed locally:

```javascript
getToken: async () => {
  let t = await CR.getToken();
  let [r, i] = await kE(yDe(t.token));
  let a = (i?.data.data || {}).fingerprint;
  if (a && n.userInfos) return n.fingerId = a, n.userInfos.deviceFingerprint = a, a;
}
```

The client therefore never sees a raw FingerprintJS hash. Whatever the
server computes from the request is what becomes `fingerId`.

⚠️ Neither `getFingerprint` endpoint could be reached directly from this
machine (they return `404` from the static host and `405 MethodNotAllowed`
from the OSS bucket, because the API gateway requires the browser's exact
request shape). The endpoints are named and referenced from the bundle; the
payload could not be captured.

### 1.4 The device-risk score — and where it is really computed

✅ Re-confirmed on **2026-11-22**, with **three corrections** to the
previous description.

The stored object is exactly as previously recorded:

```json
{
  "version": "2.4.2",
  "result": {
    "score": 90, "tier": "HIGH", "confidence": "mid",
    "proposed": { "score": 90, "tier": "HIGH", "earlyReturn": true,
                  "source": "pc-fast-path", "reasons": ["pc-desktop"] },
    "details": {
      "gpu": {
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti (0x00002D04) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "confidence": "none", "rendererConfidence": "none", "mapVersion": "1.0.3"
      }
    },
    "dimensionScores": {},
    "weights": { "sort": 0.2, "cpu": 0.2, "memory": 0.5, "image": 0.1 },
    "capsApplied": ["pc-fast-path→90"]
  }
}
```

**Correction 1 — the scoring module does exist in the browser.** The
previous survey could not find it and inferred the score was purely
server-side. It is not. The score is computed client-side in a
separately loaded chunk that the main chunk pulls in through a dynamic
import:

```javascript
import(/* @vite-ignore */ __lobbyRetryUrl
      ? __lobbyRetryUrl
      : import('./optimized-chunk-device-score.DVbwnoFr.js'))
```

That chunk exists and is 23548 bytes. It contains the `pc-fast-path`
function verbatim:

```javascript
function xe({ isPc: e, details: t, weights: n }) {
  if (!e) return null;
  let r = 'HIGH';
  return { score: 90, tier: r, confidence: 'mid',
    proposed: { score: 90, tier: r, earlyReturn: true,
                source: 'pc-fast-path', reasons: ['pc-desktop'] },
    details: t, dimensionScores: {}, weights: n,
    capsApplied: ['pc-fast-path→90'] };
}
```

and it writes the result:

```javascript
window.localStorage.setItem(p, JSON.stringify({ version: y, result: n }))
```

**Correction 2 — there are three fast paths, not one.** The survey
described only `pc-fast-path`. The module actually implements three
mutually exclusive short-circuits, checked in this order:

| Order | `source` | Trigger | Score |
|---|---|---|---|
| 1 | `market-low-model` | UA `model` matches the known-low-model list | 35 (`LOW`) |
| 2 | `android-hardware-fast-path` | Android + GPU tier + RAM heuristics | 35 / 69 / 90 |
| 3 | `pc-fast-path` | Desktop UA (`Windows NT`, `Macintosh`, `CrOS`, `X11`) | 90 (`HIGH`) |

The Android path has its own sub-branches, quoted from the chunk:

```javascript
if (t.tier === 'LOW' && Y(t) && r != null)       o = 35, s = 'LOW',  c = ['low-gpu(...)']
else if (r != null && r <= 2)                      o = 35, s = 'LOW',  c = ['low-memory(${r}GB<=2)']
else if (ve(t, r))                                 o = 35, s = 'LOW',  c = ['low-edge-gpu-low-ram(adreno-619,${r}GB<=4GB)']
else if (t.tier === 'MID' && Y(t))                 o = 69, s = 'MID',  c = ['mid-gpu(...)']
else if (t.tier === 'HIGH' && Y(t))                o = 90, s = 'HIGH', c = ['high-gpu(...)']
```

`ve()` requires `confidence === 'high' && rendererConfidence === 'exact' &&
matchedKey === 'adreno-619'` plus `deviceMemory <= 4`. So the GPU renderer
string must map to a *known* key for the fast path to fire; an unmapped
renderer (`confidence: "none"`) falls through to the full four-axis
scoring. **For NOVA this means an unmapped, synthetic GPU renderer is not
a neutral choice — it forces the slow path, where all four axes are
computed and compared.**

**Correction 3 — the tier bands are five, not three.** The previous survey
said "three tiers". The chunk defines five:

```javascript
vk = function (e) {
  return e.LOWEST = '0-39', e.LOW = '40-59', e.MEDIUM = '60-74',
         e.HIGH = '75-89', e.HIGHEST = '90-100';
}
```

The `tier` field on the stored object uses three names (`LOW` / `MID` /
`HIGH`) which map onto those bands via:

```javascript
mCe = e => e >= 90 && e <= 100 ? '90-100'
       : e >= 75 && e <= 89  ? '75-89'
       : e >= 60 && e <= 74  ? '60-74'
       : e >= 40 && e <= 59  ? '40-59' : '0-39'
```

#### The four axes and their weights

✅ The weights are confirmed in the source, in one place:

```javascript
fk = '2.4.2', pk = 'lobby@device@score@v2',
iCe = 1e3, aCe = 15e3, oCe = .2, sCe = .2, cCe = .5, lCe = .1, uCe = 3e4
```

which are used as `weights: { sort: i, cpu: a, memory: o, image: s }`.

| Axis | Weight | Source | Notes |
|---|---|---|---|
| `memory` | 0.5 | `navigator.deviceMemory` | scored `≤2 → 20`, `≤4 → 45`, `≥8 → 95`, else `70` |
| `sort` | 0.2 | sort benchmark in a Worker | `ae(sort)` |
| `cpu` | 0.2 | CPU benchmark in a Worker | `oe(cpuPure)`; a value under 5 ms is treated as "DCE" and discarded as invalid |
| `image` | 0.1 | canvas decode timing | `de(decode)` → `high` / `mid` / `low` |

The `sort` and `cpu` numbers come from a separate Worker file,
`/assets/benchmark.worker-BCVIEdOS.js` (2290 bytes), which reports
`sort`, `cpuPure`, `cpuThroughput`, `decode` and `multiRound`.

The memory scoring function, quoted exactly:

```javascript
function W(e) { return e == null ? null : e <= 2 ? 20 : e <= 4 ? 45 : e >= 8 ? 95 : 70 }
```

The decode scoring function:

```javascript
function de(e) {
  return e.median >= 100 || e.p75 >= 100 ? 'high'
       : e.median >= 50 || e.p75 >= 66 || e.p90 >= 120 ? 'mid' : 'low';
}
```

#### The GPU renderer map

✅ The map is present, and its version is **`1.0.0`** in the current build:

```javascript
R = `1.0.0`
```

The previously recorded `mapVersion: "1.0.3"` in `localStorage` is a
**stale value written by an older build** — the live source now ships
`1.0.0`. The map contains at least `adreno-306`, `adreno-308`, `adreno-405`,
`adreno-619` and a `mali-t-series` / `mali-g31` family.

The renderer is read through the standard extension, in the startup chunk:

```javascript
function _re() {
  let e = document, t = ia.get(e);
  if (t !== undefined) return t;
  let n = e.createElement('canvas');
  let r = n.getContext('webgl') || n.getContext('experimental-webgl');
  if (!r) return;
  let i = r.getExtension('WEBGL_debug_renderer_info');
  if (!i) return '';
  let a = r.getParameter(i.UNMASKED_RENDERER_WEBGL);
  return typeof a == 'string' && a.trim() && ia.set(e, a), a;
}
```

Note the canvas is **created fresh** for the probe, and the result is cached
per-document in a `WeakMap`. A NOVA profile that returns a stable renderer
from `getContext('webgl')` is read once and then cached for the page
lifetime.

#### Silent degradation — a surface that was missing entirely

✅ **New finding.** The score is not just recorded; it *gates a large set of
page behaviours*. When the score falls in the `40-59` or `0-39` bands, the
page silently disables features and announces itself as a low-end device.
The list was read out of the startup chunk:

```javascript
kk = {
  stopScrollAnimation: Ok('stopScrollAnimation', ...),
  stopRouterAnimation: Ok('stopRouterAnimation', ...),
  lightRouterAnimation: Ok('lightRouterAnimation', ...),
  stopOverlayAnimation: Ok('stopOverlayAnimation', ...),
  staticGameLoading:   Ok('staticGameLoading', ...),
  delayAutoDialogOpen: Ok('delayAutoDialogOpen', ...),
  disableAvif:         Ok('disableAvif', ...),
  deferLoginSuccessTasks: Ok('deferLoginSuccessTasks', ...),
  reduceFirstScreenEagerList: Ok('reduceFirstScreenEagerList', ...),
  asyncDecodeImageByDeviceScore: Ok('asyncDecodeImageByDeviceScore', ...),
  disableCountToAnimation: Ok('disableCountToAnimation', ...),
  disableAutoSlide: Ok('disableAutoSlide', ...),
  disableSeamlessScroll: Ok('disableSeamlessScroll', ...),
  disableWinnerScroll: Ok('disableWinnerScroll', ...),
  disableMarketingAnimation: Ok('disableMarketingAnimation', ...),
  disableApngByDeviceScore: Ok('disableApngByDeviceScore', ...),
  disableLottieAnimation: Ok('disableLottieAnimation', ...),
  disableRedPackRain: Ok('disableRedPackRain', ...)
}
```

The console even says so in Chinese, when the downgrade is first applied:

```javascript
console.info('[低端机降级] ${e} 已启用 (score=${t == null ? '-' : t}, quality=${Sk()}, scoreVersion=${n == null ? '-' : n})')
```

**This matters for NOVA directly.** A profile that scores below 60 does not
just look weak — it changes the page's behaviour in ways that are visible to
any observer: no scroll animation, no AVIF, static loading, no Lottie, no
APNG, no red-packet rain. A mid-range Android profile that scores 45 will
behave visibly differently from a real mid-range phone. The four axes must
land in a band that produces normal behaviour.

Also confirmed: the old v1 score is still written and read:

```
localStorage: lobby@device@score          = "0.0.1:100"
localStorage: lobby@device@score@detail   = "0.0.1:{ \"score\": 100, \"details\": { \"cpu\": 100, \"memory\": 100 } }"
```

with the version prefix `0.0.1` and the same two keys hard-coded in the
bundle:

```javascript
jne = `0.0.1`, vn = `lobby@device@score`, Mne = `lobby@device@score@detail`
```

⚠️ One consistency note: the `lowDeviceHint` fallback for a missing score
is hard-coded to **35 / `LOW` / `not-ready-low-fallback`**, so a profile that
never completes the device-score run is treated as the worst possible
device by every degradation gate.

### 1.5 Device identification — `LobbyDeviceCompatibility`

✅ **Re-confirmed, and relocated.** The previous survey could not find
`LobbyDeviceCompatibility` in the JavaScript. It is not in a chunk at all —
it is an **inline `<script>` in the page HTML**, defined before the bundles
load. Exact source, as served:

```javascript
const LobbyDeviceCompatibility = {
  whitelist: [
    { name: "iPhone Duo inner landscape",
      userAgent: /iPhone|iPad|Macintosh/i,
      minTouchPoints: 2, viewportWidth: 951, screenWidth: 951,
      screenHeight: { min: 585, max: 700 } },
    { name: "iPhone Duo inner landscape with outer-screen dimensions",
      userAgent: /iPhone|iPad|Macintosh/i,
      minTouchPoints: 2, viewportWidth: 951, screenWidth: 466,
      screenHeight: { min: 678, max: 678 } }
  ],
  matches() {
    return this.whitelist.some(function (e) {
      return e.userAgent.test(navigator.userAgent)
        && navigator.maxTouchPoints >= e.minTouchPoints
        && window.innerWidth === e.viewportWidth
        && window.screen.width === e.screenWidth
        && window.screen.height >= e.screenHeight.min
        && window.screen.height <= e.screenHeight.max;
    });
  }
};
```

The whitelist has **two** entries, both iPhone Duo variants — the previous
survey said "only iPhone Duo", which was right in substance. Note that the
UA regex accepts `Macintosh` as well, so a desktop Mac with
`maxTouchPoints >= 2`, `innerWidth === 951` and `screen.width === 951` would
also match the first entry.

`detectDeviceType` is a method on `window.LOBBY_UTILS`, also inline in the
HTML:

```javascript
detectDeviceType() {
  let e = navigator.userAgent,
      t = () => "undefined" != typeof LobbyDeviceCompatibility && LobbyDeviceCompatibility.matches(),
      i = () => e.match(/(iPad)/) || !e.match(/(iPhone\sOS)\s([\d_]+)/)
               && window.screen.height > window.screen.width
               && /macintosh|mac os x/i.test(e),
      o = () => /iPhone|Android.+Mobile/.test(e)
               || (navigator.maxTouchPoints && navigator.maxTouchPoints > 1
                   && /Android|webOS|iPhone|iPod|BlackBerry|IEMobile|Opera Mini/i.test(e));
  return { tablet: i, mobile: o, desktop: () => !i() && !o() && !t(),
           whitelistedMobile: t, appleMobile: () => /iPhone|iPad/i.test(e) };
}
```

The `whitelistedMobile` result feeds `mediaScreen()`, which sets an
`html` attribute to `1` when the viewport is wider than 450 px and the
device is desktop or whitelisted-mobile:

```javascript
mediaScreen() {
  let e = this.detectDeviceType(), t = e.desktop(),
      i = window.innerWidth, o = window.innerHeight, n = "mobile", a = "0",
      d = i > 450 && t, r = i > 450 && e.whitelistedMobile();
  if (d || r) a = "1";
  ...
}
```

There is also a **device redirect** that reads the same signals and rewrites
the URL between `/` and `/pc/`, using a `__device_redirect` query parameter.
A NOVA profile whose UA says "Android phone" but whose viewport is wide
enough will be bounced to `/pc/`, or will be bounced away from it if the
signals disagree.

### 1.6 The device probe function `zxe()`

✅ **Confirmed, with one correction.** The function exists and is quoted
below, but it is **not a global** — it is a module-local `function zxe()`
inside `chunk-closure-startup.js`. It is called by `Bxe` and `Vxe`, the
network-attribution helpers:

```javascript
function zxe() {
  let { os: e, device: t } = Yp(), { uuid: n } = Uw();
  return {
    screenSpec: {
      physRes: { w: screen.width, h: screen.height },
      availRes: { w: screen.availWidth, h: screen.availHeight },
      logRes:  { w: window.innerWidth, h: window.innerHeight },
      aspRatio: Number((screen.width / screen.height).toFixed(2)),
      pixDens:  window.devicePixelRatio,
      colDepth:  screen.colorDepth
    },
    langTzInfo: {
      prefLang: navigator.language,
      tzOffset:  new Date().getTimezoneOffset() * -1,
      tzName:    Intl.DateTimeFormat().resolvedOptions().timeZone
    },
    sysVer:     e.version,
    cpuCores:   navigator.hardwareConcurrency,
    deviceModel: t.model,
    deviceID:   n
  };
}
```

❌ Correction: the previous survey listed `zxe()` among the "global
variables that are read". It is a local function; the **globals it reads**
are `screen.width`, `screen.height`, `screen.availWidth`,
`screen.availHeight`, `window.innerWidth`, `window.innerHeight`,
`window.devicePixelRatio`, `screen.colorDepth`, `navigator.language`,
`Date.getTimezoneOffset`, `Intl.DateTimeFormat().resolvedOptions().timeZone`,
`navigator.hardwareConcurrency`, plus a device UUID from `Uw()` and a model
string from `Yp()`.

Also note `zxe()` is a **name collision hazard**: on `c168j.vip` the
*same* identifier `zxe` resolves to a completely different, unrelated
function in the same chunk (a `navigator.scheduling.isInputPending` probe).
Any grep-based analysis that searches for the literal `zxe` will hit both.

### 1.7 Customer identification

✅ Re-confirmed on **2026-11-22** from the live `localStorage` of all three
sites (values differ per site, structure is identical):

```
localStorage: web_lobby_next_user_webpush_visitor_fingerId
              = {"data":"4fad10ea-57a4-4dd1-b545-c715388b2ec2","expires":...}
localStorage: web__lobby__persisted__device
              = {"uuid":"64d50e44-69a6-4c46-b3b1-db9ed9094dc3"}
localStorage: web__lobby__persisted__user
              = {..., "fingerId":"", ...}
```

Measured values per site on the verification run:

| Site | `fingerId` | device `uuid` |
|---|---|---|
| `m.ssc8111.vip` | `4fad10ea-57a4-4dd1-b545-c715388b2ec2` | `64d50e44-69a6-4c46-b3b1-db9ed9094dc3` |
| `c168j.vip` | `ce614baf-ede6-4504-bcfa-5f49d40a7574` | `a95db9db-7598-444c-a726-5a7a8e29754b` |
| `yaccm8h.cc` | `6e726bb0-fe24-45fc-ace7-6af7914eec1b` | `447374c8-4d0b-4934-953b-ed1e3ad787a7` |

`fingerId` is populated **server-side** (§1.3), so on a fresh profile it is
the empty string until the server has returned one.

`deviceIdSecurityVerify` is confirmed verbatim:

```json
"deviceIdSecurityVerify": {
  "deviceIdEmail": 1, "deviceIdGoogleAuth": 1, "deviceIdLoginPasswd": 1,
  "deviceIdPassQuestion": 1, "deviceIdPhone": 1,
  "deviceIdThirdPartyLogin": 1, "deviceIdWithdrawPasswd": 1
}
```

### 1.8 Other anti-fraud configuration

✅ Re-confirmed on all three sites, byte-identical:

```json
"antiSwitch": "0",
"antiDebugStatus": 0,
"humanVerify": -1
```

`humanVerify: -1` means the human check is off at the configuration level,
but Geetest still fires at registration because `force_geetest` is set
(§1.2).

Also confirmed present:

* `lobby@network-samples` — network stability / latency / throughput
  sampling. Live value on SC88:
  `{"stability":[{"ts":1795332887219,"ok":true}],"ttfb":[],"throughput":[],"probe":[]}`
* `webLobbyindexedDBRequestStats` — IndexedDB read counter bucketed by
  latency. Live value on SC88:
  `{"buckets":{"INDEXEDDB:200":1,"INDEXEDDB_NORMAL:200":1,"INDEXEDDB:100":5,...},"totalRequests":9}`.
  Note the bucket names use the **latency in milliseconds** (`INDEXEDDB:200`
  means 200 ms), not a fixed set of three buckets.
* `lobby@support@media` — image format support, cached as `0.0.6:1`. The
  loader is inline in the HTML and fetches `/libs/browser-media-match@0.0.6/index.js`.
* `web__lobby__persisted__base` — `skinAssetsHash` with `lastMD5`. Live
  value on SC88: `lastMD5: "9420a09c1eb2c27b8a7e391c59267889"`.
* `lobby@domain@order`, `lobby@gossip`, `lobby@image@cache`,
  `lobby@lcp@banner`, `lobby@first_load`, `lobby@i18n` — all confirmed in
  the startup chunk as `lobby@*` storage keys.

### 1.9 Social login — Facebook is present but disabled

❌ **Correction.** The previous survey listed Facebook (app id `SC88.COM`)
as an available third-party login path. The configuration is:

| Site | `facebook` | `google` |
|---|---|---|
| `m.ssc8111.vip` | `{app_id:"SC88.COM",status:0}` | `{app_id:"143341858492-...",status:1}` |
| `c168j.vip` | `{app_id:"C168.COM",status:0}` | `{app_id:"1037925999164-...",status:1}` |
| `yaccm8h.cc` | `{app_id:"",status:0}` | `{app_id:"764511882549-...",status:1}` |

`status: 0` means **Facebook login is switched off on all three sites**.
Only Google Sign-In is enabled (`status: 1`). The app id is not `SC88.COM`
on every site — it follows the brand (`C168.COM` on C168, empty on CM88).

### 1.10 The API host is obfuscated

✅ **New finding.** The page does not call its own origin for API traffic.
The real API host is stored base64-encoded in the page and rewritten at
runtime. From `m.ssc8111.vip`:

```javascript
n = [["7bfa0","c2NyaXB0"], ["7bfa1","amRldTJmLTI4NjgtcHBw"], ["7bfa2","b3NzLWFjY2VsZXJhdGUuYWxpeXVuY3MuY29t"]]
```

Decoded:

| Token | Encoded | Decoded |
|---|---|---|
| `7bfa0` | `c2NyaXB0` | `script` |
| `7bfa1` | `amRldTJmLTI4NjgtcHBw` | `jdeu2f-2868-ppp` |
| `7bfa2` | `b3NzLWFjY2VsZXJhdGUuYWxpeXVuY3MuY29t` | `oss-accelerate.aliyuncs.com` |

So the API host is **`jdeu2f-2868-ppp.oss-accelerate.aliyuncs.com`**
(Alibaba OSS, same `-2868-ppp` suffix as the APK bucket
`8zl03s-2868-ppp.s3-accelerate.amazonaws.com` seen in the page). A second
base64 pair with a UUID salt performs the final rewrite to
`location.origin`, so the browser ends up talking to the page host while
the OSS bucket serves the API.

The API traffic is **encrypted**: `GET /cocos/config_data.json` on the OSS
host returns `200` but the body is ciphertext, and the startup chunk
contains a `cryptoKey` and a `crypto:{enable:!0}` request option. The same
`encrypt.js` idea as ON68, implemented natively.

The full endpoint list was extracted from the startup chunk:

```
/api/member/register                     /api/member/check/register
/api/member/fastRegister                  /api/member/fastLogin
/api/member/getFastLogin                  /api/member/getFingerprint
/api/member/noauth/getFingerprint         /api/member/login
/api/member/logout                        /api/member/listAccount
/api/member/thirdPartyLogin               /api/member/agent/login
/api/member/temporaryAccountLogin         /api/member/auth/telegram/login
/api/member/v2/user/temporary/upgrade     /api/member/check/cpf
/api/member/publicityPlaza/postingRestrictions
```

### 1.11 What the page code reads

✅ Re-confirmed on **2026-11-22**, from
`chunk-closure-startup.CUpFIxPM.js` (1579637 bytes) and
`optimized-chunk-guest-interaction.CtUX_qj1.js` (1117186 bytes).

Startup chunk:

```
navigator.userAgent ×16   userAgentData ×4        navigator.platform ×1
hardwareConcurrency ×2    maxTouchPoints ×7       screen.width ×3
screen.availWidth ×1      screen.colorDepth ×1    devicePixelRatio ×1
window.innerWidth ×8      getParameter ×1         getTimezoneOffset ×5
permissions.query ×1      indexedDB ×4            navigator.connection ×4
mimeTypes ×2              localStorage ×103       sessionStorage ×25
document.cookie ×10       performance.now ×12
```

The previous survey's figures for this chunk (`local_storage ×103`,
`session_storage ×25`, `cookie ×10`, `error_stack ×21`, `function_source ×76`,
`inner_size ×19`, `user_agent ×16`) are confirmed on SC88 and reproduce
almost exactly on the other two.

Guest-interaction chunk:

```
navigator.userAgent ×5    maxTouchPoints ×2       window.innerWidth ×1
sessionStorage ×1         performance.now ×4      measureText ×1
toDataURL ×2
```

`measureText` and `toDataURL` confirm the font measurement and canvas
hashing described previously.

`chunk-closure-entry-reuse.4y9Syu_n.js` (39128 bytes) contains exactly one
`new Worker` — the CPU benchmark entry point, matching
`optimized-chunk-device-score` spawning
`/assets/benchmark.worker-BCVIEdOS.js`.

---

## 2. ON68 (`oon6a1.ink`)

A WPS / uni-app application, structurally different from the Lobby group.

### 2.1 ThumbmarkJS

✅ The CDN URL is confirmed, and it is loaded **at runtime**, not present in
the HTML. The static HTML of `/home/register` is 9113 bytes and contains no
ThumbmarkJS reference; the script is injected by the app bundle
`chunk-web-view.310d3533.js`:

```javascript
L("https://cdn.jsdelivr.net/npm/@thumbmarkjs/thumbmarkjs/dist/thumbmark.umd.js", "ThumbmarkJS")
```

The live DOM confirms the tag is present at runtime. The library resolves
and is **version 1.13.0** (36240 bytes).

The app registers one extra component on top of the defaults:

```javascript
t.includeComponent("incognito", function () {
  return detectIncognito().then(function (e) { return e.isPrivate })
                              .catch(function () { return !1 });
})
```

⚠️ Correction on the component list: the previous survey listed sixteen
named groups (`canvasReadbackDiffBytes`, `webgl`, `audio`, `fonts`,
`hardware`, `locales`, `math`, `system`, `permissions`, `plugins`,
`speech`, `screen`, `webrtc`, `mediaDecode`, `adBaitBlocked`, and the
`ja4` / `tls.extensions` extras). Those identifiers **do** all exist in the
shipped library, but the library's own `components` object is built
dynamically from the resolved component map, and the identifiers that are
literally present as string literals in the UMD build are only
`canvas`, `audio`, `webgl`, `fonts`, `plugins`, `permissions` and `speech`.
The rest are reached through the component registry rather than as literals.
The list is therefore accurate as a description of what ThumbmarkJS
collects, but it is **not** a literal enumeration of the current build.

Confirmed present in the shipped UMD build:

```
canvasReadbackDiffBytes   commonPixelsHash    sampleHash
ja4   ja4_source   tls    adBaitBlocked        mediaDecode
applePayVersion           jsHeapSizeLimit      videocard
webrtc                    speech               math
locales                   fonts                audio
webgl                     hardware             system
screen                    plugins              permissions
```

Sending path, quoted from the library:

```javascript
const ee = "visitor_id";
function te(e) { return e.storage_property_name ? e.storage_property_name : e.property_name_factory(ee) }
const ne = `${n}_${ee}`;   // localStorage key = "<name>_visitor_id"
...
if ("undefined" != typeof navigator && "function" == typeof navigator.sendBeacon) {
  let e = !1;
  try { e = navigator.sendBeacon(r, a) } catch (t) { e = !1 }
  if (e) return void Z();
}
try {
  const e = fetch(r, { method: "POST", mode: "no-cors", keepalive: !0,
                       headers: { "Content-Type": "text/plain;charset=UTF-8" }, body: a });
  Z(), e.catch(() => {});
} catch (e) {}
```

with `r = "collect.thumbmarkjs.com/beacon"`. So the survey's description of
the send mechanism and of the `<name>_visitor_id` key is ✅ correct.

### 2.2 ClientJS is a second fingerprint library — the survey missed it

❌ **Correction, and the most substantive change in the ON68 section.**
ON68 does not use only ThumbmarkJS. It loads **two** fingerprint libraries
and fails over between them. From `chunk-web-view.310d3533.js`:

```javascript
// ThumbmarkJS
L("https://cdn.jsdelivr.net/npm/@thumbmarkjs/thumbmarkjs/dist/thumbmark.umd.js", "ThumbmarkJS")

// ClientJS — two mirrors, tried in order
L("https://cdnjs.cloudflare.com/ajax/libs/ClientJS/0.2.1/client.base.min.js", "ClientJS")
L("https://cdn.jsdelivr.net/npm/clientjs@0.2.1/dist/client.base.min.js", "ClientJS")
```

and the selection logic:

```javascript
if (!window.ThumbmarkJS) { ...load thumbmark... }
if (!window.ClientJS)   { ...load clientjs... }
...
throw new Error("Failed to load any fingerprinting library");
```

Both CDN mirrors were confirmed to resolve on the verification run:

| Script | Status | Size |
|---|---|---|
| `cdn.jsdelivr.net/npm/@thumbmarkjs/thumbmarkjs/dist/thumbmark.umd.js` | 200 | 36240 |
| `cdnjs.cloudflare.com/ajax/libs/ClientJS/0.2.1/client.base.min.js` | 200 | 29023 |
| `cdn.jsdelivr.net/npm/clientjs@0.2.1/dist/client.base.min.js` | 200 | 29023 |

**This matters for NOVA.** The two libraries read different surfaces.
ThumbmarkJS is listed in §2.1. ClientJS 0.2.1 is a separate library with
its own component set, and a profile that satisfies ThumbmarkJS's checks
may still be inconsistent under ClientJS's. Both must be satisfied, because
the code prefers whichever loads first and does not compare them.

### 2.3 Encryption

✅ Confirmed:

```html
<script src="https://www.oon6a1.ink/js/encrypt.js?v=32440"></script>
```

72394 bytes on the verification run (the survey said 72 KB — correct).

### 2.4 Identification

✅ Re-confirmed on **2026-11-22** from the live `localStorage` and
`document.cookie` of `www.oon6a1.ink`:

```
cookie:       SHELL_deviceId = 653599d5-3bd9-4d16-b75b-5d4a049324cc
localStorage: SHELL_deviceId            = 653599d5-3bd9-4d16-b75b-5d4a049324cc
localStorage: __p_preRegisterVisitorUuid = 92a2f928-796b-4c27-ba0f-a7579067acbb
localStorage: merchantCode = op88vndkf2
localStorage: MC_JS_CACHE_HASH = 1108103
localStorage: MC_VERSION = 00577ec0
```

✅ The full `localStorage` key list was captured (29 keys):

```
lang, MC_currencySymbol, MC_currencyPosition, currencySymbol,
__p_preRegisterVisitorUuid, PWA_updateHash, merchant, MC_VERSION,
currencyPosition, MC_currency, SHELL_imageDomain, SHELL_sdkCDN,
SHELL_deviceId, SHELL_imageFormat, status, hisLang, __p_language,
SHEEL_resources, MC_JS_CACHE_HASH, currency, firstOpen, MC_JS_CACHE,
mc_lang, SHELL_favicon, SHELL_splash, language, merchantCode
```

`SHELL_deviceId` is confirmed as the identifier read by the app: it appears
once in `chunk-web-view.310d3533.js` and once in `m/app.280ba8dc.js`.

`MC_JS_CACHE` holds the application's own source; `MC_JS_CACHE_HASH` is its
hash; `MC_VERSION` is `00577ec0`. ✅ all confirmed.

### 2.5 The registration payload

✅ Re-confirmed on **2026-11-22**. The register component builds its payload
in `dataParams(e)` in `m/app.280ba8dc.js`:

```javascript
dataParams(e) {
  let t = /AppVer|AppShellVer/.test(navigator.userAgent) ? "APP" : "H5";
  if ("standalone" in navigator && navigator.standalone &&
      (t = window.localStorage.getItem("WEBCLIP") ? "WEBCLIP" : "PWA"))
  return {
    countryDialingCode: this.state.countryDialingCode,
    registerUrl: window.location.hostname,
    registerMethod: t,
    username: e.username,
    password: e.password,
    confirmPassword: e.password,
    affiliateCode: e.affiliateCode || this.state.urlParams.code,
    paymentPassword: e.paymentPassword ? e.paymentPassword : "",
    recommenderId: e.recommenderId ? e.recommenderId : "",
    nickname: e.nickname ? e.nickname : "",
    payeeName: e.payeeName ? e.payeeName : "",
    email: e.email ? e.email : "",
    mobileNum: e.mobileNum1 || e.mobileNum2 ? (e.mobileNum1 ? e.mobileNum1 : e.mobileNum2) : "",
    idNumber: e.idNumber ? e.idNumber : "",
    qqNum: e.qqNum ? e.qqNum : "",
    facebook: e.facebook ? e.facebook : "",
    wechat: e.wechat ? e.wechat : "",
    line: e.line ? e.line : "",
    whatsapp: e.whatsapp ? e.whatsapp : "",
    zalo: e.zalo ? e.zalo : "",
    birthday: e.birthday || "",
    captcha: e.identifying ? e.identifying : "",
    type: 0,
    domain: this.state.urlParams.domain ? this.state.urlParams.domain : "",
    verificationCode: e.messageCode1 || e.messageCode2
      ? (e.messageCode1 ? e.messageCode1 : e.messageCode2) : ""
  }
}
```

This is the field list the previous survey did not record. Notes for NOVA:

* `registerMethod` is derived from the **user agent** (`APP` / `H5` /
  `PWA` / `WEBCLIP`). A desktop Chrome UA yields `H5`; an Android UA
  containing `AppShellVer` yields `APP`.
* `registerUrl` is `window.location.hostname` — the page's own host.
* `confirmPassword` is a copy of `password`; it is not re-entered.
* The captcha value rides in the `captcha` field, sourced from
  `e.identifying` — the name used by the captcha widget.
* There are nine optional social/contact fields (`facebook`, `wechat`,
  `line`, `whatsapp`, `zalo`, `qqNum`, `idNumber`, `payeeName`,
  `recommenderId`) alongside the mandatory `username` / `password`.

The corresponding endpoints, from the same bundle:

```
member/register
member/register/mobile
member/register/autoUsername
member/register/mail
```

### 2.6 Three captcha providers, all live in the same build

✅ Re-confirmed on **2026-11-22**. The three bundles named in the previous
survey still exist, with the same sizes, and still contain the same
markers:

| Bundle | Size | Markers found |
|---|---|---|
| `/m/vendor.encrypt.v2.dll.js` | 852953 | `encrypt` ×20 |
| `/m/chunk-common.bc1682a7.js` | 195780 | `captcha` ×68 |
| `/m/app.280ba8dc.js` | 1668863 | `initGeeGuard` ×1, `initBotion` ×1, `_recaptchaVerifier` ×6, `SHELL_deviceId` ×1, `captcha` ×63 |

The same markers were found independently in the **live** bundles that the
page actually loads today:

| Bundle | Size | Markers found |
|---|---|---|
| `/js/index.65c4d8f0.js` | 1736230 | `geetest` ×5, `captcha` ×38 |
| `/js/chunk-vendors.09a45178.js` | 1582955 | `recaptcha` ×49, `grecaptcha` ×7, `jsHeapSizeLimit` ×2, `indexedDB` ×10 |
| `/js/chunk-web-view.310d3533.js` | 1022076 | `initGeeGuard` ×1, `initBotion` ×1, `_recaptchaVerifier` ×6, `thumbmark` ×5, `SHELL_deviceId` ×1, `geetest` ×66 |

✅ The survey's conclusion — all three providers coexist in one build — is
confirmed, and the surrounding code shows what each is for:

**GeeTest** (`initGeeGuard`), with an app id and a 5-second timeout:

```javascript
let o = yield new Promise(function (e) {
  window.initGeeGuard({
    appId: r,
    protocol: "".concat(location.protocol, "//"),
    networkTimeout: 5e3
  }, e)
});
```

**Botion** (`initBotion`), with a captcha id and a 5-second timeout:

```javascript
window.initBotion({
  method: window.initBotion,
  parameters: { captchaId: e, product: "bind", language: t, hideSuccess: !0, timeout: 5e3 }
})
```

**Firebase reCAPTCHA** (`_recaptchaVerifier`), used specifically for the
Firebase SMS verification path:

```javascript
console.log("send sms firebase start"),
t.smsResult = yield Object($.j)(Object($.f)(), o, window._recaptchaVerifier),
console.log("send sms firebase success", o)
```

So the three are not three interchangeable choices: GeeTest and Botion are
the visual captchas, and Firebase reCAPTCHA guards the SMS send.

⚠️ The survey did not previously mention Botion at all in §2.1; it is now
confirmed as the second provider.

### 2.7 The `version-hash` commit element — not found

❌ **Correction.** The previous survey quoted:

```html
<div class="version-hash" style="display:none;" data-commit="abdb89c0d6e26bb47972d482b85f842102dccb20"></div>
```

This element is **not present** in the current build. Every page was
checked — `/`, `/home/register`, `/home/index`, `/home/login` — and all four
return the same 9113-byte shell in which:

* `data-commit` does not appear at all,
* the string `version-hash` does not appear,
* the string `abdb89c0d6e26bb47972d482b85f842102dccb20` does not appear,
* and no element in the live DOM carries a `data-commit` attribute.

✅ The build brand string **is** still present and unchanged:

```html
<script>var brand="32440_2026-11-03 22:27:55";console.log("brand",brand)</script>
```

### 2.8 Other anti-fraud measures

✅ Confirmed:

* `html2canvas` from cdnjs — `https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js`,
  197782 bytes, loads successfully.
* Cloudflare Web Analytics — `https://static.cloudflareinsights.com/beacon.min.js/vce701823e78c4301a35b7e07294d87301792088814092`.
* `SHELL_sdkCDN` → `https://images.8846148.com/`,
  `SHELL_imageDomain` → `https://images.7814157.com/`.
* The `status` key carries the server's gate state. Live value:
  `{"success":true,"value":{"maintenance":false,"regionRestriction":false,"whitelist":false,"test":false,...}}`.

### 2.9 Brand aliasing

✅ Confirmed. The page title is "ON68", `merchantCode` is `op88vndkf2`, and
the canonical URL in the page's own `og:url` / `link rel=canonical` is
**`https://www.on68688.com/`** — a third domain for the same application.
The favicon and image paths are under
`images.7814157.com/wsd-images-prod/op88vndkf2/`.

So the brand name, the merchant code, the canonical domain and the serving
domain are four different strings. The previous survey's warning holds and
is now sharper.

---

## 3. Jun88 (`8jjunsyj.cc`)

A Vue SPA, different from both other groups.

### 3.1 What was measured

✅ The Cloudflare Web Analytics beacon is confirmed:

```html
<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/vce701823e78c4301a35b7e07294d87301792088814092" integrity="sha512-..."></script>
```

The file is named like a hash but is Cloudflare's beacon, not a fingerprint
library. ✅ confirmed.

All scripts loaded by the page:

```
https://static.cloudflareinsights.com/beacon.min.js/vce701823e78c4301a35b7e07294d87301792088814092
https://www.8jjunsyj.cc/webpush/webSdk.produce.min.3.3.5.js
https://www.8jjunsyj.cc/static/js/runtimechunk~main.b1fcff08.js
https://www.8jjunsyj.cc/static/js/vendor-moment.31f5b79e.js
https://www.8jjunsyj.cc/static/js/r16-dom.9673ff4f.js
https://www.8jjunsyj.cc/static/js/vendor.5882b22c.js
https://www.8jjunsyj.cc/static/js/main.22489a53.js
```

### 3.2 No captcha library, no fingerprint library

✅ **Confirmed, and stronger than before.** Every one of the five
first-party bundles was fetched and grepped for
`turnstile`, `challenges.cloudflare.com`, `nrc-invisible`, `siteverify`,
`geetest`, `initGeeGuard`, `botion`, `initBotion`, `recaptcha`,
`_recaptchaVerifier`, `grecaptcha`, `fingerprintJS`, `thumbmark`:

| Bundle | Size | Matches |
|---|---|---|
| `main.22489a53.js` | 382269 | none |
| `r16-dom.9673ff4f.js` | 198358 | none |
| `vendor.5882b22c.js` | 204447 | none |
| `vendor-moment.31f5b79e.js` | 65426 | none |
| `runtimechunk~main.b1fcff08.js` | 27143 | none |

The only markers present anywhere are `toDataURL` ×2 in `main.js` (canvas
hashing) and `Function.prototype.toString` ×1.

### 3.3 The surface counts are lower than previously recorded

❌ **Correction.** The previous survey recorded
`user_agent ×5, user_agent_data ×1, screen_size ×4, pixel_ratio ×2,
inner_size ×5, to_data_url ×2, session_storage ×1, performance_timing ×2,
function_source ×21`. Re-measured across **all five** bundles on
**2026-11-22**:

| Surface | Measured total | Previously recorded |
|---|---|---|
| `navigator.userAgent` | 7 | 5 |
| `localStorage` | 5 | 5 |
| `getTimezoneOffset` | 4 | — |
| `getContext` | 3 | — |
| `document.cookie` | 3 | 3 |
| `toDataURL` | 2 | 2 |
| `sessionStorage` | 2 | 1 |
| `window.innerWidth` | 1 | 5 |
| `performance.now` | 1 | 2 |
| `screen.width` / `screen.height` | **0** | 4 |
| `devicePixelRatio` | **0** | 2 |
| `userAgentData` / `getHighEntropyValues` | **0** | 1 |
| `navigator.hardwareConcurrency` | **0** | — |
| `navigator.deviceMemory` | **0** | — |
| `getParameter` (WebGL) | **0** | — |
| `measureText` | **0** | — |

✅ The substantive conclusion is unchanged and is if anything stronger:
Jun88 reads **no** `screen.size`, **no** `devicePixelRatio`, **no**
`userAgentData`, **no** `hardwareConcurrency`, **no** `deviceMemory`, **no**
WebGL renderer, **no** font measurement. The `toDataURL` ×2 remains the one
canvas hash.

The previously recorded `function_source ×21` did not reproduce as
`Function.prototype.toString` (measured ×1 in `main.js`). The generic
`toString` token appears 45 / 38 / 49 times across the three bundles, but
that is ordinary string handling, not function-source probing. ❌ the
"21 calls" figure is not reproducible and should not be relied on.

### 3.4 `localStorage`

✅ Confirmed exactly — the same six keys, no more, no fewer:

```
tiktok-ttp, mtWebPushRandomUid, imsPublic_numberFormatSeparators,
lg, locale, mt_push_init_data
```

There is no `fingerId` and no device-risk score. ✅ confirmed.

### 3.5 Registration

✅ `/home/register` serves the **home page**, not a registration form. The
live accessibility tree of `https://www.8jjunsyj.cc/home/register` shows
the game lobby (CASINO, NỔ HÙ, BẮN CÁ, THỂ THAO, GAME BÀI, E-SPORTS, VIP,
floating action buttons), with **zero textboxes and zero input elements**.
The page also carries `Chính Sách AML` and `CLB VIP Jun88`, and the
footer states:

> "jun88.id được sở hữu và vận hành bởi Cyber Labs B.V., một công ty được
> đăng ký tại Curaçao với Mã số doanh nghiệp 166785... Curaçao Gaming
> Authority - CGA cấp phép và quản lý với Giấy phép số: OGL/2024/1696/1055,
> được cấp vào ngày 13 tháng 3 năm 2025."

✅ The licence number `OGL/2024/1696/1055` and the AML policy are confirmed.

### 3.6 The registration spec returned by the server

✅ Re-confirmed on **2026-11-22**. The application calls a public API that
needs no session:

```
GET https://api.8jjunsyj.cc/jun88-ecp/api/v1/register/setting   → 200, 31942 bytes
GET https://api.jun8808.com/jun88-ecp/api/v1/register/setting    → 200, 31942 bytes (identical)
```

❌ **Correction: 17 fields, not 18.** `regsettingmap` contains exactly 17
keys. The 5 required fields are confirmed unchanged:

| Group | Fields |
|---|---|
| Required | `mobile`, `password`, `firstname`, `captcha`, `playerid` |
| Optional | `currency`, `email`, `confirmpassword`, `birthday`, `pin`, `confirmpin`, `pic1`, `pic2`, `im1`, `im2`, `affiliateid`, `ulagentaccount` |

The flags are confirmed, with one addition:

```
allowsameemail = false
allowsamemobile = false
allowsameip    = true
allowsamename  = true      ← not previously recorded
```

`captcha_type = IMG_CAPTCHA` — ✅ confirmed. `playerid` is required, has
`minlength: "5"`, `maxlength: "12"`, `numeric: true` and `alphabet: true`,
and is browser-generated. ✅ confirmed.

`captcha` itself carries only `{"required":true,"apirequired":true,"display":true}`
— no provider hint.

---

## 4. `linkantoan.vip`

### 4.1 The page now opens — the previous "cannot be opened" is wrong

❌ **Correction.** The previous survey recorded:

```
https://linkantoan.vip/  →  SSL: UNEXPECTED_EOF_WHILE_READING
http://linkantoan.vip/   →  Remote end closed connection without response
```

and concluded the site was unreachable from this machine.

**In a real browser the site opens fine.** On **2026-11-22** the front page
`https://www.linkantoan.vip/` loaded and rendered completely — 78win
branding, the full game lobby (CASINO, JACKPOT, FISH SHOOTING, CARD GAMES,
COCKFIGHTING, SPORTS, LOTTERY, E-SPORTS, PROMOTIONS, VIP, BRAND
AMBASSADOR), and the navigation. `/home/register` also loads.

The TLS failure is real but **client-specific**. A non-browser TLS client
(OpenSSL via Python) still fails on every attempt:

```
https://www.linkantoan.vip/  →  [SSL: UNEXPECTED_EOF_WHILE_READING]   (3/3 attempts)
http://linkantoan.vip/       →  Remote end closed connection without response
http://www.linkantoan.vip/    →  [SSL: UNEXPECTED_EOF_WHILE_READING]
```

This is Cloudflare's TLS fingerprinting: the SNI is accepted and the
connection is established, then closed during the handshake for a client
whose ClientHello does not look like a browser. **It is not an IP-region
block.** DNS resolves to Cloudflare addresses, as the survey said:

```
linkantoan.vip     -> 104.18.4.14, 104.18.5.14
www.linkantoan.vip -> 104.18.4.14, 104.18.5.14
api.linkantoan.vip -> 104.18.4.14, 104.18.5.14
```

and `api.linkantoan.vip` serves a plain API response over the same TLS
stack:

```
GET https://api.linkantoan.vip/vi5prod-ecp/api/v1/register/setting  → 200, 32312 bytes
```

✅ The survey's API claim is correct. ⚠️ The survey's inference that this
is a Cloudflare regional/IP block is wrong — the API host, on the same
Cloudflare addresses, answers normally, and a browser answers normally.

### 4.2 The site is a Jun88-family clone, branded 78win

✅ Confirmed. `www.linkantoan.vip` uses the same Vue stack and the same
`vi5prod-ecp` API as Jun88:

| | Jun88 (`8jjunsyj.cc`) | linkantoan.vip |
|---|---|---|
| Page title | Jun88 Trang chủ chính thức Jun88.com | 78win - Trang chủ chính thức 78win |
| Runtime chunk | `runtimechunk~main.b1fcff08.js` | `runtimechunk~main.a65f075f.js` |
| `r16-dom` | `9673ff4f.js` | `9673ff4f.js` (identical) |
| `vendor` | `5882b22c.js` | `5882b22c.js` (identical) |
| `main` | `main.22489a53.js` | `main.f21ec10e.js` |
| API prefix | `jun88-ecp` | `vi5prod-ecp` |
| API host in page | `api.jun8808.com` | `api.78win2.cc` |
| `localStorage` keys | the same 6 | the same 6 |
| Static assets | `img.alltocon.com` | `img.alltocon.com` |
| Brand asset path | — | `img/static/desktop/brand/vi5prod/logo.png` |

The `vi5prod` string appears in linkantoan's own asset paths, confirming the
API prefix and the brand family.

⚠️ One difference worth recording: `api.78win2.cc` returns **403** with a
Cloudflare challenge page, while `api.linkantoan.vip` returns **200** with
the JSON. Both point at the same backend.

### 4.3 The registration spec is identical to Jun88 — but with 17 fields

✅ Re-confirmed on **2026-11-22**:

```
GET https://api.linkantoan.vip/vi5prod-ecp/api/v1/register/setting  → 200, 32312 bytes
```

❌ **Correction: 17 fields, not 18** — same as Jun88, with the same 5
required fields and the same flags:

```
fields      = 17
required    = playerid, password, captcha, mobile, firstname
optional    = currency, email, affiliateid, confirmpassword, confirmpin,
              im1, im2, pin, birthday, pic1, pic2, ulagentaccount
captcha_type = IMG_CAPTCHA
allowsameemail = false
allowsamemobile = false
allowsameip    = true
allowsamename  = true
```

`playerid` differs from Jun88 in one field: `playerNoOn` is `true` here
(Jun88 has `false`), meaning the player number cannot be reused.

### 4.4 Turnstile — still not confirmed, and now less likely

❌ **Correction to the framing.** The previous survey said the branch
investigation "showed `vi5prod-ecp` uses Cloudflare Turnstile with a hidden
`nrc-invisible` input", and that this could not be confirmed.

On **2026-11-22** the search was run again, more broadly than before, over
the full HTML of `/` and `/home/register` **and** every first-party
bundle:

| Bundle | Size | `turnstile` | `challenges.cloudflare.com` | `nrc-invisible` | `siteverify` |
|---|---|---|---|---|---|
| `main.f21ec10e.js` | 391120 | 0 | 0 | 0 | 0 |
| `vendor.5882b22c.js` | 204447 | 0 | 0 | 0 | 0 |
| `runtimechunk~main.a65f075f.js` | 27045 | 0 | 0 | 0 | 0 |
| page HTML | 124970 | 0 | 0 | 0 | 0 |

The same search on Jun88's bundles returns the same all-zero result.

So the Turnstile claim is ❌ **not supported by any evidence in the current
build**, and the server's own spec says `captcha_type = IMG_CAPTCHA`. The
previous survey's "two sources contradict each other" framing was accurate
as to the contradiction; the resolution is now clearer — there is no
Turnstile marker anywhere in the shipped client. The Turnstile claim should
be treated as **retracted**, not merely unconfirmed.

### 4.5 No Cloudflare analytics beacon on linkantoan

⚠️ Note a difference from Jun88: linkantoan's page loads **no**
Cloudflare Web Analytics beacon (`cloudflareinsights` count 0), while
Jun88's page does. The `vi5prod` brand ships analytics; the `78win` build
of the same code does not.

---

## 5. Cloudflare

Four of the six sites sit behind Cloudflare (`c168j.vip`, `8jjunsyj.cc`,
`linkantoan.vip`, `oon6a1.ink`). DNS confirms:

```
c168j.vip      -> 104.18.26.248, 104.18.27.248      (Cloudflare)
8jjunsyj.cc    -> 104.21.15.157, 172.67.163.25      (Cloudflare)
linkantoan.vip -> 104.18.4.14, 104.18.5.14          (Cloudflare)
oon6a1.ink     -> 104.21.19.149, 172.67.186.133     (Cloudflare)
m.ssc8111.vip  -> 3.162.58.21, 3.162.58.5, ...      (AWS CloudFront)
yaccm8h.cc     -> 107.155.62.45 ... 107.155.62.52    (Alibaba CDN)
```

Cloudflare Bot Management does things that the page code does not do:

* JA3 and JA4 fingerprints of the TLS ClientHello.
* Cloudflare's own challenge point, with the `__cf_bm` cookie.
* Behavioural analysis.

⚠️ None of this is visible in the page. The only Cloudflare artefact
measurable from the page is the Web Analytics beacon, which is a separate
product from Bot Management.

The consequence for NOVA is unchanged: **changing the TLS layer changes
JA3/JA4, and the two layers have to agree.** A profile that claims to be
an Android phone but speaks TLS from a Windows desktop is exposed at that
layer.

---

## 6. Hidden surfaces

This section collects the measurement techniques that are not obvious from
reading a page — the ones that were found during the 2026-11-22
verification and that a reader would otherwise have to rediscover.

### 6.1 The device score gates page behaviour, not just a number

✅ Covered in §1.4. The full list of seventeen behaviours that are silently
disabled on a low-scoring device is in §1.4. This is the single most
consequential hidden surface in the Lobby group: it is observable from the
outside as a difference in page behaviour, not as a field in a request.

### 6.2 There is a second GPU fast path, not just `pc-fast-path`

✅ Covered in §1.4. `market-low-model` and `android-hardware-fast-path`
fire before `pc-fast-path`. A NOVA profile that is an Android phone is
routed by the **Android** path, which keys on the GPU renderer's
`matchedKey` and `rendererConfidence` — and if the renderer is not in the
map (`confidence: "none"`, exactly what the survey's own sample data
shows), the profile takes the slow four-axis path instead.

### 6.3 The API host is base64-obfuscated in the page

✅ Covered in §1.10. `jdeu2f-2868-ppp.oss-accelerate.aliyuncs.com`,
split across a three-entry substitution table. A survey that reads the
page for `api.` URLs finds nothing; a survey that reads the decoded table
finds the OSS bucket.

### 6.4 The API traffic is encrypted

✅ Covered in §1.10. `/cocos/config_data.json` returns ciphertext. The
startup chunk carries a `cryptoKey` and a `crypto: {enable: true}` request
option. So the Lobby group is not observable by replaying its API calls
without the key.

### 6.5 `LobbyDeviceCompatibility` lives in the HTML, not in a bundle

✅ Covered in §1.5. Searching the JavaScript finds nothing — the
whitelist and the `matches()` function are in an inline `<script>` in the
page. This is why the previous survey could not locate it.

### 6.6 There is a second fingerprint library on ON68

✅ Covered in §2.2. ClientJS 0.2.1, with two CDN mirrors, failing over
against ThumbmarkJS.

### 6.7 The device score runs in a separate Worker

✅ Covered in §1.11. `/assets/benchmark.worker-BCVIEdOS.js` (2290 bytes)
reports `sort`, `cpuPure`, `cpuThroughput`, `decode` and `multiRound`. The
`sort` and `cpu` axes are therefore measured in a Worker context, not in
the main thread — so any override that behaves differently in a Worker is
visible.

### 6.8 The device score has a hard-coded fallback

⚠️ Covered in §1.4. A profile that never finishes the score run is scored
35 / `LOW` / `not-ready-low-fallback` and trips every degradation gate.
This makes "the score never arrives" worse than "the score is low".

### 6.9 The device-score chunk can be re-run and overridden

✅ The chunk reads a query parameter `mock.deviceScoreV2MultiRound` and a
`mock.deviceScoreV2` value, and `mock.deviceScoreEveryTime` /
`mock.deviceScoreV2EveryTime` force a re-run. These are test hooks left in
production code. They are useful for verification, and they are also a way
for a site to detect a visitor who has read the source.

### 6.10 A device redirect rewrites the URL from the same signals

✅ Covered in §1.5. `__device_redirect` in the query string, and a rewrite
between `/` and `/pc/`. A profile whose UA and viewport disagree will be
moved between the two layouts.

### 6.11 ON68's `registerMethod` is derived from the user agent

✅ Covered in §2.5. `APP` / `H5` / `PWA` / `WEBCLIP`, decided by
`/AppVer|AppShellVer/.test(navigator.userAgent)` and the
`navigator.standalone` flag. A desktop UA yields `H5`; a patched Android
UA that contains `AppShellVer` yields `APP`, and the two take different
server-side paths.

### 6.12 The `zxe` name collides

✅ Covered in §1.6. On `c168j.vip` the identifier `zxe` refers to a
completely unrelated function in the same chunk. Grep-based surface
analysis must match on the call shape, not the name.

---

## 7. How to re-measure

The original tooling lives in `.dataset_probe/`:

* `survey_page.py` — fetch a page, list the form fields, find embedded
  code, download the script files, then count the browser-API calls in each
  file.
* `detail_script.py` — print the code around each call, so the way the
  value is obtained and sent can be read.
* `list_components.py` — list the component groups of a fingerprint library.

Results are stored in `.dataset_probe/survey/`.

### 7.1 What the 2026-11-22 pass added

* **Decompress gzip.** All three Lobby sites serve gzip-encoded HTML.
  A fetch that does not decompress sees a binary blob and finds no
  markers. This single mistake makes `yaccm8h.cc` look empty.
* **Fetch the bundles, not just the HTML.** The most important findings in
  this pass — the device-score chunk, the `pc-fast-path` implementation,
  `initBotion`, the ClientJS loader, the obfuscated API host — are only
  visible in the JavaScript.
* **Read the response headers.** `Server:`, `x-oss-*`, `Ali-Swift-*` and
  `Server: cloudflare` settle the CDN question directly, and DNS settles
  it independently.
* **Try both a browser and a plain TLS client.** `linkantoan.vip` is
  unreachable from Python and fully reachable from a browser; the two
  channels disagree, and the disagreement is itself the finding.
* **Search for the marker in the whole page, not only in the script
  files.** `LobbyDeviceCompatibility` is inline HTML.
* **Decode base64 before giving up on a host.** The Lobby API host is
  stored as base64 in a substitution table.

---

## 8. Still not done

* No account has actually been registered on any of the six sites, so
  which fields the server enforces beyond the published spec is still
  unknown.
* The `getFingerprint` response body could not be captured (§1.3), so the
  exact shape of what the server computes from FingerprintJS is unknown.
* The Turnstile claim for `vi5prod-ecp` is **retracted** (§4.4), not
  merely unconfirmed. If Turnstile is used, it is applied below the
  application layer.
* Cloudflare's own decisions were not measured. This survey can only see
  the page; it cannot see the edge.
* NOVA's own JA3/JA4 has not been measured against the edge. That is the
  next task.
* The `linkantoan.vip` page was read in a browser, but its bundles were
  fetched through the same path as Jun88's because a plain TLS client
  cannot reach the site. The two clones share `vendor.5882b22c.js`
  byte-for-byte, so the shared code is verified, but linkantoan's own
  `main.f21ec10e.js` was fetched successfully and checked separately.
