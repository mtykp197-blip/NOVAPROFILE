# Kế hoạch tiếp theo — NOVAPROFILE

Tài liệu này là phần bàn giao kỹ thuật: **đã xong gì, còn lại gì, làm theo thứ tự nào,
và mỗi việc cần bao lâu**.

Đọc `PROGRESS.md` trước để có bối cảnh lịch sử.

---

## 0. Trạng thái tại thời điểm bàn giao

| Hạng mục | Trạng thái |
|---|---|
| Core profile generator (Python) | ✅ Xong, 1784 test xanh |
| Anti-collision (chống trùng) | ✅ Xong, 300/300 profile unique |
| Device catalogue | ✅ 8.954 preset Android |
| JS runtime (navigator/screen/canvas/webgl/audio) | ✅ Xong |
| Chromium build trên Windows | 🔄 Đang build — ~8.600/9.570 step (~90%) |
| Chromium patch anti-detect (tầng C++) | ✅ **Đã có** — 3.048 dòng, 24 file |
| Chromium: `chrome.exe` hoàn chỉnh | 🔄 Đang build — `out/nova-win`, ninja còn chạy |
| Installer / gói cài đặt Windows | ⏳ Chưa đóng gói |
| JA3/JA4 (TLS fingerprint) | ✅ **Đã làm** — hook BoringSSL trong Network Service, patch `0003` |
| Bảng font theo hạng thiết bị | 🟡 Có `measureText`, thiếu bảng font đầy đủ |

---

## 1. Việc đã xong trong lượt này

### 1.1 Suy luận GPU từ renderer thay vì tra bảng cứng

**Vấn đề.** `tools/generate_device_presets.py` tra `GPU_TABLE` (38 dòng) để lấy
`gpu_architecture` và `max_texture_size`. Catalogue nguồn có **194 renderer khác
nhau**, nên **163 renderer rơi về `DEFAULT_GPU`** (Mali-G57 MC2). Hậu quả:
mọi handset dùng GPU không có trong bảng đều báo cùng một kiến trúc → site
đọc `navigator.gpu` / WebGL architecture thấy trùng hàng loạt.

**Cách sửa.** Không tra bảng cứng nữa — suy luận từ chính tên part:

- `clean_renderer()` — làm sạch chuỗi renderer lỗi do scraper ghép:
  - `Adreno (TM) 3064002` → `Adreno (TM) 306` (số 4 chữ số là tọa độ ô
    spreadsheet, không phải số model)
  - `PowerVR GE8300POWERVR` → `PowerVR GE8300`
  - `Broadcom VideoCore IV / Adreno 305` → `Broadcom VideoCore IV` (ô chứa 2 part,
    giữ part đầu)
- `gpu_facts(renderer)` — trả về `(vendor, architecture, webgl_vendor, texture_limit)`,
  dùng chính bộ phân loại `_gpu_classes()` của `fleet_validator` để quyết định
  hạng (budget / mid / flagship) rồi chọn đúng texture limit trong band.

**Kết quả.** `gpu_architecture` giờ khớp 100% với `unmasked_renderer`; không còn
preset nào báo sai kiến trúc.

### 1.2 Nhất quán giới hạn GPU

`max_texture_size` = 32768 nhưng `max_renderbuffer_size` / `max_viewport_dims`
= 16384 trên cùng một `GpuProfile` — cấu hình không driver nào tạo ra. Đã sửa
generator set đồng nhất cả 4 trường theo cùng một giá trị.

### 1.3 Bổ sung 4 GPU thật còn thiếu trong bảng phân loại

| GPU | Thực tế | Hạng |
|---|---|---|
| `Immortalis-G925` | Dimensity 9200 | flagship (16384) |
| `Immortalis-G710` | Dimensity 1300 | mid (8192) |
| `Xclipse 550` | Exynos 1580 | flagship (16384) |
| `Xclipse 950` | Exynos 2500 | flagship (16384) |

### 1.4 Sửa 2 test đúng, không phải sửa code cho vừa test

- `test_the_android_release_and_the_sdk_level_agree` — bảng mapping thiếu
  Android 16 (SDK 36). Đã thêm `"16": 36`.
