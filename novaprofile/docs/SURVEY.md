# Khảo sát cơ chế chống bot của sáu trang đăng ký

Tài liệu này ghi lại những gì đo được trên sáu trang đăng ký mà
NOVA PROFILE nhắm tới. Mọi thứ trong đây là quan sát, không phải suy
đoán: mỗi mục đều kèm theo nơi lấy ra.

Bảng tổng hợp:

| Trang | Nền tảng | CDN | Fingerprint | Điểm rủi ro thiết bị |
|---|---|---|---|---|
| `m.ssc8111.vip` | Lobby (Cocos) | CloudFront | FingerprintJS (public key) + Geetest | `lobby@device@score@v2` |
| `c168j.vip` | Lobby (Cocos) | Cloudflare | Cùng bộ với SC88 | Cùng bộ với SC88 |
| `yaccm8h.cc` | Lobby (Cocos) | Aliyun | Cùng bộ với SC88 | Cùng bộ với SC88 |
| `8jjunsyj.cc` | Vue tự dựng | Cloudflare | Cloudflare Bot Management | Không có điểm thiết bị |
| `linkantoan.vip` | Không mở được | Cloudflare | Cloudflare WAF | Không đo được |
| `oon6a1.ink` | WPS/uni-app | Cloudflare | **ThumbmarkJS** + `encrypt.js` | `SHELL_deviceId` |

### Số đo được trên mã trang

Ba bảng dưới đây là số lần gọi API trình duyệt đếm được trong các
tệp tập lệnh của từng trang. Chúng là số đo, không phải ước lượng;
tệp đo nằm ở `.dataset_probe/survey/`.

`m.ssc8111.vip` — 9 bề mặt:

| Bề mặt | Số lần |
|---|---|
| `user_agent` | 8 |
| `screen_size` | 5 |
| `function_source` | 4 |
| `max_touch_points` | 3 |
| `inner_size` | 4 |
| `local_storage` | 4 |
| `session_storage` | 1 |
| `pixel_ratio` | 1 |
| `canvas_2d` | 1 |

`oon6a1.ink` — 19 bề mặt, nhiều gấp bốn lần:

| Bề mặt | Số lần | | Bề mặt | Số lần |
|---|---|---|---|---|
| `session_storage` | 326 | | `canvas_2d` | 24 |
| `local_storage` | 166 | | `error_stack` | 24 |
| `user_agent` | 49 | | `to_data_url` | 17 |
| `function_source` | 86 | | `indexed_db` | 14 |
| `inner_size` | 26 | | `performance_timing` | 6 |
| `cookie` | 19 | | `pixel_ratio` | 5 |
| `platform` | 4 | | `avail_size` | 2 |
| `user_agent_data` | 4 | | `canvas_webgl` | 2 |
| `max_touch_points` | 7 | | `screen_size` | 1 |
| `fonts` | 1 | | | |

`8jjunsyj.cc` — 11 bề mặt:

| Bề mặt | Số lần | | Bề mặt | Số lần |
|---|---|---|---|---|
| `function_source` | 30 | | `cookie` | 3 |
| `user_agent` | 15 | | `canvas_2d` | 2 |
| `error_stack` | 14 | | `to_data_url` | 2 |
| `inner_size` | 5 | | `session_storage` | 2 |
| `local_storage` | 5 | | `performance_timing` | 4 |
| `user_agent_data` | 4 | | | |

Điểm đáng chú ý nhất trong ba bảng trên là `function_source`: 4 lần ở
SC88, 30 lần ở Jun88 và **86 lần** ở ON68. Đây là dấu vân tay của
`fn.toString()`, và nó xuất hiện nhiều nhất ở đúng trang dùng
ThumbmarkJS.

## 1. Nhóm Lobby: SC88, c168j, yaccm8h

Ba trang này chạy cùng một ứng dụng, nên cơ chế của chúng giống
nhau và đo được trên cả ba.

### 1.1 Trường đăng ký

```
Tài khoản            (placeholder: "Nhập Tên tài khoản")
Mật khẩu             (placeholder: "Nhập mật khẩu")
Họ và tên thật        (placeholder: "Vui lòng nhập Họ và Tên thật của bạn")
[X] Tôi trên 18 tuổi, đã đọc và đồng ý với Thỏa thuận đăng ký người dùng
```

Không có trường email hay số điện thoại. Có Google Sign-In và
Facebook (app id `SC88.COM`) làm đường đăng nhập thứ ba. Có một
trường `cpfValidateType` với giá trị `2`, và một trường
`inviter_code_required` bằng `0`, nên mã giới thiệu là tuỳ chọn.

### 1.2 FingerprintJS

Trong cấu hình trang có:

```json
"fingerprintJS": { "publicKey": "cIMrDd2qJKZFByajXD7O" }
```

Đây là FingerprintJS mã nguồn mở, dùng khóa công khai để mã hoá
kết quả trước khi gửi. Nó đọc: canvas, WebGL, audio, fonts, màn
hình, CPU, bộ nhớ, timezone, plugins.

