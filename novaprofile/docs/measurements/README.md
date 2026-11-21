# Sổ đo đạc — NOVA PROFILE

Mọi số liệu đo của dự án được gom về đây để **không phải đo lại** một việc đã có kết quả.

## Quy ước

Mỗi lần đo là **một file JSON** trong `docs/measurements/data/`, đặt tên theo:

```
YYYYMMDD-HHMM-<slug>.json
```

Kèm theo là một entry trong `docs/measurements/INDEX.md` ghi lại:
- lúc đo, môi trường, commit/revision
- kết quả chính
- **kết luận**: dùng lại được hay phải đo lại

## Quy tắc bất di bất dịch

1. **Không đo lại** một cái đã có entry trong `INDEX.md` với cùng `slug` và cùng điều kiện đo → chỉ cần đọc lại.
2. Mọi script đo **phải** ghi kết quả tự động vào `data/` (không in ra rồi mất).
3. Kết quả cũ **không xóa** — giữ lại để so sánh "trước / sau" khi tối ưu.
4. Mỗi entry phải có `sha256` của dữ liệu để kiểm tra toàn vẹn.
5. Số liệu nào **không đo được** trên máy này → ghi `"status": "blocked"` + lý do, để không ai tưởng là đã xong.

## Danh mục đo đạc

| Slug | Mô tả | Script | Trạng thái |
|---|---|---|---|
| `scale-collision` | Kiểm tra va chạm theo quy mô đội profile | `novaprofile.scripts.scale_check` | ✅ có |
| `entropy-budget` | Entropy từng trục so với ngưỡng sinh nhật 100M | `novaprofile.scripts.entropy_budget` | ✅ có |
| `test-suite` | Kết quả pytest + ruff | `novaprofile.scripts.record_quality` | ✅ có |
| `core-patch-compile` | Biên dịch core_patch trên Chromium | — | 🔴 blocked (thiếu toolchain) |
| `fingerprint-live-probe` | Đo fingerprint thật từ Chromium đã patch | `novaprofile.probe` | 🔴 blocked (chưa có binary) |
| `host-leak-audit` | Rò rỉ môi trường host | `novaprofile.network.leakcheck` | ⚠️ partial |
| `batch-throughput` | Throughput sinh profile hàng loạt | `novaprofile.scripts.store_bench` | ✅ có |

---

## Nhanh chóng

```powershell
# Liệt kê tất cả lần đo
python -m novaprofile.scripts.measurements list

# Chạy một phép đo và tự lưu
python -m novaprofile.scripts.measurements run scale-collision -- --count 200000

# Đọc lại kết quả gần nhất của một phép đo
python -m novaprofile.scripts.measurements show scale-collision
```