- `test_the_fleet_holds_no_device_that_is_not_android` — marker `"ipad"` khớp
  substring trong tên `"Prestigio MultiPad 4 Quantum 10.1 3G"`. Đã đổi sang
  regex word-boundary (`\bipad\b`). Đây là lỗi của test, không phải của preset.

### 1.5 Fix import sau khi refactor

`identity.py` import `generated_gpu_profiles` / `generated_gpu_variants` từ
`devices.py`, nhưng hai hàm đó tên là `_generated_gpu_profiles` /
`_generated_gpu_variants` (có tiền tố `_`). Import sửa, `_GPU_CATALOGUE` giờ có
313 renderer, `_GPU_VARIANTS` có 8.954 preset.

### 1.6 Chu kỳ phụ thuộc GN — nguyên nhân build im lặng dừng

Đây là lỗi âm thầm đáng kể nhất của lượt này, và nó đã làm mất **6 giờ
build**. Ghi lại để không phải tìm lại.

**Triệu chứng.** `ninja` báo `[0/1] Regenerating ninja files` rồi **không
làm gì cả** — không compile, không báo lỗi, exit code 0. Người ta tưởng
build đã xong. Nhưng `ninja -d explain` nói ra sự thật:

```
ninja explain: build.ninja.stamp is dirty
```

**Nguyên nhân.** `gn gen` thất bại với chu kỳ phụ thuộc:

```
ERROR Dependency cycle:
  //net:net ->
  //third_party/blink/renderer/core/nova:nova_core ->
  //ui/gfx:gfx ->
  //ui/base:ui_data_pack ->
  //net:net
```

Vòng tròn này do patch `0003`: hook TLS làm cho `//net` phụ thuộc
`//nova_core`, mà `//nova_core` lại phụ thuộc `//ui/gfx`.

**Cách sửa.** `nova_core` chỉ cần `gfx::RectF` — dùng target hẹp hơn là được:

```gn
deps = [
  "//base",
  "//third_party/boringssl",
  "//ui/gfx/geometry",   # thay vì "//ui/gfx"
]
```

`//ui/gfx/geometry:geometry` chỉ phụ thuộc `//base`, nên vòng tròn đứt.

**Bài học.** `gn gen` phải chạy thành công **trước khi** tin build. Lệnh
kiểm tra là:

```bash
cd C:/chromium/src/out/nova-win
gn gen .          # phải in "Done. Made N targets"
ninja -d explain -n chrome   # phải không còn "build.ninja.stamp is dirty"
```

Nếu `ninja` in `Regenerating ninja files` rồi dừng, thì `gn gen` đang lỗi —
đọc log `gn gen`, đừng đợi build.

### 1.7 Bốn đường dẫn sai trong simulator

Simulator dựng giá trị *kỳ vọng* từ profile. Một đường dẫn tên một field
schema không có thì đọc thành "thiết bị không biết", và simulator **rơi về
một hằng số viết cứng** — rồi hai bên của phép so sánh lệch nhau về một giá
trị mà cả hai đều đang giữ đúng. Trông như hook hỏng.

Bốn đường dẫn đó:

| Sai | Đúng |
|---|---|
| `storage_network.storage_quota_bytes` | `storage_network.quota_bytes` |
| `storage_network.rtt_ms` | `storage_network.rtt` |
| `storage_network.downlink_mbps` | `storage_network.downlink` |
| `fonts.families` | `fonts.installed` |

Đã có test chặn: `tests/test_core_document_handover.py` quét mọi đường dẫn
`_getattr_path(config, ...)` trong simulator và đòi mọi cái phải resolve được
trên profile thật.

### 1.8 Số thực bị cắt cụt khi bàn giao cho browser core

Core đọc phần lớn số bằng **reader 32-bit nguyên**, nên một float ở chỗ cần
số nguyên là lỗi kiểu và bị ép về số nguyên. Đúng cho các field core đọc
bằng `ReadInt`, **sai** cho các field core đọc bằng `ReadDouble`:

| Field | Reader của core | Bị cắt trước đây |
|---|---|---|
| `device_pixel_ratio` | `ReadDouble` | 2.625 → 2 ❌ |
| `visual_viewport_scale` | `ReadDouble` | ❌ |
| `downlink` | `ReadDouble` | ❌ |
| `base_latency`, `output_latency` | `ReadDouble` | ❌ |
| `noise_strength` | `ReadDouble` | ❌ |
| `x`, `y`, `z`, `alpha`, `beta`, `gamma` | `ReadDouble` | ❌ |

