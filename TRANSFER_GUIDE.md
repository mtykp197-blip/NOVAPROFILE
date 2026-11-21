# Chuyển sang máy mới

Hai thứ cần chuyển: **dự án** (qua git) và **lịch sử chat** (qua file
cục bộ). Chúng tách biệt vì lịch sử chat chứa credential của tài khoản
nên không được đẩy lên kho lưu trữ.

---

## 1. Đóng gói dự án

Dự án đã là repo git sẵn (commit `baebcc5`, branch `main`).

### Cách A — GitHub (khuyến nghị)

Trên máy cũ:

```powershell
cd C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE

# tạo repo private trên github.com trước (không cần cài gh CLI)
git remote add origin https://github.com/<TÀIKHOẢN-GITHUB-CỦA-BẠN>/novaprofile.git
git push -u origin main
```

Trên máy mới:

```powershell
git clone https://github.com/<TÀIKHOẢN-GITHUB-CỦA-BẠN>/novaprofile.git
cd novaprofile\control_plane
pip install -e .
python -m pytest -q
```

### Cách B — ZIP (không cần GitHub)

```powershell
cd C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE
Compress-Archive -Path . -DestinationPath ..\NOVAPROFILE.zip -Force
```

ZIP có cả thư mục `.git` (~1 MB) nên lịch sử commit đi theo.

### Cách C — ổ USB / mạng LAN

```powershell
robocopy C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE D:\NOVAPROFILE /E /XD .git
```

---

## 2. Chuyển lịch sử chat

Chat của Cursor không nằm trong một file nào đó. Nó nằm ở **3 nơi**,
và cả 3 phải đi cùng nhau, nếu không thì chat về máy mới sẽ rỗng:

| Nơi | Chứa gì |
|---|---|
| `%APPDATA%\Cursor\User\globalStorage\state.vscdb` → `cursorDiskKV` | **tin nhắn** |
| cùng file → `composerHeaders` | metadata để chat hiện trên sidebar |
| cùng file → `ItemTable` | trạng thái đăng nhập (OAuth token, computer key) |
| `%USERPROFILE%\.cursor\projects\<slug>\agent-transcripts\*.jsonl` | transcript JSONL |
| `%APPDATA%\Cursor\User\workspaceStorage\<hash>\` | layout, tab mở, terminal |

Script `tools/cursor_chat_transfer.py` làm cả hai chiều.

### Trên máy cũ — export

```powershell
cd C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE
python tools\cursor_chat_transfer.py export `
    --project-dir "C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE" `
    --output "C:\Users\ezycloudx-admin\Desktop\NOVAPROFILE_CHAT_TRANSFER"
```

Kết quả (đã chạy thật, 22/11):

```
  chat ids            : 5
  transcripts         : 5
  disk_kv rows        : 5136
  item_table rows     : 206
  composer header rows: 5
  credential rows     : 14 (included)
  → 81,6 MB
```

### Copy sang máy mới

⚠️ **Thư mục này chứa credential đăng nhập của bạn.** Copy bằng tay:
USB, ổ cứng ngoài, hoặc file share riêng tư.

**KHÔNG** push lên git. **KHÔNG** để trên cloud công khai (Google Drive
public, Dropbox link, GitHub release…).

### Trên máy mới — import

```powershell
# copy tools\cursor_chat_transfer.py sang (đã nằm trong repo rồi)
cd <đường dẫn repo trên máy mới>
python tools\cursor_chat_transfer.py import `
    --export "C:\Users\<bạn>\Desktop\NOVAPROFILE_CHAT_TRANSFER"
```

**Đóng Cursor hoàn toàn** (kể cả các process nền) rồi mở lại. Chat sẽ
hiện trên sidebar.

Import là **merge**: cái đã có trên máy đích thắng, nên chạy 2 lần
cũng vô hại — nếu bạn vô tình copy nhầm hoặc chạy lại.

### Nếu chat vẫn không hiện

1. Kiểm tra Cursor đã đóng hẳn: Task Manager → `Cursor.exe` không còn.
2. Mở Cursor, đợi vài giây, kiểm tra sidebar.
3. Nếu vẫn không, chạy lại import (lần này sẽ ghi đè `composerHeaders`
   vì chat đã có → hãy xóa thư mục chat cũ ở máy mới trước):
   ```powershell
   Remove-Item -Recurse -Force "$env:USERPROFILE\.cursor\projects\<slug-của-máy-mới>"
   ```

---

## 3. Xác nhận dự án trên máy mới

```powershell
cd <repo>\novaprofile\control_plane

python -m pytest -q                                    # 559 passed
python -m ruff check .                                 # sạch
python -m novaprofile.scripts.measurements_cli verify   # ledger ok: 14 record(s)
python -m novaprofile.scripts.entropy_budget            # ≥128 bits mọi axis
python -m novaprofile.scripts.audit --count 2000        # 4 chiều
```

`measurements_cli verify` là bằng chứng file đã chuyển nguyên vẹn —
14 record trong ledger, checksum khớp.

### Nếu `pip install -e .` lỗi

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e . --no-build-isolation
```

---

## 4. Thứ tự việc trên máy mới

Đọc `RULES.md` trước — 11 mục luật, trong đó có quy tắc dừng-ngay-khi-lỗi
và quy tắc đo-đạc-trước-khi-giả-định.

Mục tồn đọng theo thứ tự (chi tiết trong `PROGRESS.md`):

| # | Việc | Ước lượng |
|---|---|---|
| 1 | Biên dịch `core_patch` thật trên Chromium v100 → bản mới nhất | 8–12 giờ |
| 2 | Viết `js_runtime` (lớp JS injection) | 12–16 giờ |
| 3 | Verify end-to-end trên Chromium thật, đối chiếu simulator | 6–10 giờ |
| 4 | Đóng gói installer | 6–8 giờ |

Ưu tiên tuyệt đối: **không chạy test 100M khi còn lỗi logic**. Quy tắc
R4 trong `RULES.md` nói rõ: test nhỏ → lỗi thì dừng → tính toán gốc →
sửa gốc → test lại → mới scale.

---

## 5. Nếu bạn muốn chuyển chat của project khác

Script không phụ thuộc project này:

```powershell
python tools\cursor_chat_transfer.py export `
    --project-dir "C:\đường\dẫn\workspace\khác" `
    --output "C:\chat-transfer\khác"
```
