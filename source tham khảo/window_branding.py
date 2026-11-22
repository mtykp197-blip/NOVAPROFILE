"""Apply our own icon/title to owned browser windows without patching the core."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import threading
import uuid


def set_window_app_id(hwnd,value):
    class Key(ctypes.Structure):
        _fields_=[('fmtid',ctypes.c_ubyte*16),('pid',wintypes.DWORD)]
    class Variant(ctypes.Structure):
        _fields_=[('vt',ctypes.c_ushort),('r1',ctypes.c_ushort),('r2',ctypes.c_ushort),('r3',ctypes.c_ushort),
                  ('value',ctypes.c_void_p),('padding',ctypes.c_ulonglong)]
    guid=(ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99').bytes_le)
    pointer=ctypes.c_void_p()
    api=ctypes.windll.shell32.SHGetPropertyStoreForWindow
    api.argtypes=[wintypes.HWND,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)];api.restype=ctypes.c_long
    if api(hwnd,ctypes.byref(guid),ctypes.byref(pointer))!=0:return
    table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    try:
        key=Key((ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3').bytes_le),5)
        text=ctypes.create_unicode_buffer(value);variant=Variant();variant.vt=31;variant.value=ctypes.cast(text,ctypes.c_void_p).value
        setter=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,ctypes.POINTER(Key),ctypes.POINTER(Variant))(table[6])
        setter(pointer,ctypes.byref(key),ctypes.byref(variant))
        ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p)(table[7])(pointer)
    finally:ctypes.WINFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p)(table[2])(pointer)


class WindowBranding:
    def __init__(self,pid,name,icon,profile_id=None):
        self.pid=pid;self.name=name;self.icon=Path(icon);self.stopped=threading.Event()
        self.app_id='NOVA.Profiles.'+str(profile_id or pid)
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _run(self):
        ctypes.windll.ole32.CoInitializeEx(None,0)
        user=ctypes.windll.user32
        user.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user.LoadImageW.restype=wintypes.HANDLE
        user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        user.SendMessageW.restype=wintypes.LPARAM
        user.SetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPCWSTR]
        user.DestroyIcon.argtypes=[wintypes.HANDLE]
        icon=user.LoadImageW(None,str(self.icon),1,0,0,0x10|0x40)
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        branded=set()
        def inspect(hwnd,_):
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value!=self.pid or not user.IsWindowVisible(hwnd):return True
            name=ctypes.create_unicode_buffer(128);user.GetClassNameW(hwnd,name,128)
            if name.value!='Chrome_WidgetWin_1':return True
            title=ctypes.create_unicode_buffer(1024);user.GetWindowTextW(hwnd,title,1024)
            prefix='NOVA • '+self.name
            if not title.value.startswith(prefix):user.SetWindowTextW(hwnd,prefix)
            if icon:
                user.SendMessageW(hwnd,0x80,0,int(icon));user.SendMessageW(hwnd,0x80,1,int(icon))
            if hwnd not in branded:
                try:set_window_app_id(hwnd,self.app_id);branded.add(hwnd)
                except Exception:pass
            return True
        callback=callback_type(inspect)
        try:
            while not self.stopped.is_set():
                user.EnumWindows(callback,0);self.stopped.wait(1)
        finally:
            if icon:user.DestroyIcon(icon)
            ctypes.windll.ole32.CoUninitialize()

    def close(self):self.stopped.set();self.thread.join(timeout=3)
