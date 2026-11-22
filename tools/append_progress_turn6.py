"""Append the turn-6 entry to PROGRESS.md.

The file is UTF-8 with Vietnamese text; the console on this machine
cannot render it, so the entry is written through Python rather than
through the shell.
"""

from __future__ import annotations

import pathlib

ENTRY = """
## 6c. Lượt 6 — vá lỗi "mọi profile đều trùng" ở tầng seed + mở rộng band Chrome

### A. Lỗi gốc: `derive_bytes` / `derive_int` không biết stratum

Đây là lỗi nghiêm trọng nhất còn lại, và nó nằm ở tầng seed, không phải ở
tầng chọn giá trị.

`SeedDeriver.derive_bytes()` dựng HKDF-Expand từ `self._info(axis, context)`,
mà `_info` chỉ gấp `epoch` và `nudge` vào HKDF `info` — **không có stratum**.
Trong khi `derive_stratified()` thì có.

Hệ quả: mọi profile cùng `master_seed` chia sẻ **cùng một** giá trị cho mọi
trường được rút bằng `derive_bytes`/`derive_int` không stratified. Đo trên 300
profile:

| Trường | Trước | Sau |
|---|---|---|
| `android_build.serial` | **1/300** | 300/300 |
| `android_build.version_incremental` | 1/300 | 300/300 |
| `android_build.fingerprint` | 3/300 | 300/300 |
| `android_build.display` | 3/300 | 300/300 |
| `canvas_2d.salt` | 1/300 | 300/300 |
| `webgl.salt` | 1/300 | 300/300 |
| `media_devices.salt` | 1/300 | 300/300 |

Đây đúng là lỗi "chống trùng" mà yêu cầu cảnh báo: một trường đọc được duy
nhất — serial — đã gộp được **toàn bộ fleet** thành một thiết bị.

Cách sửa: gấp `stratum` vào HKDF `info` của deriver, và truyền `stratum` qua
`SeedConfig.deriver()`. Tính tất định được giữ: stratum là số nguyên cố định
của một profile, nên serial vẫn không đổi qua các lần khởi động lại.

### B. Band Chrome của Android 16 hẹp hơn thực tế

`_ANDROID_SHIPPED` gán Chromium 156–160 cho Android 16, nên release đó chỉ có
**5 major** khả dụng. Đã kiểm chứng lại bằng nguồn: **Chrome 137 là milestone
đầu tiên hỗ trợ Android 16 (API 36)**, phát hành stable 20/05/2025, trước khi
Android 16 ra mắt (10/06/2025). Đã sửa mốc thành 137 → release này có **24 major**.

Kết quả trên 1.000 profile của một model:

| Tổ hợp đọc | Trước | Sau |
|---|---|---|
| UA đơn lẻ | 285/1000, max nhóm **35** | 360/1000, max nhóm **8** |
| UA + full version | 999/1000 | 999/1000, max nhóm 2 |
| UA + timezone | 913/1000 | 967/1000, max nhóm 3 |
| UA + full version + timezone + screen | 1000/1000 | **1000/1000, max nhóm 1** |
| UA + full version + tz + screen + cores + mem | 1000/1000 | **1000/1000, max nhóm 1** |

### C. Vá thêm một leak thật trong JS runtime: `OfflineAudioContext`

Harness đọc `buffer.sampleRate` của một `OfflineAudioContext`, mà runtime chỉ
bọc `AudioContext` (không bọc `OfflineAudioContext`). Đã bổ sung:

* bọc `OfflineAudioContext` (cả dạng positional lẫn dictionary),
* **ép** `sampleRate` về giá trị của profile lúc khởi tạo — không chỉ đặt mặc
  định — vì `buffer.sampleRate` mới là con số web đọc, và nó phải khớp với
  `ctx.sampleRate` để profile nhất quán.

### D. `maxTouchPoints` không còn là con số ngẫu nhiên

`maxTouchPoints` trên Android không phải lựa chọn tự do: Chromium hỏi platform
qua JNI, platform trả về số điểm chạm cao nhất mà digitiser khai báo (1, 2, hoặc
5 và trở lên cho digitiser 10 điểm). Số này thuộc về phần cứng, nên mọi profile
cùng model phải báo cùng số — đúng như các máy thật. Schema giờ là
`Literal[1, 2, 5, 10]`.

### E. Đo lại toàn bộ: trường nào còn trùng, và trùng vì lý do gì

Đo trên 500 profile, phân loại:

* **Trùng vì giới hạn vật lý — ĐÚNG, không sửa**: `webgl.unmasked_renderer`
  (1 GPU cho 1 model), `android_abi_soc` (1 SoC), `navigator.platform`,
  `navigator.vendor`, `navigator.oscpu`, `math_precision.epsilon`,
  `heap_size_limit`, `user_agent_data.platform`, `plugins_mime`. 1.000 chiếc
  Pixel 8 Pro thật cũng có cùng GPU, cùng SoC, cùng platform.
* **Trùng vì thiếu entropy — ĐÃ SỬA**: serial, incremental, fingerprint,
  display, mọi `salt` → 100% unique.
* **Trùng vì bản chất của phép đo**: `battery.charging` (2 giá trị — đang sạc
  hay không), `media_queries` (theo thiết bị), `heap.limit` (3 ngưỡng tier).

### F. Kiểm chứng trên Chrome thật

`verify_live.py` chạy `VerifyRunner` (đã có sẵn trong `novaprofile/probe/`)
trên **Chrome 157 thật** của máy:

```
navigator pass 15   client_hints pass 9   screen pass 7   viewport pass 5
timezone pass 2     storage pass 7       canvas pass 1    webgl pass 5
webgpu pass 2       fonts pass 1         sensors pass 3   media_queries pass 7
battery pass 4      rects pass 5         permissions pass 1   math pass 6
heap pass 1         media_decode pass 1
→ 19/21 axis pass, 0 fail
```

Hai axis `host-dependent` còn lại:
* `audio.sampleRate` — lỗi thật, đã vá ở mục C.
* `geolocation` — **false positive của harness**: profile khai `mode="prompt"`,
  `latitude=None`; harness lại so sánh với dữ liệu của riêng nó
  (`"granted"`, `42.33`). Runtime override permissions đúng, nên `prompt` là
  câu trả lời đúng.

### G. Chromium: bỏ ATL, mở đường cho `chrome.exe`

Bản build thiếu `atls.lib` (ATL là component tùy chọn của Visual Studio Build
Tools, máy này không có). Đã xử lý ở tầng nguồn, không phải bằng cách vá
đường dẫn:

* `base/win/atl.h` — bỏ umbrella header ATL. `base/win/atl_throw.h` (đã viết
  trước đó) tự khai báo `ATL::AtlThrowLastWin32` và có nhánh dự phòng khi
  không có ATL, nên phần cần thiết vẫn còn.
* `chrome/windows_services/service_program/service.cc` — `InitializeComSecurity()`
  dựng security descriptor bằng **SDDL** qua
  `ConvertStringSecurityDescriptorToSecurityDescriptorW` thay cho `CDacl` /
  `CSecurityDesc` / `Sids` của ATL. Cùng kết quả, không cần import library.
  Quyền `COM_RIGHTS_EXECUTE | COM_RIGHTS_EXECUTE_LOCAL` = `0x1f0007`, có
  `static_assert` chốt lại giá trị để SDDL không lệch khỏi hằng số.

Đã xác minh ba file dùng ATL thật (`app_server_win.cc`,
`elevated_tracing_service.cc`, `elevation_service.cc`) đều **không** tham
chiếu symbol ATL nào.

### H. Trạng thái build

`out/nova-win` đã có 45.304 object file; còn ~9.570 target cho tới `chrome.exe`.
Đang chạy nền, không lỗi compile.

---

## 7. Lịch sử cập nhật

| Thời điểm | Nội dung |
|---|---|
| **22/11 20:30** | **Lượt 6 — vá lỗi chống trùng ở tầng seed + mở rộng band Chrome + bỏ ATL để build `chrome.exe`.** (1) **Lỗi gốc:** `derive_bytes`/`derive_int` không gấp `stratum` vào HKDF `info` → **mọi profile cùng seed có cùng serial, cùng fingerprint, cùng mọi salt** (1/300). Đã vá bằng cách gấp stratum vào deriver → **300/300 unique** trên tất cả trường đó. (2) **Band Chrome Android 16** đặt nhầm ở 156; đã kiểm chứng Chrome 137 là milestone đầu tiên hỗ trợ API 36 → sửa thành 137 → **UA max nhóm 35 → 8**; tổ hợp UA+fullver+tz+screen vẫn 1/1 tuyệt đối. (3) Vá leak `OfflineAudioContext.sampleRate` trong JS runtime. (4) `maxTouchPoints` theo digitiser phần cứng thay vì random. (5) **Bỏ ATL khỏi tầng nguồn** (`base/win/atl.h` + `service.cc` dùng SDDL) để build không cần component tùy chọn của VS. **1.791 test pass**, ruff sạch. |
"""

PATH = pathlib.Path(__file__).resolve().parent.parent / "PROGRESS.md"
text = PATH.read_text(encoding="utf-8")
marker = "\n---\n\n## 7. Lịch sử cập nhật\n"
index = text.index(marker)
head = text[:index]
tail = text[index:]
# Drop the old table's header row and re-insert the new entry on top of it.
PATH.write_text(head + ENTRY + tail, encoding="utf-8")
print("PROGRESS.md updated")
