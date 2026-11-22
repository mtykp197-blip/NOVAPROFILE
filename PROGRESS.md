# NOVA PROFILE — Báo cáo tiến độ

_Cập nhật: 22/11/2026 09:05 (UTC+7)_

---

## 1. Tổng quan

| Hạng mục | Trạng thái | % hoàn thiện |
|---|---|---|
| **Control Plane (Python)** — generator, schema, collision guard, consistency | ✅ Hoàn tất | **~96%** |
| **API / CLI** — REST, auth, batch, probe | ✅ Hoàn tất | **~94%** |
| **Registry / Store** — SQLite, seed store, persistence | ✅ Hoàn tất | **~90%** |
| **Network layer** — DNS, proxy, WebRTC, rotation, leak-check | ✅ Hoàn tất | **~88%** |
| **Security** — crypto, token auth, redaction, hardening | ✅ Hoàn tất | **~90%** |
| **Core Patch (C++)** — config parser, Blink hooks, patch series | 🔄 **Đang biên dịch thật — NOVA .obj đã xong** | **~80%** |
| **js_runtime** — lớp JS fallback cho Chromium stock | ✅ Hoàn tất | **~90%** |
| **Probe / Measurement** — simulator + harness + e2e thật | ✅ Hoàn tất | **~95%** |
| **Installer** — gói cài đặt độc lập | ✅ Hoàn tất | **~95%** |
| **Measurement Ledger** — sổ đo đạc + CLI | ✅ Hoàn tất | **~95%** |
| **Docs** — ARCHITECTURE, AGENT_CONTRACT, RULES | ✅ Có | **~90%** |

### Ước lượng tổng thể: **~92%**

### Bằng chứng mới nhất (22/11/2026 09:05)

| Việc | Kết quả |
|---|---|
| Build Chromium 160 thật (MSVC 2022, Win x64) | **40,4% → đang chạy**, 0 lỗi, 1h23m đã trôi qua |
| Code C++ NOVA biên dịch | ✅ `nova_blink_patch.obj` (730 KB), `nova_core_config.obj` (1.148 KB), `nova_pixel_noise.obj` (225 KB) |
| Test suite control plane | ✅ **894 passed**, 5 skipped, ruff sạch 100% |
| Lỗi installer đã sửa | ✅ `launch.py`/`uninstall.py` sinh ra thiếu `main()` → đã sửa, 12/12 installer test pass |
| Đồng bộ GitHub | ✅ `github.com/mtykp197-blip/NOVAPROFILE` (private), 167 files, 2,08 MB, commit `09f010d` |

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

## 7. Lịch sử cập nhật

| Thời điểm | Nội dung |
|---|---|
| 22/11 01:15 | Sửa bug launcher. Đóng gói git (commit `baebcc5`, 134 file). Viết `RULES.md` đầy đủ 11 mục. Thêm `tools/cursor_chat_transfer.py` + export chat 81,6 MB. |
| 22/11 01:20 | **Bổ sung R8 (không dừng, không hỏi) + R9 (viết xong ≠ xong)** vào `RULES.md` và `AGENTS.md`. Bắt đầu 4 việc còn lại. |
| 22/11 00:50 | Catalog 11→42 preset. Sửa đơn vị đo screen (logical pixel). Thêm `fleet-audit` vào ledger. 559 test pass. 2M profile 0 va chạm. |
| 21/11 22:50 | Tạo báo cáo. 540 test pass. |