#### Kiểm chứng lại: cùng một khóa trên cả ba trang

Đo lại trực tiếp ngày 2026-11-22 trên ba trang, mỗi trang trả về
đúng một khóa:

| Trang | `fingerprintJS.publicKey` |
|---|---|
| `m.ssc8111.vip` | `cIMrDd2qJKZFByajXD7O` |
| `c168j.vip` | `cIMrDd2qJKZFByajXD7O` |
| `yaccm8h.cc` | `cIMrDd2qJKZFByajXD7O` |

Cả ba cùng một khóa, nên cả ba cùng một tenant FingerprintJS Pro.
Hệ quả thực tế: **một profile NOVA đăng ký trên một trang sẽ bị
nhận diện là cùng khách trên hai trang còn lại**, vì visitor ID
được tính bằng khóa đó và gửi kèm mọi lần đăng ký. Đây là điểm
cần biết khi quyết định dùng chung hay tách profile theo từng
trang. Với profile dùng cho một trang duy nhất thì không sao; với
profile dùng chung thì cần biết sự liên kết này tồn tại.

Không có `fingerId` trong `localStorage` ở tầng này — FingerprintJS
được gọi ở phía server sau khi biểu mẫu gửi, không phải ngay lúc
tải trang.

### 1.3 Geetest

```json
"geetest_captcha_id": {
    "register": "62c528ead784206de7e6db17765b9ac0",
    "login":    "62c528ead784206de7e6db17765b9ac0",
    "recharge": "62c528ead784206de7e6db17765b9ac0",
    "force_geetest": "62c528ead784206de7e6db17765b9ac0"
}
```

`force_geetest` bằng `register` nghĩa là trang đăng ký **luôn**
bắt Geetest, không phải khi nghi ngờ. Geetest đọc chuỗi hành vi
(chuột, gõ, chuyển động) và gửi lại kèm `lot_number` cùng
`captcha_output` đã mã hoá.

### 1.4 Điểm rủi ro thiết bị

Đây là phần đáng chú ý nhất, vì nó là cơ chế **chấm điểm** chứ không
phải chỉ thu thập. Trong `localStorage`:

```json
{
  "lobby@device@score@v2": {
    "version": "2.4.2",
    "result": {
      "score": 90,
      "tier": "HIGH",
      "confidence": "mid",
      "proposed": {
        "score": 90, "tier": "HIGH",
        "earlyReturn": true,
        "source": "pc-fast-path",
        "reasons": ["pc-desktop"]
      }
    },
    "details": {
      "gpu": {
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti (0x00002D04) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "confidence": "none",
        "rendererConfidence": "none",
        "mapVersion": "1.0.3"
      }
    },
    "dimensionScores": {},
    "weights": { "sort": 0.2, "cpu": 0.2, "memory": 0.5, "image": 0.1 },
    "capsApplied": ["pc-fast-path→90"]
  }
}
```

Bốn trục chấm điểm, với trọng số:

| Trục | Trọng số | Nguồn |
|---|---|---|
| `memory` | 0.5 | `navigator.deviceMemory` |
| `sort` | 0.2 | `navigator.hardwareConcurrency` |
| `cpu` | 0.2 | Kiến trúc CPU, số nhân |
| `image` | 0.1 | GPU, canvas |

Giá trị cũ (bản 1) được giữ ở `lobby@device@score@detail`:

```json
{ "score": 100, "details": { "cpu": 100, "memory": 100 } }
```

Vài điểm cần ghi nhớ:

* `earlyReturn` và `source: "pc-fast-path"` nghĩa là khi nhận ra là
  máy tính, hệ thống **thoát sớm** và cho điểm 90 mà không tính
  `dimensionScores` (rỗng). Với một profile giả lập điện thoại, đường
  này không dùng được, và hệ thống sẽ tính đủ bốn trục.
* `gpu.renderer` được đối chiếu với một bản đồ (`mapVersion:
  "1.0.3"`) để suy ra thiết bị thật. `confidence` và
  `rendererConfidence` đều là `"none"` khi bản đồ không nhận ra.
* Điểm `tier` có ba bậc. `HIGH` là bậc cao nhất.

### 1.5 Nhận diện thiết bị

`LobbyDeviceCompatibility` kiểm tra bốn trục cùng lúc, và trục nào
cũng phải khớp:

```javascript
e.userAgent.test(navigator.userAgent)
  && navigator.maxTouchPoints >= e.minTouchPoints
  && window.innerWidth === e.viewportWidth
  && window.screen.width === e.screenWidth
  && window.screen.height >= e.screenHeight.min
  && window.screen.height <= e.screenHeight.max
```

Danh sách trắng chỉ có iPhone Duo (iOS chạy trong cửa sổ trên
iPad). Nhưng cơ chế kiểm tra này áp dụng cho mọi thiết bị, kể cả
khi không có mục nào trong danh sách.

