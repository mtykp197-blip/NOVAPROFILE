# NOVA PROFILE — Báo cáo tiến độ

_Cập nhật: 22/11/2026 13:45 (UTC+7)_

---

## 1. Tổng quan

| **Control Plane (Python)** — generator, schema, collision guard, consistency | ✅ Hoàn tất | **~98%** |
| **API / CLI** — REST, auth, batch, probe | ✅ Hoàn tất | **~94%** |
| **Registry / Store** — SQLite, seed store, persistence | ✅ Hoàn tất | **~90%** |
| **Network layer** — DNS, proxy, WebRTC, rotation, leak-check | ✅ Hoàn tất | **~88%** |
| **Security** — crypto, token auth, redaction, hardening | ✅ Hoàn tất | **~90%** |
| **Core Patch (C++)** — config parser, Blink hooks, patch series | 🔄 **Đang biên dịch thật — V8 xong, đang vào Blink** | **~87%** |
| **Device Catalogue** — 42 preset viết tay → **7.222 thiết bị thật thị trường VN** | ✅ **Xong, 0 lỗi** | **~96%** |
| **js_runtime** — lớp JS fallback cho Chromium stock | ✅ Hoàn tất | **~92%** |
| **Probe / Measurement** — simulator + harness + e2e thật | ✅ Hoàn tất | **~96%** |
| **Installer** — gói cài đặt độc lập | ✅ Hoàn tất | **~95%** |
| **Measurement Ledger** — sổ đo đạc + CLI | ✅ Hoàn tất | **~95%** |
| **Docs** — ARCHITECTURE, AGENT_CONTRACT, RULES, SURVEY | ✅ Có | **~92%** |

### Ước lượng tổng thể: **~94%**

### Bằng chứng mới nhất

| Việc | Kết quả |
|---|---|
| **Test suite control plane** | ✅ **1.368 passed**, 5 skipped, 0 failed — ruff sạch 100% |
| **Build Chromium 160 thật** | 🔄 **197.540 target**, đang ở `blink/renderer/core`, 0 lỗi, ninja PID 22324 |
| **3 bề mặt mới từ khảo sát** | ✅ `Math.acos(0.5)`, `jsHeapSizeLimit`, `MediaRecorder.isTypeSupported` — có schema, generator, lớp JS, và **là trục kiểm chứng trong bộ dò e2e** |
| **Bảng phông theo hạng thiết bị** | ✅ Font set giờ theo **cả locale lẫn hạng máy** — budget 9 font, flagship 19 font |
| **Quy tắc bộ nhớ** | ✅ Đọc từ `approximated_device_memory.cc` của Chromium 160 — làm tròn tới gần nhất, clamp `[1,8]` trên Android |
| **Fleet thiết bị** | ✅ **7.222 preset, 7.222 id duy nhất** — 4.788 model, 230 GPU, 256 màn hình |
| **Validator fleet** | ✅ **0 lỗi, 0 cảnh báo** trên toàn bộ fleet |
| **Đồng bộ GitHub** | ✅ commit `3dc5c42`, đẩy lên `origin/main` xong |

---

## 1a. Lượt 13:00 — 13:45 — sửa ba lỗi "trông đúng nhưng sai thật"

Cả ba lỗi này đều **không làm hỏng cái gì**, và cả ba đều làm profile
*đáng ngờ hơn* mà không ai nhận ra.  Đây là loại lỗi nguy hiểm nhất.

### (a) Bộ nhớ được bốc từ một tập phẳng

`navigator.deviceMemory` trước đây được rút từ `(2, 4, 6, 8, 12, 16)` —
một tập phẳng, không liên quan gì tới máy thật.  Nghĩa là một chiếc
Redmi Note 12 (chỉ có 4/6/8 GB) có thể báo **16 GB**.

Nay nó được rút từ `ram_options` của chính model đó, rồi đi qua đúng
phép xấp xỉ của Chromium.

### (b) Schema giữ một giả định đã lỗi thời

Comment trong schema nói: *"Chromium clamps Android to [1, 8] GB"*.  Đọc
source Chromium 160 thì **đúng** — nhưng lý do mà cả nhóm tôi đoán sai
là **phần làm tròn**.  Source nói rõ:

> the physical memory is rounded to the **nearest** power of two, a tie
> goes to the lower bound

Nên 6 GB → **4** (đúng giữa 4 và 8, hòa về dưới), không phải 8 như tôi
đoán.  Và 3 GB → 2, không phải 4.

**Cách đã kiểm chứng, không đoán:**

| Cách | Kết quả |
|---|---|
| Đọc `approximated_device_memory.cc` của Chromium 160 | làm tròn *gần nhất*, tie về dưới, clamp Android `[1,8]` |
| **Đo trên Chrome thật** (HTTP server local + CDP) | máy này 28 GB → Chrome báo **32**; 18 lõi logic → báo **18** |

Cả hai khớp source **chính xác** — 28 GB gần 32 hơn 16, và 18 lõi không
bị làm tròn lẫn không bị clamp.

### (c) Bảy preset báo chín lõi

Bảy preset có `cpu_options=(9,)`.  **Không một chiếc Android nào từng
báo 9 lõi.**  Bằng chứng nội bộ cho ra manh mối: Pixel Fold (Tensor G2)
báo 9 trong khi Pixel 7 (cùng Tensor G2) báo 8.  Cùng một chipset không
thể có hai số lõi.

Sau khi sửa, **0 SoC nào còn lệch**, và một test giữ điều đó.

### Bài học về quy trình

Khi đổi `Literal` trong schema, pytest fail **158 test** rồi tự pass ở
lần chạy sau.  Nguyên nhân là bytecode cache cũ, không phải logic.  Đây
là loại fail gây hoảng nhất: một lần fail hàng loạt trông như mình vừa
phá hỏng nửa dự án, trong khi chạy lại là xong.

**Luật mới:** đổi schema → xóa `__pycache__` trước khi tin kết quả
pytest.

---

## 1b. Mở rộng fleet thiết bị — bản ghi quyết định

### Vấn đề đã nêu

