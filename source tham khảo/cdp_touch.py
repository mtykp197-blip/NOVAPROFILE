"""Configure native DevTools mobile input; keep sessions attached to each tab."""
import json
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import threading
import time
from urllib.request import urlopen

import websocket
from hidemium_engine import device_window_size
from hidemium_settings import ANDROID_PLATFORM
import nova_engine


def _accept_language(value):
    """The Accept-Language of the profile, in the form Chrome sends it.

    The payload carries the value of the HTTP header, and that value holds
    the quality factors (`vi-VN,vi;q=0.9`). Chromium copies the header
    string into `navigator.languages` as it is, so the page reads a
    language named `vi;q=0.9`, which no Chrome ever reports, and the
    header the server receives carries the factor twice:
    `vi-VN,vi;q=0.9;q=0.9`.

    Measured against the core: the genuine Hidemium profiles write the
    plain language list, and the core then sends the factor itself. So the
    payload keeps the header value, and the factors are removed before the
    value reaches the browser.    """
    parts = [part.strip() for part in str(value or '').split(',') if part.strip()]
    if not parts:
        return ''
    plain = []
    for part in parts:
        name = part.split(';')[0].strip()
        if name and name not in plain:
            plain.append(name)
    # The quality of the first language stays, the rest follow it.
    return ','.join(plain) if len(plain) > 1 else plain[0] if plain else ''


def _ua_metadata(nav=None,version=''):
    """The UA-CH metadata of the profile, in the shape Chrome 155 requires.

    Measured against the core (155.0.8059.40): `Emulation.setUserAgentOverride`
    rejects an empty `userAgentMetadata` with "mandatory field missing", so
    the probe of every profile died with `Invalid parameters` and no profile
    could be created. The metadata also has to carry every field, because
    Chromium deserializes the whole struct or nothing at all.

    The values are the ones of the profile, not of the machine: the platform
    is `Android`, the architecture is `arm` (the host reports `x86` when the
    key is left out), the model is the model of the preset and the brand
    list is the one of the installed Chrome, so the page never sends the
    empty `navigator.userAgentData.brands` that no Chrome reports.

    The brand list and the full version list are NOT written here. Measured
    against the core: `hidemium.navigator.useragent.version_useragent` is the
    only key the core reads for them, and it derives the greased brand of
    the major it announces (`149` gives `Not)A;Brand`, `156` gives
    `Not:A-Brand`). Writing them here pinned `Not(A:Brand` and a build of
    `155.0.0.0` into every profile, so the whole installation announced one
    Chrome, and the header `sec-ch-ua` the server reads no longer matched
    the brands the page reads. Leaving the two fields out makes the core
    write them, and the header and the page then agree.
    """
    nav=nav or {}
    match=re.search(r'Chrome/(\d+\.\d+\.\d+\.\d+)',str(nav.get('userAgent') or ''))
    full_version=match.group(1) if match else str(version or '')
    major=full_version.split('.')[0]
    if not major:
        major=re.sub(r'[^0-9].*$','',str(version or '')) or '155'
    return {
        'platform':'Android',
        'platformVersion':str(nav.get('osVersion') or '13.0.0'),
        'architecture':'arm',
        'model':str(nav.get('device') or ''),
        'mobile':bool(nav.get('mobile',True)),
        'bitness':'64',
        'wow64':False,
    }