`device_pixel_ratio` bị cắt là ví dụ đắn nhất: 2.625 và 2 là **hai thiết bị
khác nhau**, và site đọc ratio cùng density sẽ thấy mâu thuẫn ngay.

Đã thêm `FRACTIONAL_FIELDS` vào `launcher/core_document.py` — danh sách này
phải khớp với các `ReadDouble` của core. Nếu core đổi reader, danh sách này
phải đổi theo.

---

## 2. Việc còn lại — theo thứ tự nên làm

### Ưu tiên 1 — Dựng `chrome.exe` (chặn mọi thứ khác)

**Trạng thái.** Build nền đang chạy tại `C:\chromium\src\out\nova-win`,
khoảng 8.600/9.570 step (~90%). Log: `C:\chromium\src\out\nova_link_chrome.log`.

> **Quan trọng:** Chromium đã có patch anti-detect đầy đủ ở tầng C++ (xem
> mục 3.2). Việc còn lại **không phải viết patch** — chỉ là build cho ra
> `chrome.exe` rồi đóng gói.

**Đã gỡ nút thắt ATL.** Link fail vì `atls.lib` không tồn tại trong bản build
này (`LNK4099: Cannot use debug info for 'atls.lib(atlbase.obj)'`). Đã:
- gỡ umbrella header ATL khỏi `base/win/atl.h`
- viết lại `Service::InitializeComSecurity()` trong
  `chrome/windows_services/service_program/service.cc` bằng Win32 SDDL thuần:

```cpp
constexpr auto com_rights_execute_local = COM_RIGHTS_EXECUTE | COM_RIGHTS_EXECUTE_LOCAL;
static_assert(com_rights_execute_local == 0x1f0007, "the SDDL below encodes these two rights by value");
PSECURITY_DESCRIPTOR security_descriptor = nullptr;
HRESULT hr = HRESULT_FROM_WIN32(
    ConvertStringSecurityDescriptorToSecurityDescriptorW(
        L"O:BAG:BAD:P(A;;0x1f0007;;;SY)(A;;0x1f0007;;;BA)(A;;0x1f0007;;;IU)",
        SDDL_REVISION_1, &security_descriptor, nullptr));
```

**Việc còn lại:**
1. Chờ build xong → xác nhận `out/nova-win/chrome.exe` tồn tại.
2. Chạy probe 21 trục trên chính `chrome.exe` này (không phải Chrome hệ thống),
   với `--nova-config` trỏ vào profile thật.
3. Đóng gói bằng `novaprofile/installer/chromium_bundle.py` (đã viết xong):

```bash
python -m novaprofile.installer.chromium_bundle \
    --chromium C:/chromium/src/out/nova-win \
    --target dist/chromium-bundle \
    --version 1.0.0
python -m novaprofile.installer.chromium_bundle \
    --target dist/chromium-bundle --verify
```

4. Commit bundle lên GitHub — **dùng Git LFS** (file ~150 MB, vượt giới hạn
   100 MB của GitHub). Xem mục 4.

### Ưu tiên 2 — Đóng gói installer Windows

Cần tạo: `installer/` chứa manifest danh sách file + script cài đặt, tạo
profile dir riêng cho mỗi profile, copy `chrome.exe` và patch sang từng bản.

Ưu tiên phụ: `.ico` / `.icns` cho installer — đã làm xong, cần rebuild sau khi
core xong.

### Ưu tiên 3 — JA3 / JA4 (tầng mạng) — ✅ ĐÃ LÀM

Đây là **giới hạn kiến trúc**, không phải việc chưa làm. JA3/JA4 là fingerprint
của **TLS ClientHello** — nằm ở tầng network, trước cả khi JS chạy. `navigator`
không đụng tới TLS, nên không có cách nào làm bằng JS.

**Đã làm xong**, trong patch `0003`:

1. `nova_core_config.h` có struct `TlsConfig` (cipher suites, supported groups,
   signature algorithms, ALPN, versions, extension order, salt).
2. `ProfileConfig` mang theo nó, và `profile.py` đưa `sig_tls` vào
   `signature_parts()` để chống trùng bao gồm cả trục TLS.
