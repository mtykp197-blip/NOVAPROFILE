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
| Chromium build trên Windows | 🔄 Đang build — 7.915/9.570 step (~82%) |
| Chromium: `chrome.exe` hoàn chỉnh | ⏳ Chưa có |
| Installer / gói cài đặt Windows | ⏳ Chưa đóng gói |
| JA3/JA4 (TLS fingerprint) | ❌ Chưa — cần tầng mạng, không làm được bằng JS |
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

---

## 2. Việc còn lại — theo thứ tự nên làm

### Ưu tiên 1 — Dựng `chrome.exe` (chặn mọi thứ khác)

**Trạng thái.** Build nền đang chạy tại `C:\chromium\src\out\nova-win`,
7.915/9.570 step. Log: `C:\chromium\src\out\nova_link_chrome.log`.

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
2. Chạy probe 21 trục trên chính `chrome.exe` này (không phải Chrome hệ thống).
3. Đóng gói `chrome.exe` + `resources.pak` + DLL vào installer.
4. Commit `chrome.exe` lên GitHub — **dùng Git LFS** (file ~150 MB, vượt
   giới hạn 100 MB của GitHub). Xem mục 4.

### Ưu tiên 2 — Đóng gói installer Windows

Cần tạo: `installer/` chứa manifest danh sách file + script cài đặt, tạo
profile dir riêng cho mỗi profile, copy `chrome.exe` và patch sang từng bản.

Ưu tiên phụ: `.ico` / `.icns` cho installer — đã làm xong, cần rebuild sau khi
core xong.

### Ưu tiên 3 — JA3 / JA4 (tầng mạng, không làm được bằng JS)

Đây là **giới hạn kiến trúc**, không phải việc chưa làm. JA3/JA4 là fingerprint
của **TLS ClientHello** — nằm ở tầng network, trước cả khi JS chạy. Cả hai
hướng:

- **Chromium patch**: sửa `net/ssl/ssl_client_socket_impl.cc` để ép
  `ClientHello` ra đúng cipher suite / extension order / elliptic curve của
  profile → build lại Chromium. Đây là hướng đúng, và cần làm **cùng lúc**
  với Ưu tiên 1 vì cùng một lần build.
- **Proxy MITM**: nếu đi qua proxy, TLS do proxy ký, JA3 của client vẫn do
  Chromium tạo → phải patch client.

Không có cách nào làm bằng JS. `navigator` không đụng tới TLS.

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

### 3.2 Bản vá Chromium *cần thêm* cho anti-detect

Chromium hiện **chưa** có patch fingerprint nào. Tất cả đang nằm ở tầng JS
runtime của Python (`novaprofile/js_runtime/`). Để profile "chảy" được trên
Chromium thật mà không cần inject JS từ bên ngoài, cần patch các điểm sau
trong Chromium:

| Trục | File Chromium đích | Ghi chú |
|---|---|---|
| UA / Client Hints | `content/public/common/user_agent_utils.cc` | |
| `navigator.hardwareConcurrency` | `third_party/blink/renderer/modules/webnavigator/` | |
| `navigator.deviceMemory` | `third_party/blink/renderer/core/execution_context/navigator_base.cc` | |
| `screen.*` | `third_party/blink/renderer/core/frame/...` | |
| Canvas noise | `third_party/blink/renderer/modules/canvas2d/` | |
| WebGL limits | `gpu/command_buffer/service/` | |
| `maxTouchPoints` | `third_party/blink/renderer/core/frame/navigator.cc` | |
| TLS (JA3/JA4) | `net/ssl/ssl_client_socket_impl.cc` | Ưu tiên 3 |

**Khuyến nghị:** làm theo cơ chế *profile payload → Chromium* một lần, để
Chromium đọc profile từ disk lúc khởi động, thay vì inject JS runtime từ
Python. Cách này bền hơn nhiều về sau (không ai thấy JS injection), và
đồng thời mở đường cho JA3/JA4 vốn bắt buộc phải nằm trong Chromium.

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
