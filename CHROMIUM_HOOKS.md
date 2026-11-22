# Bản vá anti-detect — những gì đã gắn vào Chromium

Tài liệu này là **bản đồ các điểm gắn** (hook points) của lớp native trong
Chromium: file nào đã sửa, sửa ở hàm nào, và vì sao chỗ đó là chỗ đúng.

Đọc `NEXT_STEPS.md` để có bối cảnh tổng thể.

---

## 0. Nguyên tắc

Mọi giá trị đều đến từ **một nguồn duy nhất**: file JSON của profile, đọc
bởi browser process **trước khi** fork bất kỳ tiến trình con nào, rồi nhân
bản sang renderer / GPU / network service.

Không có JS hook nào trong đường chính. Lý do:

| Cách phát hiện JS hook | Vì sao native không dính |
|---|---|
| `Function.prototype.toString` | Không có hàm nào được ghi đè |
| `Object.getOwnPropertyDescriptor` | Không có property nào bị redefine |
| `Proxy` trap | Không có Proxy nào |
| Timing của getter | Getter là C++ thuần, cùng tốc độ |

Vì vậy, **mọi accessor phải được gắn tại tầng C++ của chính Blink**.

---

## 1. Bản đồ các điểm gắn

### 1.1 Danh tính và Client Hints

| File | Hàm | Trục |
|---|---|---|
| `core/frame/navigator_ua.cc` | `NavigatorUA::userAgent()` | `navigator.userAgent` |
| `core/frame/navigator_ua_data.cc` | `NavigatorUAData::platform()`, `platformVersion()`, `architecture()`, `bitness()`, `model()`, `mobile()`, `wow64()`, `brands()` | Client Hints cao entropy |
| `core/frame/navigator_id.cc` | `NavigatorID::appName()`, `appVersion()`, `appCodeName()`, `platform()`, `product()`, `productSub()`, `vendor()`, `vendorSub()` | `navigator.*` |
| `core/frame/navigator_device_memory.cc` | `NavigatorDeviceMemory::deviceMemory()` | `navigator.deviceMemory` |
| `core/frame/navigator_concurrent_hardware.cc` | `NavigatorConcurrentHardware::hardwareConcurrency()` | `navigator.hardwareConcurrency` |
| `core/frame/navigator_language.cc` | `NavigatorLanguage::language()`, `languages()` | `navigator.language` |
| `core/frame/navigator.cc` | `Navigator::maxTouchPoints()` | `navigator.maxTouchPoints` |
| `core/execution_context/navigator_base.cc` | `NavigatorBase::webdriver()` | `navigator.webdriver` |

**Ghi chú về Client Hints cao entropy.** `platformVersion`, `architecture`,
`bitness`, `model`, `wow64` **không** phải property trực tiếp của
`navigator.userAgentData`. Chúng chỉ xuất hiện qua
`navigator.userAgentData.getHighEntropyValues([...])`. Probe harness phải gọi
đúng hàm đó — nếu đọc `navigator.userAgentData.platformVersion` thì luôn ra
`undefined` và trục bị đánh dấu hỏng dù browser đúng.

### 1.2 Màn hình và khung nhìn

| File | Hàm | Trục |
|---|---|---|
| `core/frame/screen.cc` | `Screen::width()`, `height()`, `availWidth()`, `availHeight()`, `availLeft()`, `availTop()`, `colorDepth()`, `pixelDepth()` | `screen.*` |
| `core/frame/local_dom_window.cc` | `LocalDOMWindow::devicePixelRatio()` | `window.devicePixelRatio` |
| `core/frame/local_dom_window.cc` | `LocalDOMWindow::innerWidth()`, `innerHeight()` | `window.inner*` |
| `core/frame/local_dom_window.cc` | `LocalDOMWindow::outerWidth()`, `outerHeight()` | `window.outer*` |
| `core/frame/local_dom_window.cc` | `LocalDOMWindow::screenX()`, `screenY()` | `window.screen*` |

**Vì sao viewport phải bị gắn.** Kích thước cửa sổ trên desktop là thứ
người dùng resize — một giá trị không có quan hệ gì với thiết bị mà user
agent khai. Site đọc `innerWidth` rồi so với `screen.width` sẽ thấy lệch.