def native_view_size(process_id,window_bounds=None):
    """Actual Chromium render-widget size in DIPs, excluding browser UI."""
    if os.name!='nt' or not process_id:return None
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.GetDpiForWindow.argtypes=[wintypes.HWND];user.GetDpiForWindow.restype=wintypes.UINT
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    sizes=[];current_parent={}
    user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
    def child(hwnd,_):
        name=ctypes.create_unicode_buffer(100);user.GetClassNameW(hwnd,name,100)
        if name.value=='Chrome_RenderWidgetHostHWND' and user.IsWindowVisible(hwnd):
            rect=wintypes.RECT()
            if user.GetClientRect(hwnd,ctypes.byref(rect)) and rect.right>=100 and rect.bottom>1:
                root=current_parent['hwnd'];parent_rect=wintypes.RECT();user.GetClientRect(root,ctypes.byref(parent_rect))
                # Small omnibox/popup render widgets can sit above the page.
                if rect.right < parent_rect.right/2:return True
                origin=wintypes.POINT(0,0);child_origin=wintypes.POINT(0,0)
                user.ClientToScreen(root,ctypes.byref(origin));user.ClientToScreen(hwnd,ctypes.byref(child_origin))
                x=child_origin.x-origin.x;y=child_origin.y-origin.y
                dpi=user.GetDpiForWindow(root) or 96
                if x>=0 and y>=0 and parent_rect.right>x and parent_rect.bottom>y:
                    sizes.append((round((parent_rect.right-x)*96/dpi),round((parent_rect.bottom-y)*96/dpi)))
        return True
    child_callback=callback_type(child);user.EnumChildWindows.argtypes=[wintypes.HWND,callback_type,wintypes.LPARAM]
    user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    def parent(hwnd,_):
        if window_bounds:
            rect=wintypes.RECT()
            if not user.GetWindowRect(hwnd,ctypes.byref(rect)):return True
            dpi=user.GetDpiForWindow(hwnd) or 96
            width=(rect.right-rect.left)*96/dpi;height=(rect.bottom-rect.top)*96/dpi
            if abs(width-window_bounds.get('width',width))>40 or abs(height-window_bounds.get('height',height))>40:return True
        owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==process_id:
            current_parent['hwnd']=hwnd;user.EnumChildWindows(hwnd,child_callback,0)
        return True
    parent_callback=callback_type(parent);user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p];user.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
    previous=user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:user.EnumWindows(parent_callback,0)
    finally:
        if previous:user.SetThreadDpiAwarenessContext(previous)
    return max(sizes,key=lambda item:item[0]*item[1]) if sizes else None


