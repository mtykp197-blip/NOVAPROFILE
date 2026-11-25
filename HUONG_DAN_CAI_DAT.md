# NOVA PROFILE — Hướng dẫn cài đặt trên máy khác

Tài liệu này viết cho người **chưa từng chạy dự án này**. Mỗi bước đều có lệnh
để gõ, và có phần kiểm tra để biết là đã thành công.

Bạn cần chuẩn bị:

| Thứ | Cần để làm gì | Bắt buộc? |
|---|---|---|
| Máy tính Windows 10/11 (64-bit) | chạy app | **Có** |
| [Git](https://git-scm.com/download/win) | clone mã nguồn | **Có** |
| [Python 3.11+](https://www.python.org/downloads/) | chạy control plane | **Có** |
| [Node.js 18+](https://nodejs.org/) | đóng gói app | Chỉ khi tự build |
| [GitHub PAT](https://github.com/settings/tokens) | tải core (nếu repo private) | Tùy |

> **Không cần cài Google Chrome.** Core của NOVA là bản Chromium đã vá sẵn, chạy
> độc lập với Chrome.

---

## Bước 1 — Clone mã nguồn

```powershell
git clone https://github.com/mtykp197-blip/NOVAPROFILE.git
cd NOVAPROFILE
```

**Kiểm tra:** phải thấy 334 file.

```powershell
(Get-ChildItem -Recurse -File | Where-Object { $_.FullName -notmatch "\\\.git\\" }).Count
```

---

## Bước 2 — Cài Python dependencies

```powershell
cd novaprofile\control_plane
python -m pip install -e .
```

**Kiểm tra:** lệnh sau phải in ra số preset.

```powershell
python -c "from novaprofile.core.device_catalog_market import load_records, build_market_presets; r=load_records(); print(len(build_market_presets(r)), 'presets')"
```

Phải là `19381 presets`.

---

## Bước 3 — Tải core (phần quan trọng nhất)

Core là bản Chromium đã vá. Nó **không nằm trong git** vì 511 MB — Git chặn
file trên 100 MB. Nó được đặt trên trang Release.

**Cách nhanh nhất — dùng script có sẵn:**

```powershell
cd ..\..
python setup_new_machine.py
```

Script này làm **toàn bộ** các bước còn lại: tải core, cài dependencies Node,
đóng gói control plane, đóng gói app. Chỉ cần chạy.

**Cách làm thủ công — tải core:**

```powershell
curl.exe -L -o core.zip `
  https://github.com/mtykp197-blip/NOVAPROFILE/releases/download/core-win-x64/160.0.8177.0.zip

Expand-Archive core.zip -DestinationPath core\build\cores\160.0.8177.0 -Force
```

**Kiểm tra:** file phải có đủ, và phải chạy được.

```powershell
Get-ChildItem core\build\cores\160.0.8177.0 | Measure-Object | Select-Object Count
& core\build\cores\160.0.8177.0\NOVAPROFILE.exe --version
```

Phải in ra `Chromium 160.0.8177.0`.

> **Nếu báo lỗi "side-by-side configuration is incorrect"** thì thiếu file
> `160.0.8177.0.manifest` trong thư mục core. Đây là file bắt buộc — không có nó
> thì trình duyệt **không chạy được**. Tải lại zip mới đã có file này.

---

## Bước 4 — Cài dependencies Node

```powershell
cd novaprofile\desktop
npm.cmd install
```

> Dùng `npm.cmd` chứ không phải `npm`. Trên Windows, PowerShell chặn script
> `.ps1` của npm, và `npm.cmd` chạy được không cần đổi policy.

---

## Bước 5 — Đóng gói control plane

```powershell
cd ..\control_plane
python -m PyInstaller nova-backend.spec --noconfirm
```

**Kiểm tra:**

```powershell
Get-Item dist\nova-backend.exe | Select-Object Name, Length
```

Kết quả: `nova-backend.exe`, khoảng 23 MB.

---

## Bước 6 — Đóng gói app

```powershell
cd ..\desktop
npm.cmd run dist
```

**Kiểm tra:**

```powershell
Get-ChildItem dist | Select-Object Name
```

Phải có:
- `NOVAPROFILE-1.0.0-x64.exe` (~252 MB) — file cài đặt
- `win-unpacked\` — thư mục app chạy được ngay, không cần cài

---

## Bước 7 — Chạy thử

Cách nhanh nhất, không cần cài:

```powershell
Start-Process .\dist\win-unpacked\NOVAPROFILE.exe
```

Hoặc cài thật (chạy file exe trong `dist\`).

**Kiểm tra app có chạy core thật, không phải Chrome thường:**

1. Mở app → tab **Settings** → mục *Chromium core* phải ghi
   `Core present: yes`
2. Tạo một profile → **Open** (mở profile)
3. Xem tiến trình trình duyệt:

```powershell
Get-Process | Where-Object { $_.Path -like "*NOVAPROFILE*" } | Select-Object Id, Path
```

Phải thấy `...\resources\core\NOVAPROFILE.exe`.

> **Nếu mở profile báo lỗi 500** → backend không tìm thấy core. Mở file log:
> `%APPDATA%\NOVA PROFILE\logs\backend.log`

---

## Bước 8 — Dùng app

| Việc | Cách làm |
|---|---|
| Tạo 1 profile | tab **Profiles** → *Create* → chọn máy → *Create* |
| Tạo hàng loạt | tab **Profiles** → *Batch* → số lượng → *Create* |
| Mở profile | chọn profile → *Open* |
| Đóng profile | chọn profile → *Stop* |
| Xoá profile | chọn profile → *Delete* |
| Sửa cấu hình | chọn profile → *Edit* |
| Xem phân bố fleet | tab **Fleet** |
| Đổi cài đặt mặc định | tab **Settings** |

---

## Phụ lục A — Chạy ở chế độ phát triển

Không cần đóng gói gì cả:

```powershell
cd novaprofile\control_plane
python -m pip install -e .

cd ..\desktop
npm.cmd install
npm.cmd start
```

Lần chạy đầu, app dùng core ở `core\build\cores\160.0.8177.0\`.

---

## Phụ lục B — Nếu repo là private

Link tải core cần token. Lấy token rồi đặt vào biến môi trường:

```powershell
$cred = "protocol=https`nhost=github.com`n`n" | git credential fill
$env:GITHUB_TOKEN = ($cred | Select-String "^password=") -replace "^password=", ""
```

Script `setup_new_machine.py` tự làm bước này.

---

## Phụ lục C — Gỡ cài đặt

Xoá thư mục dữ liệu:

```powershell
Remove-Item -Recurse -Force "$env:APPDATA\NOVA PROFILE"
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\NovaProfile"
```

---

## Phụ lục D — Cấu trúc thư mục

```
NOVAPROFILE/
├─ setup_new_machine.py          ← script dựng máy mới
├─ novaprofile/
│  ├─ core_patch/                ← mã C++ vá Chromium + patch series
│  │  ├─ src/                    ← 12 file .cc/.h
│  │  ├─ patches/                ← 3 patch
│  │  └─ data/                   ← dataset 19.381 preset
│  ├─ control_plane/             ← engine (Python)
│  │  └─ novaprofile/
│  │     ├─ core/                ← sinh fingerprint, chống trùng
│  │     ├─ launcher/            ← mở trình duyệt
│  │     ├─ js_runtime/          ← lớp JS dự phòng
│  │     └─ api/                 ← HTTP API cho app
│  └─ desktop/electron/          ← giao diện (Electron)
└─ core/build/cores/160.0.8177.0/   ← core đã build (tải về)
```

---

## Phụ lục E — Xử lý sự cố

| Triệu chứng | Nguyên nhân | Cách sửa |
|---|---|---|
| `side-by-side configuration is incorrect` | thiếu `160.0.8177.0.manifest` | tải lại core zip |
| `no browser core is installed` | backend không thấy core | kiểm tra `resources\core\` có file không |
| Lỗi 500 khi mở profile | xem `backend.log` | thường là core chưa tải |
| `npm.ps1 cannot be loaded` | PowerShell chặn script | dùng `npm.cmd` |
| `ModuleNotFoundError: novaprofile` | chưa cài engine | `pip install -e .` ở `control_plane` |
| App mở nhưng trắng | backend chưa lên | đọc `backend.log` |

---

## Phụ lục F — Kiểm tra tổng thể

Chạy lệnh này để biết máy đã sẵn sàng chưa:

```powershell
Write-Host "1. source      :" (Get-ChildItem -Recurse -File | Where-Object { $_.FullName -notmatch "\\\.git\\" }).Count
Write-Host "2. core        :" (Test-Path core\build\cores\160.0.8177.0\NOVAPROFILE.exe)
Write-Host "3. manifest    :" (Test-Path core\build\cores\160.0.8177.0\160.0.8177.0.manifest)
Write-Host "4. backend     :" (Test-Path novaprofile\control_plane\dist\nova-backend.exe)
Write-Host "5. installer   :" (Test-Path novaprofile\desktop\dist\NOVAPROFILE-1.0.0-x64.exe)
```

Cả 5 dòng đều đúng → máy sẵn sàng.