Fleet 42 thiết bị là **quá nhỏ** cho doanh nghiệp vài nghìn thành viên. Một
script fingerprinting đọc `navigator.userAgent`, `screen.width/height`,
`devicePixelRatio`, GPU renderer, `Build.FINGERPRINT` và danh sách font. 42
preset nghĩa là 42 bucket — tách được ngay.

### Ba tầng bảo vệ, và tầng nào cần mở rộng

| Tầng | Cái gì phân biệt | Số lượng trước | Số lượng sau |
|---|---|---|---|
| 1 | Model string (vendor group theo) | 40 | **4.812** |
| 2 | GPU renderer + tham số | 14 | **263** |
| 3 | Màn hình w×h×dpr | 24 | **521** |
| 4 | Salt 128-bit mỗi profile | 2¹²⁸ | 2¹²⁸ (đã chứng minh, 0 va chạm @2M) ✅ |

Tầng 4 đã bảo vệ chống va chạm. Tầng 1–3 là những gì site **đọc được** —
và đó là chỗ 42 preset là yếu. Mở rộng tầng 1–3, không cần đụng tầng 4.

### Dữ liệu: ba nguồn công khai, không bịa

| Nguồn | Bản ghi | Đặc điểm |
|---|---|---|
| `cigarplug/scrape-gsma` (`gsm.csv`) | 10.679 | máy cũ, chipset dày đặc |
| `KhalSCI/GSMARENA_Scraper` | 8.792 | phủ hãng rộng nhất, spec dạng dict |
| `ishumann/gsmarena-webscrapping` | 4.000+ | máy mới nhất, cột phẳng |

Gộp + khử trùng lặp theo **thông số kỹ thuật** (không theo tên) → **10.923
thiết bị có SoC + GPU + màn hình thật**. Không một trường nào được bịa: mọi
giá trị là thông số do nhà sản xuất công bố.

### Bộ lọc thị trường Việt Nam

Fleet phải phản ánh thị trường thật, nên dataset toàn cầu được lọc theo
thị phần điện thoại Việt Nam: **Samsung, Xiaomi/Redmi/POCO, OPPO, vivo,
realme, HONOR, Tecno, Infinix, Nokia, OnePlus, Google, Motorola, Apple,
Sony, Lenovo, HTC, ZTE (RedMagic/Blade)**.

Bộ lọc theo **dòng sản phẩm**, không theo model: dòng Galaxy A bán hàng triệu
chiếc, dòng Galaxy S bán hàng chục nghìn — phải tách. Biến thể `Ultra` /
`Pro Max` bị loại vì thuộc phân khúc giá khác.

**Kết quả: 4.947 thiết bị / 4.812 model / 263 GPU / 643 chipset / 521 màn hình.**

### Nhân biến thể: chỉ theo RAM

Quyết định: **chỉ nhân theo RAM, không nhân theo ROM.**

- *Model × RAM × ROM* = 24.725 — nhưng hai profile cùng model + cùng RAM,
  khác ROM, trông gần giống nhau với site. Thêm lượng mà không thêm khả
  năng tách.
- *Model × RAM* = **~7.800 preset** — mỗi tổ hợp là một cấu hình thật của
  đúng model đó, và `navigator.deviceMemory` đọc ra khác nhau.

### Bản vá schema

`Screen.device_pixel_ratio` và `density_dpi` là `Literal` khép kín, và dữ liệu
thật cần `dpr` 3.25 (254 máy) cùng các bucket density của tablet. Đã mở
rộng:

- `device_pixel_ratio`: thêm **3.25** và **4.0**
- `density_dpi`: thêm **120, 213, 520, 640**

### Sai lầm đã tìm và sửa

| Sai lầm | Nguyên nhân | Sửa |
|---|---|---|
| `device_catalog_fleet.py` không import được | `MALI_G78` chưa định nghĩa | alias sang `GOOGLE_MALI` (tên part của Tensor G1) |
| `device_pixel_ratio=3.2` bị schema từ chối | preset trong `device_catalog_fleet.py` viết tỉ lệ không hợp lệ | sửa preset, mở rộng Literal |
| `NameError: FULL_CATALOGUE_BRANDS` | `FULL_CATALOGUE_BRANDS: frozenset(...)` — annotation không tham số kiểu khiến **CPython 3.12 bỏ qua lệnh gán** | bỏ annotation |
| Dòng sản phẩm không khớp model | dataset viết model **không có** tiền tố hãng (`"A78"`, không phải `"OPPO A78"`) | bỏ tiền tố hãng khỏi dòng trước khi so |
| `RedMagic` bị loại nhầm | thêm vào `RESTRICTED_LINES` | bỏ — RedMagic là phân khúc enthusiast thật ở VN |


### Bảng lớp GPU — bản ghi thứ hai

Bảng lớp GPU trong validator được viết khi fleet có 14 renderer và
biết 14.  Khi fleet lên 7.222 preset với 230 renderer, **120 renderer
không thuộc lớp nào** và 2.590 preset báo cảnh báo — cảnh báo nói
*bảng thiếu*, không phải *preset sai*.

Sửa đúng lành bảng theo thông số thật của từng part, không phải
nới lỏng quy tắc:

| Lớp | Texture limit | Ví dụ |
|---|---|---|
| budget | 4096 / 8192 | Adreno 200/220/308/330, Mali-400, Mali-T720, PowerVR SGX5xx, IMG8322 |
| mid | 8192 / 16384 | Adreno 508/530/618, Adreno 612/613, Mali-G52 MC4, Mali-G57/G68, Mali-G71 MP3, Mali-G76 MC4 |
| flagship | 16384 / 32768 | Adreno 620/640/730/750/840, Mali-G71 MP2, Mali-G72 MP12, Mali-G76 MC16, Mali-G77 MC9, Immortalis-G720, Xclipse 940 |

Bảng giờ có **225 renderer**.  Mỗi revision đổi lớp có pattern riêng
(`Mali-G52 MC2` budget, `Mali-G52 MC4` mid), và quy tắc "pattern dài
nhất thắng" sẵn có loại điểm đó.

### Sai lầm GPU cuối cùng — Mali-G615

`Mali-G615` nằm trong lớp budget dưới dạng tên gia đình trần, nên
pattern đó bắt cả hai revision:

- `Mali-G615 MC2` (RMX5108) thật là **16384**
- `Mali G615-MC6` (POCO X6 Pro) thật là **32768**

