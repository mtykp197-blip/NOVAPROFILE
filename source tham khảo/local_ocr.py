"""Local OCR for the four digit captcha of the promo pages.

The captcha shows four digits in one font with a line through them. The
digits are cut out of the picture and each cut is matched against the ten
templates that are drawn with the same font, so the digits are read on the
machine without a paid service.
"""
import base64
import io

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - the desktop bundles both
    np = None
    Image = ImageDraw = ImageFont = None

DIGITS = '0123456789'
# The size of the template of one digit.
TEMPLATE_SIZE = (32, 48)
# The font that is used for the templates. The captcha uses the same font.
FONT_CANDIDATES = (
    ('C:/Windows/Fonts/times.ttf', 44),
    ('C:/Windows/Fonts/timesbd.ttf', 44),
    ('C:/Windows/Fonts/arial.ttf', 44),
)


def _font():
    for path, size in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def build_templates():
    """The ten templates of the digits, drawn with the same font."""
    if Image is None:
        raise RuntimeError('Pillow is not available')
    font = _font()
    templates = []
    for digit in DIGITS:
        image = Image.new('L', TEMPLATE_SIZE, 255)
        draw = ImageDraw.Draw(image)
        box = draw.textbbox((0, 0), digit, font=font)
        width = box[2] - box[0]
        height = box[3] - box[1]
        draw.text(((TEMPLATE_SIZE[0] - width) / 2 - box[0],
                   (TEMPLATE_SIZE[1] - height) / 2 - box[1]),
                  digit, fill=0, font=font)
        templates.append(_normalize(255 - np.array(image, dtype=np.int16)))
    return templates


# The size every digit is fitted into, so a digit of any size and any
# position is compared with a template of the same shape.
FIT_SIZE = (20, 30)


def _normalize(ink):
    """The ink of a digit, cut to its own bounds and fitted to one size."""
    rows = np.where((ink > 40).any(axis=1))[0]
    columns = np.where((ink > 40).any(axis=0))[0]
    if not len(rows) or not len(columns):
        return np.zeros((FIT_SIZE[1], FIT_SIZE[0]), dtype=np.int16)
    piece = ink[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1]
    target = Image.fromarray(np.uint8(255 - np.clip(piece, 0, 255))).resize(FIT_SIZE, Image.LANCZOS)
    return 255 - np.array(target, dtype=np.int16)


def _load_image(image):
    """The picture as a grey array, from raw bytes or from a data url."""
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    else:
        text = str(image or '')
        if text.startswith('data:'):
            text = text.split(',', 1)[1]
        raw = base64.b64decode(text)
    return Image.open(io.BytesIO(raw)).convert('L')


def _ink(pixels):
    """The darker the pixel, the more ink it has."""
    return 255 - pixels


def _erode(mask):
    """Takes a thin mark away. A pixel stays only when all of its
    neighbours are marked, so a line of one or two pixels goes away and the
    thick strokes of a digit stay."""
    result = mask.copy()
    height, width = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.zeros_like(mask)
            source_y = slice(max(0, dy), height + min(0, dy))
            source_x = slice(max(0, dx), width + min(0, dx))
            target_y = slice(max(0, -dy), height + min(0, -dy))
            target_x = slice(max(0, -dx), width + min(0, -dx))
            shifted[target_y, target_x] = mask[source_y, source_x]
            result &= shifted
    return result


def _dilate(mask):
    """Makes the marks a little fatter again, so the digits keep their
    shape after the thin lines were taken away."""
    result = mask.copy()
    height, width = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.zeros_like(mask)
            source_y = slice(max(0, dy), height + min(0, dy))
            source_x = slice(max(0, dx), width + min(0, dx))
            target_y = slice(max(0, -dy), height + min(0, -dy))
            target_x = slice(max(0, -dx), width + min(0, -dx))
            shifted[target_y, target_x] = mask[source_y, source_x]
            result |= shifted
    return result


def _threshold(ink):
    """The ink value that separates the dark marks from the light ground.

    The digits are dark and the ground is white, so the value is found with
    the Otsu method: the value that splits the ink in two parts with the
    smallest difference between them.
    """
    values = ink[ink > 60]
    if not values.size:
        return 128
    histogram = np.bincount(values.astype(int), minlength=256).astype(float)
    total = histogram.sum()
    sum_all = (np.arange(256) * histogram).sum()
    weight_low = sum_low = 0
    best_variance = -1
    best = 128
    for value in range(256):
        weight_low += histogram[value]
        if not weight_low:
            continue
        weight_high = total - weight_low
        if not weight_high:
            break
        sum_low += value * histogram[value]
        mean_low = sum_low / weight_low
        mean_high = (sum_all - sum_low) / weight_high
        variance = weight_low * weight_high * (mean_low - mean_high) ** 2
        if variance > best_variance:
            best_variance = variance
            best = value
    return best


def _remove_line(ink):
    """Takes the line that goes through the picture away.

    The line is a long, nearly horizontal run of ink, so every row where the
    ink reaches across most of the picture is a row of the line. Those pixels
    are put back as a light grey, so the digits are not joined into one piece.
    """
    mask = ink > 40
    if not mask.any():
        return ink
    height, width = mask.shape
    result = ink.copy()
    for row in range(height):
        line = mask[row]
        if not line.any():
            continue
        # The longest run of ink in this row.
        longest = run = 0
        for value in line:
            run = run + 1 if value else 0
            longest = max(longest, run)
        if longest * 2 > width:
            result[row] = np.minimum(result[row], 30)
    return result


def read_digits(image):
    """The four digits of the picture, or an empty string."""
    if np is None:
        raise RuntimeError('Numpy is not available')
    picture = _load_image(image)
    pixels = np.array(picture, dtype=np.int16)
    ink = _ink(pixels)
    # The value that tells the dark marks from the light ground.
    threshold = _threshold(ink)
    # The picture is cut to the part that has the digits on it.
    row_ink = (ink > threshold).sum(axis=1)
    column_ink = (ink > threshold).sum(axis=0)
    if not row_ink.any() or not column_ink.any():
        return ''
    top = int(np.argmax(row_ink > 0))
    bottom = int(len(row_ink) - np.argmax(row_ink[::-1] > 0))
    left = int(np.argmax(column_ink > 0))
    right = int(len(column_ink) - np.argmax(column_ink[::-1] > 0))
    crop = ink[top:bottom, left:right]
    if crop.size == 0:
        return ''
    # The digits are cut out by the columns that have ink, so a digit that
    # touches its neighbour is still found on its own.
    column_has_ink = (crop > threshold).sum(axis=0)
    groups = []
    start = None
    for index, value in enumerate(column_has_ink):
        if value > 0 and start is None:
            start = index
        elif value == 0 and start is not None:
            groups.append((start, index))
            start = None
    if start is not None:
        groups.append((start, len(column_has_ink)))
    # Two parts that touch are one digit.
    merged = []
    for group in groups:
        if merged and group[0] - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], group[1])
        else:
            merged.append(group)
    if not merged:
        return ''
    templates = build_templates()
    digits = []
    for start, end in merged:
        piece = crop[:, start:end]
        piece_height, piece_width = piece.shape
        if piece_height == 0 or piece_width == 0:
            continue
        # The piece is cut to its own bounds and fitted to the size of the
        # templates, so they can be compared.
        target_pixels = _normalize(piece)
        best = None
        best_score = None
        for digit, template in zip(DIGITS, templates):
            score = float(np.mean(np.abs(template - target_pixels)))
            if best_score is None or score < best_score:
                best_score = score
                best = digit
        if best is not None:
            digits.append(best)
    return ''.join(digits)
