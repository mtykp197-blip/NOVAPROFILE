"""Owned native input switch; never injects controls into customer web pages."""
import ctypes
from ctypes import wintypes
import os
import threading
import time


class ProfileInputButton:
    def __init__(self, browser_pid, get_mode, change_mode, *, foreground_window=None):
        self.browser_pid=browser_pid;self.get_mode=get_mode;self.change_mode=change_mode
        self.foreground_window=foreground_window
        self.stopped=threading.Event();self.busy=False;self.error_until=0
        self.state={'ready':False,'error':None}
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def toggle(self):
        if self.stopped.is_set() or self.busy:return
        self.busy=True
        def change():
            try:self.change_mode('touch' if self.get_mode()=='mouse' else 'mouse')
            except Exception as exc:
                self.error_until=time.monotonic()+3;self.state['error']=type(exc).__name__
            finally:self.busy=False
        threading.Thread(target=change,daemon=True).start()

    def _run(self):
        if os.name!='nt':return
        user=ctypes.WinDLL('user32',use_last_error=True)
        gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        pointer=ctypes.c_ssize_t
        procedure=ctypes.WINFUNCTYPE(pointer,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
        user.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
            ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
        user.CreateWindowExW.restype=wintypes.HWND
        user.GetForegroundWindow.restype=wintypes.HWND
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        user.SetWindowLongPtrW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_void_p];user.SetWindowLongPtrW.restype=ctypes.c_void_p
        user.CallWindowProcW.argtypes=[ctypes.c_void_p,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.CallWindowProcW.restype=pointer
        user.SetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPCWSTR]
        user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user.DestroyWindow.argtypes=[wintypes.HWND]
        user.GetDpiForWindow.argtypes=[wintypes.HWND];user.GetDpiForWindow.restype=wintypes.UINT
        user.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        user.TranslateMessage.argtypes=[ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.argtypes=[ctypes.POINTER(wintypes.MSG)];user.DispatchMessageW.restype=pointer
        user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.SendMessageW.restype=pointer
        gdi.GetStockObject.argtypes=[ctypes.c_int];gdi.GetStockObject.restype=wintypes.HANDLE
        hwnd=None;old_proc=None;last_key=False;last_label=None
        @procedure
        def handler(window,message,wparam,lparam):
            if message==0x21:return 3  # MA_NOACTIVATE: clicking never steals browser focus.
            if message==0x202:
                self.toggle();return 0
            return user.CallWindowProcW(old_proc,window,message,wparam,lparam)
        try:
            # Tool window, no activation, native STATIC with notification/centering.
            hwnd=user.CreateWindowExW(0x08000080,'STATIC','NOVA',0x80800101,0,0,130,28,None,None,None,None)
            if not hwnd:raise OSError('Cannot create input switch')
            old_proc=user.SetWindowLongPtrW(hwnd,-4,ctypes.cast(handler,ctypes.c_void_p))
            if not old_proc:raise OSError('Cannot attach input switch')
            user.SendMessageW(hwnd,0x30,gdi.GetStockObject(17),1)
            self.state={'ready':True,'error':None,'hwnd':hwnd}
            while not self.stopped.is_set():
                msg=wintypes.MSG()
                while user.PeekMessageW(ctypes.byref(msg),None,0,0,1):
                    user.TranslateMessage(ctypes.byref(msg));user.DispatchMessageW(ctypes.byref(msg))
                foreground=user.GetAncestor(self.foreground_window() if self.foreground_window else user.GetForegroundWindow(),2)
                pid=wintypes.DWORD();user.GetWindowThreadProcessId(foreground,ctypes.byref(pid))
                name=ctypes.create_unicode_buffer(80);user.GetClassNameW(foreground,name,80)
                visible=pid.value==self.browser_pid and name.value=='Chrome_WidgetWin_1'
                key=bool(user.GetAsyncKeyState(0x77)&0x8000)  # F8, only this foreground profile.
                if visible:
                    if key and not last_key:self.toggle()
                    rect=wintypes.RECT()
                    if user.GetWindowRect(foreground,ctypes.byref(rect)):
                        dpi=(user.GetDpiForWindow(foreground) or 96)/96
                        width,height=round(130*dpi),round(28*dpi)
                        x=rect.right-width-round(18*dpi);y=rect.bottom-height-round(26*dpi)
                        user.SetWindowPos(hwnd,ctypes.c_void_p(-1),x,y,width,height,0x0010|0x0040)
                    mode=self.get_mode()
                    label=('Đang đổi…' if self.busy else 'Thử lại · F8' if time.monotonic()<self.error_until else
                           'Chuột · F8 ↔' if mode=='mouse' else 'Cảm ứng · F8 ↔')
                    if label!=last_label:user.SetWindowTextW(hwnd,label);last_label=label
                else:user.ShowWindow(hwnd,0)
                last_key=key
                self.stopped.wait(.08)
        except Exception as exc:self.state={'ready':False,'error':type(exc).__name__}
        finally:
            if hwnd:
                if old_proc:user.SetWindowLongPtrW(hwnd,-4,old_proc)
                user.DestroyWindow(hwnd)

    def close(self):
        self.stopped.set();self.thread.join(timeout=2)