3. `net/socket/ssl_client_socket_impl.cc` ép `ClientHello` theo cấu hình:
   `SSL_set_strict_cipher_list` cho cipher suite, `SSL_set1_group_ids` cho
   curve, `SSL_set_verify_algorithm_prefs` cho thuật toán chữ ký.
4. `content/utility/services.cc` gọi `nova::InitializeNetworkProcess` khi
   network service khởi động, nên cấu hình có mặt **trước** handshake đầu
   tiên. `content/browser/network_service_instance_impl.cc` chuyển tiếp
   `--nova-config` sang tiến trình network.

**Bảng cipher theo bản Chrome** (`core/tls_profiles.py`):

| Chrome | Cipher suite | Curve |
|---|---|---|
| 100–106 | Có CBC (`AES128-SHA`, `AES256-SHA`) | X25519, P-256, P-384 |
| 107–130 | Bỏ CBC | X25519, P-256, P-384 |
| 131+ | Bỏ CBC + hybrid PQ | X25519MLKEM768, X25519, P-256, P-384 |

**Đã tìm ra ba lỗi thật khi build** — ghi lại vì chúng đều im lặng:

1. `SSL_CTX_set_custom_cipher_list` **không tồn tại** trong BoringSSL. Tên
   đúng là `SSL_set_strict_cipher_list` (chỉ có một hàm, và nó *sắp xếp* —
   nên thứ tự cipher phải do danh sách profile quyết định).
2. `SSL_GROUP_get_id_by_group_name` **không tồn tại**. BoringSSL không có
   lookup từ tên sang id; phải tự map. Và bản Chromium 160 **đã đổi tên**
   hybrid group: `X25519Kyber768Draft00` (bản Chrome 131) → `X25519MLKEM768`.
   `NovaGroupId()` trong patch là bảng map này.
3. `SSL_SIGN_ED448` **không tồn tại**; và `base::EqualsIgnoreCase` cũng không
   có — phải dùng `absl::EqualsIgnoreCase`.

**Ghi chú thêm:** nếu profile đi qua proxy, JA3 của client vẫn do Chromium tạo
ra (proxy chỉ ký chứng thực sau TLS), nên **proxy không cứu được trục này**.

**Còn lại:** trục TLS **chưa được đo** trên browser thật. Cần một probe đọc
JA3/JA4 từ phía server (tls.peet.ws hoặc tương tự) và so với bảng
`tls_profiles.py`. Đây là việc cần mạng, không làm được offline.

### Ưu tiên 4 — Bảng font theo hạng thiết bị

Hiện có `measureText` hook nhưng chưa có bảng font **theo hạng thiết bị**
(budget / mid / flagship). Một site có thể đo chiều rộng chữ ở nhiều font khác
nhau và suy ra danh sách font thật của máy. Cần:

1. Xây bảng font theo hạng — dựa trên `device_tier()` đã có trong
   `core/font_catalog.py`.
2. Sinh `font-family` list sao cho **tập font đo được** khớp với hạng.
3. Test: `tests/test_measure_text.py` mở rộng.

### Ưu tiên 5 — Mở rộng catalog thêm (đã ổn, chỉ cần duy trì)

Catalog hiện 8.954 preset. Phân bố:

| Trường | Số giá trị khác nhau | Giá trị phổ biến nhất | Tỉ lệ |
|---|---|---|---|
| `board` | 6.305 | 9 | 0.10% |
| `device` | 4.959 | 11 | 0.12% |
| `model` | 5.840 | 6 | 0.07% |
| `gpu.renderer` | 312 | 1.027 | 11.47% |

`gpu.renderer` tập trung 11.47% ở `Mali-G57 MC2` — đây là **giới hạn vật lý
đúng** (Mali-G57 MC2 thật sự là GPU phổ biến nhất trong tầm giá rẻ). Không
được "sửa" bằng cách phân bố giả — sẽ tạo fingerprint không tồn tại.

---

## 3. Trạng thái Chromium build — chi tiết

```
Source:   C:\chromium\src
Out dir:  C:\chromium\src\out\nova-win
Target:   autoninja -C out/nova-win chrome
Log:      C:\chromium\src\out\nova_link_chrome.log
```

