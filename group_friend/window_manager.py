"""Manage a bound native window without changing its account or desktop.

The app remains rendered behind other windows; this is not headless execution.
"""
from datetime import datetime, timezone
import os
import re
import time

from .douyin import ObservationError


def creation_ticks(iso):
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?Z', iso)
    if match is None:
        raise ObservationError('invalid_process_start_time')
    whole = datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc)
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return int((whole - epoch).total_seconds()) * 10_000_000 + int((match[2] or '').ljust(7, '0'))


class NativeWindows:
    def __init__(self):
        if os.name != 'nt':
            raise ObservationError('windows_required')
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.dwm = ctypes.WinDLL('dwmapi')
        for name in ('IsWindow', 'IsIconic', 'IsWindowVisible'):
            method = getattr(self.user, name)
            method.argtypes, method.restype = [w.HWND], w.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetWindowTextW.restype = ctypes.c_int
        self.user.GetForegroundWindow.argtypes, self.user.GetForegroundWindow.restype = [], w.HWND
        self.user.SetForegroundWindow.argtypes, self.user.SetForegroundWindow.restype = [w.HWND], w.BOOL
        self.user.ShowWindow.argtypes, self.user.ShowWindow.restype = [w.HWND, ctypes.c_int], w.BOOL
        self.user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        self.user.SetWindowPos.restype = w.BOOL
        self.kernel.OpenProcess.argtypes, self.kernel.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        self.kernel.CloseHandle.argtypes, self.kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
        self.kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = w.BOOL
        self.dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long

    def identity(self, hwnd):
        if not self.user.IsWindow(hwnd):
            raise ObservationError('window_unavailable')
        pid = self.w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, self.c.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise ObservationError('process_identity_unavailable')
        try:
            times = [self.w.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(process, *(self.c.byref(t) for t in times)):
                raise ObservationError('process_identity_unavailable')
            started = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
        finally:
            self.kernel.CloseHandle(process)
        title = self.c.create_unicode_buffer(512)
        self.user.GetWindowTextW(hwnd, title, len(title))
        return pid.value, started, title.value

    def cloaked(self, hwnd):
        value = self.w.DWORD()
        result = self.dwm.DwmGetWindowAttribute(hwnd, 14, self.c.byref(value), self.c.sizeof(value))
        return result == 0 and bool(value.value)

    def minimized(self, hwnd):
        return bool(self.user.IsIconic(hwnd))

    def visible(self, hwnd):
        return bool(self.user.IsWindowVisible(hwnd))

    def restore(self, hwnd):
        self.user.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE

    def lower(self, hwnd):
        return bool(self.user.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x13))  # BOTTOM, no move/size/activation

    def foreground(self):
        return self.user.GetForegroundWindow()

    def activate(self, hwnd):
        return bool(self.user.IsWindow(hwnd) and self.user.SetForegroundWindow(hwnd))


class WindowManager:
    def __init__(self, api=None, sleep=time.sleep):
        self.api = NativeWindows() if api is None else api
        self.sleep = sleep

    def validate(self, binding):
        if self.api.identity(binding.window_handle) != (binding.process_id,
                creation_ticks(binding.process_started_at), binding.window_title):
            raise ObservationError('client_restarted_rebind_required')

    def prepare(self, binding):
        self.validate(binding)
        hwnd = binding.window_handle
        if self.api.cloaked(hwnd):
            raise ObservationError('window_on_other_desktop')
        if not self.api.minimized(hwnd) and self.api.visible(hwnd):
            return False
        previous = self.api.foreground()
        self.api.restore(hwnd)
        if not self.api.lower(hwnd):
            raise ObservationError('window_background_failed')
        for _ in range(10):
            if not self.api.minimized(hwnd) and self.api.visible(hwnd):
                break
            self.sleep(.05)
        else:
            raise ObservationError('window_restore_failed')
        self.return_focus(binding, previous)
        self.sleep(.25)  # allow the renderer/accessibility tree to refresh
        return True

    def foreground(self):
        return self.api.foreground()

    def return_focus(self, binding, previous):
        if previous and previous != binding.window_handle and self.api.foreground() == binding.window_handle:
            if self.api.activate(previous):
                self.api.lower(binding.window_handle)
                return True
        return False
