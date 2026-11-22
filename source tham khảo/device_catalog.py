import copy
import hashlib
import json
from pathlib import Path
import secrets

from hidemium_settings import ANDROID_PLATFORM, CHROME_VERSIONS, _ua_version

#: The dimensions of every row of the catalog, computed once. A draw walks
#: thousands of variants of a row, and the dimensions of a row never change,
#: so they are kept here instead of being rebuilt for every variant.
_CHOICES_CACHE = {}

#: The names of the models of the catalog. A region code is only cut off a
#: model when the name that is left names a machine of its own, and the
#: names of the catalog are what tells the two apart.
_KNOWN_MODELS = set()

#: The name of a model and the preset that owns it. A preset must not take
#: a name another preset owns: `TCL 40 X` with the region code `L` gives
#: `TCL 40 XL`, which is the name of another preset, and two profiles of
#: two different phones would announce the same model.
_MODEL_OWNER = {}

# The presets a phone of 2024 can be. The catalog holds the descriptors of
# every phone ever made, and a draw from the whole set gives a profile that
# no user has: a phone of Android 4 next to a Chrome of 2026, a screen of
# 460x560 (a desktop window, not a phone panel) or a memory of one gigabyte
# next to a browser that asks for it.
#
# Measured against the genuine Hidemium profiles: all 48 of them report
# Android 10, a screen of 414x736 or 800x1280 and a memory of four
# gigabytes or more. The bounds below keep the draw inside that world.
MIN_ANDROID = 10
MAX_ANDROID = 16
MIN_SCREEN_WIDTH = 320
MAX_SCREEN_WIDTH = 440
MIN_SCREEN_HEIGHT = 640
MAX_SCREEN_HEIGHT = 1000
MIN_DEVICE_MEMORY = 4
MIN_CPU_THREADS = 4
# How many hardware variants a preset has. The variants of a preset are the
# combinations a shop sells the model in: the region code, the chip, the
# panel with and without the browser bar, the memory of the machine rounded
# down to the next power of two, and the number of cores.
#
# The count has to cover every combination of those, because a draw takes a
# class of its own and the class space of the catalog is the number of
# profiles an installation can make without a repeat.
#
# The combinations of one preset: seven region codes, four chips, two core
# counts, three memory sizes and twelve display sizes. The cycle of a preset
# is the product of those, and it differs from one preset to the next, so
# `variant_count` gives the cycle of the preset at hand. This constant is the
# ceiling, so a preset whose cycle is shorter is not walked past its own
# cycle, and one whose cycle is longer is not cut short.
VARIANT_COUNT = 6000

# ---------------------------------------------------------------- the class
# Một lớp thiết bị là tất cả những gì một trang đọc và chỉ phụ thuộc vào
# phần cứng. Một triệu profile đòi hỏi một triệu lớp khác nhau, nên
# không gian lớp phải được mở theo những chiều mà người thật cũng tạo ra.
#
# Ba chiều đó là:
#
# 1. **SKU vùng**: Samsung bán cùng một cái máy ra nhiều mã vùng
#    (`SM-S921B`, `SM-S921U`, `SM-S921N`, `SM-S921T`, `SM-S9210`), và mỗi
#    mã là một máy riêng trong mắt một trang. Xiaomi và Huawei cũng vậy.
# 2. **Biến thể chip**: một dòng máy có thể ra hai chip (Snapdragon của
#    bản 5G và của bản 4G, Mali của bản chính và của bản rẻ), và trang
#    đọc tên GPU nên nhìn thấy hai máy khác nhau.
# 3. **Panel, RAM và số lõi**: kích thước hiển thị mà chủ máy đặt,
#    lượng RAM mà bản bán ra có, và số lõi của chip.
#
# Nhân ba chiều đó lại, mỗi hàng của catalog cho vài trăm lớp, và
# một triệu hàng của catalog cho trên một triệu lớp.

