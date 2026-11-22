# NOVA Profile Engine — Agent Rules

## Quy tắc bắt buộc: Không dừng, không hỏi

Người dùng đã cấp **toàn quyền** cho dự án này. Vì vậy:

> **Không dừng để hỏi ý kiến. Không dừng để báo cáo tiến độ giữa chừng.
> Làm đến khi xong thì mới dừng.**

Mọi quyết định bình thường sẽ là một câu hỏi (chọn cách nào, dùng thư viện
nào, cấu trúc module thế nào, giữ hay xóa file) — thì đó là quyết định
**được làm**, không phải câu hỏi **được hỏi**.

| Tình huống | Phải làm |
|---|---|
| Có hai cách hợp lệ | chọn một, ghi lý do, đi tiếp |
| Thiếu dependency | cài nó, hoặc viết code theo API đã tài liệu hoá |
| Test fail | chẩn đoán, sửa, chạy lại — không báo cáo rồi chờ |
| File trông như rác | đọc nó, kiểm tra tham chiếu, rồi mới quyết định |
| Việc hoá ra lớn hơn dự kiến | làm luôn, theo thứ tự |
| Người dùng bảo "làm hết" | làm hết, theo thứ tự mở khoá cho phần còn lại |

**Ngoại lệ duy nhất**: quyết định không thể hoàn tác rẻ và thay đổi *sản
phẩm* chứ không phải *cách hiện thực* (ví dụ xóa file cốt lõi, đổi ý
nghĩa của interface công khai). Những quyết định đó **ghi vào
`PROGRESS.md`**, không hỏi, và chọn phương án an toàn.

## Quy tắc bắt buộc: Viết xong ≠ Xong

Một bước chỉ được coi là xong khi nó **đã được kiểm chứng**, không phải
khi nó **đã được viết**.

| Khẳng định | Cách kiểm chứng |
|---|---|
| "Code chạy được" | test pass |
| "Đủ nhanh" | đo đạc đã ghi vào ledger |
| "Chạy được ở máy khác" | đã chạy thật trên nền tảng đích |
| "Đã đầy đủ" | mọi mặt của axis đã được phủ |

Viết code là nửa dễ. Nửa còn lại mới là nửa có giá trị: nửa tạo ra bằng
chứng. Bước đã viết mà chưa chạy thì **chưa xong**, và không được báo
là xong.

## Quy tắc bắt buộc: Test tự tạo input thì không thể fail

Luật này có thật, không phải lý thuyết. Patch series Chromium đã mang
**54 hunk, cả 54 khai báo sai số dòng**, nên `git apply` từ chối cả hai
patch. **82 test vẫn pass** — vì chúng dựng patch từ chính dữ liệu mà
patch được kiểm tra, nên test chỉ chứng minh code tự nhất quán. Một
patch series tự nhất quán chính là một patch series hỏng.

> **Một test mà tự tạo input của chính nó thì không thể fail.**  Khi sản
> phẩm phải gặp thế giới bên ngoài — patch vào cây Chromium của hãng
> khác, wheel mà khách hàng cài, profile mà website nhìn thấy — thì cách
> kiểm chứng phải chạy **với chính thế giới đó**.

| Sản phẩm | Test không thể fail | Cách kiểm chứng thật |
|---|---|---|
| Patch cho Chromium | áp patch vào patch do test sinh ra | `git apply --check` trên **checkout Chromium nguyên bản**, không `--recount`, không `--3way`, không fuzz |
| Wheel phát hành | import từ cây nguồn | cài vào virtualenv sạch rồi import từ đó |
| Profile | assert field bằng schema | chạy trong **trình duyệt thật** rồi đọc surface ngược lại |
| Cấu hình | parse bằng parser của dự án | parse bằng **parser upstream** |

**Thói quen**: khi toàn bộ mục đích của một thành phần là gặp một hệ
thống bên ngoài, việc đầu tiên cần làm là **lấy bản sao của hệ thống đó**.
Một checkout Chromium 23 GB tốn một giờ và một ổ cứng — rẻ hơn vô số
giờ test không thể fail.

Hai hệ quả:

- **Lấy bản thật sớm**, kể cả khi chưa có gì phụ thuộc vào nó.
- **Không bao giờ kiểm chứng sản phẩm trên bản sao do chính test tạo
  ra.** Bản sao mang theo đúng lỗi đó.

## Quy tắc bắt buộc: Dùng agent song song, quản lý chặt

Người dùng cho phép dùng nhiều agent để đẩy nhanh tiến độ. Nhưng agent
được dùng **đúng cách** thì mới nhanh; dùng sai thì chậm hơn làm trực tiếp
và tốn token.

### Nguyên tắc

> **Một agent — một nhóm file — một cách kiểm chứng.**  Hai agent không
> bao giờ sửa cùng một file.  Và agent viết code **không** được quyết
> định code đó có chạy không.

### Cách giao việc cho agent

Phải đưa đủ 4 thứ:

1. **Danh sách đường dẫn file được phép tạo/sửa.**  Liệt kê từng file,
   không ghi "phần backend".  "Bạn sở hữu thư mục X" là chấp nhận được;
   "bạn phụ trách backend" là không.
2. **Interface phải đáp ứng**, chép từ `AGENT_CONTRACT.md`.  Agent không
   được tự bịa interface mới.
3. **Bộ test phải làm pass**, đánh số tên, kèm lệnh chạy.
4. **Bằng chứng phải báo lại**: agent phải dán **output thật** của lệnh đã
   chạy, không phải lời khẳng định.

### Phân chia công việc

**Agent chính giữ:**

- các **điểm tích hợp** — launcher, CLI, `__init__.py` export.  Nhiều agent
  chạm vào, hai agent sửa là đụng nhau.
- **phán đoán xuyên suốt** — kết luận "một giá trị sai trên toàn fleet" là
  quyết định về sản phẩm, không thuộc một module.
- **kiểm chứng cuối** — chạy suite, sửa cái vỡ, kiểm tra các phần khớp
  nhau.

**Agent phụ trách:**

- các **module độc lập** — một bảng, một builder, một harness.  Đơn vị ủy
  quyền lý tưởng là module viết được theo interface cố định và kiểm
  chứng bằng file test riêng.
- **khối lượng lớn** — 117 tọa độ, ma trận phiên bản, bộ trích 12 axis.

### Không bao giờ ủy quyền

| Việc | Vì sao giữ lại |
|---|---|
| Sửa bug trong module dùng chung | hai agent sẽ sửa cùng một file |
| Đổi interface công khai | ảnh hưởng mọi agent |
| Kết luận "sản phẩm đang sai" | đó là quyết định sản phẩm |
| Kiểm chứng cuối | chỉ một agent nhìn thấy toàn cảnh |

### Agent tìm thấy bug ngoài phạm vi

Agent **báo lại**, không tự sửa.  Đó là điều giữ cho việc ghép code trở
nên dễ.

### Trong lúc agent chạy

Agent chính **không đợi**.  Nó làm phần không ai được giao.  Đợi agent là
điều duy nhất làm cho agent song song chậm hơn làm trực tiếp.

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