### 1.3 Hình học (rects)

| File | Hàm | Ghi chú |
|---|---|---|
| `core/dom/element.cc` | `Element::GetBoundingClientRect()` | |
| `core/dom/element.cc` | `Element::getClientRects()` | |
| `core/dom/range.cc` | `Range::getBoundingClientRect()` | |
| `core/dom/range.cc` | `Range::getClientRects()` | |

Cơ chế: `nova::ApplyRectDeltas(rect, surface)` cộng thêm **số nguyên** pixel.
Số nguyên là bắt buộc — offset phân số sẽ dịch hình chữ nhưng không dịch
pixel của nó, làm hỏng tính nhất quán giữa layout và paint.

Tham số `surface` ("element" / "range") làm cho hai bề mặt **lệch nhau một
chút**: trang nào so sánh rect của element với rect của range sẽ không thấy
chúng bằng nhau.

### 1.4 Đồ hoạ — canvas và WebGL

| File | Hàm | Ghi chú |
|---|---|---|
| `modules/canvas/canvas2d/base_rendering_context_2d.cc` | `BaseRenderingContext2D::getImageData()` | Nhiễu áp lên buffer RGBA trả về |
| `modules/webgl/webgl_rendering_context_base.cc` | `WebGLRenderingContextBase::getParameter()` | `UNMASKED_VENDOR_WEBGL` / `UNMASKED_RENDERER_WEBGL` |
| `modules/webgl/webgl_rendering_context_base.cc` | `WebGLRenderingContextBase::ReadPixelsImpl()` | Nhiễu áp lên buffer readback |

Điểm gắn canvas là **một chỗ duy nhất** — `getImageData()` — nên nó phủ được
`getImageData`, `toDataURL`, `toBlob` và các biến thể `OffscreenCanvas` cùng
lúc, vì tất cả đều đi qua cùng một đường đọc pixel.

**Đây là điểm quan trọng nhất của cả hệ thống.** Có một kỹ thuật chống gian
lận dựa trên việc shader render toạ độ nội suy rồi đọc lại — nếu WebGL bị
sửa, giá trị đọc ra sẽ không khớp với hàm toán học. Vì vậy nhiễu **không
được** áp ở tầng shader hay ở tầng lệnh draw, mà phải áp ở **tầng readback**
(`ReadPixelsImpl`), nơi mà giá trị trả về cho script đã bị nhiễu và giá trị
gốc không thể khôi phục.

### 1.5 WebGPU

| File | Hàm | Ghi chú |
|---|---|---|
| `modules/webgpu/gpu_adapter_info.cc` | `GPUAdapterInfo::vendor()` | |
| `modules/webgpu/gpu_adapter_info.cc` | `GPUAdapterInfo::architecture()` | |
| `modules/webgpu/gpu_adapter_info.cc` | `GPUAdapterInfo::device()` | |
| `modules/webgpu/gpu_adapter_info.cc` | `GPUAdapterInfo::description()` | |

Adapter info mang theo vendor + kiến trúc + phiên bản driver — một bộ chuỗi
định danh **máy** chính xác hơn nhiều so với user agent.

### 1.6 Âm thanh

| File | Hàm | Trục |
|---|---|---|
| `modules/webaudio/base_audio_context.h` | `BaseAudioContext::sampleRate()` | `AudioContext.sampleRate` |
| `modules/webaudio/audio_context.cc` | `AudioContext::baseLatency()` | `AudioContext.baseLatency` |
| `modules/webaudio/audio_context.cc` | `AudioContext::outputLatency()` | `AudioContext.outputLatency` |
| `modules/webaudio/audio_destination_node.cc` | `AudioDestinationNode::maxChannelCount()` | `destination.maxChannelCount` |
| `modules/webaudio/offline_audio_destination_handler.cc` | `OfflineAudioDestinationHandler::DoOfflineRendering()` | Nhiễu mẫu âm thanh |

**Vì sao nhiễu mẫu là bắt buộc.** Web Audio fingerprint là tổng của một
oscillator và một compressor, và kết quả ổn định tới **15 chữ số đáng kể**
trên mọi máy chạy cùng một build. Hai profile render cùng một graph sẽ cho
cùng một fingerprint trừ khi chính các mẫu bị nhiễu.