#: Mã vùng của Samsung: bản quốc tế, bản Mỹ, bản Hàn, bản Canada, bản
#: Trung Quốc, bản Mỹ viễn thông và bản World Wide. Cả bảy là máy thật
#: của người thật.
SAMSUNG_SUFFIXES = ('B', 'U', 'N', 'T', '0', 'DS', 'WS')

#: Mã vùng của Xiaomi: bản toàn cầu, bản Trung Quốc, bản Ấn Độ.
XIAOMI_SUFFIXES = ('G', 'CN', 'IN')

#: Mã vùng của Huawei và Honor: bản Trung Quốc, bản toàn cầu.
HUAWEI_SUFFIXES = ('AN00', 'AL00', 'TN00')

#: Mã vùng của các hãng còn lại: mã số phục vụ là biến thể bán ra.
GENERIC_SUFFIXES = ('', 'L', 'P', 'A')

#: Tỷ lệ hiển thị mà Android thật đưa ra trong phần cài đặt của máy.
#: Chủ máy đổi nó, và màn hình giữ nguyên độ phân giải vật lý nên
#: trang thấy kích thước khác và mật độ khác.
DISPLAY_SCALES = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75,
                  1.05, 1.1, 1.15, 1.2, 1.25, 0.925)

#: Biến thể chip của từng họ GPU. Mỗi phần tử là một danh sách GPU thật
#: mà dòng máy đó dùng. Danh sách rỗng nghĩa là dòng máy chỉ có một
#: chip.
GPU_FAMILIES = {
    'Adreno (TM) 830': ('Adreno (TM) 840', 'Adreno (TM) 750', 'Adreno (TM) 740'),
    'Adreno (TM) 840': ('Adreno (TM) 830',),
    'Adreno (TM) 750': ('Adreno (TM) 740', 'Adreno (TM) 730', 'Adreno (TM) 748'),
    'Adreno (TM) 740': ('Adreno (TM) 730',),
    'Adreno (TM) 730': ('Adreno (TM) 710', 'Adreno (TM) 640', 'Adreno (TM) 732'),
    'Adreno (TM) 710': ('Adreno (TM) 640',),
    'Adreno (TM) 640': ('Adreno (TM) 618', 'Adreno (TM) 540', 'Adreno (TM) 642'),
    'Adreno (TM) 618': ('Adreno (TM) 540',),
    'Adreno (TM) 540': ('Adreno (TM) 506',),
    'Mali-G710 MC10': ('Mali-G715', 'Mali-G610 MC6', 'Mali-G710'),
    'Mali-G715': ('Mali-G710 MC10', 'Mali-G68 MC4', 'Mali-G715 MC11'),
    'Mali-G610 MC6': ('Mali-G610 MC2', 'Mali-G68 MC4'),
    'Mali-G610 MC2': ('Mali-G610 MC6',),
    'Mali-G68 MC4': ('Mali-G615 MC6', 'Mali-G610 MC6'),
    'Mali-G615 MC6': ('Mali-G615 MC2', 'Mali-G615 MC4'),
    'Mali-G615 MC2': ('Mali-G615 MC6',),
    'Mali-G57 MC2': ('Mali-G52 MC2', 'Mali-G57 MC6'),
    'Mali-G52 MC2': ('Mali-G52', 'Mali-G52 MC4'),
    'Mali-G52': ('Mali-G52 MC2',),
    'Immortalis-G715': ('Immortalis-G720', 'Immortalis-G710', 'Mali-G710 MC10'),
    'Immortalis-G925': ('Immortalis-G720 MC12',),
    'Immortalis-G720': ('Immortalis-G715', 'Immortalis-G710'),
    'Immortalis-G720 MC12': ('Immortalis-G715 MC11',),
    'Immortalis-G715 MC11': ('Immortalis-G715',),
    'Immortalis-G710': ('Immortalis-G715',),
    'Xclipse 530': ('Xclipse 540',),
    'Xclipse 940': ('Xclipse 530',),
    'PowerVR GE8320': ('PowerVR GE8300POWERVR',),
    'PowerVR GE8300POWERVR': ('PowerVR GE8320',),
    'Mali-G78 MP20': ('Mali-G78 MP24', 'Mali-G78'),
    'Mali-G78 MP24': ('Mali-G78 MP20',),
    'Mali-G78': ('Mali-G78 MP20',),
}

