"""Adapter for Android profile configuration using the locally installed core.

Only fields verified by the isolated probe are mapped. Canvas, audio, TLS,
WebGPU and proxy credentials are deliberately reported as unverified.
"""
from pathlib import Path
import json
import math
import os
import re
import subprocess
import uuid

from hidemium_native_config import encode_config


def _language_list(value):
    """The language list without the quality factors of the header.

    The core copies the value of `hidemium.languages` into
    `navigator.languages` as it is, so the quality factors of the
    `Accept-Language` header would show up as the name of a language
    (`vi;q=0.9`), which no Chrome reports.
    """
    parts = []
    for part in str(value or '').split(','):
        name = part.split(';')[0].strip()
        if name and name not in parts:
            parts.append(name)
    return ','.join(parts)

APP_DIR = Path(__file__).resolve().parent


def device_window_size(payload):
    screen=payload['config']['fingerprint']['screen']
    return {'width':int(screen['width']),'height':int(screen['height'])}


def discover_core(data_root=None):
    local=Path(os.environ.get('LOCAL_PROFILES_CORE',APP_DIR/'runtime/hidemium-core/chrome.exe'))
    if data_root is None and local.is_file():
        versions=[p.parent.name for p in local.parent.glob('*/chrome.dll') if re.fullmatch(r'\d+\.\d+\.\d+\.\d+',p.parent.name)]
        if len(versions)==1:return {'executable':str(local.resolve()),'version':versions[0]}
    if data_root is None:
        cfg = Path.home()/'AppData/Roaming/Hidemium_4/data_path.cfg'
        data_root = Path(cfg.read_text(encoding='utf8').strip())
    candidates = []
    for path in (Path(data_root)/'browser').rglob('chrome.exe'):
        versions = [p.parent.name for p in path.parent.glob('*/chrome.dll')
                    if re.fullmatch(r'\d+\.\d+\.\d+\.\d+',p.parent.name)]
        if len(versions)==1:
            candidates.append((tuple(map(int,versions[0].split('.'))),path,versions[0]))
    if not candidates:
        raise FileNotFoundError('Khong tim thay browser core Hidemium da cai')
    _,exe,version = max(candidates,key=lambda x:x[0])
    return {'executable':str(exe),'version':version}


def _number(value, name, low, high, integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f'{name}: expected finite number')
    if not low<=value<=high or (integer and int(value)!=value):
        raise ValueError(f'{name}: outside supported range')
    return int(value) if integer else value


def compile_android(payload, engine_version):
    config = payload['config']
    if config.get('os')!='android' or config.get('browser','chrome')!='chrome':
        raise ValueError('Adapter currently supports Android Chrome profiles only')
    fp = config['fingerprint']
    nav = fp['navigator']
    ua = nav['userAgent']
    android=re.search(r'Android\s+([0-9.]+)',ua)
    if not android or android.group(1).split('.')[0]!=str(nav['osVersion']).split('.')[0]:
        raise ValueError('Phien ban Android trong UA phai khop osVersion')
    if not isinstance(nav['device'],str) or nav['device'] not in ua:
        raise ValueError('Model thiet bi trong UA phai khop model da chon')
    match = re.search(r'Chrome/(\d+)\.',ua)
    if not match:
        raise ValueError('User-Agent must carry a Chrome version')
    # The major of the UA does not have to be the major of the core: the
    # genuine Hidemium app writes the version of the browser it simulates,
    # and the core reports it verbatim in the UA and in the UA-CH brands.
    # Measured against the core: `Chrome/143.0.0.0` with the build
    # `143.0.7499.4` makes the page report `Chromium 143` and the frozen UA,
    # so a profile can announce any Chrome that exists.
    #
    # The build of the profile is the one it announces. The UA of a Chrome on
    # Android is frozen at `MAJOR.0.0.0`, so the build cannot be read from
    # it: the profile keeps it in `navigator.chromeVersion`. Measured
    # against the core: `version_useragent` is the only key the core reads
    # for the UA-CH brands and for the high entropy version, so it holds the
    # full build, like the 48 genuine Hidemium profiles do (`149.0.7827.3`).
    build = str(nav.get('chromeVersion') or '').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', build):
        build = engine_version
    elif build.split('.')[0] != match.group(1):
        raise ValueError('Build cua Chrome phai cung major voi user agent')
    screen = fp['screen']
    webgl = fp.get('webgl2',{})
    native = {
        'hidemium.navigator.useragent.useragent':ua,
        'hidemium.navigator.useragent.platforms':nav.get('platform','Linux armv81'),
        'hidemium.navigator.device_memory':_number(nav['deviceMemory'],'deviceMemory',1,256,True),
        'hidemium.navigator.hardware_concurrency':_number(nav['hardwareConcurrency'],'hardwareConcurrency',1,256,True),
        'hidemium.navigator.max_touch_point':_number(nav['maxTouchPoints'],'maxTouchPoints',0,64,True),
        'hidemium.navigator.screen.width':_number(screen['width'],'screen.width',1,16384,True),
        'hidemium.navigator.screen.height':_number(screen['height'],'screen.height',1,16384,True),
        'hidemium.navigator.screen.avail_width':screen['width'],
        'hidemium.navigator.screen.avail_height':screen['height'],
        'hidemium.navigator.screen.color_depth':24,
        'hidemium.navigator.screen.pixcel_depth':24,
        'hidemium.navigator.pixel_ratio':_number(screen['pixelRatio'],'pixelRatio',0.1,10),
        'hidemium.navigator.is_mobile':bool(nav.get('mobile',True)),
        'hidemium.navigator.os.platform_os':'Android',
        'hidemium.navigator.os.platforms_version':str(nav['osVersion']),
        'hidemium.navigator.useragent.model':nav['device'],
        'hidemium.navigator.useragent.fullversion':build,
        'hidemium.languages':_language_list(fp['languages']['acceptLanguage']),
    }
    timezone = fp.get('timezone',{})
    if timezone.get('isProxy') or timezone.get('base')=='Fill IP':
        raise ValueError('Timezone from proxy IP is not implemented; supply an explicit timezone')
    if timezone.get('nameTimezone') or timezone.get('value'):
        native['hidemium.timezone']=timezone.get('nameTimezone') or timezone['value']
    for field,key in [('unmaskedVendor','hidemium.webgl.vendor'),('unmaskedRenderer','hidemium.webgl.renderer')]:
        if webgl.get(field):
            native[key]=webgl[field]
    # Validate all serialized strings before allocating or writing a profile directory.
    encode_config(native)
    mapped = {'navigator','screen','languages','timezone','webgl2'}
    unverified = sorted(set(fp)-mapped)
    unverified += ['webgl2.'+k for k in sorted(set(webgl)-{'unmaskedVendor','unmaskedRenderer'})]
    unverified += ['navigator.'+k for k in sorted(set(nav)-{
        'userAgent','platform','deviceMemory','hardwareConcurrency','maxTouchPoints','osVersion','device',
        'chromeVersion','mobile'})]
    return {'native_config':native,'unverified_fields':unverified,
            'limitations':['WebGL metadata verified; actual Android GPU rendering is not verified.',
                           'Canvas/audio/TLS/WebGPU fingerprint behavior is not verified.',
                           'UA-CH architecture still reports x86 in the payload probe; Android parity is incomplete.',
                           'This runs a Windows browser core; it is not an Android OS emulator.']}