Cơ chế: `nova::ApplyAudioNoise(samples, count, offset)` cộng nhiễu ở **bit
thứ 24** của mantissa — dưới mức mà fingerprint giải được, nhưng trên mức
mà đường ống âm thanh lượng tử hoá. Offset truyền vào làm nhiễu **ổn định
theo vị trí mẫu**, nên fingerprint của một profile không đổi giữa hai lần
đọc, còn fingerprint của hai profile không bao giờ trùng.

`sampleRate()` được gắn ở `BaseAudioContext` — tức là **cả** `AudioContext`
lẫn `OfflineAudioContext` đều nhận giá trị của profile, chỉ với một chỗ sửa.

### 1.7 Cảm biến chuyển động

| File | Hàm | Ghi chú |
|---|---|---|
| `modules/device_orientation/device_motion_event_pump.cc` | `DeviceMotionEventPump::GetDataFromSharedMemory()` | |

Desktop **không có** accelerometer. Giá trị đọc được là `NaN` — và `NaN`
là một tín hiệu cực mạnh, mạnh hơn cả việc có giá trị "sai".

### 1.8 Thiết bị media

| File | Hàm | Ghi chú |
|---|---|---|
| `modules/mediastream/media_devices.cc` | `MediaDevices::EnumerateDevices()` → vòng lặp tạo device | |

Cơ chế: `nova::RewriteMediaDeviceId(device_id, kind, index)` trộn id của host
với salt của profile. Id giữ **nguyên hình dạng UUID** — một trang kiểm tra
định dạng sẽ từ chối bất kỳ thứ gì khác.

Lý do phải làm: id của host **giống nhau trên mọi profile** chạy trên cùng
máy. Một trang ghi lại id rồi so sánh giữa hai lần đăng nhập sẽ thấy chúng
trùng nhau — đúng thứ mà anti-detect cần tránh.

### 1.9 Dung lượng lưu trữ

| File | Hàm | Ghi chú |
|---|---|---|
| `modules/quota/storage_manager.cc` | `QueryStorageUsageAndQuotaCallback()` | |

Quota của host là một phần nhỏ của ổ đĩa máy, usage phụ thuộc vào những gì
khác nằm trong thư mục profile. Cả hai đều dễ bị phát hiện.

### 1.10 Font

| File | Hàm | Ghi chú |
|---|---|---|
| `core/css/font_face_set.cc` | `FontFaceSet::check()` | `document.fonts.check('16px Roboto')` |

Kho font của host là kho font của máy Windows — hàng trăm family, trong đó
có cả font doanh nghiệp. Profile khai báo font của thiết bị mà nó khai là.

Các family generic (`serif`, `sans-serif`, `monospace`, …) luôn coi là có mặt.

### 1.11 JA3 / JA4 — bắt tay TLS

| File | Hàm | Ghi chú |
|---|---|---|
| `net/socket/ssl_client_socket_impl.cc` | `SSLClientSocketImpl::StartHandshake()` → `SSL_set_strict_cipher_list()` | Thứ tự cipher suite |
| `net/socket/ssl_client_socket_impl.cc` | `SSL_set1_group_ids()` | Thứ tự curve (supported groups) |
| `net/socket/ssl_client_socket_impl.cc` | `SSL_set_verify_algorithm_prefs()` | Thứ tự thuật toán chữ ký |
| `net/socket/ssl_client_socket_impl.cc` | `NovaGroupId()` | Tên group của profile → id của BoringSSL |
| `content/utility/services.cc` | `RunNetworkService()` → `nova::InitializeNetworkProcess()` | Đọc `--nova-config` trong network service |
| `content/browser/network_service_instance_impl.cc` | `ServiceProcessHost::Options` | Truyền `--nova-config` xuống network service |

Đây là trục **không thể** làm bằng JS. JA3 là MD5 của ClientHello — byte đi
trên wire — và nó được tạo ra trong network service **trước khi** bất kỳ
script nào chạy. Cách duy nhất để đổi nó là đổi những byte client gửi đi.