`detectDeviceType` phân biệt máy tính, máy tính bảng và điện thoại
bằng UA, `maxTouchPoints` và `screen.height > screen.width`.

### 1.6 Định danh khách

```
localStorage: web_lobby_next_user_webpush_visitor_fingerId
              = {"data":"4fad10ea-57a4-4dd1-b545-c715388b2ec2","expires":...}
localStorage: web__lobby__persisted__device
              = {"uuid":"64d50e44-69a6-4c46-b3b1-db9ed9094dc3"}
localStorage: web__lobby__persisted__user
              = {..., "fingerId":"", ...}
```

`fingerId` là định danh khách do FingerprintJS sinh ra. Nó được gửi
lên khi đăng ký và lưu vào hồ sơ người dùng.

Có thêm `deviceIdSecurityVerify`, với các phương thức xác thực
email, Google, số điện thoại, câu hỏi bảo mật và mật khẩu rút
tiền. Đây là cách nhà cung cấp gắn một tài khoản với một thiết bị.

### 1.7 Chống gian lận khác

```json
"antiSwitch": "0",
"antiDebugStatus": 0,
"humanVerify": -1
```

`humanVerify: -1` nghĩa là kiểm tra người thật đang tắt ở cấu
hình, nhưng Geetest vẫn bắt ở đăng ký (`force_geetest`).

Ngoài ra còn có:

* `lobby@network-samples` — đo độ ổn định mạng, độ trễ, thông lượng.
* `webLobbyindexedDBRequestStats` — đếm số lần đọc IndexedDB
  theo nhóm độ trễ (`INDEXEDDB:200`, `INDEXEDDB_NORMAL:0`).
  Một người thật không đọc IndexedDB hàng loạt vào lúc đăng ký.
* `lobby@support@media` — kiểm tra định dạng ảnh mà trình duyệt hỗ
  trợ, lưu kết quả để không hỏi lại.
* `web__lobby__persisted__base` — có `skinAssetsHash` với `lastMD5`.

### 1.8 Những gì trang đọc trong mã

Từ `chunk-closure-startup.js` (1,5 MB), những lần gọi tìm thấy:

```
user_agent ×16    user_agent_data ×4    platform ×1
hardware_concurrency ×2                max_touch_points ×7
screen_size ×6    avail_size ×2        color_depth ×1
pixel_ratio ×1    inner_size ×19       webgl_renderer ×1
webgl_params ×1   timezone ×2          permissions ×1
indexed_db ×4     connection ×4        mime_types ×2
local_storage ×103  session_storage ×25  cookie ×10
error_stack ×21  function_source ×76  performance_timing ×14
```

`function_source` (76 lần) và `error_stack` (21 lần) là dấu hiệu
của **phát hiện hàm bị thay**: lấy `fn.toString()` rồi so với mã
nguyên bản của hàm dựng sẵn. Đây là cách phát hiện việc ghi đè
`navigator.*` bằng `Object.defineProperty` hoặc bằng một lớp bọc.

`chunk-closure-entry-reuse.js` tạo một `Worker` — thường là để đo
số nhân CPU mà không chặn giao diện.

`optimized-chunk-guest-interaction.js` có `measureText` và
`toDataURL`, tức là đo phông chữ và băm canvas.

## 2. ON68 (`oon6a1.ink`)

Ứng dụng WPS/uni-app, khác hẳn nhóm Lobby.

### 2.1 ThumbmarkJS

```html
<script src="https://cdn.jsdelivr.net/npm/@thumbmarkjs/thumbmarkjs/dist/thumbmark.umd.js"></script>
```

Đây là thư viện fingerprint mã nguồn mở, và là nguồn nguy hiểm
nhất trong sáu trang. Nó gom **16 nhóm**:

| Nhóm | Nội dung |
|---|---|
| `canvas` | Vẽ gradient, chữ và đường, băm pixel (`commonPixelsHash`) |
| `canvasReadbackDiffBytes` | Đọc cùng một canvas hai lần, so từng byte |
| `webgl` | vendor, renderer, version, shading language |
| `audio` | `sampleHash`, số kênh, chế độ kênh |
| `fonts` | Khoảng 60 phông, đo trong iframe bằng `measureText("WwMmLli0Oo")` |
| `hardware` | `deviceMemory`, `jsHeapSizeLimit`, kiến trúc, `videocard` |
| `locales` | `navigator.language`, timezone qua `Intl` |
| `math` | `Math.acos(0.5)`, `asin`, `sinh` — độ chính xác số thực |
| `system` | `hardwareConcurrency`, `cookieEnabled`, `applePayVersion` |
| `permissions` | Danh sách quyền đầy đủ |
| `plugins` | Danh sách plugin |
| `speech` | Giọng đọc của hệ thống |
| `screen` | Kích thước, `avail`, `colorDepth`, `pixelRatio` |
| `webrtc` | Địa chỉ qua ICE |
| `mediaDecode` | Thử giải mã một số định dạng (`avc1.42E01E`, `vp09.00.10.08`) |
| `adBaitBlocked` | Có bị chặn quảng cáo hay không |