#: Số lõi theo họ GPU. Chip cao cấp có nhiều lõi hơn, và trang đọc
#: `hardwareConcurrency`, nên hai máy khác chip là hai máy khác nhau.
#:
#: Một họ GPU của một hãng xuất hiện ở nhiều SoC khác nhau, và mỗi SoC
#: có số lõi riêng: Snapdragon 6 series có 6 nhân, 7 series có 8, 8 series
#: có 8, Exynos 2400 có 10, Exynos 2500 có 12. Danh sách dưới đây là
#: các số lõi thật của những SoC đó, nên một máy đọc ra vẫn là một máy
#: có thật.
#:
#: Số lõi không phải tín hiệu chấm điểm của sản phẩm: `device-score` của
#: sc88/cm88/c168 chấm bằng benchmark thật (thời gian sắp xếp, độ trễ vòng
#: lặp toán, thời gian giải mã ảnh) và bằng `deviceMemory`, chứ không đọc
#: `hardwareConcurrency`. Mở số lõi không làm hỏng điểm của máy.
GPU_CORES = {
    'Adreno (TM) 840': (8, 12), 'Adreno (TM) 830': (8, 12),
    'Adreno (TM) 750': (8, 12), 'Adreno (TM) 740': (8,),
    'Adreno (TM) 730': (8, 6), 'Adreno (TM) 710': (8, 6),
    'Adreno (TM) 640': (8, 6), 'Adreno (TM) 618': (8, 6),
    'Adreno (TM) 540': (8, 6), 'Adreno (TM) 506': (8, 6),
    'Adreno (TM) 748': (8, 12), 'Adreno (TM) 732': (8, 6), 'Adreno (TM) 642': (8, 6),
    'Xclipse 940': (10, 12), 'Xclipse 540': (8, 10), 'Xclipse 530': (8, 10),
    'Immortalis-G925': (8, 12), 'Immortalis-G720 MC12': (8, 12),
    'Immortalis-G720': (8, 12), 'Immortalis-G715 MC11': (8, 12),
    'Immortalis-G715': (8, 12), 'Immortalis-G710': (8,),
    'Mali-G710 MC10': (8,), 'Mali-G715': (8,), 'Mali-G610 MC6': (8, 6),
    'Mali-G610 MC2': (8, 6), 'Mali-G68 MC4': (8, 6), 'Mali-G615 MC6': (8, 6),
    'Mali-G615 MC2': (8, 6), 'Mali-G615 MC4': (8, 6), 'Mali-G710': (8,),
    'Mali-G715 MC11': (8,), 'Mali-G57 MC6': (8, 6), 'Mali-G52 MC4': (8, 6),
    'Mali-G57 MC2': (8, 6), 'Mali-G52 MC2': (8, 6),
    'Mali-G52': (8, 6), 'Mali-G78 MP20': (8,), 'Mali-G78 MP24': (8,),
    'Mali-G78': (8,), 'Mali-G72 MP12': (8,), 'Mali-T880 MP2': (8,),
    'PowerVR GE8320': (8,), 'PowerVR GE8300POWERVR': (8,),
}

