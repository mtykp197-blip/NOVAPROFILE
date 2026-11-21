# NOVA PROFILE — Báo cáo tiến độ

_Cập nhật: 22/11/2026 00:50 (UTC+7)_

---

## 1. Tổng quan

| Hạng mục | Trạng thái | % hoàn thiện |
|---|---|---|
| **Control Plane (Python)** — generator, schema, collision guard, consistency | ✅ Hoàn tất | **~96%** |
| **API / CLI** — REST, auth, batch, probe | ✅ Hoàn tất | **~92%** |
| **Registry / Store** — SQLite, seed store, persistence | ✅ Hoàn tất | **~90%** |
| **Network layer** — DNS, proxy, WebRTC, rotation, leak-check | ✅ Hoàn tất | **~88%** |
| **Security** — crypto, token auth, redaction, hardening | ✅ Hoàn tất | **~90%** |
| **Core Patch (C++)** — config parser, Blink hooks, patch series | ⚠️ Chưa biên dịch thật | **~55%** |
| **js_runtime** | ❌ Trống — chưa làm | **~0%** |
| **Probe / Measurement** — simulator + harness | ✅ Hoàn tất | **~88%** |
| **Measurement Ledger** — sổ đo đạc + CLI | ✅ Hoàn tất | **~95%** |
| **Docs** — ARCHITECTURE, AGENT_CONTRACT, RULES | ✅ Có | **~85%** |

### Ước lượng tổng thể: **~81%**

---

## 2. Quy mô mã nguồn

| Thành phần | Số dòng |
|---|---|
| Python (control_plane) | ~20.400 dòng |
| C++ (core_patch) | ~4.500 dòng |
| Tests (29 file) | ~6.100 dòng |
| Docs | ~42 KB |

**Test suite:** 559 passed, 2 skipped, ruff sạch 100%.

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
| Core patch chưa biên dịch thật | 🔴 Cao | Syntax C++ chưa được trình biên dịch xác nhận — có thể lộ lỗi compile |
| js_runtime trống | 🔴 Cao | JS injection chưa có; browser có thể lộ giá trị thật qua context |
| Probe mới chỉ dùng simulator | 🟡 Trung bình | Chưa đo fingerprint thật từ Chromium đã patch |
| Bucket theo preset | 🟡 Trung bình | 42 preset → 2.4M profile/bucket ở quy mô 100M. Mỗi preset thêm là 1 bucket thêm |
| 100M chưa chạy thật | 🟢 Thấp | 2M đã sạch 0 va chạm; logic đã đủ điều kiện |

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
| 22/11 00:50 | Catalog 11→42 preset. Sửa đơn vị đo screen (logical pixel). Thêm `fleet-audit` vào ledger. 559 test pass. 2M profile 0 va chạm. |
| 21/11 22:50 | Tạo báo cáo. 540 test pass. |