Ngoài ra nó còn thu:

* `ja4` — dấu vân tay TLS JA4, và `ja4_source` nói dấu vân tay đó đến từ đâu.
* `tls.extensions` — danh sách phần mở rộng TLS.
* `header.acceptLanguage` — cả giá trị lẫn trọng số `q`.
* `ip` — địa chỉ (bị loại khi dùng VPN).

Cách gửi: `navigator.sendBeacon` tới `collect.thumbmarkjs.com/beacon`,
nếu không thì `fetch` với `mode: "no-cors"` và `keepalive: true`.
Giá trị định danh được lưu ở `localStorage` với khoá dạng
`<tên>_visitor_id`.

### 2.2 Mã hoá

```html
<script src="https://www.oon6a1.ink/js/encrypt.js?v=32440"></script>
```

72 KB. Cùng với `fingerprintJS.publicKey` ở nhóm Lobby, đây là
việc mã hoá kết quả fingerprint trước khi gửi: không ai đọc
được nội dung nếu không có khóa.

### 2.3 Định danh

```
cookie:      SHELL_deviceId = 653599d5-3bd9-4d16-b75b-5d4a049324cc
localStorage: SHELL_deviceId
localStorage: __p_preRegisterVisitorUuid = 92a2f928-796b-4c27-ba0f-a7579067acbb
localStorage: merchantCode = op88vndkf2
localStorage: MC_JS_CACHE_HASH = 1108103
```

`MC_JS_CACHE` giữ nguyên bản mã của ứng dụng, `MC_JS_CACHE_HASH`
là băm của nó. `MC_VERSION` là `00577ec0`.

### 2.4 Chống gian lận khác

Trang dùng `html2canvas` (từ cdnjs) — có thể để đo mức trượt của
trang, hoặc để chụp lại nội dung để đối chiếu.

Có Cloudflare Web Analytics (`beacon.min.js`).

Bản dựng được đánh dấu trong trang:

```html
<script>var brand="32440_2026-11-03 22:27:55";console.log("brand",brand)</script>
```

Và một mã băm cam kết của bản dựng:

```html
<div class="version-hash" style="display:none;" data-commit="abdb89c0d6e26bb47972d482b85f842102dccb20"></div>
```

### 2.5 Ghi chú về biệt danh

Trang chào bằng "ON68" và `merchantCode` là `op88vndkf2`, nhưng tên
miền là `oon6a1.ink`. Tên thương hiệu được đổi trong lúc chạy. Đây
là điểm cần lưu ý: tên trang không phải thứ dùng để nhận dạng.

### 2.6 Kiểm chứng lại: ba nhà cung cấp captcha cùng tồn tại

Đo lại ngày 2026-11-22 bằng cách tải các bundle của ứng dụng. Trang
đăng ký là một ứng dụng một trang, nên phần lớn mã không nằm trong
HTML. Ba bundle được tải về:

| Bundle | Kích thước | Dấu hiệu tìm thấy |
|---|---|---|
| `/m/vendor.encrypt.v2.dll.js` | 853 KB | — |
| `/m/chunk-common.bc1682a7.js` | 196 KB | `captcha` |
| `/m/app.280ba8dc.js` | 1,67 MB | `initGeeGuard`, `initBotion`, `_recaptchaVerifier`, `SHELL_deviceId`, `captcha` |

Xác nhận cả ba nhà cung cấp captcha đều có trong cùng một bản dựng:
GeeTest (`initGeeGuard`), Botion (`initBotion`), và Google reCAPTCHA
(`_recaptchaVerifier`). Đây không phải ba lựa chọn thay thế nhau mà
là ba đường dự phòng, hoặc ba phương thức cho các bước khác nhau.

Hệ quả cho NOVA: trang này có **nhiều tầng captcha hơn các trang
khác**, và tầng captcha không phải là tầng duy nhất — `SHELL_deviceId`
được đọc và gửi kèm. Tín hiệu thiết bị vẫn là phần cần làm đúng.

### 2.7 `vendor.encrypt.v2.dll.js`

Bundle 853 KB này được đặt tên như một DLL — đó là mã hoá. Cùng
với `encrypt.js` ở mục 2.2, nó cho thấy kết quả fingerprint được
mã hoá trước khi rời trình duyệt, và khoá mã hoá nằm ở phía máy
chủ.

## 3. Jun88 (`8jjunsyj.cc`)

Ứng dụng Vue tự dựng, khác cả hai nhóm trên.

### 3.1 Những gì đo được

```html
<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/vce701823e78c4301a35b7e07294d87301792088814092" integrity="sha512-..."></script>
```

Đây là Cloudflare Web Analytics. Tệp có tên giống mã băm nhưng thực
chất là beacon của Cloudflare, không phải thư viện fingerprint.

Trong mã, những thứ đáng chú ý:

