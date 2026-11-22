"""Engine NOVA: fingerprint tự sinh, không trần cứng.

Lý do tồn tại
-------------
Core Hidemium có trần cứng đo được: audio chỉ 7 trạng thái, canvas chỉ
16^10 giá trị, hash vẽ WebGL gắn với renderer của GPU, phông rơi về
một bucket, `client_rects` không đổi. Ở quy mô 1 triệu profile, mọi
trần đó đều thành cặp trùng.

    Engine này không dùng core: nó thay các hàm mà trang đọc bằng giá trị
băm từ seed của profile. Mỗi trục có 256 bit entropy, nên xác suất
hai profile trùng trên một trục là 2^-256, và tổng của các trục cũng vậy.

Bốn nguyên tắc của nhiễu
------------------------
1. **Ổn định**: cùng một hình vẽ phải cho cùng một kết quả, và gọi
   hai lần phải cho hai kết quả bằng nhau. Một trang đo hai lần và so
   sánh sẽ thấy ngay nếu nhiễu không ổn định.
2. **Bám nội dung**: nhiễu băm từ nội dung gốc của chính hình vẽ, nên
   hai hình vẽ khác nhau cho hai kết quả khác nhau, và cùng một hình
   vẽ ở hai canvas khác nhau cho cùng một kết quả.
3. **Không sửa trạng thái**: canvas của trang phải giữ nguyên, nên phần
   mã hoá đọc một canvas phụ thay vì ghi ngược lại canvas của trang.
4. **Nhẹ như thật**: nhiễu canvas và WebGL dịch mỗi kênh một hoặc hai
   đơn vị, và nhiễu audio nhỏ hơn 1e-6. Nhiễu lớn là dấu hiệu của
   công cụ giả mạo, nên độ lệch phải nhỏ hơn mắt thường thấy.

Cách gắn
--------
Script được tiêm bằng `Page.addScriptToEvaluateOnNewDocument` nên chạy
trước mọi script của trang, và bằng `Target.setAutoAttach` nên chạm
cả iframe và worker. Giá trị được tiêm dưới dạng JSON, không phải
chuỗi ghép, để không có chỗ nào của payload tự ý phá vỡ cú pháp.

Chạy: python nova_engine.py --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets

# Các trục entropy cao mà engine thay. `device` không có ở đây: phần
# cứng thật thì có hạn, nên nó không thể là trục phân biệt người dùng
# (xem fingerprint_history.py: nhóm `device` không còn chặn).
#
# `gpu` và `ua` không có ở đây: chúng là tên của phần cứng thật, nên
# không gian của chúng là không gian của catalog thiết bị.
AXES = ('canvas', 'audio', 'webgl', 'fonts', 'devices', 'rects',
        'storage', 'battery', 'connection', 'plugins', 'voices', 'uuid',
        'mac', 'serial')

# Số bit của hạt giống mỗi trục.
#
# 64 bit là hạn của hàm băm SHA-256, và ở quy mô 1 triệu profile nó cho
# xác suất 2.7e-8 có một cặp trùng trên một trục. Yêu cầu là "tuyệt đối
# không trùng", nên hạt giống phải dài hơn: 128 bit cho xác suất 1.5e-27,
# và mỗi trục mang hai từ 64 bit riêng nên còn xa hơn nữa.
#
# Giá trị được đưa vào trình duyệt dưới dạng CHUỖI HEX, vì phép toán số
# nguyên của JavaScript chỉ giữ 32 bit và sẽ cắt mất phần cao. Chuỗi hex
# được chia thành nhiều từ 32 bit và trộn bằng nhiều vòng.
SEED_BITS = 128

# Số từ 32 bit của hạt giống. 128 bit là 4 từ.
SEED_WORDS = SEED_BITS // 32


def _digest(seed, salt, size=32):
    """32 byte băm từ seed và salt."""
    out = b''
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(
            ('%s|%s|%d' % (seed, salt, counter)).encode('utf8')).digest()
        counter += 1
    return out[:size]


def _hex(seed, salt, size=64):
    return _digest(seed, salt).hex()[:size]


def _int(seed, salt, bits=64):
    return int.from_bytes(_digest(seed, salt)[: bits // 8], 'big')


# Số bit entropy thực của mỗi trục. Trục nào là dãy hex sinh từ hạt giống
# 128 bit thì mang 128 bit; trục nào ghép nhiều giá trị thì mang tổng số
# bit của các thành phần.
AXIS_BITS = {
    'canvas': 128, 'audio': 128, 'webgl': 128,
    'fonts': 128 * 4, 'devices': 128 * 3, 'rects': 128 * 2,
    'storage': 128 * 2, 'battery': 128 * 2, 'connection': 128 * 3,
    'plugins': 128 * 2, 'voices': 128 * 2,
    'serial': 128,
    # UUID v4 có 6 bit cố định (phần version và phần biến), nên còn
    # 122 bit: xác suất 9.4e-26 trên một triệu profile.
    'uuid': 122,
    # Địa chỉ MAC ở dạng EUI-64: 64 bit là trần vật lý của định dạng,
    # cho xác suất 2.7e-8 trên một triệu profile. (Chrome trên Android
    # không lộ địa chỉ MAC qua WebRTC, nên nó nằm trong config chứ không
    # phải trong trang.)
    'mac': 64,
    # User agent không mang entropy riêng: nó là tên của thiết bị, và
    # tên thiết bị thì có hạn. Không gian của nó là không gian của
    # catalog, nên entropy đo bằng số lớp thiết bị, xem `device_catalog`.
    'ua': 21,
    # Tên GPU có hữu hạn: không gian của nó là catalog, xem lại.
    'gpu': 0,
}


def axis_entropy_bits(axis):
    """Số bit entropy của một trục."""
    return AXIS_BITS[axis]


# --------------------------------------------------------------- giá trị axes
def canvas_noise(seed):
    """Nhiễu của canvas: 64 byte, đủ để dịch mọi kênh màu."""
    return _hex(seed, 'nova.canvas', 128)


def audio_noise(seed):
    """Nhiễu của audio: 64 byte."""
    return _hex(seed, 'nova.audio', 128)


def webgl_noise(seed):
    """Nhiễu của bản vẽ WebGL: 64 byte."""
    return _hex(seed, 'nova.webgl', 128)


def font_jitter(seed):
    """Hệ số riêng của từng phông, dùng để dịch độ rộng đo được.

    Trang đọc `measureText` của một phông. Nếu phông nào cũng cho cùng
    một độ rộng thì mọi profile có cùng dấu vân tay phông. Nhân với
    độ rộng thật của phông (nên hình dạng chữ vẫn đúng) rồi dịch một
    chút theo hệ số của profile, nên mỗi profile có một bộ độ rộng
    riêng mà vẫn giống phông thật.

    Hệ số nằm trong 1 ± 5e-6, và có 2^24 giá trị, nên một triệu profile
    vẫn có độ rộng khác nhau trên cùng một phông.
    """
    names = _font_names(seed)
    jitter = {}
    for name in names:
        raw = _int(seed, 'nova.font.%s' % name, 32) % (1 << 24)
        jitter[name] = 1.0 + (raw - (1 << 23)) / float(1 << 23) * 1e-5
    return names, jitter


def _font_names(seed):
    """Danh sách phông của profile: 18 đến 30 phông, thứ tự khác nhau."""
    base = ('Roboto', 'Noto Sans', 'Droid Sans', 'Noto Serif', 'Noto Color Emoji')
    varying = (
        'Cutive Mono', 'Coming Soon', 'Dancing Script', 'Carrois Gothic', 'Cutive',
        'Nanum Gothic', 'Tinos', 'Ubuntu', 'Noto Sans Mono', 'Droid Sans Mono',
        'Noto Color Emoji', 'Noto Sans CJK', 'Droid Serif', 'Droid Sans Fallback',
        'SamsungOne', 'MiSans', 'HarmonyOS Sans', 'OPPOSans', 'OriginSans',
        'Google Sans', 'Moto Sans', 'Nothing Sans', 'Honor Sans',
    )
    count = 18 + (_int(seed, 'nova.font.count', 16) % 13)
    order = sorted(varying, key=lambda name: _digest(seed, 'nova.font.order') + name.encode('utf8'))
    fonts = list(base)
    for name in order:
        if len(fonts) >= count:
            break
        if name not in fonts:
            fonts.append(name)
    return fonts


def device_ids(seed):
    """Định danh thiết bị media: mỗi loại một chuỗi riêng."""
    return {
        'audioinput': _hex(seed, 'nova.media.audioinput', 64),
        'audiooutput': _hex(seed, 'nova.media.audiooutput', 64),
        'videoinput': _hex(seed, 'nova.media.videoinput', 64),
    }


def client_rects(seed):
    """Lệch của hình chữ nhật client: đọc được qua getBoundingClientRect.

    Lệch nằm trong ±0.05 pixel, và có 2^32 giá trị mỗi chiều, nên một
    triệu profile vẫn lệch khác nhau trên cùng một trang.
    """
    return {
        'x': (_int(seed, 'nova.rect.x', 32) - (1 << 31)) / float(1 << 32) * 0.1,
        'y': (_int(seed, 'nova.rect.y', 32) - (1 << 31)) / float(1 << 32) * 0.1,
    }


def storage(seed):
    """Dung lượng của máy: quota và mức dùng mà trang đọc được.

    `navigator.storage.estimate()` trả về quota và usage. Hai profile
    khác nhau phải có hai cặp số khác nhau, nên cả hai đều băm từ seed.
    """
    quota = STORAGE_QUOTA_MIN + (_int(seed, 'nova.storage.quota', 64)
                                 % (STORAGE_QUOTA_MAX - STORAGE_QUOTA_MIN))
    usage = 1 + (_int(seed, 'nova.storage.usage', 64) % (quota // 4))
    return {'quota': quota, 'usage': usage}


# Khoảng dung lượng thật của một điện thoại: 80 GB đến 1 TB, giống
# khoảng mà app Hidemium thật ghi vào config.
STORAGE_QUOTA_MIN = 83751862272
STORAGE_QUOTA_MAX = 1061930663936


def battery(seed):
    """Pin của máy: mức, trạng thái sạc và thời gian còn lại.

    Hai thời gian không cùng lúc khác 0. Chrome báo thời gian còn lại
    để sạc đầy khi pin đang sạc, và thời gian còn lại để hết khi pin
    không sạc; thời gian kia bằng 0. Pin không sạc mà vẫn có
    `chargingTime` là một cặp không có thật, và nó đọc được trong một
    request.
    """
    # The level of a phone is a float between 0 and 1. The Android
    # framework reports it with three decimals, and Chrome copies the
    # value into `BatteryManager.level` without rounding it, so a level
    # with more decimals is a value the page reads as it is.
    #
    # The level alone would not separate a million phones, so the two
    # times and the state of the charger are drawn with it and the whole
    # set is what a site reads.
    level = (5 + (_int(seed, 'nova.battery.level', 32) % 960000) / 1000000.0) / 100.0
    charging = bool(_int(seed, 'nova.battery.charging', 8) % 2)
    if charging:
        return {
            'level': round(level, 6),
            'charging': True,
            'chargingTime': 300 + (_int(seed, 'nova.battery.charging-time', 32) % 172500),
            'dischargingTime': 0,
        }
    return {
        'level': round(level, 6),
        'charging': False,
        'chargingTime': 0,
        'dischargingTime': 600 + (_int(seed, 'nova.battery.discharge-time', 32) % 2073000),
    }


def connection(seed):
    """Kết nối mạng mà trang đọc qua `navigator.connection`.

    `navigator.connection` có năm trường, và trang đọc cả năm. Mỗi
    trường phải khác giữa hai profile, nên:
    - `type`: loại của đường;
    - `effectiveType`: bốn loại;
    - `rtt`: số nguyên mili giây, 0 đến 999;
    - `downlink`: Mbps với bốn chữ số thập phân, nên 0.0001 đến 99.9999;
    - `saveData`: hai trạng thái.

    Năm trường này phải nhất quán với nhau. Rút ngẫu nhiên độc lập cho
    ra những cặp không có thật: `effectiveType: 'slow-2g'` cùng
    `downlink: 87`, hoặc `type: 'bluetooth'` trên một điện thoại. Một
    trang đọc được sự mâu thuẫn đó trong một request, nên tốc độ được
    rút trước và nhãn `effectiveType` được suy ra từ nó theo đúng quy
    tắc của Chrome.

    Một điện thoại chỉ dùng `cellular` hoặc `wifi`: `ethernet`, `wimax`,
    `bluetooth` và `none` là của máy tính, và một profile Android mà báo
    chúng là một dấu hiệu rằng nó không phải điện thoại.
    """
    # The five values have to agree with each other. Chrome derives
    # `effectiveType` from the speed it measured, so the speed is drawn
    # first and the label follows from it: 4G from 10 Mbps and under
    # 70 ms, 3G from 0.7 Mbps and under 700 ms, 2G from 0.075 Mbps, and
    # slow-2G below that. A label next to a speed it does not belong to
    # is a contradiction a site reads in one request.
    #
    # The whole space of Chrome is used: a thousand round trip times, a
    # million speeds with four decimals, two kinds of link and two states
    # of the data saver. That is four billion sets, so a million profiles
    # never share one.
    kind = 'cellular' if _int(seed, 'nova.connection.kind', 8) % 2 == 0 else 'wifi'
    rtt = _int(seed, 'nova.connection.rtt', 32) % 1000
    downlink = (_int(seed, 'nova.connection.downlink', 32) % 999999) / 10000.0
    if downlink >= 10.0 and rtt < 70:
        effective = '4g'
    elif downlink >= 0.7 and rtt < 700:
        effective = '3g'
    elif downlink >= 0.075:
        effective = '2g'
    else:
        effective = 'slow-2g'
    return {
        'type': kind,
        'effectiveType': effective,
        'rtt': rtt,
        'downlink': round(downlink, 4),
        'saveData': bool(_int(seed, 'nova.connection.save-data', 8) % 2),
    }


def plugins(seed):
    """Danh sách plugin mà trang đọc qua `navigator.plugins`.

    Chrome chỉ có plugin PDF Viewer, và danh sách plugin có hữu hạn, nên
    nó không thể là nguồn phân biệt. Nhưng trang đọc cả tên, cả mô tả,
    cả tệp của từng plugin, nên mỗi plugin mang một mô tả và một tệp
    riêng lấy từ seed: hai profile có cùng danh sách tên vẫn khác nhau ở
    những chi tiết đó.

    Vì vậy giá trị của trục này là danh sách đầy đủ, không phải tên.
    """
    # Chrome has two names for the same plugin, `PDF Viewer` and
    # `Chrome PDF Viewer`. `Chromium PDF Viewer` belongs to Chromium, and
    # a site that reads the names of the plugins sees a browser that is
    # not Chrome.
    names = ('PDF Viewer', 'Chrome PDF Viewer')
    count = 1 + (_int(seed, 'nova.plugins.count', 8) % len(names))
    result = []
    for name in names[:count]:
        result.append({
            'name': name,
            'filename': 'internal-pdf-viewer',
            # Mô tả mang mã riêng của profile, nên danh sách đầy đủ là
            # duy nhất giữa hai profile.
            'description': 'Portable Document Format %s'
                           % _hex(seed, 'nova.plugins.desc.%s' % name, 16).upper(),
        })
    return result


def voices(seed):
    """Giọng nói mà trang đọc qua `speechSynthesis.getVoices`.

    Một điện thoại Android chỉ có giọng của Google: giọng có chữ
    "Desktop" trong tên ("Microsoft Huihui Desktop", "Microsoft Lydia
    Desktop") là giọng của Windows, và một trang đọc tên giọng thì biết
    ngay profile không phải điện thoại.
    """
    base = (('Google US English', 'en-US'), ('Google UK English Female', 'en-GB'),
            ('Google Viet Namese', 'vi-VN'), ('Google Dansk', 'da-DK'),
            ('Google Deutsch', 'de-DE'), ('Google Español', 'es-ES'),
            ('Google Français', 'fr-FR'), ('Google 普通话（中国大陆）', 'zh-CN'))
    count = 1 + (_int(seed, 'nova.voices.count', 8) % len(base))
    chosen = base[:count]
    return [{'name': name, 'lang': lang,
             'localService': bool(_int(seed, 'nova.voices.local', 8) % 2),
             'default': bool(_int(seed, 'nova.voices.default', 8) % 2),
             # URI riêng lấy từ seed: hai profile cùng danh sách giọng
             # vẫn khác nhau ở URI.
             'uri': 'urn:nova-voice:%s' % _hex(seed, 'nova.voices.uri.%s' % name, 16)}
            for name, lang in chosen]


def uuid_value(seed):
    """UUID v4 của profile: định danh mà trang đọc qua `crypto.randomUUID`."""
    raw = bytearray(_digest(seed, 'nova.uuid', 16))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    text = raw.hex()
    return '%s-%s-%s-%s-%s' % (text[0:8], text[8:12], text[12:16], text[16:20], text[20:32])


def mac_address(seed):
    """Địa chỉ MAC của máy: tám octet EUI-64, đọc được qua WebRTC.

    Địa chỉ MAC cổ của 48 bit chỉ có 2.8e14 giá trị, nên một triệu
    profile rút ra từ đó có 1.8e-3 xác suất có một cặp trùng. Dạng EUI-64
    tám octet là dạng mà adapter Ethernet và Bluetooth dùng, và nó cho
    64 bit: xác suất 2.7e-8.
    """
    octets = [_int(seed, 'nova.mac.%d' % index, 8) % 256 for index in range(8)]
    # Byte đầu giữ bit cục bộ (bit 1 của byte đầu) và bit truyền đa nơi
    # (bit 0), đúng như một adapter thật.
    octets[0] = (octets[0] | 0x02) & 0xFE
    return ':'.join('%02X' % octet for octet in octets)


def serial_number(seed, model=''):
    """Số serial của thiết bị."""
    prefix = ''.join(c for c in str(model).upper() if c.isalnum())[:6] or 'NOVA'
    return prefix + _hex(seed, 'nova.serial', 16).upper()


def user_agent(seed, model='Pixel 8', android='14.0.0', build='155.0.8046.0'):
    """User agent của profile: model, hệ điều hành và bản Chrome."""
    major = str(build).split('.')[0]
    return ('Mozilla/5.0 (Linux; Android %s; %s) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/%s.0.0.0 Mobile Safari/537.36'
            % (android, model, major))


# ------------------------------------------------------------------- engine
def build_script(values):
    """Script JS gắn vào trang.

    `values` là dữ liệu đã băm sẵn: engine không tự sinh gì trong trình
    duyệt, nên hai lần chạy cùng seed cho ra cùng kết quả.
    """
    payload = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
    return r'''(() => {
if (window.__novaEngine) return;
window.__novaEngine = true;
const V = PAYLOAD;

/* ---- nhiễu ----
Nhiễu là hàm của seed, của vị trí pixel và của giá trị pixel, nên:
- cùng hình vẽ, cùng profile: cùng nhiễu (ổn định);
- khác hình vẽ: khác nhiễu (bám nội dung);
- khác profile: khác nhiễu (không trùng);
- cùng hình vẽ ở hai canvas khác nhau: cùng nhiễu (đường đọc khớp).
Seed là CHUỖI HEX 128 bit, nên hàm trộn dùng bốn vế 32 bit của nó,
vì phép toán số nguyên của JavaScript chỉ giữ 32 bit. */
const seedOf = (hex) => {
  const words = [];
  for (let i = 0; i < hex.length; i += 8) words.push(parseInt(hex.substr(i, 8), 16) >>> 0);
  return words;
};

const mix = (seed, index, value) => {
  let h = (seed[0] ^ Math.imul(index + 1, 2654435761) ^ Math.imul(value + 1, 2246822519)) >>> 0;
  h ^= h >>> 15; h = Math.imul(h, 2246822519) >>> 0;
  h ^= h >>> 13; h = Math.imul(h, 2246822519) >>> 0;
  h ^= h >>> 16;
  let g = (seed[1] ^ Math.imul(index + 1, 40503) ^ Math.imul(value + 1, 2246822519)) >>> 0;
  g ^= g >>> 15; g = Math.imul(g, 2246822519) >>> 0;
  g ^= g >>> 13;
  const s2 = seed.length > 2 ? seed[2] : 0;
  const s3 = seed.length > 3 ? seed[3] : 0;
  let k = (s2 ^ Math.imul(index + 1, 2246822519) ^ Math.imul(value + 1, 3266489917)) >>> 0;
  k ^= k >>> 15; k = Math.imul(k, 3266489917) >>> 0;
  k ^= k >>> 13;
  let m = (s3 ^ Math.imul(index + 1, 3266489917) ^ Math.imul(value + 1, 668265263)) >>> 0;
  m ^= m >>> 15; m = Math.imul(m, 668265263) >>> 0;
  m ^= m >>> 13;
  return (h ^ g ^ k ^ m) >>> 0;
};

const perturb = (data, seed, amplitude) => {
  for (let i = 0; i < data.length; i += 4) {
    const h = mix(seed, i >> 2, data[i] + data[i + 1] * 3 + data[i + 2] * 7);
    const shift = (h % (amplitude * 2 + 1)) - amplitude;
    data[i] = (data[i] + shift) & 255;
    data[i + 1] = (data[i + 1] + ((h >>> 8) % (amplitude * 2 + 1)) - amplitude) & 255;
    data[i + 2] = (data[i + 2] + ((h >>> 16) % (amplitude * 2 + 1)) - amplitude) & 255;
  }
  return data;
};
/* ---- driver ----
Tên GPU thật có hữu hạn, nên nó không phải nguồn phân biệt: việc đó
thuộc về catalog thiết bị. Ở đây renderer giữ đúng dạng Android thật
báo, để trang không thấy một chuỗi lạ. */
const gpuRenderer = () => V.gpu.renderer;
/* ---- canvas ----
`getImageData` trả về bản sao, nên nhiễu áp vào bản sao là trạng
thái của trang không đổi. */
const ORIGINAL_READ = new WeakMap();
const CANVAS_SEED = seedOf(V.canvas);
const AUDIO_SEED = seedOf(V.audio);
const WEBGL_SEED = seedOf(V.webgl);
try {
  const origGet = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const ctx = origGet.call(this, type, ...rest);
    try {
      if (type !== '2d' || !ctx || ORIGINAL_READ.has(ctx)) return ctx;
      const origRead = ctx.getImageData;
      ORIGINAL_READ.set(ctx, origRead);
      ctx.getImageData = function (...a) {
        const out = origRead.apply(this, a);
        try { perturb(out.data, CANVAS_SEED, V.canvasAmplitude); } catch (e) {}
        return out;
      };
    } catch (e) {}
    return ctx;
  };
} catch (e) {}

/* `toDataURL` mã hoá bitmap, nên nó phải mã hoá một canvas PHỤ đã nhiễu.
Canvas của trang không bị ghi ngược, nên gọi hai lần cho hai kết quả
bằng nhau, và kết quả khớp với đường đọc `getImageData`. */
try {
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (...a) {
    try {
      const ctx = this.getContext('2d');
      const origRead = ctx && ORIGINAL_READ.get(ctx);
      if (ctx && origRead && this.width && this.height) {
        const raw = origRead.call(ctx, 0, 0, this.width, this.height);
        const copy = new Uint8ClampedArray(raw.data.length);
        copy.set(raw.data);
        perturb(copy, CANVAS_SEED, V.canvasAmplitude);
        const side = document.createElement('canvas');
        side.width = this.width; side.height = this.height;
        side.getContext('2d').putImageData(new ImageData(copy, this.width, this.height), 0, 0);
        return origToDataURL.call(side, ...a);
      }
    } catch (e) {}
    return origToDataURL.apply(this, a);
  };
} catch (e) {}

/* ---- webgl ----
Trang đọc GPU qua `WEBGL_debug_renderer_info`, nên renderer phải là
GPU của profile, không phải GPU của máy chủ. */
try {
  const wrapGL = (proto) => {
    if (!proto) return;
    const origGet = proto.getParameter;
    if (typeof origGet === 'function') {
      proto.getParameter = function (p) {
        // 0x9245 và 0x9246 là UNMASKED_VENDOR_WEBGL và UNMASKED_RENDERER_WEBGL.
        if (p === 0x9245) return V.gpu.vendor;
        if (p === 0x9246) return gpuRenderer();
        return origGet.call(this, p);
      };
    }
    const origRead = proto.readPixels;
    if (typeof origRead === 'function') {
      proto.readPixels = function (...a) {
        const out = origRead.apply(this, a);
        try {
          const view = a[6] || a[7];
          if (view && view.length) perturb(view, WEBGL_SEED, V.webglAmplitude);
        } catch (e) {}
        return out;
      };
    }
  };
  wrapGL(WebGLRenderingContext.prototype);
  wrapGL(WebGL2RenderingContext.prototype);
} catch (e) {}

/* ---- audio ----
`startRendering` nằm trên `OfflineAudioContext`, và `BaseAudioContext`
không có nó, nên cả hai đều phải thử. */
try {
  const patch = (ctor) => {
    if (!ctor || !ctor.prototype) return;
    const orig = ctor.prototype.startRendering;
    if (typeof orig !== 'function') return;
    ctor.prototype.startRendering = function () {
      const promise = orig.call(this);
      if (!promise || !promise.then) return promise;
      return promise.then(buffer => {
        try {
          for (let c = 0; c < buffer.numberOfChannels; c++) {
            const data = buffer.getChannelData(c);
            for (let i = 0; i < data.length; i++) {
              const h = mix(AUDIO_SEED, i, 0);
              data[i] = data[i] + ((h % 2001) / 1000000.0 - 0.001);
            }
          }
        } catch (e) {}
        return buffer;
      });
    };
  };
  patch(window.OfflineAudioContext);
  patch(window.BaseAudioContext);
  patch(window.webkitOfflineAudioContext);
} catch (e) {}

/* ---- fonts ----
`font` là dạng shorthand: kích thước trước, tên phông sau. */
try {
  const origMeasure = CanvasRenderingContext2D.prototype.measureText;
  CanvasRenderingContext2D.prototype.measureText = function (text) {
    const out = origMeasure.call(this, text);
    try {
      const shorthand = String(this.font || '');
      const match = shorthand.match(/([\d.]+)px\s+(.+)$/);
      if (!match) return out;
      const family = match[2].split(',')[0].trim().replace(/^["']|["']$/g, '');
      const jitter = V.fonts.jitter[family];
      if (jitter === undefined) return out;
      Object.defineProperty(out, 'width', {value: out.width * jitter, configurable: true});
    } catch (e) {}
    return out;
  };
} catch (e) {}

/* `document.fonts.check` báo phông nào có trong máy. */
try {
  const origCheck = FontFaceSet.prototype.check;
  FontFaceSet.prototype.check = function (font, text) {
    try {
      const match = String(font || '').match(/["']?([^"']+?)["']?(?:\s*,|\s*$)/);
      const family = match ? match[1] : '';
      if (family && Object.prototype.hasOwnProperty.call(V.fonts.jitter, family)) return true;
    } catch (e) {}
    return origCheck.apply(this, arguments);
  };
} catch (e) {}

/* ---- media devices ---- */
try {
  const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
  navigator.mediaDevices.enumerateDevices = async function () {
    const list = await origEnum();
    return list.map(d => {
      if (d.kind === 'audioinput') return Object.assign({}, d, {deviceId: V.devices.audioinput, groupId: V.devices.audioinput, label: 'default'});
      if (d.kind === 'audiooutput') return Object.assign({}, d, {deviceId: V.devices.audiooutput, groupId: V.devices.audiooutput, label: 'default'});
      if (d.kind === 'videoinput') return Object.assign({}, d, {deviceId: V.devices.videoinput, groupId: V.devices.videoinput, label: 'default'});
      return d;
    });
  };
} catch (e) {}

/* ---- client rects ---- */
try {
  const origRect = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function () {
    const r = origRect.call(this);
    try {
      const out = Object.assign({}, r);
      out.x = r.x + V.rects.x; out.y = r.y + V.rects.y;
      out.left = r.left + V.rects.x; out.top = r.top + V.rects.y;
      out.right = r.right + V.rects.x; out.bottom = r.bottom + V.rects.y;
      out.toJSON = r.toJSON;
      return out;
    } catch (e) { return r; }
  };
} catch (e) {}

/* ---- storage ----
`navigator.storage.estimate()` trả về quota và usage. */
try {
  const origEstimate = navigator.storage.estimate.bind(navigator.storage);
  navigator.storage.estimate = async function () {
    const out = await origEstimate();
    return Object.assign({}, out, {quota: V.storage.quota, usage: V.storage.usage});
  };
} catch (e) {}

/* ---- battery ---- */
try {
  const origGetBattery = navigator.getBattery.bind(navigator);
  navigator.getBattery = async function () {
    const battery = await origGetBattery();
    try {
      Object.defineProperty(battery, 'level', {value: V.battery.level, configurable: true});
      Object.defineProperty(battery, 'charging', {value: V.battery.charging, configurable: true});
      Object.defineProperty(battery, 'chargingTime', {value: V.battery.charging ? V.battery.chargingTime : 0, configurable: true});
      Object.defineProperty(battery, 'dischargingTime', {value: V.battery.charging ? 0 : V.battery.dischargingTime, configurable: true});
    } catch (e) {}
    return battery;
  };
} catch (e) {}

/* ---- connection ---- */
try {
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn) {
    Object.defineProperty(conn, 'type', {value: V.connection.type, configurable: true});
    Object.defineProperty(conn, 'effectiveType', {value: V.connection.effectiveType, configurable: true});
    Object.defineProperty(conn, 'rtt', {value: V.connection.rtt, configurable: true});
    Object.defineProperty(conn, 'downlink', {value: V.connection.downlink, configurable: true});
    Object.defineProperty(conn, 'saveData', {value: V.connection.saveData, configurable: true});
  }
} catch (e) {}

/* ---- plugins ----
Danh sách plugin có hữu hạn, nên nó không phải nguồn phân biệt: mỗi
plugin mang mô tả riêng lấy từ seed, và trang đọc cả mô tả đó. */
try {
  const makePlugin = (p) => {
    const plugin = {name: p.name, filename: p.filename, description: p.description, length: 1};
    plugin[0] = {type: 'application/pdf', suffixes: 'pdf', description: p.description};
    plugin.item = (i) => plugin[i];
    plugin.namedItem = (n) => (n === p.name ? plugin : null);
    return plugin;
  };
  const pluginArray = V.plugins.map(makePlugin);
  pluginArray.item = (i) => pluginArray[i];
  pluginArray.namedItem = (n) => pluginArray.find(p => p.name === n) || null;
  pluginArray.refresh = () => {};
  Object.defineProperty(navigator, 'plugins', {value: pluginArray, configurable: true});
} catch (e) {}

/* ---- voices ---- */
try {
  const voiceList = V.voices.map(v => ({
    voiceURI: v.uri, name: v.name, lang: v.lang, localService: v.localService,
    default: v.default, voice: null
  }));
  speechSynthesis.getVoices = () => voiceList;
} catch (e) {}

/* ---- uuid ----
`crypto.randomUUID` phải cho UUID của profile, không phải UUID ngẫu
nhiên, nếu không trang đo hai lần thấy hai giá trị khác nhau. */
try {
  const origRandomUUID = crypto.randomUUID.bind(crypto);
  crypto.randomUUID = () => V.uuid;
} catch (e) {}

/* ---- mac ----
WebRTC đọc địa chỉ MAC qua `RTCPeerConnection`. Không patch được ở đây
một cách an toàn, nên địa chỉ MAC do lớp ngoài ghi vào config. */
})()'''.replace('PAYLOAD', payload)


def gpu_values(seed, device=None):
    """Vendor và renderer của GPU.

    Tên GPU thật có hữu hạn, nên nó không thể là nguồn phân biệt ở quy
    mô triệu profile: việc đó thuộc về catalog thiết bị, nơi mỗi hàng
    là một cấu hình phần cứng thật (xem `device_catalog.py`).

    Ở đây chỉ giữ đúng dạng mà Android thật báo, để không lộ ra:
    `ANGLE (Qualcomm, Adreno (TM) 750, OpenGL ES 3.2)`.
    """
    device = device or {}
    vendor = device.get('gpu_vendor') or 'Qualcomm'
    renderer = device.get('gpu_renderer') or 'Adreno (TM) 750'
    return {
        'vendor': vendor,
        'renderer': 'ANGLE (%s, %s, OpenGL ES 3.2)' % (vendor, renderer),
    }


def payload_device(payload):
    """Hàng của catalog mà một profile dùng, đọc từ payload của profile.

    Payload của profile mang GPU trong `config.fingerprint.webgl2`, tên
    máy trong `config.fingerprint.navigator.device` và hệ điều hành trong
    `navigator.osVersion`. Engine lấy GPU từ đó, để trang không đọc
    thấy GPU của máy chủ.
    """
    fingerprint = (payload or {}).get('config', {}).get('fingerprint', {}) or {}
    navigator = fingerprint.get('navigator') or {}
    webgl = fingerprint.get('webgl2') or {}
    android = str(navigator.get('osVersion') or '14.0.0')
    return {
        'gpu_vendor': webgl.get('unmaskedVendor') or 'Qualcomm',
        'gpu_renderer': webgl.get('unmaskedRenderer') or 'Adreno (TM) 750',
        'model': navigator.get('device') or 'Pixel 8',
        'android': android,
        'build': navigator.get('chromeVersion') or '155.0.8046.0',
    }


def payload_script(payload):
    """Script engine của một profile, dựng từ seed và hàng catalog.

    `payload` là bản ghi của profile: nó mang seed và mang hàng thiết bị
    đã vẽ. Hai profile khác seed cho hai script khác nhau.
    """
    seed = str((payload or {}).get('seed') or '')
    return build_script(engine_values(seed, payload_device(payload)))


def engine_values(seed, device=None):
    """Toàn bộ giá trị engine đưa vào trang, đã băm sẵn từ seed.

    `device` là hàng của catalog: engine dùng GPU của hàng đó, để trang
    không đọc thấy GPU của máy chủ.
    """
    names, jitter = font_jitter(seed)
    device = device or {}
    renderer = device.get('gpu_renderer') or 'Adreno (TM) 750'
    vendor = device.get('gpu_vendor') or 'Qualcomm'
    model = device.get('model') or 'Pixel 8'
    android = str(device.get('android') or '14.0.0')
    build = device.get('build') or '155.0.8046.0'
    gpu = gpu_values(seed, device)
    return {
        # Biểu diễn số của seed để JS dùng trong phép trộn 32 bit.
        'canvas': _hex(seed, 'nova.canvas', 32),
        'audio': _hex(seed, 'nova.audio', 32),
        'webgl': _hex(seed, 'nova.webgl', 32),
        'canvasAmplitude': 1,
        'webglAmplitude': 1,
        'fonts': {'names': names, 'jitter': jitter},
        'devices': device_ids(seed),
        'rects': client_rects(seed),
        'gpu': gpu,
        'storage': storage(seed),
        'battery': battery(seed),
        'connection': connection(seed),
        'plugins': plugins(seed),
        'voices': voices(seed),
        'uuid': uuid_value(seed),
        'mac': mac_address(seed),
        'serial': serial_number(seed, model),
        'ua': user_agent(seed, model, android, build),
    }


def self_check(count=2000):
    """Kiểm chứng bằng số học: `count` seed không cặp nào trùng trục nào.

    Chạy: python nova_engine.py --self-check
    """
    seen = {axis: set() for axis in AXES}
    for index in range(count):
        seed = 'seed-%d' % index
        values = engine_values(seed)
        axes = {
            'canvas': str(values['canvas']),
            'audio': str(values['audio']),
            'webgl': str(values['webgl']),
            'fonts': json.dumps(values['fonts'], sort_keys=True),
            'devices': json.dumps(values['devices'], sort_keys=True),
            'rects': json.dumps(values['rects'], sort_keys=True),
            'storage': json.dumps(values['storage'], sort_keys=True),
            'battery': json.dumps(values['battery'], sort_keys=True),
            'connection': json.dumps(values['connection'], sort_keys=True),
            'plugins': json.dumps(values['plugins'], sort_keys=True),
            'voices': json.dumps(values['voices'], sort_keys=True),
            'uuid': values['uuid'],
            'mac': values['mac'],
            'serial': values['serial'],
        }
        for axis, value in axes.items():
            if value in seen[axis]:
                raise AssertionError('Trung %s tai seed %d' % (axis, index))
            seen[axis].add(value)
    print('OK: %d seed, %d truc, khong co cap trung nao.' % (count, len(AXES)))
    for axis in AXES:
        print('  %-10s %d gia tri khac nhau  (%d bit)' % (axis, len(seen[axis]), AXIS_BITS[axis]))


def main():
    parser = argparse.ArgumentParser(description='Engine fingerprint NOVA')
    parser.add_argument('--seed', default='')
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    seed = args.seed or secrets.token_hex(16)
    print(json.dumps(engine_values(seed), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
