# NOVA PROFILE — Bộ nhận diện thương hiệu

Tài liệu này là để bạn tự làm phần hình ảnh. Tôi (agent) không tạo được file
ảnh vector/bitmap chất lượng trong repo này, nên phần việc được chia làm:

- **Bạn**: tạo ảnh gốc bằng công cụ tạo ảnh, theo prompt bên dưới.
- **Tôi**: nhận file ảnh, đặt vào đúng chỗ, viết code sinh `.ico`/`.icns`,
  nối vào installer, thêm shortcut Start Menu + Desktop, đóng gói lại.

Cứ làm theo prompt, đặt file vào `novaprofile/brand/`, rồi bảo tôi "ảo xong".

---

## 1. Thư mục cần tạo

```
novaprofile/
└── asset/
    ├── nova-logo-1024.png      # ảnh gốc, 1024×1024, nền trong suốt
    ├── nova-logo-512.png       # 512×512
    ├── nova-logo-256.png       # 256×256
    ├── nova-logo-128.png       # 128×128
    ├── nova-logo-64.png        # 64×64
    ├── nova-logo-32.png        # 32×32
    ├── nova-wordmark.png       # chữ NOVA PROFILE, ngang, nền trong suốt
    └── nova-appicon.png        # 1024×1024, icon dạng macOS (nhiều lớp)
```

Đặt ảnh vào **`novaprofile/asset/`** (không phải `brand/`) — đó là nơi code
đọc ảnh. Tôi sẽ tự copy sang thư mục cài đặt.

Yêu cầu kỹ thuật:

| Thuộc tính | Yêu cầu |
|---|---|
| Định dạng | PNG, **nền trong suốt** (alpha) |
| Màu | sRGB, 8 bit mỗi kênh |
| Kích thước | 1024×1024 cho bản gốc, các bản nhỏ là **phóng to** từ bản gốc, không vẽ lại |
| Không | Không viền, không đổ bóng, không gradient ngoài thiết kế |

**Quan trọng**: các bản nhỏ phải resize từ bản 1024 bằng LANCZOS, **không** để
AI vẽ lại từng kích thước. AI vẽ logo 32×32 sẽ méo chữ và vỡ đường.

---

## 2. Prompt tạo ảnh

### 2.1 Icon chính — `nova-logo-1024.png`

```
A minimal, premium app icon for a technical product called "NOVA PROFILE".
Subject: a single abstract geometric mark that suggests a "profile" and a
"signal" at the same time — a rounded hexagonal shield or aperture shape,
with a single clean diagonal light-sweep cutting through its centre, and
three small ascending bars or dots forming a subtle ascending trail along
one edge, evoking a signal reading and a rising graph.

Style: modern flat-vector with a very subtle depth. Think Linear, Raycast,
Arc Browser, Stripe or Vercel — precise, quiet, engineered, not playful.
Rounded geometry, consistent 8-point grid alignment, optically balanced.

Colour: deep midnight navy to near-black base (#0B1020 → #131A2E), with a
single vivid electric cyan-to-violet gradient accent (#22D3EE → #7C5CFF)
on the light-sweep and the signal trail. No other colours.

Rendering: perfectly crisp vector-like edges, clean anti-aliasing, no
texture, no noise, no drop shadow, no outer border, no rounded-square
app-icon tile behind the mark (the mark floats on transparency).

Composition: the mark occupies about 72% of the canvas, perfectly centred
with even optical padding on all four sides.

Output: a single icon, 1024×1024, transparent background, no text, no
letterforms, no mockup, no device frame, no shadow.
```

### 2.2 Wordmark — `nova-wordmark.png`

```
A clean wordmark reading exactly "NOVA PROFILE" in a modern geometric
sans-serif typeface — tight, technical, confident. Similar in spirit to
Inter, Söhne, Neue Haas Grotesk or a custom technical grotesk.

All-caps. Generous but even letter-spacing (about 6% of the cap height).
The word "NOVA" in solid bright white (#FFFFFF) and the word "PROFILE" in
a lighter weight at about 70% opacity of the same white, separated by a
thin vertical divider bar in electric cyan (#22D3EE).

Purely typographic, no icon, no emblem, no tagline, no subtitle, no
slogan. Flat vector, crisp edges, no texture, no shadow, no 3D.

Horizontal lockup, transparent background, generous left and right
padding, total height around 256 px with the baseline aligned so the
logo can be scaled freely.
```