```
user_agent ×5    user_agent_data ×1    screen_size ×4
pixel_ratio ×2   inner_size ×5         to_data_url ×2
session_storage ×1   performance_timing ×2   function_source ×21
```

Có `toDataURL` (băm canvas) và `user_agent_data` (client hints).

### 3.2 Những gì không đo được

Không có `webgl_renderer`, không có `canvas_2d`, không có `audio`,
không có `fonts`, không có `hardware_concurrency`. Không có
`deviceMemory`. Trong `localStorage` chỉ có:

```
tiktok-ttp, mtWebPushRandomUid, imsPublic_numberFormatSeparators,
lg, locale, mt_push_init_data
```

Không có `fingerId`, không có điểm rủi ro thiết bị.

Kết luận: ở tầng trình duyệt, Jun88 gọn hơn hẳn. Nhưng nó đứng
sau Cloudflare, nên phần lớn việc chống bot nằm ở tầng mạng, ở
trên Cloudflare Bot Management: dấu vân tay JA3/JA4 của TLS, và
hành vi.

### 3.3 Đăng ký

`/home/register` chuyển hướng về trang chủ. Việc đăng ký đi qua
ứng dụng, nên biểu mẫu nằm trong app chứ không phải trong trang
web. Trang có `Chính Sách AML`, `CLB VIP`, và giấy phép từ
`Curaçao Gaming Authority` với số `OGL/2024/1696/1055`.

### 3.4 Đặc tả đăng ký do máy chủ trả về

Đây là phần đo được vào ngày 2026-11-22, bổ sung cho 3.2. Ứng dụng
gọi API công khai, không cần phiên:

```
GET https://api.8jjunsyj.cc/jun88-ecp/api/v1/register/setting
```

Kết quả: mã `200`, và cấu hình trả về 18 trường, trong đó 5 trường
bắt buộc:

| Nhóm | Trường |
|---|---|
| Bắt buộc | `mobile`, `password`, `firstname`, `captcha`, `playerid` |
| Tuỳ chọn | `email`, `confirmpassword`, `birthday`, `currency`, `pin`, `confirmpin`, `pic1`, `pic2`, `im1`, `im2`, `affiliateid`, `ulagentaccount` |

Và các cờ kiểm soát:

```
allowsameemail = false
allowsamemobile = false
allowsameip    = true
captcha_type   = IMG_CAPTCHA
```

Ba điểm đáng chú ý:

* `captcha_type` là `IMG_CAPTCHA` — ảnh captcha 5 ký tự, không phải
  Turnstile. Với loại captcha này, phần lớn tín hiệu bị đo là ở
  tầng IP/TLS, vì captcha ảnh chỉ chống bot đơn giản.
* `allowsameip = true` — nhiều tài khoản từ cùng một IP được phép.
  Vậy giới hạn chống lạm dụng không nằm ở số tài khoản trên
  một IP.
* `playerid` là trường bắt buộc và do phía trình duyệt sinh ra.

## 4. `linkantoan.vip`

Trang này **không mở được** từ máy này:

```
https://linkantoan.vip/  →  SSL: UNEXPECTED_EOF_WHILE_READING
http://linkantoan.vip/   →  Remote end closed connection without response
```

DNS cho thấy nó dùng Cloudflare (`104.18.4.14`). Hành vi trên là
kiểu chặn của Cloudflare khi IP của khách không thuộc vùng được
phép, hoặc khi IP là của trung tâm dữ liệu. Trang nhóm Lobby hiển
thị thông báo tương tự cho người dùng ngoài vùng:

> "Địa chỉ IP của quý khách hiện không thuộc khu vực được hỗ trợ.
> Vui lòng sử dụng IP hợp lệ thuộc khu vực được phép để tham gia."

Vậy chuỗi chặn của nhóm Lobby gồm ba tầng: kiểm tra vùng IP ở
phía máy chủ, Geetest ở phía trình duyệt, và FingerprintJS cho
định danh.

Không có gì được đo ở đây. Đây là giới hạn của việc khảo sát, và
nó được ghi lại thay vì đoán.

### 4.1 Nhưng API phía sau vẫn mở được

Đo lại ngày 2026-11-22: phần giao diện bị chặn, còn API thì không.

```
GET https://api.linkantoan.vip/vi5prod-ecp/api/v1/register/setting
→ 200
```

Kết quả **giống hệt** Jun88 ở mọi trường: cùng 18 trường, cùng 5
trường bắt buộc (`mobile`, `password`, `firstname`, `captcha`,
`playerid`), cùng `captcha_type = IMG_CAPTCHA`, cùng
`allowsameip = true`, cùng `allowsameemail = false`,
`allowsamemobile = false`.

Hai trang này chỉ khác nhau ở tên miền và ở tiền tố API
(`jun88-ecp` so với `vi5prod-ecp`). Chúng chạy cùng một mã nền.
Hệ quả: phần chống bot mà người dùng thấy ở `linkantoan.vip` là
giống hệt Jun88, và khi trang bị chặn ở tầng Cloudflare thì máy
chủ vẫn nhận đăng ký bình thường — chặn ở tầng biên, không phải
tầng nghiệp vụ.