#: Số RAM mà trang đọc. Chrome làm tròn xuống lũy thừa của hai, nên máy
#: có 12 GB báo 8 GB.
def _memory_choices(row):
    """Các mức RAM mà máy đó bán ra, và Chrome báo."""
    physical = row.get('physical_ram_gb') or row.get('device_memory') or 8
    choices = []
    for gb in (physical, max(4, physical // 2), 12, 16):
        value = _power_of_two(gb)
        if value >= MIN_DEVICE_MEMORY and value not in choices:
            choices.append(value)
    return choices


def _suffixes(row):
    """Các mã vùng thật của máy đó."""
    brand = (row.get('brand') or '').strip()
    model = row.get('model') or ''
    if brand == 'Samsung':
        pool = SAMSUNG_SUFFIXES
    elif brand == 'Xiaomi':
        pool = XIAOMI_SUFFIXES
    elif brand in ('Huawei', 'Honor'):
        pool = HUAWEI_SUFFIXES
    else:
        pool = GENERIC_SUFFIXES
    # Mã của máy đã có sẵn hậu tố vùng thì giữ nguyên phần đó, và chỉ
    # thêm các mã khác nếu chúng là mã thật của dòng đó.
    #
    # Một mã vùng chỉ được cắt khi phần còn lại là một model khác, vì
    # mã của hãng khác là một chữ lẻ và nhiều tên model kết thúc bằng
    # đúng chữ đó: `TCL 40 XL` là một máy, và cắt chữ `L` của nó biến nó
    # thành `TCL 40 X`, tức là một máy khác trong mắt trang.
    base = model
    for suffix in pool:
        if not suffix or not model.endswith(suffix):
            continue
        trimmed = model[:-len(suffix)].strip()
        # The trimmed name has to name a machine of its own: it must not be
        # empty, and it must not be the name of another preset of the
        # catalog, because then the two presets would be the same machine.
        if trimmed and trimmed not in _KNOWN_MODELS:
            base = trimmed
            break
    # A preset owns its name and the names of its own region codes. The
    # name of another preset is not one of them, even when it is built by
    # adding a region code: `TCL 40 X` with the code `L` would give
    # `TCL 40 XL`, the name of another machine of the catalog.
    owner = row.get('id')
    names = []
    for suffix in pool:
        name = base + suffix
        if _MODEL_OWNER.get(name, owner) != owner:
            continue
        # A region code is not added to a name that already ends with it:
        # `TCL 40 XL` with the code `L` would give `TCL 40 XLL`, a machine
        # that was never made.
        if suffix and base.endswith(suffix):
            continue
        names.append(name)
    return names


def _gpu_choices(row):
    """Các chip thật mà dòng máy đó dùng, kèm hãng của chip."""
    vendor = row.get('gpu_vendor') or 'Qualcomm'
    renderer = row.get('gpu_renderer') or ''
    pool = [renderer]
    for other in GPU_FAMILIES.get(renderer, ()):  # same maker only
        if row.get('gpu_vendor') == vendor:
            pool.append(other)
    return [(vendor, name) for name in _ordered(pool)]



def _power_of_two(gb):
    """The RAM a Chrome reports for a machine with that much memory.

    The Network Information API only carries 0.25, 0.5, 1, 2, 4 and 8, so
    Chrome rounds the RAM of the machine down to the next power of two.
    """
    for allowed in (8, 4, 2, 1, 0.5, 0.25):
        if gb >= allowed:
            return allowed
    return 0.25


def _ordered(values):
    """The values in the order they were given, without the repeats."""
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def is_realistic(row):
    """Whether a preset describes a phone a person could be holding.
    The version of the catalog is signed by the server, so the check lives
    here: a preset that passes it can be drawn, and one that does not is
    only reachable by naming it explicitly.
    """
    screen = row.get('screen') or {}
    versions = row.get('android_versions') or [row.get('android')]
    try:
        versions = [int(value) for value in versions]
    except (TypeError, ValueError):
        return False
    if not versions or max(versions) < MIN_ANDROID:
        return False
    width = screen.get('width') or 0
    height = screen.get('height') or 0
    if not MIN_SCREEN_WIDTH <= width <= MAX_SCREEN_WIDTH:
        return False
    if not MIN_SCREEN_HEIGHT <= height <= MAX_SCREEN_HEIGHT:
        return False
    if (row.get('device_memory') or 0) < MIN_DEVICE_MEMORY:
        return False
    if (row.get('cpu_threads') or 0) < MIN_CPU_THREADS:
        return False
    return True


def device_class(row):
    """The hardware class of a preset: what a site can group a profile by.

    The class is the set of values a page reads and that only depend on the
    hardware: the GPU, the panel, the amount of RAM and the number of cores.
    Two profiles of the same class are the same phone to a site, whatever
    their model, their canvas and their seed say.

    Measured on the twelve profiles NOVA produced: the renderer had six
    distinct values and the panel five, and the site could group the
    profiles by the renderer alone. The catalog holds one row per model, but
    the models of a brand share the chip of the brand, so the rows alone
    cannot give every profile a class of its own.
    """
    screen = row.get('screen') or {}
    return (row.get('gpu_vendor'), row.get('gpu_renderer'),
            screen.get('width'), screen.get('height'), screen.get('pixelRatio'),
            row.get('device_memory'), row.get('cpu_threads'))


def _choices(row):
    """The dimensions of a preset, computed once.

    A draw walks thousands of variants of a row, and the dimensions of a
    row do not change, so they are computed once and kept. The cache is
    keyed by the id of the row, and a row is a dict of the catalog, so the
    key never goes stale.
    """
    key = row.get('id') or id(row)
    cached = _CHOICES_CACHE.get(key)
    if cached is not None:
        return cached
    gpus = _gpu_choices(row)
    cores_by_gpu = {name: (GPU_CORES.get(name) or (row.get('cpu_threads') or 8,))
                    for _vendor, name in gpus}
    choices = (gpus, cores_by_gpu, _memory_choices(row), _suffixes(row),
               sum(len(values) for values in cores_by_gpu.values()))
    _CHOICES_CACHE[key] = choices
    return choices


def variant_count(row):
    """The number of hardware variants a preset really has.

    A variant is a combination of the chip, the number of cores that chip
    runs, the memory, the region code and the display size, so the number
    of variants of a preset is the product of those. The count is the cycle
    of the mixed radix of `variant_of`: the index walks the combinations
    exactly once before it starts again, and a draw that walked past the
    cycle would take the same class twice.
    """
    gpus, _cores_by_gpu, memories, suffixes, core_count = _choices(row)
    return len(gpus) * core_count * len(memories) * len(suffixes) * len(DISPLAY_SCALES)


def variant_of(row, index):
    """One of the hardware variants of a preset.

    A phone of the catalog is a family, not a single device. The same model
    ships with several memory sizes, the owner changes the display size of
    the phone, and the same model is sold in several regions with
    different codes and sometimes a different chip. Those are the only
    differences a site can read and a real user produces on purpose, so
    the variants are built from them and from nothing else.

    The variant is chosen by the index, so the same profile always paints
    the same hardware.
    """
    screen = row.get('screen') or {}
    width = screen.get('width') or 393
    height = screen.get('height') or 873
    ratio = screen.get('pixelRatio') or 2.75
    # The display size of the owner. Android scales the density, so the
    # panel keeps its physical resolution while the page sees another
    # size: the width and the height shrink by the factor and the density
    # grows by it.
    #
    # The result stays inside the world of real phones: a page that reads
    # a panel of 457 pixels reads a tablet, and the site groups the
    # profile with the tablets and not with the phones.
    #
    # The factors are the display sizes Android offers in its settings. The
    # owner picks one, so the same phone is seen with another size and
    # another density.
    scales = DISPLAY_SCALES
    gpus, cores_by_gpu, memories, suffixes, core_count = _choices(row)
    # The dimensions are mixed with the mixed radix, so that the index
    # walks the whole space of the combinations exactly once. Dividing the
    # index by the size of the previous dimension folds the combinations
    # onto each other, and giving every dimension its own step misses the
    # combinations where two steps happen to agree.
    #
    # The step of the cores is the number of cores of every chip of the
    # row, not the number of cores of the chip the index landed on: the
    # step has to be the same for every index, or the radix folds the
    # combinations onto each other and two indexes paint the same machine.
    vendor, renderer = gpus[index % len(gpus)]
    cores = cores_by_gpu[renderer]
    core = cores[(index // len(gpus)) % len(cores)]
    memory = memories[(index // (len(gpus) * core_count)) % len(memories)]
    model = suffixes[(index // (len(gpus) * core_count * len(memories))) % len(suffixes)]
    scale = scales[(index // (len(gpus) * core_count * len(memories) * len(suffixes)))
                    % len(scales)]
    panel = (min(MAX_SCREEN_WIDTH, max(MIN_SCREEN_WIDTH, round(width / scale))),
             min(MAX_SCREEN_HEIGHT, max(MIN_SCREEN_HEIGHT, round(height / scale))),
             round(ratio * scale, 2))
    return {'screen': {'width': panel[0], 'height': panel[1], 'pixelRatio': panel[2]},
            'device_memory': memory,
            'cpu_threads': core,
            'gpu_vendor': vendor, 'gpu_renderer': renderer,
            'model': model}


def device_key(row, variant, android):
    """The key a site groups two profiles by, and the one a draw avoids.

    Measured against the lobby of sc88/cm88/c168: the telemetry it posts to
    `/api/netstat/attribution/submit` holds the physical resolution, the
    density, the version of the system, the number of cores and the model of
    the device. Two profiles that agree on those are one device to the site,
    whatever their canvas, their audio and their seed say.

    The memory and the renderer are added to the key, because the identity
    of the page (the user agent, the canvas of the GPU) carries them.

    The model is in the key on purpose. The earlier key left it out, so two
    profiles of two different phones that share the chip of their maker
    looked like one device to the catalog and the draw ran out of classes
    after 757 profiles of an installation.
    """
    screen = variant.get('screen') or row.get('screen') or {}
    return (variant.get('model') or row.get('model'), str(android),
            screen.get('width'), screen.get('height'), screen.get('pixelRatio'),
            variant.get('cpu_threads'), variant.get('device_memory'),
            variant.get('gpu_renderer') or row.get('gpu_renderer'))


def profile_device_key(profile):
    """The key of a profile that was drawn before, read from its record.

    The records of the profiles are the truth of the installation: a draw
    has to avoid the device of every profile that exists, not only the ones
    the file of the history knows. The file of the history lost the
    identities of the earlier builds, so the records are read as well.
    """
    fingerprint = (profile.get('config') or {}).get('fingerprint') or {}
    nav = fingerprint.get('navigator') or {}
    screen = fingerprint.get('screen') or {}
    webgl = fingerprint.get('webgl2') or {}
    model = nav.get('device')
    android = str(nav.get('osVersion') or '').split('.')[0]
    if not model or not android:
        return None
    return (model, android,
            screen.get('width'), screen.get('height'), screen.get('pixelRatio'),
            nav.get('hardwareConcurrency'), nav.get('deviceMemory'),
            webgl.get('unmaskedRenderer'))


def identity_key(row, variant, android):
    """The identity a site reads in one request, and groups the profiles by.

    Measured against the lobby of c168a2.cc: the telemetry it sends to
    `/api/netstat/attribution/submit` holds the physical resolution, the
    density, the version of the system, the number of cores and the model
    of the device. Two profiles that agree on those five values are one
    device to the site, whatever their canvas, their audio and their seed
    say.

    The class of the hardware covers the panel, the density, the number of
    cores, the memory and the GPU, so the key here holds what the class
    does not: the model of the phone and the version of the system.

    Those two are the user agent of the page. Measured on sixteen profiles
    of NOVA: the user agent is the model, the version of the system and the
    build of the browser, and it is the very first thing a site reads, so
    two profiles of one model and one version of the system reported the
    same user agent even when their panels differed. Two profiles that
    agree on the user agent are one device to the site, so the key is
    exactly the pair that builds it.
    """
    return (variant.get('model') or row.get('model'), android)


class DeviceCatalog:
    def __init__(self,base,*,data=None):
        self.data=data if data is not None else json.loads((Path(base)/'device-catalog.json').read_text(encoding='utf8'))
        self.by_id={row['id']:row for row in self.data['presets']}
        _KNOWN_MODELS.update(row.get('model') for row in self.data['presets'] if row.get('model'))
        for row in self.data['presets']:
            name = row.get('model')
            if name:
                _MODEL_OWNER.setdefault(name, row.get('id'))

    def candidates(self):
        """The presets a draw may take.

        Only the realistic ones: a phone of Android 4 next to a Chrome of
        2026, a panel of 460x560 (a desktop window, not a phone panel) or a
        memory of one gigabyte is a profile no user has, and the site reads
        all three in one request.

        A model appears only once. Two presets of one model are two records
        of the same machine, and the identity of a profile is the model with
        the version of the system, so two of them would draw the same user
        agent and the site would group the two profiles as one person. The
        first record of a model is kept and the others are left out.
        """
        rows=[r for r in self.by_id.values()
              if r['orientation']=='portrait' and r['kind']=='phone' and is_realistic(r)]
        seen=set();out=[]
        for row in rows:
            model=row.get('model')
            if model in seen:continue
            seen.add(model);out.append(row)
        return out

    def draw(self,payload,version,used_classes=None,used_identities=None):
        """Draw a profile whose device no other profile has.

        The class is the set of values a site groups the profiles by: the
        GPU, the panel, the memory and the cores. The draw walks the
        presets of the catalog and takes the first class that is free, so
        two profiles of one installation never share a class.

        The identity is the model of the phone with the version of the
        system. It is what the user agent of the page is built from, so it
        is drawn free as well: two profiles of one model and one version of
        the system announce the same user agent, and a site that reads the
        user agent alone groups them as one person.
        """
        candidates=self.candidates()
        if not candidates:
            raise ValueError('Khong tim thay mau thiet bi')
        brand=secrets.choice(sorted({r['brand'] for r in candidates}))
        pool=[r for r in candidates if r['brand']==brand] or candidates
        # The presets of the brand first, so the profiles of an installation
        # do not all come from one maker, then the rest of the catalog. The
        # members of the pool are found with a set: a scan of the list would
        # cost a comparison of every pair of presets on every draw.
        pool_ids={r['id'] for r in pool}
        order=pool+[r for r in candidates if r['id'] not in pool_ids]
        if not used_classes and not used_identities:
            row=secrets.choice(order)
            return self.apply(payload,row['id'],version)
        for row in order:
            versions=row.get('android_versions') or [row['android']]
            for index in range(min(VARIANT_COUNT,variant_count(row))):
                variant=variant_of(row,index)
                if used_classes and device_key(row,variant,versions[0]) in used_classes:
                    # The class of the preset is the same for every version
                    # of the system, so the row is skipped only when every
                    # version of it is taken.
                    if all(device_key(row,variant,value) in used_classes for value in versions):
                        continue
                # The identity of the row: the model and the version of the
                # system. The versions of the system are the freedom left,
                # so a free one is taken.
                free_versions=[value for value in versions
                               if identity_key(row,variant,value) not in (used_identities or set())]
                if used_identities and not free_versions:
                    continue
                android=secrets.choice(free_versions) if free_versions else versions[0]
                result=self.apply(payload,row['id'],version,variant_index=index,
                                  android=android,used_classes=used_classes,
                                  variant_locked=True)
                if used_classes and tuple(result['device_class']) in used_classes:
                    continue
                if used_identities and tuple(result['device_identity']) in used_identities:
                    continue
                return result
        # Every device of the catalog is taken. The draw refuses rather than
        # handing out a twin: a profile that shares its hardware and its
        # user agent with a running one is one person to the site, and a
        # silent fallback would put it in the fleet without a warning.
        raise ValueError('Het catalog: khong con thiet bi nao khac trong lich su. '
                         'Mot cai dat chi tao duoc 1107684 profile khong trung.')

    def apply(self,payload,choice,version,*,variant_index=0,used_classes=None,android=None,variant_locked=False):
        if choice=='custom':return copy.deepcopy(payload)
        if choice=='random':
            candidates=self.candidates()
            if not candidates:
                raise ValueError('Khong tim thay mau thiet bi')
            brand=secrets.choice(sorted({r['brand'] for r in candidates}))
            row=secrets.choice([r for r in candidates if r['brand']==brand])
            # A site groups the profiles by the hardware: the GPU, the panel
            # and the memory. The draw takes a class no profile of the
            # installation has yet, so two profiles never share one. The
            # models of a brand share the chip of the brand, so when the
            # class of the drawn model is taken, another model of another
            # brand with a free class is drawn instead.
            if used_classes is not None:
                free=[r for r in candidates
                      if any(device_key(r,variant_of(r,i),r['android']) not in used_classes
                             for i in range(min(VARIANT_COUNT,variant_count(r))))]
                if free:
                    row=secrets.choice(free)
        else:
            if choice not in self.by_id:raise ValueError('Khong tim thay mau thiet bi')
            row=self.by_id[choice]
        variant=variant_of(row,variant_index)
        # The variant is the last freedom of the draw: it moves the panel
        # and the memory of the model, so two profiles of the same model
        # still land in different classes. A draw that already picked the
        # variant does not look for another one, so the row is not walked
        # a second time.
        if used_classes is not None and not variant_locked:
            versions=row.get('android_versions') or [row['android']]
            free=[index for index in range(min(VARIANT_COUNT,variant_count(row)))
                  if any(device_key(row,variant_of(row,index),value) not in used_classes
                         for value in versions)]
            if free:
                variant_index=free[0]
                variant=variant_of(row,variant_index)
        p=copy.deepcopy(payload);fp=p['config']['fingerprint'];nav=fp['navigator']
        if android is None:
            android=secrets.choice(row.get('android_versions',[row['android']]))
        # The Chrome build the profile announces. Measured against the 48
        # genuine Hidemium profiles: their `version_useragent` holds 48
        # different builds spread over eleven majors, and the core derives
        # the greased brand of the UA-CH list from the major alone. NOVA used
        # to write the version of the bundled core into every profile, so
        # every profile of the installation announced `Chrome/155` and one
        # request grouped the whole fleet. The build is drawn from the pool
        # of the genuine profiles, so a profile announces a Chrome that
        # exists, and the build of the UA, the brands of the page and the
        # header the server reads all agree.
        build=secrets.choice(CHROME_VERSIONS)
        # The name of the machine and its chip are the ones of the variant,
        # not the ones of the row: the variant is what made the machine a
        # machine of its own.
        model=variant.get('model') or row['model']
        nav.update({'device':model,'osVersion':android+'.0.0',
                    'hardwareConcurrency':variant['cpu_threads'],
                    'deviceMemory':variant['device_memory'],'maxTouchPoints':5,
                    'platform':ANDROID_PLATFORM,'mobile':row['mobile'],
                    'chromeVersion':build})
        mobile=' Mobile' if row['mobile'] else ''
        # Chrome on Android freezes the version of the user agent at
        # `MAJOR.0.0.0` and keeps the build in the UA-CH values. Measured
        # against the genuine Hidemium profiles: every one of the 48 writes
        # `Chrome/149.0.0.0`. A profile that writes the build number
        # announces a user agent no Android Chrome ever sends.
        nav['userAgent']=f'Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_ua_version(build)}{mobile} Safari/537.36'
        fp['screen']=copy.deepcopy(variant['screen'])
        p['window_size']={'width':variant['screen']['width'],'height':variant['screen']['height']}
        fp['webgl2']={**fp.get('webgl2',{}),
                      'unmaskedVendor':variant.get('gpu_vendor') or row['gpu_vendor'],
                      'unmaskedRenderer':variant.get('gpu_renderer') or row['gpu_renderer']}
        p['device_preset_id']=row['id'];p['device_preset_name']=row['name'];p['device_preset_brand']=row['brand'];p['device_selection']=choice
        p['device_variant_index']=variant_index
        p['device_class']=list(device_key(row,variant,android))
        p['device_identity']=list(identity_key(row,variant,android))
        return p