class MobileController:
    def __init__(self,profile_dir,payload,version,startup_url=None,*,browser_pid=None,geo=None):
        self.browser_pid=browser_pid
        self.geo=geo or {}
        self.window_sizes={}
        self.visible_size=None
        self.tab_states={}
        self.last_applied={}
        self.target_retry={}
        self.directory=Path(profile_dir)
        self.payload=payload
        self.input_mode=payload.get('input_mode','touch' if payload.get('touch_mode',True) is not False else 'mouse')
        self.version=version
        self.startup_url=startup_url
        self.navigation_done=False
        self.display_size=device_window_size(payload)
        self.command_lock=threading.RLock()
        self.stopped=threading.Event()
        self.sessions={}
        self.frame_ids=set()
        self.state={'ready':False,'tabs':0,'error':None}
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def _run(self):
        try:
            active=self.directory/'DevToolsActivePort'
            for _ in range(150):
                if self.stopped.wait(.1):return
                try:
                    port=int(active.read_text().splitlines()[0])
                    break
                except (OSError,ValueError,IndexError):pass
            else:raise RuntimeError('DevTools startup timed out')
            while not self.stopped.is_set():
                try:
                    with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=3) as response:targets=json.load(response)
                    self.poll_targets(targets)
                except Exception as exc:
                    # A navigation, closed socket, or temporary DevTools error
                    # must never permanently stop emulation for all other tabs.
                    self.state={'ready':bool(self.sessions),'tabs':len(self.sessions),'error':type(exc).__name__}
                self.stopped.wait(.2)
        except Exception as exc:
            if not self.stopped.is_set():self.state={'ready':False,'tabs':len(self.sessions),'error':type(exc).__name__}

    def drop_target(self,target_id):
        ws=self.sessions.pop(target_id,None)
        if ws:
            try:ws.close()
            except Exception:pass
        self.frame_ids.discard(target_id)
        self.window_sizes.pop(target_id,None);self.tab_states.pop(target_id,None);self.last_applied.pop(target_id,None)

    def apply_geo(self,ws):
        """Give the page the location and the time of the proxy exit.

        The native config cannot grant the permission of the browser, so the
        location comes from DevTools. The commands are best effort: a page
        without the permission still shows the country of the exit.
        """
        geo=self.geo
        if not geo:return
        from runtime_probe import call
        with self.command_lock:
            if geo.get('timezone'):
                try:call(ws,'Emulation.setTimezoneOverride',{'timezoneId':geo['timezone']},request_id=80)
                except Exception:pass
            if geo.get('latitude') is not None and geo.get('longitude') is not None:
                try:call(ws,'Browser.grantPermissions',{'permissions':['geolocation']},request_id=81)
                except Exception:pass
                try:
                    call(ws,'Emulation.setGeolocationOverride',{'latitude':float(geo['latitude']),
                        'longitude':float(geo['longitude']),'accuracy':100},request_id=82)
                except Exception:pass

    def connect_target(self,target):
        from runtime_probe import call
        # Chrome needs more than three seconds to open its first socket, and
        # a short timeout made the connection fail before the emulation was
        # applied, so the page read the screen of the machine.
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=30,suppress_origin=True)
        try:
            with self.command_lock:
                for index,(method,params) in enumerate(self.commands(),10):call(ws,method,params,request_id=index)
                self.apply_geo(ws)
                if not self.navigation_done:
                    window=call(ws,'Browser.getWindowForTarget',{'targetId':target['id']},request_id=90)
                    call(ws,'Browser.setWindowBounds',{'windowId':window['windowId'],'bounds':{'width':int(self.display_size['width']),'height':int(self.display_size['height'])}},request_id=91)
                    if self.startup_url and self.startup_url!='about:blank' and target.get('url')!=self.startup_url:
                        call(ws,'Page.navigate',{'url':self.startup_url},request_id=100)
                    self.navigation_done=True
            self.sessions[target['id']]=ws
        except Exception:
            ws.close();raise

    def poll_targets(self,targets):
        # A cross-origin iframe runs in its own renderer process, so it is a
        # separate target. Chromium refuses Emulation.setDeviceMetricsOverride
        # on it ("can only be executed on top-level targets"), but the touch
        # emulation and the user agent are accepted, and without them the
        # frame shows the mouse of the machine instead of the touch of the
        # profile. The iframe is followed so a game inside it behaves like
        # the page that holds it.
        pages={t['id']:t for t in targets if t['type']=='page'}
        frames={t['id']:t for t in targets if t['type']=='iframe'}
        for target_id in set(self.sessions)-pages.keys()-frames.keys():self.drop_target(target_id)
        for target_id in set(self.target_retry)-pages.keys()-frames.keys():self.target_retry.pop(target_id,None)
        error=None
        for target_id,target in pages.items():
            if self.stopped.is_set():return
            if time.monotonic()<self.target_retry.get(target_id,0):continue
            try:
                if target_id not in self.sessions:self.connect_target(target)
                self.fit_window(self.sessions[target_id],target_id)
            except Exception as exc:
                self.drop_target(target_id);self.target_retry[target_id]=time.monotonic()+1
                error=type(exc).__name__
        for target_id,target in frames.items():
            if self.stopped.is_set():return
            if time.monotonic()<self.target_retry.get(target_id,0):continue
            try:
                if target_id not in self.sessions:self.connect_frame(target)
            except Exception as exc:
                self.drop_target(target_id);self.target_retry[target_id]=time.monotonic()+1
                error=type(exc).__name__
        self.state={'ready':bool(self.sessions),'tabs':len(self.sessions),'error':error}

    def connect_frame(self,target):
        """Attach to an out-of-process iframe and apply the frame commands."""
        from runtime_probe import call
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=30,suppress_origin=True)
        try:
            with self.command_lock:
                for index,(method,params) in enumerate(self.frame_commands(),10):call(ws,method,params,request_id=index)
            self.sessions[target['id']]=ws
            self.frame_ids.add(target['id'])
        except Exception:
            ws.close();raise

    def engine_command(self):
        """Lenh tien engine NOVA vao trang.

        Script duoc tien bang `Page.addScriptToEvaluateOnNewDocument`, nen
        no chay truoc moi script cua trang, va no chay lai cho moi
        trang moi. Cach nay cham ca iframe va worker, va trang khong
        nhin thay gi.

        Core Hidemium co tram cung: audio chi bay trang thai, canvas chi
        16^10 gia tri, hash ve WebGL gan voi renderer cua GPU. Engine nay
        thay cac ham do bang gia tri bam tu seed cua profile, nen hai
        profile khac nhau khong the ve cung mot dau van tay.
        """
        return ('Page.addScriptToEvaluateOnNewDocument',
                {'source': nova_engine.payload_script(self.payload)})

    def frame_commands(self):
        """The emulation commands an out-of-process iframe accepts."""
        fp=self.payload['config']['fingerprint'];nav=fp['navigator']
        mode=getattr(self,'input_mode',self.payload.get('input_mode','touch' if self.payload.get('touch_mode',True) is not False else 'mouse'))
        touch_enabled=self.payload.get('touch_mode',True) is not False or mode=='touch'
        ua=nav['userAgent']
        match=re.search(r'Chrome/(\d+\.\d+\.\d+\.\d+)',ua)
        full_version=match.group(1) if match else str(self.version)
        return [
            self.engine_command(),
            ('Emulation.setTouchEmulationEnabled',{'enabled':touch_enabled,'maxTouchPoints':int(nav.get('maxTouchPoints',5)) or 1}),
            ('Emulation.setUserAgentOverride',{
                'userAgent':ua,
                'acceptLanguage':_accept_language(fp['languages']['acceptLanguage']),
                'platform':nav.get('platform') or ANDROID_PLATFORM,
                'userAgentMetadata':_ua_metadata(nav,self.version),
            }),
        ]

    def commands(self,view=None):
        fp=self.payload['config']['fingerprint'];nav=fp['navigator'];screen=fp['screen']
        mode=getattr(self,'input_mode',self.payload.get('input_mode','touch' if self.payload.get('touch_mode',True) is not False else 'mouse'))
        touch_enabled=self.payload.get('touch_mode',True) is not False or mode=='touch'
        display=getattr(self,'display_size',device_window_size(self.payload))
        # Fit both dimensions, then let the responsive viewport use all space.
        # Width-only scaling can put fixed-height game controls below the window.
        width,height=view if view else (display['width'],display['height'])
        scale=max(.0001,min(10,width/screen['width'],height/screen['height']))
        # Keep Android CSS dimensions stable unless the owner chooses fill.
        fill=self.payload.get('viewport_mode','phone')=='responsive'
        viewport_width=max(1,int(width/scale+1e-9)) if fill else screen['width']
        viewport_height=max(1,int(height/scale+1e-9)) if fill else screen['height']
        ua=nav['userAgent']
        match=re.search(r'Chrome/(\d+\.\d+\.\d+\.\d+)',ua)
        full_version=match.group(1) if match else str(self.version)
        major=full_version.split('.')[0]
        commands=[
            # The engine goes in first: the script has to run before the
            # scripts of the page, so it is injected before any emulation
            # and before the navigation.
            self.engine_command(),
            ('Emulation.setDeviceMetricsOverride',{'width':viewport_width,'height':viewport_height,
                'deviceScaleFactor':screen['pixelRatio'],'mobile':True,
                'scale':scale,'dontSetVisibleSize':True,
                'screenWidth':screen['width'],'screenHeight':screen['height'],
                'screenOrientation':{'type':'landscapePrimary' if screen['width']>screen['height'] else 'portraitPrimary',
                                     'angle':90 if screen['width']>screen['height'] else 0}}),
            ('Emulation.setTouchEmulationEnabled',{'enabled':touch_enabled,'maxTouchPoints':int(nav.get('maxTouchPoints',5)) or 1}),
            ('Emulation.setEmitTouchEventsForMouse',{'enabled':touch_enabled and mode!='mouse','configuration':'mobile'}),
        ]
        # The metadata carries the values of the profile. Measured against
        # the core: an override that leaves it out empties the brand list of
        # the page, and the page then sends an empty
        # `navigator.userAgentData.brands`, which no Chrome ever does, and
        # Chrome 155 refuses an empty metadata object outright, so the
        # profile cannot even be measured.
        commands.append(('Emulation.setUserAgentOverride',{
            'userAgent':ua,
            'acceptLanguage':_accept_language(fp['languages']['acceptLanguage']),
            'platform':nav.get('platform') or ANDROID_PLATFORM,
            'userAgentMetadata':_ua_metadata(nav,self.version),
        }))
        return commands

    def metrics_command(self,view=None):
        """The emulation command that paints the panel of the profile.

        `commands()` puts the engine script first, so the emulation is not
        at a fixed index: taking the first one gave the engine script
        instead, and the panel of the profile was never applied, so the
        page read the screen of the machine.
        """
        for method,params in self.commands(view=view):
            if method=='Emulation.setDeviceMetricsOverride':return params
        raise RuntimeError('khong co lenh emulation trong lenh cua profile')

    def fit_window(self,ws,target_id,force=False):
        from runtime_probe import call
        with self.command_lock:
            observed=call(ws,'Runtime.evaluate',{'expression':"({visible:document.visibilityState==='visible',origin:performance.timeOrigin})",'returnByValue':True},request_id=305)
            state=(observed.get('result') or {}).get('value') or {}
            if not force and state.get('visible') is False:
                self.tab_states[target_id]=(False,state.get('origin'))
                return
            window=call(ws,'Browser.getWindowForTarget',{'targetId':target_id},request_id=310)
            if window.get('bounds',{}).get('windowState')=='minimized':
                self.tab_states[target_id]=(False,state.get('origin'))
                return
            size=native_view_size(getattr(self,'browser_pid',None),window.get('bounds'))
            if size is None:size=(max(1,window['bounds']['width']),max(1,window['bounds']['height']-87))
            marker=(True,state.get('origin'),window['windowId'])
            metrics=self.metrics_command(view=size)
            layout=call(ws,'Page.getLayoutMetrics',request_id=306)
            viewport=layout.get('cssVisualViewport') or layout.get('visualViewport') or {}
            zoom=viewport.get('scale',1)
            valid=(abs(viewport.get('clientWidth',0)*zoom-metrics['width'])<=2 and abs(viewport.get('clientHeight',0)*zoom-metrics['height'])<=2)
            changed=(self.window_sizes.get(target_id)!=size or self.tab_states.get(target_id)!=marker)
            # Reassert the surface periodically: Chromium may recreate the
            # render widget when activating an existing tab without resizing.
            stale=time.monotonic()-self.last_applied.get(target_id,0)>=10
            if force or changed or not valid or stale:
                call(ws,'Emulation.setVisibleSize',{'width':size[0],'height':size[1]},request_id=312)
                call(ws,'Emulation.setDeviceMetricsOverride',metrics,request_id=311)
                self.last_applied[target_id]=time.monotonic()
            self.window_sizes[target_id]=size;self.tab_states[target_id]=marker

    def set_input_mode(self,mode):
        if mode not in ('touch','mouse'):raise ValueError('Che do thao tac khong hop le')
        from runtime_probe import call
        with self.command_lock:
            self.input_mode=mode
            for target_id,ws in list(self.sessions.items()):
                try:
                    if target_id in self.frame_ids:
                        # A frame only accepts the touch emulation; the mouse
                        # to touch conversion is a top-level command.
                        method,params=self.frame_commands()[0]
                        call(ws,method,params,request_id=329)
                        continue
                    # Native Android touch capability is already enabled on
                    # normal profiles. Do not reset its input stream each click.
                    if self.payload.get('touch_mode',True) is False:
                        method,params=self.commands()[1]
                        call(ws,method,params,request_id=329)
                    method,params=self.commands()[2]
                    call(ws,method,params,request_id=330)
                except Exception:
                    # Reconnected and newly opened tabs receive the chosen mode.
                    self.drop_target(target_id);self.target_retry[target_id]=time.monotonic()+1

    def set_display_size(self,width,height):
        from runtime_probe import call
        self.display_size={'width':width,'height':height}
        self.visible_size=None
        self.window_sizes.clear()
        self.tab_states.clear();self.last_applied.clear()
        with self.command_lock:
            for target_id,ws in list(self.sessions.items()):
                if target_id in self.frame_ids:continue
                call(ws,'Emulation.setDeviceMetricsOverride',self.commands()[0][1],request_id=200)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)
        for ws in list(self.sessions.values()):
            try:ws.close()
            except Exception:pass
        self.sessions.clear()