def prepare_profile(payload, storage_root=None, core=None):
    core = core or discover_core()
    compiled = compile_android(payload,core['version'])
    # Authentication must not be silently dropped, as happened in the legacy launcher.
    proxy = payload.get('proxy') or {}
    if proxy.get('user') or proxy.get('pass') or proxy.get('username') or proxy.get('password'):
        raise ValueError('Authenticated proxy support has not been verified for this adapter')
    proxy_arg = None
    if proxy.get('type') not in (None,'','Without proxy','none'):
        scheme = str(proxy['type']).lower()
        host = proxy.get('ip','')
        port = proxy.get('port',0)
        if isinstance(port,str) and port.isdecimal():
            port=int(port)
        if scheme not in ('http','socks4','socks5') or not re.fullmatch(r'[A-Za-z0-9.-]+',host):
            raise ValueError('Unsupported proxy address or type')
        _number(port,'proxy.port',1,65535,True)
        proxy_arg=f'--proxy-server={scheme}://{host}:{port}'
    extensions = payload.get('extensions') or []
    extension_paths = []
    for extension in extensions:
        if not isinstance(extension,str):
            raise ValueError('Extension IDs require resolution; supply unpacked extension directories')
        folder=Path(extension).resolve()
        if ',' in str(folder) or not (folder/'manifest.json').is_file():
            raise ValueError('Invalid unpacked extension directory')
        extension_paths.append(str(folder))
    root = Path(storage_root) if storage_root else APP_DIR/'data/native-user-data'
    profile_dir = root/('native_'+uuid.uuid4().hex)
    profile_dir.mkdir(parents=True,exist_ok=False)
    (profile_dir/'config.hidemium').write_text(encode_config(compiled['native_config']),encoding='utf8')
    args=[core['executable'],'--enable-hidemium-config','--user-data-dir='+str(profile_dir.resolve()),
          '--no-first-run','--no-default-browser-check']
    args.append(proxy_arg or '--no-proxy-server')
    if extension_paths:
        args.append('--load-extension='+','.join(extension_paths))
    unverified = list(compiled['unverified_fields'])
    if proxy_arg:
        unverified.append('proxy runtime behavior')
    if extension_paths:
        unverified.append('extension runtime behavior')
    return {'profile_dir':str(profile_dir.resolve()),'engine':core,
            'command':args,'unverified_fields':unverified,
            'limitations':compiled['limitations']}


def launch_prepared(prepared, startup_url='about:blank'):
    if not isinstance(startup_url,str) or startup_url.startswith('-'):
        raise ValueError('Invalid startup URL')
    return subprocess.Popen([*prepared['command'],startup_url])
