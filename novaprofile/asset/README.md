# Thư mục `asset` — ảnh thương hiệu

Đặt ảnh logo của NOVA PROFILE vào đây. Hướng dẫn đầy đủ (kèm prompt tạo ảnh)
nằm trong [`../brand/BRANDING.md`](../brand/BRANDING.md).

## Danh sách file cần có

| File | Kích thước | Dùng cho |
|---|---|---|
| `nova-logo-1024.png` | 1024×1024 | ảnh gốc, nguồn của mọi kích thước khác |
| `nova-logo-512.png` | 512×512 | icon trong giao diện, splash screen |
| `nova-logo-256.png` | 256×256 | icon lớn |
| `nova-logo-128.png` | 128×128 | icon vừa |
| `nova-logo-64.png` | 64×64 | icon nhỏ |
| `nova-logo-32.png` | 32×32 | icon rất nhỏ, favicon |
| `nova-wordmark.png` | ngang | chữ NOVA PROFILE |
| `nova-appicon.png` | 1024×1024 | icon cho macOS |

## Yêu cầu

- **PNG, nền trong suốt** (alpha channel), màu sRGB 8 bit.
- Các bản nhỏ **resize từ bản 1024** bằng LANCZOS — không vẽ lại từng cỡ.
  AI vẽ logo 32×32 sẽ méo chữ và vỡ đường.
- Không viền, không đổ bóng, không nền ô vuông phía sau mark.

## Sau khi đặt ảnh xong

Báo lại, tôi sẽ:

1. Sinh `nova.ico` (7 kích thước: 16/24/32/48/64/128/256) và `nova.icns`.
2. Copy toàn bộ vào thư mục cài đặt, đặt `nova.ico` ở gốc để Explorer hiện
   icon thư mục.
3. Tạo shortcut **Start Menu** và **Desktop** trỏ vào `.ico`.
4. Dùng bảng màu trong `../brand/theme.py` cho header của CLI.

Code đọc ảnh từ đây: `novaprofile/brand/icons.py` (hàm `_find` tìm trong
`asset/` trước, `brand/` sau).