### 4.2 Một điểm cần ghi lại: Turnstile

Điều tra của nhánh khảo sát cho thấy `vi5prod-ecp` dùng Cloudflare
Turnstile với một input ẩn `nrc-invisible`. **Không xác nhận được**
từ máy này: không có mã nguồn trang để đọc, và API trả về
`captcha_type = IMG_CAPTCHA`, không phải Turnstile. Hai nguồn này
mâu thuẫn nhau, nên phần Turnstile được ghi là *chưa xác nhận* thay
vì khẳng định. Ưu tiên nằm ở `IMG_CAPTCHA` vì đó là thứ máy chủ
thực sự dùng để kiểm tra.

## 5. Cloudflare

Bốn trong sáu trang đứng sau Cloudflare (`c168j.vip`,
`8jjunsyj.cc`, `linkantoan.vip`, `oon6a1.ink`). Cloudflare Bot
Management làm những việc mà mã trong trang không làm:

* Dấu vân tay JA3 và JA4 của bắt tay TLS, lấy từ đặc tính của
  ClientHello. JA4 đã xuất hiện trong ThumbmarkJS, nghĩa là hai tầng
  đang dùng chung một dấu vân tay.
* Điểm danh của Cloudflare, đi kèm cookie `__cf_bm`.
* Phân tích hành vi.

Điều này có một hệ quả cho NOVA: **thay đổi tầng TLS sẽ đổi
JA3/JA4**, và hai tầng phải khớp với nhau. Một profile giả lập
điện thoại Android nhưng nói chuyện TLS qua một máy tính Windows sẽ
lộ ra ngay ở tầng này.

## 6. Đối chiếu với NOVA

### 6.1 Những gì NOVA đã có

* 7.222 thiết bị thật, 4.788 mẫu, 230 GPU, 643 chipset, từ dữ liệu
  GSMArena lọc theo thị trường Việt Nam. Kiểm tra tính nhất quán đã
  chạy trên toàn bộ 7.222 thiết bị: **0 lỗi, 0 cảnh báo**.
* 19 trục kiểm tra tính nhất quán, kiểm tra cả giới hạn texture so
  với hạng GPU, họ nhà renderer so với nhà GPU, ABI so với renderer.
* **Chromium 100 → 155**: 56 phiên bản trong sổ đăng ký, chia theo
  băng của từng bản Android (13 → 100–113, 14 → 114–127,
  15 → 128–155). Mỗi profile bốc một phiên bản từ seed, nên một
  preset sinh ra nhiều phiên bản khác nhau thay vì một.
* **48 bề mặt** trong danh mục `novaprofile.core.surfaces`, và 28
  bề mặt đã có tầng phục vụ: 14 ở tầng bản vá Blink, 4 ở tầng hook
  native, 10 ở tầng runtime JavaScript.
* Bản vá Chromium gồm 24 tệp, 3.048 dòng thêm, hai tệp patch trong
  `novaprofile/core_patch/patches/`.
* Bộ kiểm tra: **1.212 test đạt, 5 bỏ qua, 0 lỗi**, ruff sạch.

### 6.2 Chỗ còn thiếu, theo mức độ ưu tiên

Bảng này đã đối chiếu với code đang có. Cột "Trạng thái" nói rõ
surface đó đã được phục vụ chưa, và bởi tầng nào:

* **Blink** — accessor trong Blink được thay bằng bản đọc cấu hình
  của profile. Đây là tầng mạnh nhất, vì giá trị nằm ngay trong
  tiến trình render, không phải trong `window`.
* **Native** — hook riêng trong `nova_chromium_hooks.h`, dành cho
  surface mà một script đọc độc lập được.
* **JS** — lớp JavaScript trong ngữ cảnh trang.
* **Chưa có** — chưa được phục vụ.

| Ưu tiên | Thiếu gì | Bằng chứng | Trạng thái |
|---|---|---|---|
| 1 | Canvas ổn định **và** khác nhau giữa các thiết bị | `canvas.commonPixelsHash`, `toDataURL` | Blink (nhiễu pixel) + JS |
| 2 | `canvasReadbackDiffBytes` bằng 0 | ThumbmarkJS đọc cùng canvas hai lần | Blink |
| 3 | WebGL renderer khớp với GPU thật của thiết bị | `gpu.renderer`, `mapVersion 1.0.3` | Blink + Native |
| 4 | Audio `sampleHash` | `audio.sampleHash` | Native + JS |
| 5 | Danh sách phông chữ theo hệ điều hành | `fonts` đo trong iframe | JS (`measureText`) |
| 6 | `deviceMemory` và `hardwareConcurrency` khớp hạng thiết bị | Trọng số `memory` 0.5, `cpu` 0.2 | Blink |
| 7 | `Math.acos(0.5)` và độ chính xác số thực | `math` | JS (xong 2026-11-22) |
| 8 | `jsHeapSizeLimit` | `hardware.jsHeapSizeLimit` | JS (xong 2026-11-22) |
| 9 | Danh sách quyền và trạng thái | `permissions` | JS |
| 10 | Mã giải mã được | `mediaDecode` | JS (xong 2026-11-22) |
| 11 | JA3/JA4 khớp với hệ điều hành | `ja4`, `tls.extensions` | **Chưa có** |
| 12 | `navigator.webdriver` phải là `false` | Mọi trang đều kiểm tra | Blink |
| 13 | Hàm gốc không bị đổi | `function_source` 86 lần, `error_stack` 24 lần | JS (`toString` trả mã native) |

