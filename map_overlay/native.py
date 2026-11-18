"""Public desktop/window APIs only. Never opens a game process handle."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import os

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.MonitorFromRect.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD]
user32.MonitorFromRect.restype = wt.HANDLE


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT),
                ("work", wt.RECT), ("flags", wt.DWORD), ("device", wt.WCHAR * 32)]


user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MonitorInfo)]


def enable_dpi_awareness():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def windows():
    result = []

    @WNDENUMPROC
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != os.getpid() and client_box(hwnd, strict=False):
                    result.append((int(hwnd), title.value))
        return True

    user32.EnumWindows(callback, 0)
    return sorted(result, key=lambda pair: (not any(s in pair[1].lower() for s in ["原神", "genshin"]), pair[1]))


def client_box(hwnd, strict=True):
    if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        if strict:
            raise ValueError("目标窗口已关闭或最小化，请重新选择。")
        return None
    rect = wt.RECT()
    origin = wt.POINT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        if strict:
            raise OSError("无法获取游戏窗口的客户区。")
        return None
    width, height = rect.right, rect.bottom
    if min(width, height) < 64:
        if strict:
            raise ValueError("窗口画面过小。")
        return None
    return (origin.x, origin.y, width, height)


def foreground_is(hwnd):
    foreground = user32.GetForegroundWindow()
    return bool(hwnd and foreground and user32.GetAncestor(foreground, 2) == hwnd)


def focus(hwnd):
    user32.SetForegroundWindow(hwnd)


def set_capture_excluded(hwnd, excluded):
    # WDA_EXCLUDEFROMCAPTURE excludes our overlay from desktop capture.
    return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11 if excluded else 0))


def monitor_for(box, with_handle=False):
    x, y, w, h = box
    rect = wt.RECT(x, y, x + w, y + h)
    monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("无法获取显示器信息。")
    m = info.monitor
    if x < m.left or y < m.top or x + w > m.right or y + h > m.bottom:
        raise ValueError("首版要求游戏窗口完整位于同一台显示器内。")
    bounds = (m.left, m.top, m.right - m.left, m.bottom - m.top)
    return (info.device, bounds, int(monitor)) if with_handle else (info.device, bounds)


SCREENSHOT_HOTKEY = 4
HOTKEYS = {1: 0x77, 2: 0x78, 3: 0x79, SCREENSHOT_HOTKEY: 0x7A}  # F8 through F11


def register_hotkeys(hwnd):
    result = []
    for key_id, key in HOTKEYS.items():
        if user32.RegisterHotKey(hwnd, key_id, 0x4003, key):  # Ctrl+Alt+NoRepeat
            result.append(key_id)
    return result


def unregister_hotkeys(hwnd, registered):
    for key_id in registered:
        user32.UnregisterHotKey(hwnd, key_id)


def hotkey_message(pointer):
    message = wt.MSG.from_address(int(pointer))
    return int(message.wParam) if message.message == 0x0312 else None