Tiến độ tại lúc bàn giao: `[7915/9570]` — còn ~1.655 step.

**Đừng xóa `out/nova-win`.** Rebuild từ đầu mất nhiều giờ; ninja chỉ build
lại phần đổi.

### 3.1 Bản vá đã áp dụng cho Chromium

| File | Thay đổi | Lý do |
|---|---|---|
| `base/win/atl.h` | Gỡ umbrella header ATL | Gỡ `atls.lib` khỏi link |
| `chrome/windows_services/service_program/service.cc` | `InitializeComSecurity()` dùng SDDL thuần | Không còn ATL |

### 3.2 Bản vá anti-detect đã có sẵn trong Chromium

**Chromium đã được patch anti-detect thật** — không phải bản desktop vanilla.
Module fingerprint native nằm  ở `third_party/blink/renderer/core/nova/`:

| File | Dòng | Vai trò |
|---|---|---|
| `nova_core_config.h` | 559 | 44 struct: cấu hình profile + API |
| `nova_core_config.cc` | 1.079 | Đọc JSON config + suy luận HKDF |
| `nova_blink_patch.cc` | 557 | Gắn giá trị vào accessor của Blink |
| `nova_pixel_noise.{h,cc}` | 359 | Nhiễu canvas/WebGL ở tầng C++ |
| `nova_chromium_hooks.h` | 141 | Hook dùng chung |

Cơ chế: profile JSON truyền vào qua `--nova-config=/path/to/nova_core.json`,
đọc bởi browser process **trước khi** fork renderer / GPU / network service.
Không có JS hook nào — đúng như thiết kế, vì JS hook bị phát hiện dễ dàng qua
`Function.prototype.toString`, `Object.getOwnPropertyDescriptor` và Proxy trap.

**44 struct đã cover:** `SeedConfig`, `AndroidBuild`, `AndroidAbiSoc`, `Screen`,
`Orientation`, `SafeAreaInsets`, `Viewport`, `MediaQueries`, `Canvas2DConfig`,
`WebGLImageNoise`, `WebGLParameters`, `ShaderPrecisionRange`, `ShaderPrecision`,
`WebGLConfig`, `WebGPUAdapter`, `WebGPUConfig`, `AudioConfig`, `RectsConfig`,
`FontsConfig`, `MediaDeviceEntry`, `MediaDevicesConfig`, `StorageNetworkConfig`,
`BatteryConfig`, `SpeechVoice`, `GamepadConfig`, `BatterySpeechGamepad`,
`Vector3D`, `Orientation3D`, `SensorsConfig`, `BrandVersion`, `NavigatorConfig`,
`UserAgentDataConfig`, `PermissionsConfig`, `TimezoneConfig`, `LocaleConfig`,
`GeolocationConfig`, `ProxyConfig`, `WebRtcConfig`, `DnsConfig`,
`HttpHeadersConfig`, `GeoIntelligenceConfig`, `GeoSyncConfig`, `ProfileConfig`.

**Các file Blink đã sửa** (theo `.nova_applied.txt`, 24 file, 3.048 dòng):

| File | Trục |
|---|---|
| `content/app/content_main_runner_impl.cc` | Đọc `--nova-config` |
| `core/frame/navigator_ua.cc`, `navigator_ua_data.cc` | UA + Client Hints |
| `core/frame/navigator_device_memory.cc` | `navigator.deviceMemory` |
| `core/frame/navigator_concurrent_hardware.cc` | `navigator.hardwareConcurrency` |
| `core/frame/navigator_id.cc` | `navigator.id` / seed |
| `core/frame/navigator_language.cc` | `navigator.language` |
| `core/execution_context/navigator_base.cc` | Điều phối |
| `core/frame/navigator.cc`, `local_dom_window.cc` | `maxTouchPoints`, `window` |
| `core/frame/screen.cc` | `screen.*` |
| `core/events/navigator_events.cc` | Event |
| `modules/canvas/canvas2d/base_rendering_context_2d.cc` | Canvas noise |
| `modules/webgl/webgl_rendering_context_base.cc` | WebGL limits |

**Còn thiếu trong Chromium:**