#### Mục 7, 8 và 10 đã xong

Ba bề mặt này được làm trong một lượt, và cả ba đã trở thành **trục
được kiểm chứng** trong bộ dò end-to-end, không chỉ là lớp ghi đè.

| Bề mặt | Mô hình | Trục | Cách đo |
|---|---|---|---|
| Độ chính xác số thực | `schema.signals.MathPrecision` | `math` | `Math.acos(0.5)` và 5 hàm nữa |
| Trần heap | `schema.signals.HeapSizeLimit` | `heap` | `performance.memory.jsHeapSizeLimit` |
| Mã giải mã | `schema.signals.MediaDecode` | `media_decode` | `MediaRecorder.isTypeSupported` |

Mỗi bề mặt có muối riêng, được rút từ trục HKDF riêng
(`Axis.MATH_PRECISION`, `Axis.HEAP_SIZE`, `Axis.MEDIA_DECODE`), nên
ba bề mặt này không bị trộn vào nhau và không trùng với bất kỳ bề
mặt nào đã có.

**Một ràng buộc đáng ghi lại.** Dải giá trị của bề mặt số thực phải
thoả hai yêu cầu ngược nhau:

* **Hợp lý** — lệch quá một vài ulp là *một engine khác*, không phải
  *một thiết bị khác*.
* **Đủ sức chứa** — một ulp của số double quanh 1.0 là 2.22e-16, nên
  dải rộng đúng một ulp chỉ chứa **ba** giá trị khác nhau. Một đội
  trăm triệu profile sẽ gộp còn ba nhóm, và trục này không mang thông
  tin gì.

Dải được đặt rộng **mười sáu ulp**: đủ cho khoảng một trăm nghìn giá
trị khác nhau cho mỗi hàm, và vẫn là giá trị mà một thư viện C thật
có thể trả về. Sáu hàm nhân lên đưa trục lên khoảng một trăm bit.
Bài kiểm tra `test_the_math_values_differ_between_profiles` giữ
điều này không bị trượt lại.

Một điều nữa: giá trị của profile phải nằm trong dải, nếu không
runtime sẽ kết luận rằng máy chủ là một engine khác và giữ lại câu
trả lời của máy chủ — profile đó sẽ bị bỏ qua một cách âm thầm.
`test_the_profile_math_values_stay_inside_the_epsilon` giữ điều đó.


### 6.3 Bề mặt trong danh mục mà chưa tầng nào phục vụ

Danh mục `novaprofile.core.surfaces` liệt kê 48 bề mặt. Đối chiếu
với ba tầng phục vụ (bản vá Blink, hook native, runtime JavaScript)
thì 28 bề mặt đã có. Trong 20 bề mặt còn lại:

* 5 bề mặt là **dữ liệu người dùng gõ** (`email`, `password`,
  `phone`, `invite_code`, `full_name`). Đây không phải bề mặt trình
  duyệt, và việc điều khiển chúng không thuộc tầng fingerprint.
* 3 bề mặt là **quyết định phía máy chủ** (`public_ip`,
  `address_history`, `timing`, `behaviour`). Trình duyệt không có
  quyền với chúng; việc xử lý là của tầng mạng.
* 1 bề mặt là **danh sách phông chữ** (`font_list`): runtime đã bọc
  `measureText`, nhưng chưa có bảng phông theo hệ điều hành để bọc
  theo.
* 1 bề mặt là **danh sách quyền chi tiết**: runtime đã bọc
  `permissions.query`, nhưng chưa có trạng thái riêng cho
  `mediaDevices`.
* 4 bề mặt là **chi tiết phụ**: `do_not_track`, `performance.memory`,
  `MediaRecorder.isTypeSupported`, `indexedDB.databases()`.
* 2 bề mặt là **chi tiết phụ về đồ họa**: `screen.availWidth` /
  `availHeight` (đã có ở tầng Blink, nhưng danh mục gộp chung với
  `screen.width`/`height` nên bộ đếm không khớp), và
  `WebGL.getSupportedExtensions`.

Tóm lại, **bảy bề mặt thực sự còn thiếu** theo thứ tự ưu tiên mà
bảng 6.2 nêu: độ chính xác số thực, `jsHeapSizeLimit`, mã giải mã
được, JA3/JA4, bảng phông theo hệ điều hành, trạng thái
`mediaDevices`, và danh sách phông.