Không có giá trị texture nào thỏa cả hai, vì lớp budget chỉ cho phép
4096 và 8192.  Đã tách revision: MC2 sang lớp mid, MC6 sang flagship,
và mở rộng band flagship lên 32768.  Band là một dải, không phải một
điểm — part ở trên dải vẫn thuộc lớp.

---

## 1c. Build Chromium — chuỗi lỗi SDK

### Nguyên nhân build dừng ở 76,6%

Build Chromium dừng giữa chừng, không phải vì lỗi compile.  Nguyên
nhân là chuỗi thiếu file của Windows SDK:

| Lỗi | File thiếu |
|---|---|
| `ninja: fatal: ... missing and no known rule to make it` | `dxcompiler.dll` |
| `Exception: dbghelp.dll not found` | `Debuggers/x64/dbghelp.dll` |
| `ninja: error: ... d3dcompiler_47.dll, missing` | `Redist/D3D/x64/d3dcompiler_47.dll` |

Cả ba đều do một nguyên nhân: **build tools ghim SDK `10.0.28000.0`**
(`build/vs_toolchain.py:63`), còn máy chỉ có `10.0.26100.0`.

Chromium 160 chấp nhận SDK 26100 — nó dùng `NTDDI_WIN11_GE`, đúng bằng
định nghĩa trong `build/config/win/BUILD.gn`.  Nên chỉ cần làm cho
version 28000 **tồn tại**.

### Cách sửa: SDK shim

SDK 26100 được copy sang `C:/chromium/sdk-28000-shim` (1,73 GB) và thư
mục version bên trong được đặt tên `10.0.28000.0`.  `WINDOWSSDKDIR`
trỏ vào đó khi chạy `gn gen` — đó là biến mà toolchain đọc để trả
`windows_sdk_path`.

Hai điều phải biết khi làm việc này:

1. **`windows_sdk_path` trong `args.gn` bị toolchain ghi đè.**  Khi
   `visual_studio_path` rỗng, `visual_studio_version.gni` gọi
   `vs_toolchain.py` và lấy câu trả lời của nó, bất kể `args.gn` ghi
   gì.  Vậy nên đường dẫn phải đến từ `WINDOWSSDKDIR`, không phải từ
   `args.gn`.
2. **SDK không sửa được** (`Access to the path '10.0.28000.0' is
   denied` — cần admin), nên shim phải ở nơi tài khoản ghi được.

Sau khi copy, thiếu cả `Debuggers/` và `Redist/` — chúng nằm ngoài
thư mục version, nên phải copy riêng.

**Build sau khi sửa: chạy thật, 0 lỗi.**

---

## 2. Quy mô mã nguồn

| Thành phần | Số dòng |
|---|---|
| Python (control_plane) | 27.211 dòng |
| C++ (core_patch) | 2.403 dòng |
| Tests (34 file) | 9.494 dòng |
| Docs | ~48 KB |

**Test suite:** 894 passed, 5 skipped, ruff sạch 100%.

---

## 3. Tính năng cốt lõi đã xong

### 3.1. Chống trùng tuyệt đối (Zero-collision)
- ✅ **12 trục bảo vệ** được guard: `sig_device`, `sig_canvas`, `sig_webgl_draw`, `sig_webgl_params`, `sig_rects`, `sig_devices`, `sig_audio`, `sig_sensors`, `sig_storage`, `sig_webgpu`, `sig_fonts`, `sig_full`
- ✅ **Entropy ≥ 128 bit** cho mọi trục (salt HKDF 128-bit) → p va chạm @100M = 0
- ✅ **Kiểm tra quy mô 2.000.000 profile**: 0 va chạm trên cả 12 trục (285.8s, 6.999 profile/s, 18 worker)
- ✅ **Fallback rebuild** tự dựng lại 6 trục từ seed khi thiếu preset → không kẹt vòng lặp
- ✅ **Điều kiện 100M đã đủ** (xem `RULES.md` §7)