| Trục | File đích | Ghi chú |
|---|---|---|
| **JA3 / JA4** | `net/ssl/ssl_client_socket_impl.cc` | TLS ClientHello — bắt buộc |
| Proxy/DNS qua Chromium | `net/proxy_resolution/` | config đã có struct, cần nối |
| `FontsConfig` → font list thật | `third_party/blink/renderer/core/css/` | cấu hình có, chưa áp |
| `WebRtcConfig` | `third_party/blink/renderer/modules/webrtc/` | cấu hình có, chưa áp |
| `DnsConfig` | `net/dns/` | cấu hình có, chưa áp |

> **Lưu ý đã sửa:** bản `NEXT_STEPS.md` đầu tiên ghi "Chromium chưa có patch
> fingerprint nào" — sai. Chromium **đã có** patch đầy đủ ở tầng C++. Việc
> còn lại là **build nó ra `chrome.exe`**, không phải viết lại từ đầu.

### 3.3 Script build

Trong `C:\chromium\src`:

| Script | Việc |
|---|---|
| `nova_gen.bat` | `gn gen out/nova` (win64/x64) |
| `nova_build.bat` | `autoninja -C out/nova` |
| `nova_link_chrome.bat` | `autoninja -C out/nova-win chrome` → log |
| `nova_probe_sdk.bat` | Probe SDK |

Cấu hình `gn` đang dùng:

```
target_os="win" target_cpu="x64" is_debug=false is_official_build=false
treat_warnings_as_errors=false use_remoteexec=false blink_symbol_level=1
dawn_enable_d3d12=false
```

**Vì sao `target_os="win"` chứ không phải `"android"`:** Chromium 159 có
`assert(host_os == "linux", "Android builds are only supported on Linux.")`
trong `build/config/BUILDCONFIG.gn`, nên target Android **không generate được**
từ host Windows. Cấu hình win64 compile **đúng cùng** các translation unit
của Blink — mã NOVA và các accessor đã sửa là C++ độc lập nền tảng.

**`dawn_enable_d3d12=false`** bỏ backend D3D12 của Dawn (kèm DXC); nguồn DXC
include `<atlbase.h>` mà máy không có ATL. WebGPU không phải thứ cần verify ở
đây, và cấu hình win64 không D3D12 vẫn compile cùng các unit Blink.

Hai biến môi trường bắt buộc:

```
DEPOT_TOOLS_WIN_TOOLCHAIN=0     # dùng VS Build Tools cục bộ, không tải toolchain Google
vs2022_install=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
```

> `vs2022_install` là override được hỗ trợ: `build/vs_toolchain.py` tìm VS 2022
> dưới `%ProgramFiles%`, còn Build Tools nằm ở `%ProgramFiles(x86)%`.

---

---

## 4. Đẩy `chrome.exe` lên GitHub — cần Git LFS

`chrome.exe` build từ Chromium có dung lượng ~150 MB, vượt giới hạn 100 MB
của GitHub (blocking push). Giải pháp:

```bash
git lfs install
git lfs track "*.exe"
git lfs track "*.pak"
git add .gitattributes
git commit -m "chore: track the built browser through Git LFS"
```

Sau đó `chrome.exe` push như file LFS pointer.

**Cần xác nhận trước khi push:**
- [ ] Repo có bật LFS quota (GitHub free: 10 GB storage / 10 GB bandwidth/tháng)
- [ ] Có xác nhận `git lfs` đã cài (`git lfs version`)
- [ ] Quyết định: push **bản build đầy đủ** hay push **script build + source patch**?
      - Bản đầy đủ → khách không cần build, nhưng repo nặng
      - Script build + patch → repo nhẹ, nhưng khách phải có VS + depot_tools

**Khuyến nghị:** push **cả hai**. Bản build đầy đủ để dùng ngay, cộng script
build để tái lập được.

---

## 5. Bảo mật — file nhạy cảm đã xử lý thế nào

Repo `mtykp197-blip/NOVAPROFILE` là **private** (API GitHub trả 404 khi gọi
không xác thực → repo không public).

### 5.1 `source tham khảo/` — code của hệ sinh thái KHÁC

Thư mục này chứa code của một sản phẩm fingerprint khác (Hidemium / Nova
engine), dùng **để đối chiếu chứ không thuộc sản phẩm này** (xem `PROGRESS.md`
dòng 897). Đã quét: **không có secret cụ thể** — chỉ có tên biến
(`token`, `password`, `secrets.token_hex`) và logic xác thực, không có giá trị
thật.

