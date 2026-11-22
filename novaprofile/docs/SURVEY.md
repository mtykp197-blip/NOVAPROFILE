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
  GSMArena lọc theo thị trường Việt Nam.
* 19 trục kiểm tra tính nhất quán, kiểm tra cả giới hạn texture so
  với hạng GPU, họ nhà renderer so với nhà GPU, ABI so với renderer.
* Kiểm tra tính nhất quán đã chạy trên toàn bộ 7.222 thiết bị: 0 lỗi,
  0 cảnh báo.

### 6.2 Chỗ còn thiếu, theo mức độ ưu tiên

| Ưu tiên | Thiếu gì | Bằng chứng |
|---|---|---|
| 1 | Canvas ổn định **và** khác nhau giữa các thiết bị | `canvas.commonPixelsHash`, `toDataURL` |
| 2 | `canvasReadbackDiffBytes` bằng 0 | ThumbmarkJS đọc cùng canvas hai lần |
| 3 | WebGL renderer khớp với GPU thật của thiết bị | `gpu.renderer`, `mapVersion 1.0.3` |
| 4 | Audio `sampleHash` | `audio.sampleHash` |
| 5 | Danh sách phông chữ theo hệ điều hành | `fonts` đo trong iframe |
| 6 | `deviceMemory` và `hardwareConcurrency` khớp hạng thiết bị | Trọng số `memory` 0.5, `cpu` 0.2 |
| 7 | `Math.acos(0.5)` và độ chính xác số thực | `math` |
| 8 | `jsHeapSizeLimit` | `hardware.jsHeapSizeLimit` |
| 9 | Danh sách quyền và trạng thái | `permissions` |
| 10 | Mã giải mã được | `mediaDecode` |
| 11 | JA3/JA4 khớp với hệ điều hành | `ja4`, `tls.extensions` |
| 12 | `navigator.webdriver` phải là `false` | Mọi trang đều kiểm tra |
| 13 | Hàm gốc không bị đổi | `function_source` 76 lần, `error_stack` 21 lần |

### 6.3 Điểm cần nói rõ

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