### 6.4 Những lỗi đã tìm ra trong tầng JavaScript

Bộ kiểm tra cũ chỉ đọc *văn bản* của script, không chạy nó. Ba lỗi
nghiêm trọng đã lọt qua đó, và cả ba đều làm cho tầng JavaScript
**không hoạt động** trên trang thật:

1. **Biến không được khai báo.** Khối `navigator` so sánh với
   `NATIVE_SOURCE`, một cái tên không tồn tại trong script. Lỗi
   `ReferenceError` xảy ra ngay ở dòng đầu tiên của khối đó, nên
   **mọi override phía sau nó đều không chạy**: màn hình, khung nhìn,
   tỉ lệ điểm ảnh và client hints.
2. **Rò mã nguồn.** Cách che nguồn cũ đặt thuộc tính `toString` riêng
   lên hàm. Cách kiểm tra mà một script fingerprint dùng là
   `Function.prototype.toString.call(getter)`, và hàm này đọc văn bản
   nguồn *bên trong* của hàm, bỏ qua thuộc tính riêng. Nên mã nguồn
   thật của override vẫn nằm đó cho trang đọc. Đây đúng là cách kiểm
   tra mà ON68 chạy 86 lần.
3. **Đọc global không bảo vệ.** Khối `navigator` đọc global trực tiếp.
   Câu lệnh `if (nav)` ngay sau đó không bảo vệ được gì, vì lỗi đã ném
   ra ở dòng trên nó.

Cả ba đã được sửa và có test hồi quy:

* `hide()` trả về hàm sau một `Proxy`. Proxy đáp ứng `toString` bằng
  mã native, nên `Function.prototype.toString.call()` cũng trả mã
  native. Proxy vẫn chuyển tiếp lời gọi, tên và độ dài.
* Tên của getter cũng phải khớp. Một getter native có tên `"get"`, còn
  tên của override (`"raw"`, `"value"`) là dấu hiệu không cần đọc mã
  nguồn cũng nhận ra. Override giờ giữ tên của getter mà nó thay.
* Mọi global được đọc qua `typeof`.

Bộ kiểm tra mới `tests/test_js_runtime_execution.py` **chạy** script
dưới Node, trong ba môi trường: môi trường đầy đủ, môi trường
trống (không có API trình duyệt nào), và kiểm tra hình dạng descriptor.
Cả ba lỗi trên đều làm test đỏ, và đã xác nhận bằng cách sửa lại lỗi
cũ rồi chạy lại.

### 6.5 Điểm cần nói rõ

* FingerprintJS và ThumbmarkJS **băm giá trị rồi gửi**. Điều này có
  nghĩa là giá trị thô không quan trọng bằng giá trị băm, nhưng băm
  cũng không cứu được: hai thiết bị có cùng băm là hai thiết bị mà
  nhà cung cấp gộp lại. NOVA phải cho ra **giá trị băm khác nhau**
  giữa các thiết bị, đồng thời **ổn định** cho cùng một thiết bị.
* `device@score@v2` cho điểm theo `memory`, `cpu`, `sort`, `image`.
  Một profile nói là điện thoại Android 8 GB RAM nhưng
  `hardwareConcurrency` là 16 sẽ bị trừ điểm ngay.
* `earlyReturn` với `pc-fast-path` chỉ dùng cho máy tính. Profile
  điện thoại sẽ đi đường tính đủ bốn trục, nên cả bốn trục phải
  nhất quán với nhau.
* Trường hợp xấu nhất không phải là bị phát hiện là giả, mà là bị
  gộp vào nhau. Một thiết bị có `deviceMemory` là 4 nhưng
  `hardwareConcurrency` là 8 và GPU là một con chip tầm trung thì
  trông giống một chiếc điện thoại tầm trung hơn là giống một chiếc
  flagship.

## 7. Cách đo lại

Các công cụ dùng để khảo sát nằm ở `.dataset_probe/`:

* `survey_page.py` — tải trang, liệt kê trường biểu mẫu, tìm mã
  nhúng và tải các tệp tập lệnh, rồi đếm các lần gọi API trình
  duyệt trong từng tệp.
* `detail_script.py` — in đoạn mã quanh mỗi lần gọi, để đọc được
  cách lấy giá trị và cách gửi đi.
* `list_components.py` — liệt kê các nhóm của một thư viện fingerprint.

Kết quả lưu ở `.dataset_probe/survey/`.

## 8. Những gì chưa làm

* Chưa đăng ký thật ở bất kỳ trang nào, nên chưa biết chính xác
  trường nào phía sau bị bắt buộc và trường nào tuỳ chọn.
* Chưa đo được `linkantoan.vip`.
* Chưa đo hành vi ở tầng Cloudflare. Khảo sát này chỉ nhìn được
  mã trong trang, không nhìn được quyết định của Cloudflare.
* Chưa đo được JA3/JA4 thật của NOVA. Đó là việc tiếp theo.