Cơ chế: `nova::GetTlsCipherSuites()` trả về danh sách cipher của profile
(theo tên BoringSSL, ví dụ `TLS_AES_128_GCM_SHA256`). Danh sách được ghép
bằng `:` rồi đưa vào `SSL_set_strict_cipher_list()`.

Các suite mà profile không nêu được **thêm lại vào cuối** theo thứ tự stock,
nên handshake vẫn offer đủ những gì Chromium offer — profile chỉ quyết định
**cái nào đi trước**.

Cùng cơ chế cho curve (`SSL_set1_group_ids`) và thuật toán chữ ký
(`SSL_set_verify_algorithm_prefs`).

**Vì sao phải truyền config xuống network service.** Network service chạy
trong tiến trình riêng, nên nó không thấy config của browser process. Chromium
dựng lại command line cho mỗi tiến trình con — vì vậy phải sửa
`ServiceProcessHost::Options` để thêm `--nova-config`, đúng như đã làm cho
renderer và GPU process.

**Bảng ClientHello theo phiên bản Chrome** nằm ở
`novaprofile/control_plane/novaprofile/core/tls_profiles.py`: Chrome 100–106
còn suite CBC, Chrome 107 trở đi bỏ CBC, Chrome 131 trở đi có hybrid key
agreement. Profile khai Chrome 131 mà gửi handshake của Chrome 120 là mâu
thuẫn ở hai chỗ rẻ nhất để đối chiếu.

**Ba API mà BoringSSL không có** — ghi lại vì đều biên dịch lỗi:

| Tên không tồn tại | Tên thật |
|---|---|
| `SSL_CTX_set_custom_cipher_list` | `SSL_set_strict_cipher_list` |
| `SSL_GROUP_get_id_by_group_name` | không có — dùng `NovaGroupId()` |
| `SSL_SIGN_ED448` | không có — BoringSSL chỉ có ED25519 |

Và `base::EqualsIgnoreCase` cũng không có: phải dùng `absl::EqualsIgnoreCase`.

**Tên hybrid group đã đổi.** Chrome 131 gửi `X25519Kyber768Draft00`; group đã
chuẩn hoá là `X25519MLKEM768`, và Chromium 160 chỉ biết tên mới. Profile vẫn
dùng tên bản draft (vì đó là tên trong ClientHello thật của bản Chrome đó),
nên `NovaGroupId()` nhận **cả hai** và trả về cùng một id.

**Chu kỳ phụ thuộc GN.** Hook TLS làm cho `//net` phụ thuộc `//nova_core`,
mà `//nova_core` phụ thuộc `//ui/gfx` — và `//ui/gfx` → `//ui/base` → `//net`.
Vòng tròn đó làm `gn gen` fail, và `ninja` **dừng im lặng** (exit 0, không
compile gì). Sửa bằng cách dùng target hẹp hơn: `//ui/gfx/geometry` thay vì
`//ui/gfx` — core chỉ cần `gfx::RectF`.

### 1.12 Điều phối ở tầng tiến trình

| File | Hàm |
|---|---|
| `content/app/content_main_runner_impl.cc` | Đọc `--nova-config` ở browser process |
| `content/browser/renderer_host/render_process_host_impl.cc` | `AppendRendererCommandLine()` — truyền `--nova-config` xuống renderer |
| `content/gpu/gpu_main.cc` | `GpuMain()` — `nova::InitializeGpuProcess()` |
| `content/browser/network_service_instance_impl.cc` | `ServiceProcessHost::Options` — truyền `--nova-config` xuống network service |
| `content/utility/services.cc` | `RunNetworkService()` — `nova::InitializeNetworkProcess()` |

**Điểm mấu chốt.** Chromium **dựng lại** command line cho mỗi tiến trình con.
Nếu chỉ truyền `--nova-config` cho browser, thì renderer sẽ **không** có nó —
và renderer là nơi phần lớn accessor sống. Vì vậy phải sửa
`AppendRendererCommandLine` để truyền xuống. Tương tự cho GPU process và
network service.

---

## 2. Bảng tra nhanh: trục nào còn thiếu