### 2.3 Biến thể dự phòng — nếu prompt chính không cho kết quả tốt

Nếu bản "aperture / signal trail" quá rối hoặc AI vẽ nhiều chi tiết thừa,
dùng prompt tối giản hơn này — nó dễ ra kết quả sạch hơn ở kích thước nhỏ:

```
A single, extremely minimal geometric app icon. One rounded hexagon
outline with a soft luminous gradient stroke that fades from electric
cyan at the top-left to violet at the bottom-right, on a transparent
background. The hexagon is slightly open at one edge. Nothing else.

Ultra-clean, flat vector, 1024×1024, centred, generous padding,
crisp anti-aliased edges, no shadow, no text, no background tile.
```

Sau đó ghép bản tối giản này với wordmark ở mục 2.2 — logo chữ + icon
tối giản là tổ hợp rủ ít rủi ro nhất.

---

## 3. Màu thương hiệu (dùng thống nhất)

| Vai trò | Hex | Dùng ở đâu |
|---|---|---|
| Nền sâu | `#0B1020` | nền tối, splash screen, header installer |
| Nền nhẹ | `#131A2E` | nền panel |
| Nhấn chính | `#22D3EE` | cyan — accent, thanh tiến trình, focus ring |
| Nhấn phụ | `#7C5CFF` | violet — đầu gradient |
| Chữ sáng | `#FFFFFF` | text chính |
| Chữ mờ | `#94A3B8` | text phụ |
| Cảnh báo | `#F59E0B` | amber — cảnh báo trong `nova doctor` |
| Lỗi | `#EF4444` | red — lỗi trong `nova doctor` |

Đặt các giá trị này thành hằng số trong `novaprofile/brand/theme.py` để
CLI, installer và tài liệu dùng chung một nguồn.

---

## 4. Thứ tự làm sau khi có ảnh

Báo tôi, tôi làm tiếp toàn bộ phần còn lại:

1. `novaprofile/brand/__init__.py` — nạp ảnh, `icon_path()`, `wordmark_path()`.
2. `novaprofile/brand/theme.py` — bảng màu ở trên.
3. `novaprofile/brand/icons.py` — sinh `.ico` đa kích thước (16/24/32/48/64/128/256)
   từ PNG bằng Pillow; sinh `.icns` cho macOS.
4. `novaprofile/installer/builder.py`:
   - copy ảnh vào staging (`brand/`, `assets/`);
   - tạo shortcut **Start Menu** và **Desktop** bằng `WScript.Shell`
     với `IconLocation` trỏ vào `.ico`;
   - thêm `--icon` cho uninstaller;
   - ghi `icon.ico` ở gốc staging để Explorer hiện đúng icon thư mục.
5. `novaprofile/cli/main.py` — Rich console: dùng màu thương hiệu cho header
   của `nova doctor` / `nova presets`.
6. Kiểm thử: ảnh phải còn sau khi cài, shortcut phải có icon, uninstaller
   phải dọn shortcut nhưng **giữ** dữ liệu profile.

---

## 5. Kiểm tra trước khi gửi

- Mở ảnh trên nền **trắng** và trên nền **tối** — mark phải nổi rõ trên cả hai.
- Thu nhỏ về 16×16: hình dạng phải còn nhận ra, không thành một vết mờ.
- Thu nhỏ về 32×32: đường viền không được rò rỉ, không xuất hiện răng cưa.
- Chữ trong wordmark: không được dính vào nhau, không được cắt mất nét.
- Không có viền trắng/đen quanh mark (tức alpha sạch).

Nếu bạn không chắc, gửi cả bản 1024 và một bản preview 32×32 đã resize —
tôi sẽ đánh giá trước khi đóng gói.