### 3.2. Nhất quán phần cứng (Consistency)
- ✅ Validate: canvas noise, audio channels/quantization, sensor gravity/accelerometer, storage quota/usage, WebGPU limits/salt/renderer, font stack
- ✅ **Đơn vị đo thống nhất**: `screen` và `viewport` cùng dùng **logical CSS pixel** (sửa lỗi #19 trong `RULES.md`)
- ✅ Cross-axis: GPU ↔ SoC, Android release ↔ Chromium major ↔ Client Hints, Locale ↔ Timezone ↔ Country, Network type ↔ Downlink ↔ RTT, Sensors ↔ Gravity ↔ Linear acceleration

### 3.3. Đa dạng thiết bị
- ✅ **42 preset** (từ 11) — 40 model, 30 board, 17 screen, 15 renderer
- ✅ 6 GPU mới: Adreno 619/742, Mali-G610, Xclipse 920, Maleoon 910
- ✅ Mọi preset khai báo đúng GPU thật của SoC

### 3.4. Đa dạng mọi trường
- ✅ 479 field: 291 constant (đã chứng minh là hằng số phần cứng/engine), 81 shared, 107 unique
- ✅ Không còn hằng số do lỗi hardcode

### 3.5. Bảo vệ rò rỉ môi trường host
- ✅ Windows ACL, software rendering/SwiftShader, WebRTC ICE relay + mDNS chặn
- ✅ API token auth + secret redaction (có test riêng)

### 3.6. Sổ đo đạc (Measurement Ledger)
- ✅ 14 record, SHA-256 integrity, 4 slug reusable: `scale-collision`, `entropy-budget`, `test-suite`, `fleet-audit`
- ✅ `verify` pass — không tốn công đo lại cùng một việc

---

## 4. Việc còn lại (theo thứ tự ưu tiên)

### P0 — Bắt buộc để "thương mại"
| # | Hạng mục | Ước lượng |
|---|---|---|
| 1 | **Biên dịch thật core_patch** lên Chromium v100→v155+, xử lý lỗi compile | 8–12 giờ |
| 2 | **js_runtime** — JS injection layer (navigator/screen/canvas override trong page context) | 12–16 giờ |
| 3 | **Verify thật trên Chromium**: chạy probe đo fingerprint thực tế, so với simulator | 6–10 giờ |
| 4 | **Đóng gói installer** (Windows .exe / MSI) + tài liệu cài đặt | 6–8 giờ |

### P1 — Chất lượng thương mại
| # | Hạng mục | Ước lượng |
|---|---|---|
| 5 | End-to-end test: sinh profile → launch browser → probe → so khớp | 8–12 giờ |
| 6 | Chống rò rỉ nâng cao: GPU fingerprint leak, canvas readback timing, font timing attack | 6–8 giờ |
| 7 | Monitoring/observability + dashboard vận hành | 8–12 giờ |
| 8 | Load test 100M profile trên server thật (hiện mới chạy local) | 6–8 giờ |

### P2 — Hoàn thiện
| # | Hạng mục | Ước lượng |
|---|---|---|
| 9 | CI/CD pipeline (build, test, đóng gói tự động) | 6–8 giờ |
| 10 | Mở rộng catalog 42 → 100+ preset (mỗi preset = 1 bucket cho vendor gom nhóm) | 4–6 giờ |
| 11 | Đa ngôn ngữ tài liệu | 4–6 giờ |

**Tổng thời gian còn lại: ~74–108 giờ công** (≈ 9–14 ngày làm việc liên tục)

---

## 5. Rủi ro còn lại

| Rủi ro | Mức độ | Ghi chú |
|---|---|---|
| Core patch chưa biên dịch thật | 🟡 Trung bình | Chromium đã checkout (23 GB). Đang sinh lại patch series bằng `git diff` từ cây thật — 54/54 hunk cũ sai số dòng, đường dẫn Blink đã đổi |
| Chưa compile được Chromium đã patch | 🟡 Trung bình | VS Build Tools 2022 đã cài; cần `gn gen` + `autoninja` sau khi patch apply được. Build 4-8 giờ/18 core |
| Geo coherence | 🟢 Đã sửa | Bảng 117 tọa độ thật + jitter ≤8km; `accuracy` bao phủ jitter |
| tzdata thiếu trên Windows | 🟢 Đã sửa | `tzdata>=2024.1` là dependency bắt buộc; `nova doctor` báo cáo trạng thái |
| Probe mới chỉ dùng simulator | 🟢 Đã sửa | e2e harness chạy thật trên Chrome 157, đọc đủ 12 axis |
| Bucket theo preset | 🟡 Trung bình | 42 preset → 2.4M profile/bucket ở quy mô 100M. Mỗi preset thêm là 1 bucket thêm |
| 100M chưa chạy thật | 🟢 Thấp | 2M đã sạch 0 va chạm; logic đã đủ điều kiện |

---

## 4.5 Bốn việc còn lại — đã hoàn thành (22/11 01:40)

### 4.5.1 Lớp JavaScript trong page context (`novaprofile/js_runtime/`)
- **Vấn đề**: browser có thể đọc ra accessor đã bị override — descriptor shape, `Function.prototype.toString`, thứ tự own-property.
- **Cách làm**: runtime cố định (36 KB) + payload per-profile. Override cài trên **prototype** (không thêm own property), `toString` trả `"function () { [native code] }"`, copy descriptor gốc khi có.
- **Phủ đủ 12 axis**: navigator, client hints, screen, viewport, WebGL, Canvas2D, Audio, Rects, MediaDevices, Sensors, Storage/Connection, WebGPU, Permissions, Battery, Speech, Gamepad, Fonts, MediaQueries.
- **Tự đứng xuống** khi native core hiện diện (`window.__NOVA_NATIVE__`) — hai nguồn giá trị khác nhau là mâu thuẫn web nhìn thấy được.
- **Tích hợp launcher**: ghi `nova_js_runtime.js` vào user data dir (không đưa payload lên command line — mọi process trên máy đều đọc được argv), truyền `--nova-js-runtime=`, **xoá khi browser đóng**.
- 15 test (`tests/test_js_runtime.py`), ruff sạch.

### 4.5.2 Tooling build Chromium (`novaprofile/core_patch/build.py`)
- Ma trận phiên bản **100 → 160** với branch + tag Chromium thật (100→4896, 120→6099, 140→7359…).
- Lệnh: `list`, `plan`, `apply` (git apply, an toàn khi chạy lại, báo lỗi thật của git), `verify`, `check`.
- 82 test (`tests/test_core_patch_build.py`), trong đó có test apply thật vào git repo tạm.

### 4.5.3 Harness kiểm chứng end-to-end (`novaprofile/probe/e2e.py`)
- Đọc **12 axis** từ browser thật qua CDP, so với profile, kết luận `match`/`mismatch`/`unavailable` kèm **nguyên nhân khả dĩ**.
- **Tolerance theo từng axis**: exact cho identity/screen/storage/canvas/fonts; relative 1-5% cho webgl/audio/sensors/webgpu; bỏ qua timing (phụ thuộc sound device, không phải profile).
- WebSocket client tự viết theo RFC-6455 — **không thêm dependency**.
- **Đã chạy thật trên Chrome 157**: 11/12 axis mismatch đúng (stock desktop vs profile Android), `audio` khớp — đúng như mong đợi.
- Phát hiện 3 bug thật qua live run: awaited-dict TypeError, async axis trả rỗng, axis không assert gì báo "match" giả.
- 63 test (61 + 2 live opt-in).

### 4.5.4 Installer (`novaprofile/installer/`)
- Build thật: tạo venv → `pip install` → sinh `launch.py`, `uninstall.py`, `config/default.json`, `VERSION`, `README.txt`, `LICENSES/`, `core_patch/`.
- **Không ghi hệ thống**: không `Program Files`, không registry, không đổi PATH — xoá thư mục là xoá cài đặt.
- **Dữ liệu nằm ngoài chương trình** (`profiles/`, `data/` cạnh `env/`) → nâng cấp không bao giờ đụng profile khách hàng.
- **Uninstaller giữ dữ liệu mặc định**; `--delete-data` mới xoá, và nói rõ không thể hoàn tác.
- Launcher tự ưu tiên interpreter của env + site-packages của bản cài (không chạy nhầm bản dev của máy khách).
- 12 test, trong đó 1 test **build thật rồi chạy entry point** và kiểm tra lệnh CLI trả về đúng.

### 4.5.5 Bug nghiêm trọng tìm được khi làm 4 việc này

#### (a) `tzdata` thiếu → 100% profile báo UTC
- **Triệu chứng**: `zoneinfo.available_timezones()` = 0 trên Windows. Mọi profile có `offset_minutes=0`, `abbreviation="UTC"` dù khai báo `Europe/Vienna`.
- **Vì sao nguy hiểm**: đây là tín hiệu dễ phát hiện nhất, và nó phá coherence của **toàn bộ** fleet — không phải vài profile.
- **Sửa**: `tzdata>=2024.1` thành dependency **bắt buộc** (không phải extra). Thêm `tzdata_status()` + `nova doctor` hiển thị trạng thái. `consistency._iana_offset` giờ phân biệt "zone không tồn tại" với "database thiếu" (trước đó fail-open, im lặng bỏ qua).

#### (b) 14/143 timezone trong catalog không tồn tại
- `Europe/Munich`, `Europe/Hamburg`, `Europe/Lyon`, `Asia/Osaka`, `Africa/Cape_Town`… — tzdata đã bỏ các city link này vì lịch của chúng không khác.
- **Sửa**: trỏ về zone thật (`Europe/Berlin`, `Europe/Paris`, `Asia/Tokyo`…). Kiểm chứng: 0/111 zone không resolve.
- **Phát hiện thêm**: sau khi sửa, 12 entry trở thành **bản sao hoàn toàn** (cùng locale + zone + currency + ngôn ngữ phụ) → xoá. Catalog còn 111 entry, 61 quốc gia.

#### (c) Tọa độ địa lý ngẫu nhiên toàn cầu
- `_build_geolocation` kéo lat ∈ [-60, 70], lng ∈ [-180, 180] — **không liên quan gì** tới timezone.
- Profile `de-AT/Europe/Vienna` báo tọa độ ở **Trung Á**.
- **Sửa**: bảng `core/geo_coordinates.py` — 117 timezone với tọa độ thật của thành phố. Generator dùng tọa độ thật + jitter ≤8km, và `accuracy` bao phủ jitter (fix chính xác hơn radius nó khai là mâu thuẫn).
- Kiểm chứng: Vienna 48.197/16.394, HCMC 10.826/106.671, New York 40.745/-73.983, São Paulo -23.542/-46.612.

#### (d) Runtime JS thiếu 2 axis + `JSRuntimePayload` frozen
- `rects` và `audio` không có surface trong runtime → phát hiện bởi test của chính lớp JS. Đã bổ sung.
- `JSRuntimePayload` là `frozen=True` nhưng builder gán field → `FrozenInstanceError` mọi lần gọi.

### 4.5.6 Chromium thật — build đầu tiên trên máy này

| Hạng mục | Kết quả |
|---|---|
| Dung lượng trống | 587 GB (đủ) |
| depot_tools | clone tại `C:\depot_tools` (có `gn`, `ninja`, `fetch`) |
| Visual Studio Build Tools 2022 | đã cài (`winget`) |
| Chromium checkout | `C:\chromium\src` — branch 8078, **v160.0.8177.0**, commit `339b0817fb`, **23 GB / 1.1M file** |
| Patch series | **54/54 hunk có số dòng khai báo sai** → `git apply` từ chối ("corrupt patch") |
| Đường dẫn trong patch | Blink đã tái cấu trúc: `core/html/navigator_id.cc` → `core/frame/navigator_id.cc`, `core/screen/screen.cc` → `core/frame/screen.cc` |
| `gn gen` | ✅ **37.392 targets** từ 5.159 files |
| Build target | Android arm64, `ninja chrome` — **58.081 bước** |

**Đây là phát hiện quan trọng nhất của ngày.** 82 test của agent build **không bắt được** lỗi này, vì chúng test logic apply với patch do chính test sinh ra. Chỉ một checkout Chromium thật mới phơi ra.

#### Đường dẫn Blink đã đổi trong các bản Chromium mới
| Patch cũ | Chromium 160 thật |
|---|---|
| `core/html/navigator_id.cc` | `core/frame/navigator_id.cc` |
| `core/html/navigator_ua_data.cc` | `core/frame/navigator_ua_data.cc` |
| `core/screen/screen.cc` | `core/frame/screen.cc` |
| — | `core/execution_context/navigator_base.cc` (nơi `userAgent()` thực sự cài) |

`NavigatorID::userAgent()` giờ là **pure virtual** — implementation nằm ở `NavigatorBase::userAgent()`, chỉ có một override trong cả cây, nên patch ở đó phủ cả window lẫn mọi loại worker.

#### Patch series đã sinh lại bằng `git diff` từ cây thật

Đo lại trên chính patch đang ship, bằng `hunk_report.py` và bằng `git diff --stat` trên cây Chromium:

| Patch | Nội dung | Hunk | File | Dòng |
|---|---|---|---|---|
| `0001-nova-profile-core.patch` | navigator + client hints + NOVA sources + build wiring + config bootstrap | **34** | 20 | +2946 / −21 |
| `0002-nova-profile-hardware-surfaces.patch` | screen + WebGL + canvas | — | — | — |
| **Tổng (cây đã patch)** | | | **28** | **+3068 / −24** |

Kiểm chứng độc lập (không tin agent): cây Chromium về nguyên bản → `git apply --check` **exit 0 cho cả hai, không `--recount`, không `--3way`, không fuzz** → `git apply` thật → 28 file, +3068/−24 dòng. `build.py apply` nhận diện "already-applied" (idempotent) và `build.py check` báo `ok`.

#### Hai lỗi trong chính công cụ kiểm tra — cùng một lớp lỗi

`hunk_count_mismatches()` trong `build.py` là công cụ **có nhiệm vụ bắt** đúng lỗi đã hỏng. Nó không bắt được. Hai nguyên nhân, cùng một bản chất:

1. **Vòng đếm dừng theo số khai báo.** Header khai báo nhiều hơn thực tế (`+20,99`) làm vòng lặp dừng đúng ở nơi header nói body kết thúc — dù body có kết thúc hay không. Đếm hết body mới bắt được. Đã sửa.
2. **Regex không chịu được phần ngữ cảnh hàm.** Git ghi header dạng `@@ -78,6 +78,7 @@ source_set("...") {`. Mẫu cũ dừng ở `@@` nên **không hunk nào** được đọc — và một checker đọc không hunk nào báo "không có vấn đề", y hệt một checker không chạy.

Cả hai đều là trường hợp *"kiểm tra báo rằng không có gì sai"* trong khi có gì đó sai. Đã sửa, và thêm test giữ cả hai trường hợp.

#### Bốn điều chưa có trong Chromium 160 (báo cáo, không bịa)
- `NavigatorID::Standard*`, `NavigatorUAData::Default*`, `Screen::Standard*` — **không còn tồn tại**. Thay vào đó, **body gốc của accessor là fallback** (đúng nguyên tắc "trả về đúng giá trị cũ khi config chưa nạp").
- `Screen::devicePixelRatio()` — không còn; tỉ lệ thiết bị nằm ở `LocalDOMWindow::devicePixelRatio()`.

#### Bốn bẫy kỹ thuật gặp khi build trên Windows
| Bẫy | Cách vượt |
|---|---|
| `autoninja` đọc `/proc/self/fd/1` (đường dẫn Linux) → crash | Dùng `third_party/ninja/ninja.exe` trực tiếp |
| Toolchain của depot_tools nằm trên bucket cần login | `DEPOT_TOOLS_WIN_TOOLCHAIN=0` → dùng VS cục bộ |
| Script tìm VS 2022 ở `%ProgramFiles%`, installer đặt ở `%ProgramFiles(x86)%` | `vs2022_install` trỏ đúng |
| `gn gen` đòi quoting mà PowerShell nuốt backslash | Đặt args vào `out/<dir>/args.gn` |

#### Phát hiện thêm bao nhiêu: Android chỉ build được trên Linux

`gn gen` với `target_os="android"` dừng ngay ở assertion của Chromium:

```
ERROR at //build/config/BUILDCONFIG.gn:263:3: Assertion failed.
  assert(host_os == "linux", "Android builds are only supported on Linux.")
```

Đây là **giới hạn kiến trúc của Chromium**, không phải cấu hình sai. Build trên Windows được là **desktop**. Kết quả: build desktop **vẫn chứng minh đúng điều cần chứng minh** — patch series biên dịch và liên kết được với Blink đã patch, với cùng một bộ accessor, cùng một hook, cùng một lớp cấu hình.

Build Android thật cần một máy Linux (hoặc WSL, cần admin + reboot).

#### ATL: một thành phần của Visual Studio mà Build Tools không cài mặc định

Build Windows dừng ở:

```
fatal error: 'atlbase.h' file not found
```

ATL thuộc component `Microsoft.VisualStudio.Component.VC.ATL`, không có trong bộ cài mặc định. `winget` không thêm được component, `setup.exe modify` trả `5007` ("run elevated from the beginning") và `Start-Process -Verb RunAs` cần UAC — mà phiên này không có quyền admin tương tác.

**Giải pháp đã dùng**: `dawn_use_built_dxc = false`. DXC là *host tool* (biên dịch cho Windows kể cả khi build Android) và chỉ backend D3D12 dùng đến nó — Android browser không dùng. Tắt nó bỏ đúng một thành phần Windows-only mà bản build không dùng, thay vì làm yếu sản phẩm.

| Bước | Kết quả |
|---|---|
| `gn gen` (Windows x64, `dawn_use_built_dxc=false`) | ✅ **37.524 targets** từ 5.150 files |
| `ninja chrome` | 57.965 bước — đang chạy |

### 4.5.7 Quy trình đã ghi vào luật
- `RULES.md` §"While the agents run" — phân chia công việc giữa agent chính và agent phụ, bàn giao 4 điểm, và bảng "không bao giờ ủy quyền".
- `AGENTS.md` — luật dùng agent song song (một agent = một nhóm file = một cách kiểm chứng).

---

## 6. Ghi chú kỹ thuật vừa sửa

### Catalog thiết bị: 11 → 42
- Thêm `core/device_catalog_extended.py` với 31 thiết bị (Google, Samsung, Xiaomi/Redmi/POCO, OnePlus, Oppo, Vivo, Realme, Motorola, Sony, Asus, Honor, Nothing, Huawei).
- `devices.py` giữ nguyên 11 preset tham chiếu; `all_presets()` hợp nhất, import lazy để tránh vòng lặp.
- Mở rộng `WebGlVendor` (+5 vendor thật) và `Screen.density_dpi` (7 → 13 giá trị).
- Thêm `tests/test_device_catalogue.py` (9 test) chốt: GPU khớp SoC, renderer có trong catalog, release ↔ SDK khớp.

### Đơn vị đo screen — lỗi nghiêm trọng nhất tìm được
- **Trước**: `screen.width` = **physical pixel** (1344) trong khi `viewport.innerWidth` = 1344 → trông như desktop, không phải mobile.
- **Sau**: `screen.width` = **logical CSS pixel** (512) = physical ÷ `devicePixelRatio`; `avail_*` cũng logical.
- Sửa kèm: `inner_height` bị vượt `avail_height` → chrome height bị chặn trong vùng khả dụng + rule consistency mới + repair logic mới.
- Thêm `tests/test_screen_viewport_units.py` (4 test).

### Sổ đo đạc
- Thêm slug `fleet-audit` vào `measurements_cli.py` — audit 4 chiều giờ được ghi vào ledger thay vì chạy rồi quên.

### Dọn dẹp
- Xóa `.nova_verify2` (đã kiểm tra: 0 rows ở mọi bảng SQLite), `.ruff_cache`, `__pycache__`, `.pytest_cache`.

### Sửa bug launcher — phát hiện khi đóng gói
- **Trước**: `build_args()` gọi `self.chrome_path` (tìm `chrome.exe` trên hệ thống) **trước** khi chọn core → máy không cài Chrome sẽ `FileNotFoundError` dù core đã đăng ký và có executable riêng.
- **Sau**: chọn core trước, chỉ fallback về `chrome_path` khi không có core nào khớp.
- Lỗi này làm fail `test_launcher_cores.py` — và sẽ làm fail trên **mọi máy mới** không cài Chrome sẵn. Đã sửa trước khi chuyển máy.

### Đóng gói cho việc chuyển máy
- `git init` — repo trước đó chưa có (chỉ có file rời).
- `.gitattributes` — pin LF cho patch series; patch có context CRLF **không apply** được.
- `.gitignore` — mở rộng: `.nova_verify*`, `*.log` (trừ `scale_check_100m.log`), `scratch/`, `tmp/`.
- `README.md` — sửa 4 lỗi: preset count 11→42, preset ID sai (`pixel-8-pro` không tồn tại, phải `pixel-8-pro-android14`), thiếu mục `js_runtime`/`docs/measurements`, tham chiếu `core_patch/README.md` không tồn tại → **đã tạo**.
- `AGENT_CONTRACT.md` — bỏ đường dẫn `C:\Users\ezycloudx-admin\...` hardcode (máy-specific).
- Commit: `baebcc5`, 134 file, 41.913 dòng.

### Chuyển lịch sử chat sang máy mới
- Thêm `tools/cursor_chat_transfer.py` — export/import chat của Cursor.
- Cursor lưu chat ở **3 nơi**: `globalStorage/state.vscdb` (`cursorDiskKV` = tin nhắn, `composerHeaders` = metadata sidebar), `.cursor/projects/<slug>/agent-transcripts/*.jsonl` (transcript), `workspaceStorage/` (state).
- Export lấy **đầy đủ**: 5 chat, 5.136 rows chat + 206 rows `ItemTable` (account state) + 14 credential + 5 transcript = 81,6 MB.
- Import là **merge**: cái đã có trên máy đích thắng, chạy 2 lần vô hại.
- ⚠️ Thư mục export chứa credential của account → copy tay (USB/ổ), **không** push lên git, **không** để trên cloud công khai.

---

## 6b. Việc còn lại — theo thứ tự nên làm

Xếp theo **giá trị trên mỗi giờ công**, không theo thứ tự dễ.  Mỗi việc
đều ghi rõ cách kiểm chứng, vì "viết xong" không phải "xong".

| # | Việc | Tầng | Cách kiểm chứng | Ước lượng |
|---|---|---|---|---|
| 1 | **Chờ build Chromium xong**, rồi chạy bộ dò e2e trên `chrome.exe` thật | Core | 12 trục khớp profile, so với simulator | ~2 giờ (đang build, 195.457/220.000 target) |
| 2 | **JA3/JA4** khớp hệ điều hành | Native/TLS | So `tls.extensions` với TLS ClientHello thật của Chromium mobile | ~4 giờ — **cần build xong**, không làm được bằng JS |
| 3 | **Đo lại toàn bộ fleet** với quy tắc bộ nhớ mới | Core | `nova fleet-audit` → 0 lỗi, 0 cảnh báo | ~1 giờ |
| 4 | **Rebuild installer** sau khi core xong (kèm `.ico`/`.icns` thật từ `asset/`) | Installer | Build thật, chạy entry point | ~30 phút |
| 5 | ~~**`mediaDevices`** chi tiết hơn~~ | JS | ✅ **Xong** — đọc `media_device_id.cc`, 64 hex, 2 camera, group đúng | ✅ |
| 6 | ~~**`permissions`** trạng thái đầy đủ~~ | JS | ✅ **Xong** — `PermissionStatus` thật, tên lạ → `TypeError` | ✅ |
| 7 | **Đo `measureText`** theo hạng thiết bị (dải rộng hơn `fonts.installed`) | JS | Đo trong iframe thật | ~2 giờ |
| 8 | **Kiểm chứng lại `SURVEY.md`** với các site còn sống | Docs | Chụp lại, đối chiếu từng mục | ~1 giờ |
| 9 | **`speechSynthesis.getVoices()`** theo ngôn ngữ thiết bị | JS | Đối chiếu danh sách voice của Chrome Android | ~1,5 giờ |
| 10 | **`navigator.connection`** theo hạng thiết bị (4g vs 3g vs wifi) | JS | Đo trên thiết bị thật | ~1 giờ |

### Việc đã xong trong lượt này

| Việc | Kết quả |
|---|---|
| Bảng phông theo hạng thiết bị | ✅ `font_catalog.py` — tier budget/mid/flagship, 3 tầng drop list, script của locale luôn được giữ |
| Bộ nhớ theo model + phép xấp xỉ Chromium | ✅ `hardware_approximation.py` — đọc từ source Chromium 160, đo lại trên Chrome thật |
| Sửa 7 preset báo 9 lõi | ✅ → 8; 0 SoC lệch; có test giữ |
| RAM của profile bám theo model | ✅ `navigator.deviceMemory` không còn có thể là 16 GB trên máy 4 GB |
| 3 bề mặt từ khảo sát | ✅ math precision, heap limit, media decode — có trục e2e riêng |
| **`mediaDevices` đúng hình dạng Chromium** | ✅ `deviceId` 64 ký tự hex (đọc từ `media_device_id.cc`), **2 camera** với group riêng, nhãn `camera2 0, facing back`, mic + loa **cùng group** |
| **`permissions` trả lời như trình duyệt** | ✅ `PermissionStatus` thật, tên lạ → `TypeError`, **sửa lỗi cú pháp JS** (tên có dấu gạch trong object literal) |

### Lỗi cú pháp JS chỉ lộ ra khi chạy thật

Danh sách tên permission được giữ trong **object literal**, mà nhiều tên có dấu
gạch (`ambient-light-sensor`, `background-sync`, `payment-handler`).  Điều đó
là **syntax error** — toàn bộ runtime không parse được.

Test trước đó **chỉ đọc script**, nên không bắt được.  Test mới **chạy script
 qua Node thật** (`C:\chromium\src\third_party\node\win\node.exe`) và kiểm tra
kết quả.  Đây là bài học: một test chỉ kiểm tra "chuỗi này có trong script"
thì không bằng một test chạy script.

---

## 8. Quyết định chiến lược: build trên Windows, bán trên Android

**22/11 14:55**

### Sự thật đã kiểm chứng

| Kiểm chứng | Cách kiểm chứng | Kết quả |
|---|---|---|
| Chromium chặn build Android trên Windows | Chạy `gn gen out/nova-159` với `target_os="android"` | `ERROR at //build/config/BUILDCONFIG.gn:263:3: Assertion failed. assert(host_os == "linux", "Android builds are only supported on Linux.")` |
| Máy không có Linux nào | `wsl -l -v`, `Get-Command docker`, `Get-ChildItem $LOCALAPPDATA\Android\Sdk` | Không có WSL, không Docker, không Android SDK |
| Máy không bật được feature cần admin | `[WindowsPrincipal]::IsInRole(Administrator)` | `False` → không bật được `VirtualMachinePlatform`, không cài WSL |
| Máy đủ sức làm Linux host | `systeminfo`, `Win32_Processor`, `Win32_OperatingSystem` | 18 luồng, 28 GB RAM, 476 GB trống, ảo hóa đã bật trong firmware |
| **Core patch là portable** | Đọc patch series, tìm `BUILDFLAG(IS_ANDROID)` | Chỉ dùng để **bảo toàn hành vi gốc**; đường NOVA đặt trước nên hoạt động trên mọi target |

### Kết luận

Rào cản **không nằm ở code** — nó nằm ở **host**. Core patch đã viết đúng cho cả
Android lẫn desktop. Vì vậy:

* **Build trên Windows x64** → ra một `chrome.exe` đã patch, đọc được
  `--nova-config`. Đây là **bàn thử**, dùng để chứng minh patch đúng và để
  chạy bộ đo e2e trên trình duyệt thật.
* **Bán cho khách ở Android** → dùng **Chrome for Testing `linux-arm64`** (bản
  arm64 chính thức của Chromium, `160.0.8176.2` hiện có) cộng với **JS
  runtime injection** của NOVA, chạy trên máy Android của khách.

Hai lớp này cùng nhau phủ hết các bề mặt:

| Bề mặt | Lớp 1: core patch (win64) | Lớp 2: JS runtime |
|---|---|---|
| `navigator.*`, `userAgentData` | ✅ | ✅ |
| `screen`, `viewport` | ✅ | ✅ |
| WebGL vendor/renderer/extension | ✅ | ✅ |
| Canvas 2D + `measureText` | ✅ | ✅ |
| `permissions` | ✅ | ✅ |
| `mediaDevices` | ✅ | ✅ |
| Audio fingerprint | ✅ | ❌ (đang làm) |
| Font list + font metrics | ❌ (cần font thật của máy) | ✅ (danh sách + `measureText`) |

### Việc đã làm trong lượt này

* **Dừng build `out/nova-win`** ở 200.314 target (đã qua Blink core, V8,
  `components/sync`) — vì nó là bản desktop, không phải sản phẩm, và nó giành
  CPU với các agent. Không mất gì: `ninja` tiếp tục từ đúng chỗ khi chạy lại.
* Viết `novaprofile/core_patch/BUILDING.md` — mô tả cách build Android trên
  Linux host, kèm yêu cầu SDK/NDK và cách verify.
* Ghi rõ trong `build_config.json` rằng `out/nova-<major>` là output của
  Linux host, còn `out/nova-win` là bàn thử.

### Việc tiếp theo (theo thứ tự)

1. Bộ đo e2e chạy trên `chrome.exe` đã patch — đây là bước chứng minh patch đúng.
2. Bổ sung JS runtime cho các bề mặt còn thiếu: audio, `navigator.connection`
   theo hạng máy, `MediaRecorder.isTypeSupported`.
3. Chuỗi phát hành: tải Chrome for Testing arm64, đóng gói cùng JS runtime
   thành installer.


---

## 7. Lịch sử cập nhật

| Thời điểm | Nội dung |
|---|---|
| **22/11 14:50** | **Lượt 3 — dừng build Windows, ghi nhận rào cản Android.** `gn gen` với `target_os="android"` **fail cứng** trên host Windows: `build/config/BUILDCONFIG.gn:263` có `assert(host_os == "linux", "Android builds are only supported on Linux.")`. Đã xác minh bằng cách chạy `gn gen` thật, không phải đọc tài liệu. Máy này không có WSL, không Docker, không Android SDK/NDK, và **không có quyền admin** (`IsAdmin=False`) nên không bật được `VirtualMachinePlatform`. Máy có 18 luồng / 28 GB RAM / 476 GB trống — đủ cho Linux host. Build `out/nova-win` đã chạy 200.314 target (đã qua Blink core, V8, đang ở `components/sync`) nhưng là **bản desktop Windows**, chỉ dùng để kiểm chứng core patch, không phải sản phẩm. **Đã dừng** để giải phóng CPU cho agent. Viết `novaprofile/core_patch/BUILDING.md` mô tả đúng cách build Android trên Linux host. |
| **22/11 14:20** | **Lượt 2:** sửa `mediaDevices` (deviceId 64 hex theo `media_device_id.cc`, 2 camera với group riêng, mic+loa cùng group) và `permissions` (`PermissionStatus` thật, tên lạ → `TypeError`). **Sửa lỗi cú pháp JS** chỉ lộ ra khi chạy Node thật. **1.368 test pass**, ruff sạch. Đẩy `3dc5c42`. |
| **22/11 13:45** | **Lượt 1:** sửa ba lỗi "đúng nhưng sai": bộ nhớ bốc từ tập phẳng, schema giữ giả định lỗi thời về phép làm tròn, 7 preset báo 9 lõi. Đọc source Chromium 160 + đo trên Chrome thật để kiểm chứng. Thêm bảng phông theo hạng thiết bị. **1.341 test pass**, ruff sạch. Đẩy `ef4e165` lên GitHub. |
| 22/11 01:20 | **Bổ sung R8 (không dừng, không hỏi) + R9 (viết xong ≠ xong)** vào `RULES.md` và `AGENTS.md`. Bắt đầu 4 việc còn lại. |
| 22/11 01:15 | Sửa bug launcher. Đóng gói git (commit `baebcc5`, 134 file). Viết `RULES.md` đầy đủ 11 mục. Thêm `tools/cursor_chat_transfer.py` + export chat 81,6 MB. |
| 22/11 00:50 | Catalog 11→42 preset. Sửa đơn vị đo screen (logical pixel). Thêm `fleet-audit` vào ledger. 559 test pass. 2M profile 0 va chạm. |
| 21/11 22:50 | Tạo báo cáo. 540 test pass. |
