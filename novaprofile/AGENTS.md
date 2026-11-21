# NOVA Profile Engine — Agent Rules

## Quy tắc bắt buộc: Dọn rác có kiểm soát

Sau khi hoàn thành một phần việc, **phải dọn rác** (file rác, test tạm, code cũ không dùng,
file backup, output thừa). Nhưng **trước khi xóa bất kỳ file nào, phải quan sát thật kỹ
để chắc chắn không xóa nhầm file chính**.

### Quy trình dọn rác (bắt buộc theo đúng thứ tự)

1. **Liệt kê đầy đủ** — `Get-ChildItem -Recurse -Force` (bao gồm file ẩn) trong phạm vi
   dự án. Ghi lại danh sách trước khi làm gì.
2. **Phân loại từng file** vào 1 trong 4 nhóm:
   - `KEEP` — file chính, đang được import/tham chiếu.
   - `KEEP-CHECK` — nghi ngờ, phải kiểm tra thêm.
   - `DELETE-SAFE` — rác rõ ràng: `__pycache__/`, `.pyc`, `.pytest_cache/`, `.ruff_cache/`,
     `*.tmp`, `*.log`, `*.bak`, `*.old`, `*~`, `.DS_Store`, `Thumbs.db`,
     file output rác, test scratch tự tạo rồi bỏ dở.
   - `DELETE-REVIEW` — có vẻ cũ/không dùng nhưng **chưa chắc** → phải kiểm tra tham chiếu.
3. **Kiểm tra tham chiếu cho nhóm `DELETE-REVIEW` và `KEEP-CHECK`:**
   - `Grep` toàn repo cho tên file / tên module (không chỉ import Python — còn kiểm tra
     `pyproject.toml`, `docs/`, `.md`, script shell/powershell, path trong string).
   - Kiểm tra file có được `import` ở đâu không:
     `python -c "import novaprofile.X"` hoặc grep tên module.
   - Nếu còn bất kỳ tham chiếu nào → chuyển sang `KEEP`.
4. **Xác minh repo vẫn khỏe sau khi xóa:**
   - `python -m pytest tests -q` (nếu test tồn tại)
   - `python -c "import novaprofile"` và import từng subpackage
   - `python -m ruff check novaprofile`
5. **Nếu xóa làm hỏng gì → khôi phục ngay** và đánh dấu file là `KEEP`.
6. **Báo cáo** trong phần tổng kết: đã xóa file nào, giữ lại file nào đã kiểm tra
   tham chiếu, và vì sao.

### Cấm

- Cấm xóa hàng loạt bằng `Remove-Item -Recurse -Force <thư mục>` mà không liệt kê và kiểm tra từng mục.
- Cấm xóa file có nội dung "có vẻ lạ" mà chưa đọc.
- Cấm xóa file nguồn (`*.py`, `*.ts`, `*.cpp`, `*.h`, `*.md`, `*.json`, `*.toml`) chỉ vì
  "không thấy dùng" — phải grep tham chiếu trước và nếu là file chính thì loại khỏi danh sách xóa.
- Cấm dùng `git clean -xfd` (repo này chưa commit xong).
- Cấm xóa file của agent khác đang chạy song song — chỉ xóa trong phạm vi sở hữu của mình
  hoặc khi được giao rõ.

### An toàn trên Windows

- Xóa từng file bằng `Remove-Item -LiteralPath <đường dẫn tuyệt đối>` — KHÔNG dùng wildcard
  với đường dẫn tuyệt đối (PowerShell wildcard + `-LiteralPath` xung đột).
- Trước khi xóa, in ra đường dẫn tuyệt đối và nội dung/ngày sửa để tự kiểm chứng lần cuối.
- Nếu file đang bị process giữ (locked), dừng lại và báo, không cưỡng ép.

## Quy tắc chung về code

- Python 3.12, Pydantic v2, type hints đầy đủ, `from __future__ import annotations`.
- Không để lại `TODO`, `pass  # TODO`, placeholder, hay hàm rỗng.
- Không sửa file ngoài phạm vi sở hữu được giao.
- Mọi test phải deterministic, không gọi network thật.

## Đọc thêm

- `docs/AGENT_CONTRACT.md` — interface contract giữa các agent (bắt buộc đọc).