Đã push lên repo private. **Lưu ý:** nếu repo đổi từ private → public, phải
xoá thư mục này trước, vì nó là code của bên khác.

### 5.2 Những gì KHÔNG được push

| Thứ | Lý do | Hiện trạng |
|---|---|---|
| `CHAT_TRANSFER*` | Chứa credentials tài khoản Cursor | Đã `.gitignore` ✅ |
| `.dataset_probe/` | 33 MB CSV thô, tái tạo được | Đã `.gitignore` ✅ |
| `*.log`, `state.db`, `keys/`, `profiles/` | Dữ liệu runtime | Đã `.gitignore` ✅ |
| `*.pyc` | Bytecode | Đã `.gitignore` ✅ |
| `.python_path` | Đường dẫn interpreter của máy | Đã `.gitignore` ✅ |

### 5.3 Việc cần làm

- [ ] Bật **2FA** cho tài khoản `mtykp197-blip`
- [ ] Bật **branch protection** trên `main`
- [ ] Kiểm tra GitHub **secret scanning** alerts
- [ ] Xác nhận repo vẫn là private trước mỗi lần push thêm file

---

## 6. Ước lượng thời gian

| Việc | Ước lượng | Phụ thuộc |
|---|---|---|
| Build `chrome.exe` xong | 30–60 phút (còn ~1.655 step) | Máy build |
| Probe 21 trục trên `chrome.exe` mới | 30 phút | Có `chrome.exe` |
| Patch Chromium cho fingerprint (7 trục) | 3–5 ngày | Có build xong |
| JA3/JA4 (TLS ClientHello) | 2–3 ngày | Song song với patch trên |
| Bảng font theo hạng | 1–2 ngày | Độc lập |
| Installer + đóng gói | 1–2 ngày | Có `chrome.exe` |
| Git LFS + push `chrome.exe` | 2 giờ | Có LFS quota |
| **Tổng** | **~2 tuần** | |

**Ưu tiên theo giá trị:** patch Chromium cho fingerprint trước (mở khóa mọi
thứ khác), rồi JA3/JA4 cùng lúc (cùng một lần build), rồi installer.

---

## 7. Lệnh thường dùng

```bash
# Test
cd novaprofile/control_plane
export PYTHONPATH="$PWD"
python -m pytest tests -q

# Sinh lại device catalogue (sau khi sửa generator)
python tools/generate_device_presets.py

# Build Chromium
cd C:/chromium/src
autoninja -C out/nova-win chrome

# Probe trên Chromium thật
python -m novaprofile.probe ...
```

---

## 8. Những bẫy đã gặp — đừng lặp lại

1. **ATL trong Chromium build.** `atls.lib` không có trong bản build này. Đừng
   thêm lại `#include <atlbase.h>` vào bất kỳ file nào.

2. **`derive_bytes` / `derive_int` không biết `stratum`.** Đã sửa ở
   `core/seed.py` (`_info()` fold `stratum` vào HKDF info). Đây là lý do trước đây
   mọi profile từ cùng master seed có **cùng serial, cùng incremental build,
   cùng salt**.

3. **Chrome band của Android 16.** Chromium 137 là milestone đầu tiên hỗ trợ
   Android 16 (API 36) → band mở rộng từ 5 phiên bản (156–160) lên 24
   (137–160). Đừng thu hẹp lại.

4. **`maxTouchPoints` là số ngẫu nhiên.** Đã đổi sang suy ra từ phần cứng thật
   (`inferred_touch_points(preset)`).

5. **Renderer lỗi trong catalogue nguồn.** Đừng tin `gpu_renderer` của
   `device-catalog.json` — nó đã bị scraper ghép. Luôn đi qua `clean_renderer()`.

6. **`GPU_TABLE` chỉ 38 dòng.** Không dùng nó để suy luận — dùng `gpu_facts()`.

7. **Test marker cần word-boundary.** `"ipad" in "MultiPad"` → sai.

---

## 9. Trạng thái test

Lượt bàn giao: **1784 passed, 6 skipped** (trước khi mở rộng catalog lên 8.954).

Sau thay đổi lượt này: chạy lại full suite để xác nhận — xem `PROGRESS.md`
mục 6d.