| Trục | Trạng thái |
|---|---|
| identity (UA, appVersion, platform…) | ✅ |
| client_hints (kể cả cao entropy) | ✅ |
| audio (sample rate, latency, channel, nhiễu mẫu) | ✅ |
| screen | ✅ |
| viewport | ✅ |
| rects | ✅ |
| canvas | ✅ |
| webgl | ✅ |
| webgpu | ✅ |
| fonts | ✅ |
| media_devices | ✅ |
| sensors | ✅ |
| storage | ✅ |
| **JA3 / JA4 (TLS ClientHello)** | ✅ — `net/socket/ssl_client_socket_impl.cc`, config truyền xuống network service |
| **DNS** | ❌ — cấu hình có (`DnsConfig`), chưa nối vào `net/dns/` |
| **Proxy trong Chromium** | ❌ — cấu hình có (`ProxyConfig`), chưa nối |
| **WebRTC** | ❌ — cấu hình có (`WebRtcConfig`), chưa nối |

---

## 3. Cấu trúc file của lớp native

```
third_party/blink/renderer/core/nova/
├── BUILD.gn               target: nova_core (source_set)
├── nova_core_config.h     45 struct: cấu húc profile + API công khai
├── nova_core_config.cc    đọc JSON + suy luận HKDF-SHA256
├── nova_blink_patch.cc    các accessor gắn giá trị của profile
├── nova_pixel_noise.{h,cc} nhiễu pixel tất định (canvas/WebGL)
└── nova_chromium_hooks.h  header mà file đã sửa include
```

`nova_core` là `source_set` chứ không phải `component`, để link được vào cả
Blink core lẫn Blink modules mà không kéo cả Blink vào browser process.

### 3.1 Ba nguyên tắc của lớp native

1. **Tất định.** Cùng một profile, mở lại sau khi restart → ra **cùng** hash
   canvas, cùng fingerprint audio, cùng danh sách device id.
2. **Tách bạch.** Hai profile khác nhau → không bao giờ ra cùng giá trị ở
   **bất kỳ** trục nào. Đây là điều làm cho đội hình profile trông giống
   những chiếc máy thật chứ không giống những bản sao của một máy.
3. **Không tự mâu thuẫn.** Mọi trục phải thỏa mãn *cross-surface* check:
   `screen.width` phải khớp `outerWidth`, `devicePixelRatio` phải khớp tỉ lệ
   `screen.width / viewport.innerWidth`, `unmaskedRenderer` phải khớp hạng
   của `hardwareConcurrency`.

---

## 4. Vì sao một số trục không thể làm bằng JS

| Trục | Tầng | Vì sao JS không đụng tới |
|---|---|---|
| ~~JA3 / JA4~~ | TLS ClientHello | Xảy ra **trước** khi JS chạy; là byte trên wire — **đã xử lý ở tầng C++** |
| ~~TLS fingerprint~~ | `net/socket/` | Tầng network service — **đã xử lý ở tầng C++** |
| DNS leak | `net/dns/` | Tầng network service |
| Canvas readback | Blink | Có thể, nhưng JS hook bị phát hiện → phải native |
| WebGL readback | GPU process | Tương tự |

Đây là **giới hạn kiến trúc**, không phải việc chưa làm.

---

## 5. Cách kiểm chứng

```bash
# 1. Build
cd C:/chromium/src
autoninja -C out/nova-win chrome

# 2. Probe trên chính chrome.exe vừa build, đối chiếu với simulator
cd novaprofile/control_plane
python -m novaprofile.probe.verify_built_core \
    --binary C:/chromium/src/out/nova-win/chrome.exe \
    --profile <profile.json>

# 3. So 2 lần đọc cùng một trục → phải giống hệt nhau (tất định)
# 4. So 2 profile khác nhau → phải khác nhau ở mọi trục (tách bạch)
```

Bộ probe dùng **15 trục**, mỗi trục có một `Tolerance` riêng:

- `exact` — phải khớp tuyệt đối (identity, client hints, fonts, media devices)
- `relative` — sai số cho phép theo tỉ lệ (rects, sensors)
- `absolute` — sai số tuyệt đối cho phép

Xem `novaprofile/control_plane/novaprofile/probe/e2e.py` để biết biên độ cụ
thể của từng trục và lý do của nó.
