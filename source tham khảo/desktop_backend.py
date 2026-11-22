"""Local desktop backend: isolated profiles, native core, touch, proxy and API."""
import argparse
import ctypes
import copy
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).parent/'build-deps'))
from hidemium_engine import compile_android,discover_core,device_window_size
from hidemium_native_config import encode_config
from proxy_relay import ProxyRelay
from cdp_touch import MobileController
from fingerprint_history import FingerprintHistory
from runtime_probe import probe
from device_catalog import DeviceCatalog, device_key, profile_device_key, variant_of
from catalog_updates import CatalogCache
import proxy_geo
import hidemium_settings
from workspace_features import WorkspaceFeatures
from window_branding import WindowBranding
from input_toolbar import ProfileInputButton
from promo_accounts import PromoAccounts
from security_policy import COMMERCIAL_REQUIRED
from commercial_client import configured_client, LicenseRequired
from public_errors import record_error,scrub_errors

BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
SITE_KEYS=('sc88','cm88','c168','jun88','78win','on68')


def _windows_process_tree():
    """Return PID -> (parent PID, executable name) from a native process snapshot."""
    if os.name!='nt':return {}
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_=[('dwSize',ctypes.c_ulong),('cntUsage',ctypes.c_ulong),('th32ProcessID',ctypes.c_ulong),
                  ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',ctypes.c_ulong),
                  ('cntThreads',ctypes.c_ulong),('th32ParentProcessID',ctypes.c_ulong),
                  ('pcPriClassBase',ctypes.c_long),('dwFlags',ctypes.c_ulong),('szExeFile',ctypes.c_wchar*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[ctypes.c_ulong,ctypes.c_ulong]
    kernel.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
    kernel.Process32FirstW.argtypes=[ctypes.c_void_p,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype=ctypes.c_int
    kernel.Process32NextW.argtypes=[ctypes.c_void_p,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype=ctypes.c_int
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if not handle or handle==ctypes.c_void_p(-1).value:return {}
    result={}
    try:
        entry=PROCESSENTRY32W();entry.dwSize=ctypes.sizeof(entry)
        ok=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            result[int(entry.th32ProcessID)]=(int(entry.th32ParentProcessID),entry.szExeFile)
            ok=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:kernel.CloseHandle(handle)
    return result


def _windows_process_command_line(pid):
    """Read a process command line with limited query rights; return empty on denial."""
    if os.name!='nt':return ''
    class UNICODE_STRING(ctypes.Structure):
        _fields_=[('Length',ctypes.c_ushort),('MaximumLength',ctypes.c_ushort),('Buffer',ctypes.c_void_p)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,0,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:return ''
    try:
        ntdll=ctypes.WinDLL('ntdll')
        query=ntdll.NtQueryInformationProcess
        query.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)]
        query.restype=ctypes.c_long
        needed=ctypes.c_ulong()
        query(handle,60,None,0,ctypes.byref(needed))  # ProcessCommandLineInformation
        if needed.value<ctypes.sizeof(UNICODE_STRING):return ''
        buffer=ctypes.create_string_buffer(needed.value+16)
        status=query(handle,60,buffer,len(buffer),ctypes.byref(needed))
        if status<0:return ''
        value=ctypes.cast(buffer,ctypes.POINTER(UNICODE_STRING)).contents
        if not value.Buffer or not value.Length:return ''
        return ctypes.wstring_at(value.Buffer,value.Length//2)
    except Exception:return ''
    finally:kernel.CloseHandle(handle)


def _owned_core_pids(session,tree=None):
    """Find the profile's browser by its data directory, including an existing singleton."""
    tree=_windows_process_tree() if tree is None else tree
    if not tree:return []
    parent=session['process'].pid;core=Path(session.get('core_executable','')).name.casefold()
    directory=os.path.normcase(os.path.normpath(str(session.get('directory','')))).casefold()
    needle=('--user-data-dir='+directory).replace('"','')
    exact=[]
    for pid,(_ppid,name) in tree.items():
        if name.casefold()!=core:continue
        command=_windows_process_command_line(pid).replace('"','')
        normalized=os.path.normcase(os.path.normpath(command)).casefold()
        if needle in normalized:exact.append(pid)
    if exact:
        exact_set=set(exact)
        roots=[pid for pid in exact if tree.get(pid,(None,''))[0] not in exact_set]
        return roots or exact
    # Fallback for restricted process-query access: follow the launcher's descendants.
    depths={parent:0};frontier=[parent]
    while frontier:
        next_frontier=[]
        for pid,(ppid,_name) in tree.items():
            if ppid in frontier and pid not in depths:
                depths[pid]=depths[ppid]+1;next_frontier.append(pid)
        frontier=next_frontier
    candidates=[]
    for pid,depth in depths.items():
        item=tree.get(pid)
        if item and item[1].casefold()==core:candidates.append((depth,pid))
    candidates.sort()
    return [pid for _depth,pid in candidates]


def _set_session_browser_pid(session,browser_pid):
    if not browser_pid:return
    session['browser_pid']=browser_pid
    touch=session.get('touch')
    if touch:touch.browser_pid=browser_pid
    branding=session.get('branding')
    if branding:branding.pid=browser_pid
    input_button=session.get('input_button')
    if input_button:input_button.browser_pid=browser_pid


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+secrets.token_hex(4)+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    os.replace(temporary,path)


class ProfileManager(WorkspaceFeatures):
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.records=self.root/'profiles';self.records.mkdir(exist_ok=True)
        self.sessions={};self.lock=threading.RLock();self._fingerprint_runtime_verified=False
        self._geo_cache={}
        self.core=discover_core()
        self.template=json.loads((BASE/'examples/android-native-profile.json').read_text(encoding='utf8'))
        self.keys=set(json.loads((BASE/'native-key-catalog.json').read_text(encoding='utf8')))
        self.history=FingerprintHistory(self.root/'fingerprint-history.sqlite3')
        self.template['fingerprint_modes']={'canvas':'noise-brave','audio':'noise'}
        self.template['settings']=hidemium_settings.default_settings()
        self.template['touch_mode']=True
        self.template['device_selection']='random'
        # The address of the machine never shows on a page, and the place of
        # the profile is Vietnam unless the owner asks for the exit of the
        # proxy.
        self.template['webrtc_mode']='proxy_only'
        self.template['geo_mode']='vietnam'
        self.template['defaults_format_version']=2
        self.template['name']='NOVA'
        self.template['config']['name']='NOVA'
        self.template['window_size']=device_window_size(self.template)
        self.template['startup_url']='https://kiemtraip.com'
        self.template['startup_url_version']=1
        self.catalog=DeviceCatalog(BASE)
        self.promo_accounts=PromoAccounts(self.root)
        self.init_workspace()
        self.license=configured_client(BASE,self.root,COMMERCIAL_REQUIRED)
        self._otp_cache=None
        self._otp_cache_lock=threading.Lock()
        self.catalog_cache=CatalogCache(BASE,self.root,self.license.public) if self.license else None
        if self.catalog_cache:self.catalog=DeviceCatalog(BASE,data=self.catalog_cache.data)
        self.license_monitor_stop=threading.Event()
        if self.license:
            threading.Thread(target=self._monitor_license,daemon=True).start()

    def update_device_catalog(self):
        if not self.license:raise LicenseRequired('Commercial license required')
        envelope=self.license.device_catalog()
        with self.lock:
            changed=self.catalog_cache.apply(envelope)
            self.catalog=DeviceCatalog(BASE,data=self.catalog_cache.data)
            return {'changed':changed,'version':self.catalog.data['version'],'presets':len(self.catalog.by_id),'models':len({r['model'] for r in self.catalog.by_id.values()})}

    def require_license(self):
        if self.license:self.license.require()

    def require_owner_registration(self):
        self.require_license()
        if not self.license or self.license.status().get('owner_registration') is not True:
            raise ValueError('Chỉ key admin NOVA mới được xem và sửa link website.')

    def require_owner_registration_local(self):
        """The owner check without a round trip to the server.

        The claims of the signed lease are already in memory, so the check
        never asks the server and never waits for the lock of the license
        client. That lock is held by the OTP and the captcha calls while
        they talk to the server, so a check that waits for it holds the app
        for as long as the solver takes.
        """
        if not self.license or self.license.status().get('owner_registration') is not True:
            raise ValueError('Chỉ key admin NOVA mới được xem và sửa link website.')

    def otp_active_cached(self, ttl=2.0):
        """The list of the codes, kept for a moment.

        Many tabs of the same machine ask for the codes at the same time and
        they all want the same list. One call to the server is enough for all
        of them, so the machine does not flood the server with one call per
        tab and second.
        """
        now=time.monotonic()
        with self._otp_cache_lock:
            if self._otp_cache and now-self._otp_cache[0]<ttl:
                return self._otp_cache[1]
        result=self.license.otp_request('/v1/otp/active')
        with self._otp_cache_lock:
            self._otp_cache=(time.monotonic(),result)
        return result

    @staticmethod
    def validate_site_links(value):
        if not isinstance(value,dict) or set(value)!=set(SITE_KEYS):
            raise ValueError('Cần nhập đủ link cho SC88, CM88, C168, JUN88, 78WIN và ON68.')
        result={}
        for site in SITE_KEYS:
            raw=value[site]
            if not isinstance(raw,str) or not raw.strip() or len(raw.strip())>500:
                raise ValueError('Link '+site.upper()+' không hợp lệ.')
            link=raw.strip()
            try:parsed=urlsplit(link);host=parsed.hostname;port=parsed.port
            except ValueError:raise ValueError('Link '+site.upper()+' không hợp lệ.') from None
            if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.fragment or (port and port!=443):
                raise ValueError('Link '+site.upper()+' phải là HTTPS hợp lệ, không chứa thông tin đăng nhập hoặc fragment.')
            # A missing part of the link shows up as the word undefined in it,
            # so the link is thrown away instead of being used.
            if 'undefined' in link.lower() or 'null' in link.lower():
                raise ValueError('Link '+site.upper()+' chứa "undefined" nên không hợp lệ.')
            result[site]=link
        return result

    def _monitor_license(self):
        while not self.license_monitor_stop.wait(30):
            if not self.license.token and not self.sessions:continue
            try:self.require_license()
            except Exception:
                # A transient network failure cannot invalidate a still-valid
                # signed lease. New operations still require online approval.
                if self.license.status().get('active'):continue
                # Only processes started and owned by this manager are stopped.
                for pid in list(self.sessions):self.stop(pid)

    def path(self,pid):
        if not re.fullmatch(r'p_[a-f0-9]{24}',pid):raise ValueError('ID profile khong hop le')
        return self.records/(pid+'.json')

    def get(self,pid):
        path=self.path(pid)
        if not path.exists():raise KeyError('Khong tim thay profile')
        payload=json.loads(path.read_text(encoding='utf8'))
        payload['window_size']=device_window_size(payload)
        # The settings of the Hidemium v5 tab must always be complete, also
        # for a profile that was created before the tab existed.
        settings=dict(hidemium_settings.default_settings());settings.update(payload.get('settings') or {})
        payload['settings']=settings
        return payload

    def listing(self):
        out=[]
        for path in self.records.glob('p_*.json'):
            p=self.get(path.stem);p['session']=self.status(p['id'])
            if p.get('proxy',{}).get('pass'):p['proxy']['pass']='••••••'
            out.append(p)
        return sorted(out,key=lambda p:p['created_at'],reverse=True)

    def defaults(self):
        p=self.root/'defaults.json'
        result=json.loads(p.read_text(encoding='utf8')) if p.exists() else copy.deepcopy(self.template)
        if result.get('defaults_format_version',0)<2:
            result['device_selection']='random';result['defaults_format_version']=2
        result['window_size']=device_window_size(result)
        if result.get('startup_url_version',0)<1 and result.get('startup_url') in (None,'','about:blank'):
            result['startup_url']='https://kiemtraip.com'
        result.setdefault('startup_url_version',1)
        if result.get('config',{}).get('name')=='Android core probe - synthetic configuration':
            result['config']['name']='NOVA';result['name']='NOVA'
        result.setdefault('webrtc_mode','proxy_only')
        result.setdefault('geo_mode','vietnam')
        settings=dict(hidemium_settings.default_settings());settings.update(result.get('settings') or {})
        result['settings']=settings
        return result

    def prepare_runtime(self):
        """Own a runtime copy so Chromium's AppContainer can read public binaries.

        The grant is read/execute only and never targets profiles, tokens or cookies.
        Run by the desktop backend as the interactive user, not by a shell installer.
        """
        source=Path(self.core['executable']).parent.resolve()
        engine_root=(self.root/'engine').resolve()
        revision='default'
        if self.license:
            payload=self.license.core_manifest.get('payload','')
            if not isinstance(payload,str) or not payload:raise ValueError('Invalid signed runtime manifest')
            revision=hashlib.sha256(payload.encode('ascii')).hexdigest()[:16]
        target=(engine_root/(self.core['version']+'-'+revision)).resolve()
        if target.parent!=engine_root:raise ValueError('Invalid engine directory')
        engine_root.mkdir(parents=True,exist_ok=True)
        # The browser version can stay the same while a signed runtime is
        # refreshed. Keying the local copy by the signed manifest prevents an
        # older cache from being reused after an app update.
        if self.license and target.exists():
            try:
                self.license.verify_runtime(target)
            except Exception:
                # Repair only this generated engine cache, and only from the
                # bundled runtime after its signed inventory verifies.
                self.license.verify_runtime(source)
                quarantine=target.with_name(target.name+'.invalid-'+secrets.token_hex(4))
                target.rename(quarantine)
                shutil.copytree(source,target)
                self.license.verify_runtime(target)
        elif not target.exists():
            if self.license:self.license.verify_runtime(source)
            shutil.copytree(source,target)
            if self.license:self.license.verify_runtime(target)
        marker=target/'.runtime-ready'
        if not marker.exists():
            permission=subprocess.run(['icacls',str(target),'/grant','*S-1-15-2-1:(OI)(CI)(RX)','*S-1-15-2-2:(OI)(CI)(RX)'],capture_output=True)
            if permission.returncode==0:marker.write_text('public browser binaries: appcontainer read/execute '+revision,encoding='utf8')
            self.runtime_access='ready' if permission.returncode==0 else 'unverified'
        else:self.runtime_access='ready'
        if self.license:self._fingerprint_runtime_verified=True
        self.core={**self.core,'executable':str(target/'chrome.exe')}

    def validate(self,payload):
        result=compile_android(payload,self.core['version'])
        payload['window_size']=device_window_size(payload)
        override=payload.get('native_overrides',{})
        if not isinstance(override,dict) or set(override)-self.keys:raise ValueError('Native config chua duoc nhan dien')
        encode_config(override)
        startup=payload.get('startup_url','about:blank')
        if not isinstance(startup,str) or not (startup=='about:blank' or urlsplit(startup).scheme in ('http','https')):
            raise ValueError('Trang mo dau phai la HTTP/HTTPS hoac about:blank')
        px=payload.get('proxy') or {}
        if px.get('type') not in ('Without proxy',None,'','none'):
            if str(px['type']).lower() not in ('http','socks5'):raise ValueError('Chi ho tro HTTP/SOCKS5')
            if not isinstance(px.get('ip'),str) or not px['ip'].strip():raise ValueError('Thieu dia chi proxy')
            port=int(px.get('port',0))
            if not 1<=port<=65535:raise ValueError('Cong proxy khong hop le')
        for ext in payload.get('extensions',[]):
            if not isinstance(ext,str) or ',' in ext or not (Path(ext)/'manifest.json').is_file():
                raise ValueError('Tien ich phai la thu muc da giai nen, co manifest.json')
        return result

    def random_device(self,payload):
        path=self.root/'device-draw-history.json'
        used=json.loads(path.read_text()) if path.exists() else []
        if not isinstance(used,list):used=[]
        # The history holds the devices the profiles of the installation
        # already have, so the next draw takes a device nobody has yet. A
        # site groups the profiles by the GPU, the panel, the memory, the
        # cores, the model and the version of the system: two profiles of
        # one device are one person to it, whatever their canvas says.
        #
        # The file of the earlier builds held the ids of the drawn presets.
        # Those ids are turned into their device, so the profiles made
        # before are counted too.
        classes={tuple(item) for item in used if isinstance(item,list) and len(item)==8}
        identities={tuple(item) for item in used if isinstance(item,list) and len(item)==2}
        for item in used:
            if isinstance(item,str):
                row=self.catalog.by_id.get(item)
                if row:
                    variant=variant_of(row,0)
                    classes.add(device_key(row,variant,row['android']))
        # Both families are kept in the file. The devices stop the hardware
        # of two profiles from being the same, and the identities stop the
        # user agent of two profiles from being the same. The file of the
        # earlier builds held the devices only, so the identities were lost
        # at the first write and the twelfth profile of an installation
        # already announced the model and the version of the system of the
        # third one.
        used=[list(item) for item in classes]+[list(item) for item in identities]
        # The profiles that exist are the truth of the installation. The
        # file of the history can miss a profile (it was written by another
        # build, or it was deleted by hand), so the records are read as
        # well: a draw must never repeat a device that already runs.
        for record in self.records.glob('p_*.json'):
            try:
                key=profile_device_key(json.loads(record.read_text(encoding='utf8')))
            except (OSError,ValueError):
                continue
            if key:
                classes.add(key)
                identities.add((key[0],key[1]))
        result=self.catalog.draw(payload,self.core['version'],classes,identities)
        klass=result.get('device_class')
        if klass:used.append(list(klass))
        identity=result.get('device_identity')
        if identity:used.append(list(identity))
        atomic(path,used)
        result['device_selection']='random'
        return result

    def redraw_device(self,p):
        """Draw another device for a profile the server refused.

        The server refused the reservation because the profile shares a
        signal family with a profile of another customer: the panel, the
        model, the cores or the canvas. A new seed keeps the hardware, so
        the site would group the two profiles as one device. The device is
        therefore drawn again, and the history of the installation grows so
        the next profile does not take the same class either.

        The draw works on a copy of the profile, so everything the owner
        chose (the name, the group, the proxy, the settings) is kept.
        """
        result=self.random_device(p)
        p.update(result)
        return p

    def create(self,body):
        self.require_license()
        with self.lock:
            p=copy.deepcopy(body.get('payload') or self.defaults())
            selection=body.get('device_selection','random')
            p=self.random_device(p) if selection=='random' else self.catalog.apply(p,selection,self.core['version'])
            nav=p['config']['fingerprint']['navigator']
            # The built-in preset still names the build of the bundled core.
            # Every other profile announces the Chrome it drew, so the fleet
            # of an installation is not one Chrome.
            if nav['userAgent']==self.template['config']['fingerprint']['navigator']['userAgent']:
                nav['userAgent']=nav['userAgent'].replace('155.0.8059.40',self.core['version'])
                nav['chromeVersion']=self.core['version']
            # A Chrome on Android freezes the version of its user agent at
            # `MAJOR.0.0.0` and keeps the build in the UA-CH values. The
            # built-in preset carries the build in the UA itself, which no
            # Android Chrome ever sends, so the preset is frozen here too.
            nav.setdefault('chromeVersion',self.core['version'])
            major=re.search(r'Chrome/(\d+)\.',nav['userAgent'])
            if major:
                nav['userAgent']=re.sub(r'Chrome/\d+\.\d+\.\d+\.\d+',
                                        'Chrome/%s.0.0.0'%major.group(1),nav['userAgent'])
            self.validate(p)
            p['id']='p_'+secrets.token_hex(12);p['name']=str(body.get('name') or p['config'].get('name') or 'Android profile')[:150]
            p['config']['name']=p['name'];p['seed']=secrets.token_hex(16)
            p['created_at']=datetime.now(timezone.utc).isoformat()
            p['touch_mode']=body.get('touch_mode',True)
            self.validate_group(body.get('group_id',p.get('group_id')))
            p['group_id']=body.get('group_id',p.get('group_id')) or None
            p.setdefault('native_overrides',{});p.setdefault('startup_url','about:blank')
            p.setdefault('fingerprint_modes',{'canvas':'noise-brave','audio':'noise'})
            settings=dict(hidemium_settings.default_settings());settings.update(p.get('settings') or {})
            p['settings']=settings
            self.apply_geo(p)
            self.assign_fingerprint(p)
            atomic(self.path(p['id']),p)
            return p

    def update(self,pid,body):
        with self.lock:
            if self.status(pid)['running']:raise ValueError('Dung profile truoc khi sua')
            old=self.get(pid);p=copy.deepcopy(body.get('payload') or old)
            for key in ['id','seed','created_at']:p[key]=old[key]
            # The nudge of the image value belongs to the profile: an edit
            # that drops it would paint the pixels of the seed again, and the
            # profile would take the draw hash of the profile before it.
            p['webgl_image_nudge']=old.get('webgl_image_nudge',0)
            p['name']=str(body.get('name',p.get('name',old['name'])))[:150]
            p['config']['name']=p['name']
            settings=dict(hidemium_settings.default_settings());settings.update(p.get('settings') or {})
            p['settings']=settings
            self.validate(p)
            self.apply_geo(p)
            self.assign_fingerprint(p)
            p['updated_at']=datetime.now(timezone.utc).isoformat();atomic(self.path(pid),p)
            return p

    def rotate(self,pid):
        with self.lock:
            if self.status(pid)['running']:raise ValueError('Dung profile truoc khi doi seed')
            p=self.get(pid);p['seed']=secrets.token_hex(16);self.apply_geo(p);self.assign_fingerprint(p);atomic(self.path(pid),p)
            return p

    def apply_geo(self,p):
        """Resolve the place of the profile and keep it with the profile.

        The place is resolved once, so the fingerprint that is measured and
        the browser that is launched always show the same country.
        """
        mode=p.get('geo_mode','vietnam')
        if mode=='none':p['geo']=None;return None
        geo=None
        if mode=='proxy':
            geo=self.exit_geo(p.get('proxy') or {},p.get('id'))
        if not geo:geo=proxy_geo.random_location(p.get('seed',''))
        p['geo']=geo
        return geo

    def exit_geo(self,px,pid=None):
        """The country and the exit IP of the proxy.

        The exit IP is resolved through the proxy itself, so the answer is
        the address the proxy hands out, never the address of the proxy
        host. A rotating proxy hands out a different address on every
        connection, so the answer is never shared between two profiles: the
        cache is keyed by the profile, not by the proxy. A cache keyed by
        the proxy would give the second profile the address of the first
        one, and the page would show a WebRTC address that never left the
        machine.
        """
        if px.get('type') in ('Without proxy',None,'','none'):return None
        key=pid or str(px.get('ip',''))+':'+str(px.get('port',''))+':'+str(px.get('user',''))
        cached=self._geo_cache.get(key)
        if cached is None:
            relay=None
            try:
                relay=ProxyRelay(px)
                cached=proxy_geo.resolve_exit_geo(relay.url)
            except Exception:
                cached=None
            finally:
                if relay:relay.close()
            if cached:self._geo_cache[key]=cached
        return cached

    def native_config(self,p):
        native={**self.validate(p)['native_config'],**p.get('native_overrides',{})}
        screen=p['config']['fingerprint']['screen']
        # WebRTC never shows the real address of the machine: the mode of the
        # profile decides it, the addresses come from network.hidemium.
        settings=p.get('settings') or {}
        native['hidemium.webrtc.mode']=hidemium_settings.webrtc_core_mode(settings,p.get('webrtc_mode'))
        geo=p.get('geo')
        if geo:
            if geo.get('timezone'):native['hidemium.timezone']=geo['timezone']
            if geo.get('latitude') is not None:
                native['hidemium.geolocation.latitude']=float(geo['latitude'])
                native['hidemium.geolocation.longitude']=float(geo['longitude'])
                native['hidemium.geolocation.accuracy']=100
        nav=p['config']['fingerprint']['navigator']
        if nav.get('platform','Linux armv81') in ('Linux armv8l','Linux armv81'):
            # The core reads the platform string verbatim, and the DevTools
            # override sends the same one, so the page and the worker agree.
            native['hidemium.navigator.useragent.platforms']=hidemium_settings.ANDROID_PLATFORM
        # Native settings provide a baseline; MobileController also applies
        # matching UA metadata through CDP to every page target and worker.
        # Measured against the core: the key `architecture` makes the page
        # report an empty `navigator.userAgentData.architecture`, so it is not
        # written here. The CDP override of the app sends `arm` to every
        # target, page and worker, which is what the page reads.
        native['hidemium.navigator.useragent.bitness']='64'
        # The Hidemium v5 settings of the profile.
        hidemium_settings.apply_native(native,settings,p.get('seed',''),p)
        # The core turns the native screen size into the maximum size of the
        # OS window (WM_GETMINMAXINFO): with 360x800 the window is locked at
        # 376x808 and cannot be dragged bigger. The screen of the page comes
        # from the CDP emulation instead, which also reaches the cross-origin
        # frames when the site isolation is off. So the size keys are removed
        # after the settings are written, and the window stays free.
        #
        # The removal has to come last: the settings write the size of the
        # panel again, and a size left in the config locks the window of the
        # profile at the size of the panel.
        for key in ('width','height','avail_width','avail_height','outer_width','outer_height',
                    'inner_width','inner_height'):
            native.pop('hidemium.navigator.screen.'+key,None)
        if screen.get('pixelRatio'):native['hidemium.navigator.pixel_ratio']=screen['pixelRatio']
        return native

    def measure(self,mobile,directory):
        """Measure the profile, retrying the readings that are known to flake.

        The runtime probe reads the page twice and refuses a reading when a
        field changes between the two. Some fields are volatile: the WebGPU
        adapter can time out, so the same profile measures fine on the next
        run. The retry keeps a flaky reading from failing the whole profile.
        """
        last=None
        for _ in range(3):
            try:
                return probe(self.core['executable'],self.native_config(mobile.payload),directory,
                             mobile.commands())
            except RuntimeError as exc:
                last=exc
                diagnostic=getattr(exc,'safe_diagnostic',None) or {}
                if diagnostic.get('stage')!='javascript':raise
            # The probe creates the directory itself, so the next try needs a
            # fresh one.
            shutil.rmtree(directory,ignore_errors=True)
        raise last

    def assign_fingerprint(self,p):
        self.require_license()
        # The core is fully verified before the first fingerprint measurement
        # in this process. Launch still verifies it again before real use.
        if self.license and not self._fingerprint_runtime_verified:
            self.license.verify_runtime(Path(self.core['executable']).parent)
            self._fingerprint_runtime_verified=True
        # Each candidate is measured in a disposable user-data directory, never in an existing user's profile.
        for attempt in range(8):
            observed=self._measure_candidate(p)
            # The guard against a draw hash that another profile already uses.
            # The site groups the profiles by the pixels of a WebGL drawing,
            # so two profiles with the same draw hash are one person, even
            # when the rest of their fingerprint differs. The profile is
            # nudged in the deep columns of the image value and measured
            # again; the core keeps every digit, so the pixels move.
            for _ in range(3):
                if not self.history.draw_owner(observed,p['id']):break
                p['webgl_image_nudge']=int(p.get('webgl_image_nudge') or 0)+1
                observed=self._measure_candidate(p)
            # Three nudges that still land on a draw hash of another GPU row
            # mean the pixels cannot be separated this way. The whole seed is
            # rolled instead: the canvas and the audio move with it, so the
            # profile is a new device rather than a twin of a running one.
            if self.history.draw_owner(observed,p['id']):
                p['seed']=secrets.token_hex(16)
                p['webgl_image_nudge']=0
                continue
            groups=self.history.fingerprint_groups(observed)
            signature=groups['full'];legacy_signature=self.history.legacy_signature(observed)
            if self.history.would_collide(observed,p['id']):
                self.history.reserve(observed,p['id'])
                p['seed']=secrets.token_hex(16)
                p['webgl_image_nudge']=0
                continue
            if self.license and not self.license.reserve(signature,p['id'],groups,legacy_signature):
                # The server refused: the profile shares a signal family with
                # a profile of another customer. The seed is not enough,
                # because the family that collided is the hardware of the
                # phone: a new seed keeps the same panel, the same model
                # and the same cores, and the site would group the two
                # profiles as one device anyway. The device is drawn again,
                # so the panel, the model and the version of the system are
                # new ones.
                p['seed']=secrets.token_hex(16)
                p['webgl_image_nudge']=0
                self.redraw_device(p)
                continue
            signature=self.history.reserve(observed,p['id'])
            if signature:
                p['fingerprint_signature']=signature;p['fingerprint_observed']=observed
                p['fingerprint_retries']=attempt;p['fingerprint_verified_at']=datetime.now(timezone.utc).isoformat()
                return
            p['seed']=secrets.token_hex(16)
            p['webgl_image_nudge']=0
        raise ValueError('Khong cap duoc fingerprint khac sau 8 lan do; khong tao profile trung')

    def _measure_candidate(self,p):
        """Measure the profile in a disposable directory and throw it away."""
        directory=self.root/'verification'/('v_'+secrets.token_hex(12))
        mobile=object.__new__(MobileController);mobile.payload=p;mobile.version=self.core['version']
        try:
            return self.measure(mobile,directory)
        finally:
            resolved=directory.resolve();allowed=(self.root/'verification').resolve()
            if resolved.parent==allowed and resolved.name.startswith('v_'):
                shutil.rmtree(resolved,ignore_errors=True)

    def status(self,pid):
        session=self.sessions.get(pid)
        if not session:return {'running':False}
        candidates=_owned_core_pids(session)
        browser_pid=session.get('browser_pid')
        if browser_pid in candidates:
            _set_session_browser_pid(session,browser_pid)
        elif candidates:
            _set_session_browser_pid(session,candidates[0])
        elif session['process'].poll() is not None:
            self._clean_session(pid)
            return {'running':False}
        else:
            _set_session_browser_pid(session,session['process'].pid)
        return {'running':True,'pid':session.get('browser_pid',session['process'].pid),
                'touch':session['touch'].state if session.get('touch') else {'ready':False}}

    def _clean_session(self,pid):
        session=self.sessions.pop(pid,None)
        if not session:return
        if session.get('touch'):session['touch'].close()
        if session.get('input_button'):session['input_button'].close()
        if session.get('relay'):session['relay'].close()
        if session.get('branding'):session['branding'].close()
        session['log'].close()

    def set_input_mode(self,pid,mode):
        self.require_license()
        if mode not in ('touch','mouse'):raise ValueError('Che do thao tac khong hop le')
        with self.lock:
            p=self.get(pid)
            p['input_mode']=mode
            # Input preference is not a new fingerprint; preserve seed and history.
            atomic(self.path(pid),p)
            session=self.sessions.get(pid)
            if session and session.get('touch'):session['touch'].set_input_mode(mode)
            return {'input_mode':mode}

    def effective_extensions(self,payload):
        # Defaults are live launch settings for every profile, not a snapshot
        # copied only when the profile was created. Keep private additions too.
        combined=[*self.defaults().get('extensions',[]),*payload.get('extensions',[])]
        selected=[];seen=set()
        for value in combined:
            if not isinstance(value,str) or ',' in value:raise ValueError('Duong dan tien ich khong hop le')
            extension=Path(value).resolve()
            if not (extension/'manifest.json').is_file():raise ValueError('Khong tim thay manifest.json cua tien ich; vui long chon lai thu muc')
            key=os.path.normcase(str(extension))
            if key not in seen:selected.append(str(extension));seen.add(key)
        return selected

    def launch(self,pid,diagnostic=False):
        self.require_license()
        if self.license:self.license.verify_runtime(Path(self.core['executable']).parent)
        with self.lock:
            if self.status(pid)['running']:raise ValueError('Profile dang chay')
            p=self.get(pid);compiled=self.validate(p)
            extensions=self.effective_extensions(p)
            if self.license:
                signature=p.get('fingerprint_signature')
                if not signature or not self.license.reserve(signature,pid):
                    raise LicenseRequired('Profile khong dat doi chieu tap trung')
            directory=(self.root/'user-data'/pid).resolve()
            if directory.parent!=(self.root/'user-data').resolve():raise ValueError('Profile path invalid')
            directory.mkdir(parents=True,exist_ok=True)
            native=self.native_config(p)
            (directory/'config.hidemium').write_text(encode_config(native),encoding='utf8')
            # The addresses of the proxy and the place of the profile live in
            # network.hidemium; without it the WebRTC mode has no effect.
            settings=p.get('settings') or {}
            geo=p.get('geo') or {}
            public_ip=settings.get('webrtc_public_ip') or (geo.get('ip') or '')
            # The exit IP of the proxy is the address the page must show. When
            # the place of the profile is not the exit of the proxy, the exit
            # is resolved here so the WebRTC address matches the connection.
            px=p.get('proxy') or {}
            if not public_ip and px.get('type') not in ('Without proxy',None,'','none'):
                exit_geo=self.exit_geo(px,p.get('id'))
                if exit_geo:public_ip=exit_geo.get('ip') or ''
            local_ip=settings.get('webrtc_local_ip') or ''
            network=hidemium_settings.network_hidemium(settings,geo,public_ip,local_ip)
            (directory/'network.hidemium').write_text(network,encoding='utf8')
            # The header `Accept-Language` comes from the pref of Chromium,
            # so without it the server receives the language of the machine
            # of the host while the page reports the language of the profile.
            hidemium_settings.write_language_pref(
                directory,(p.get('config',{}).get('fingerprint',{}).get('languages',{}) or {}).get('acceptLanguage',''))
            active=directory/'DevToolsActivePort'
            active.unlink(missing_ok=True)
            screen=p['config']['fingerprint']['screen']
            command=[self.core['executable'],'--enable-hidemium-config','--user-data-dir='+str(directory),
                     '--no-first-run','--no-default-browser-check','--disable-background-networking',
                     '--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
                     '--touch-events=enabled','--top-chrome-touch-ui',
                     # The CDP emulation only reaches the top level page. With
                     # the site isolation a cross-origin frame runs in its own
                     # renderer process and keeps the real screen of the
                     # machine, so its content is laid out on another size than
                     # the parent and a click misses the control of the game.
                     # NetworkServiceSandbox keeps the network service in the
                     # browser process. In the sandboxed service the WebRTC
                     # substitution of the core is skipped, the STUN request
                     # leaves the machine directly and the page reads the real
                     # public IP in the srflx candidate.
                     # Chromium 155 only knows the CamelCase feature names. The
                    # old lower case spellings are unknown to the feature list
                    # and make the core show the 'unsupported feature flag'
                    # bubble on every launch.
                    '--disable-features=SitePerProcess,IsolateOrigins,NetworkServiceSandbox',
                     f'--window-size={screen["width"]},{screen["height"]}']
            # The switches of the Hidemium v5 launcher come from the settings.
            command.extend(hidemium_settings.command_flags(settings))
            relay=None
            if px.get('type') not in ('Without proxy',None,'','none'):
                relay=ProxyRelay(px);command.append('--proxy-server='+relay.url)
                command.append('--force-webrtc-ip-handling-policy=disable_non_proxied_udp')
            else:command.append('--no-proxy-server')
            if extensions:command.append('--load-extension='+','.join(extensions))
            url=self.diagnostic_url if diagnostic else p.get('startup_url','about:blank')
            # Configure mobile dimensions before loading the saved start page.
            command.append('about:blank')
            log=(directory/'launcher.log').open('ab')
            try:
                process=subprocess.Popen(command,stdout=log,stderr=log)
                controller=MobileController(directory,p,self.core['version'],url,browser_pid=process.pid,geo=p.get('geo'))
                branding=WindowBranding(process.pid,p['name'],BASE/'nova.ico',p['id'])
                input_button=ProfileInputButton(process.pid,lambda:controller.input_mode,
                    lambda mode:self.set_input_mode(pid,mode))
                self.sessions[pid]={'process':process,'browser_pid':process.pid,
                                    'core_executable':self.core['executable'],'directory':directory,
                                    'relay':relay,'touch':controller,'branding':branding,
                                    'input_button':input_button,'log':log}
                # Chromium may detach from its launcher. Resolve the actual core PID
                # before status is returned so the UI and native helpers track the window.
                for _ in range(30):
                    candidates=_owned_core_pids(self.sessions[pid])
                    if candidates:
                        _set_session_browser_pid(self.sessions[pid],candidates[0])
                        if active.is_file():break
                    if process.poll() is not None and not candidates:
                        time.sleep(.1)
                    else:time.sleep(.1)
            except Exception:
                if relay:relay.close()
                log.close();raise
            return self.status(pid)

    def stop(self,pid):
        with self.lock:
            if self.sync and pid in [self.sync.master,*self.sync.followers]:self.stop_sync()
            session=self.sessions.get(pid)
            if session:
                tree=_windows_process_tree()
                candidates=_owned_core_pids(session,tree)
                browser_pid=session.get('browser_pid')
                targets=[]
                if browser_pid in tree:targets.append(browser_pid)
                targets.extend(candidate for candidate in candidates if candidate not in targets)
                launcher=session['process']
                if launcher.poll() is None and launcher.pid not in targets:targets.append(launcher.pid)
                for target in targets:
                    subprocess.run(['taskkill','/PID',str(target),'/T','/F'],capture_output=True)
                try:launcher.wait(timeout=8)
                except subprocess.TimeoutExpired:raise ValueError('Chua dung duoc browser')
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    current=_windows_process_tree()
                    if not any(target in current for target in targets):break
                    time.sleep(.1)
                else:raise ValueError('Chua dung duoc browser')
            self._clean_session(pid)
            return {'running':False}

    def delete(self,pid):
        with self.lock:
            self.get(pid)
            if self.status(pid)['running']:raise ValueError('Dung profile truoc khi xoa')
            self.path(pid).unlink()
            # Keep browsing files as a recoverable archive; never recursively delete on API request.
            directory=self.root/'user-data'/pid
            if directory.exists():
                archive=self.root/'archive';archive.mkdir(exist_ok=True)
                directory.rename(archive/(pid+'_'+secrets.token_hex(4)))
            return {'deleted':True}

    def close(self):
        self.license_monitor_stop.set()
        for job in self.jobs.values():job['cancelled']=True
        self.stop_sync()
        with self.lock:
            for pid in list(self.sessions):self.stop(pid)


def build_server(manager,token,port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value):
            value=scrub_errors(value)
            body=json.dumps(value,ensure_ascii=False).encode('utf8')
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def dispatch(self):
            path=urlsplit(self.path).path
            if self.command=='GET' and path=='/diagnostics':
                body=(BASE/'diagnostics.html').read_bytes();self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body);return
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                self.send(401,{'error':'API token required'});return
            # A web page must not be able to use the API token, but the NOVA
            # extension talks to it from its own origin.
            origin=self.headers.get('Origin')
            if origin not in (None,'null') and not origin.startswith('chrome-extension://'):
                self.send(403,{'error':'Browser origins are not allowed'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<0 or size>2_000_000:raise ValueError('Request qua lon')
                body=json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(body,dict):raise ValueError('JSON phai la object')
                method=self.command
                if manager.license and not path.startswith('/v1/license/') and path!='/v1/shutdown':
                    state=manager.license.status()
                    if not state.get('active'):
                        # A lease that ran out is renewed here, in the
                        # background of the request. The user is only asked for
                        # the key when the renewal itself fails, so a short
                        # outage does not push the user out of the app.
                        try:manager.require_license()
                        except Exception:
                            raise LicenseRequired('Key da het han hoac chua kich hoat; vui long nhap key con han') from None
                if path=='/v1/license/status' and method=='GET':result=manager.license.status() if manager.license else {'required':False,'active':True}
                elif path=='/v1/api-info' and method=='GET':
                    # The extension needs the local address and token so it can
                    # reach the admin-only endpoints from inside the profile.
                    manager.require_owner_registration_local()
                    result={'port':self.server.server_port,'token':token}
                elif path=='/v1/license/features' and method=='GET':
                    # The claims are already in memory, so the panel of the
                    # owner is shown without a round trip to the server.
                    result={'owner_registration':bool(manager.license and manager.license.status().get('owner_registration') is True)}
                elif path=='/v1/site-links' and method=='GET':
                    manager.require_owner_registration_local()
                    if not manager.license:raise ValueError('License server unavailable')
                    result=manager.license.site_links()
                    if not isinstance(result,dict):raise ValueError('Phản hồi link server không hợp lệ.')
                    links=manager.validate_site_links(result.get('links')) if result.get('links') else None
                    if links:
                        cache=manager.root/'site-links-cache.json'
                        cached=json.loads(cache.read_text(encoding='utf8')) if cache.exists() else None
                        if links!=cached:atomic(cache,links)
                    if not links and not (manager.root/'site-links-cache.json').exists():raise ValueError('Server chưa cấu hình link website.')
                    links=json.loads((manager.root/'site-links-cache.json').read_text(encoding='utf8'))
                    result={'links':links}
                elif path=='/v1/license/restore' and method=='POST':result=manager.license.restore_login()
                elif path=='/v1/promo-accounts' and method=='GET':
                    manager.require_owner_registration_local()
                    result={'accounts':manager.promo_accounts.list_all(),'paths':manager.promo_accounts.paths()}
                elif path=='/v1/promo-accounts' and method=='POST':
                    manager.require_owner_registration_local()
                    site=str(body.get('site') or '');kind=str(body.get('kind') or 'success')
                    line=str(body.get('line') or '');message=str(body.get('message') or '')
                    action=str(body.get('action') or 'add')
                    if action=='add':result=manager.promo_accounts.add(site,kind,line,message)
                    elif action=='remove':result=manager.promo_accounts.remove(site,kind,line)
                    elif action=='clear':result=manager.promo_accounts.clear(site,kind)
                    else:raise ValueError('Thao tac khong hop le.')
                elif path=='/v1/otp/status' and method=='GET':
                    manager.require_owner_registration_local()
                    result=manager.license.otp_request('/v1/otp/status')
                elif path=='/v1/otp/active' and method=='GET':
                    manager.require_owner_registration_local()
                    # Many tabs of the same machine ask for the codes at the
                    # same time, and they all want the same list. The list is
                    # kept for a moment, so the machine does not ask the
                    # server once per tab and second.
                    result=manager.otp_active_cached()
                elif path=='/v1/otp/rent' and method=='POST':
                    manager.require_owner_registration_local()
                    tier=str(body.get('tier') or '');service=str(body.get('service') or '')
                    if tier not in ('otp1','otp2') or not service:raise ValueError('Chon OTP1 hoac OTP2.')
                    request_key=secrets.token_hex(8)
                    result=manager.license.otp_request('/v1/otp/rent',{'tier':tier,'service':service,'request_key':request_key})
                elif path=='/v1/captcha/solve' and method=='POST':
                    manager.require_owner_registration_local()
                    task=str(body.get('task') or '');image=str(body.get('image') or '');instruction=str(body.get('instruction') or '');site=str(body.get('site') or '')
                    guide=str(body.get('guide') or '')
                    if task not in ('text','text_mixed','coordinates','slider') or not image:raise ValueError('Captcha khong hop le.')
                    result=manager.license.captcha_solve(task,image,instruction,site,guide)
                elif path=='/v1/otp/rerent' and method=='POST':
                    manager.require_owner_registration_local()
                    tier=str(body.get('tier') or '');phone=str(body.get('phone') or '').strip()
                    if tier not in ('otp1','otp2') or not phone:raise ValueError('Chon OTP1 hoac OTP2 va nhap so dien thoai.')
                    request_key=secrets.token_hex(8)
                    result=manager.license.otp_request('/v1/otp/rerent',{'tier':tier,'phone':phone,'request_key':request_key})
                elif path=='/v1/license/app-update' and method=='POST':result=manager.license.app_update()
                elif path=='/v1/license/login/start' and method=='POST':
                    if not manager.license:raise LicenseRequired('Chua cau hinh')
                    result=manager.license.start_login()
                elif path=='/v1/license/login/poll' and method=='POST':
                    if not manager.license:raise LicenseRequired('Chua cau hinh')
                    result=manager.license.poll_login()
                elif path=='/v1/license/device' and method=='GET':
                    if not manager.license:raise ValueError('Ban chu may khong dung cap phep thuong mai')
                    result={'public_key':manager.license.pem,'device_id':manager.license.device}
                elif path=='/v1/license/activate' and method=='POST':
                    if not manager.license:raise ValueError('Ban chu may khong dung cap phep thuong mai')
                    result=manager.license.activate(body['key'],body.get('telegram_ticket'))
                elif path=='/v1/status' and method=='GET':
                    result={'engine':{'version':manager.core['version']},'profiles':len(list(manager.records.glob('p_*.json'))),
                            'native_keys':len(manager.keys),'api_port':self.server.server_port,'fingerprint_history':manager.history.stats(),
                            'sync':manager.sync.state if manager.sync else {'running':False}}
                elif path=='/v1/profiles' and method=='GET':result=manager.listing()
                elif path=='/v1/profiles' and method=='POST':result=manager.create(body)
                elif path=='/v1/defaults' and method=='GET':result=manager.defaults()
                elif path=='/v1/defaults' and method=='PUT':
                    body['defaults_format_version']=2
                    body['startup_url_version']=1
                    manager.validate(body);atomic(manager.root/'defaults.json',body);result=body
                elif path=='/v1/native-keys' and method=='GET':result=sorted(manager.keys)
                elif path=='/v1/catalog/update' and method=='POST':result=manager.update_device_catalog()
                elif path=='/v1/devices' and method=='GET':result=manager.catalog.data
                elif path=='/v1/device-preview' and method=='POST':result=manager.catalog.apply(body.get('payload') or manager.defaults(),body.get('device_selection','random'),manager.core['version'])
                elif path=='/v1/groups' and method=='GET':result=manager.groups()
                elif path=='/v1/groups' and method=='POST':result=manager.create_group(body.get('name'))
                elif path=='/v1/bulk' and method=='POST':result=manager.start_bulk(body)
                elif re.fullmatch(r'/v1/jobs/j_[a-f0-9]{20}',path) and method=='GET':result=copy.deepcopy(manager.jobs[path.rsplit('/',1)[1]])
                elif re.fullmatch(r'/v1/jobs/j_[a-f0-9]{20}/cancel',path) and method=='POST':
                    manager.jobs[path.split('/')[-2]]['cancelled']=True;result={'cancelled':True}
                elif path=='/v1/windows/arrange' and method=='POST':result=manager.arrange(body.get('ids',[]))
                elif path=='/v1/sync/start' and method=='POST':raise ValueError('Dong bo thao tac da duoc bo theo yeu cau')
                elif path=='/v1/sync/stop' and method=='POST':result=manager.stop_sync()
                elif path=='/v1/sync/status' and method=='GET':result=manager.sync.state if manager.sync else {'running':False}
                elif path=='/v1/fingerprints' and method=='GET':result={'stats':manager.history.stats(),'items':manager.history.recent()}
                elif path=='/v1/shutdown' and method=='POST':
                    result={'stopping':True};threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:
                    match=re.fullmatch(r'/v1/profiles/(p_[a-f0-9]{24})(?:/(launch|stop|rotate|diagnose|input))?',path)
                    if not match:self.send(404,{'error':'Endpoint not found'});return
                    pid,action=match.groups()
                    if action and method=='POST':
                        result={'launch':lambda:manager.launch(pid),'diagnose':lambda:manager.launch(pid,True),
                                'stop':lambda:manager.stop(pid),'rotate':lambda:manager.rotate(pid),
                                'input':lambda:manager.set_input_mode(pid,body.get('mode'))}[action]()
                    elif not action and method=='GET':result=manager.get(pid)
                    elif not action and method=='PUT':result=manager.update(pid,body)
                    elif not action and method=='DELETE':result=manager.delete(pid)
                    else:self.send(405,{'error':'Method not allowed'});return
                self.send(200,result)
            except KeyError as e:self.send(404,record_error(manager.root,e))
            except (ValueError,TypeError,json.JSONDecodeError) as e:self.send(400,record_error(manager.root,e))
            except Exception as e:self.send(500,record_error(manager.root,e))
        do_GET=dispatch;do_POST=dispatch;do_PUT=dispatch;do_DELETE=dispatch
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    manager.diagnostic_url=f'http://127.0.0.1:{server.server_port}/diagnostics'
    return server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True);parser.add_argument('--port',type=int,default=18300)
    args=parser.parse_args()
    def report_unhandled(_type,error,_trace):
        print(json.dumps(record_error(Path(args.data),error)),file=sys.stderr,flush=True)
    sys.excepthook=report_unhandled
    threading.excepthook=lambda event:report_unhandled(event.exc_type,event.exc_value,event.exc_traceback)
    manager=ProfileManager(args.data);manager.prepare_runtime()
    token_file=manager.root/'api-token.txt'
    token=token_file.read_text().strip() if token_file.exists() else secrets.token_urlsafe(32)
    if not token:token=secrets.token_urlsafe(32)
    token_file.write_text(token,encoding='utf8')
    try:server=build_server(manager,token,args.port)
    except OSError:server=build_server(manager,token,0)
    atomic(manager.root/'runtime.json',{'port':server.server_port,'pid':os.getpid()})
    print(json.dumps({'ready':True,'port':server.server_port,'token':token}),flush=True)
    try:server.serve_forever()
    finally:manager.close();server.server_close()


if __name__=='__main__':main()
