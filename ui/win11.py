"""Win32 外观增强：磨砂玻璃（亚克力）、暗色标题栏、圆角、拖动/缩放。

全部通过 ctypes 直接调 DWM / user32，**任何一项失败都静默降级**，
不影响程序在旧系统或关闭特效的机器上运行。

Windows 11 的版本探测注意：注册表 `ProductName` 至今仍写着 "Windows 10"
（微软已知的历史遗留），必须用 `CurrentBuildNumber` 判断：
    22621+ = Windows 11 22H2 起，支持 DWMWA_SYSTEMBACKDROP_TYPE
    22000  = Windows 11 21H2
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

# ---- DWM 属性编号 ----
DWMWA_USE_IMMERSIVE_DARK_MODE = 20          # 1903+（旧编号 19，见下）
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19      # 1809~1903
DWMWA_WINDOW_CORNER_PREFERENCE = 33         # Win11
DWMWA_SYSTEMBACKDROP_TYPE = 38              # Win11 22H2+

DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2        # Mica
DWMSBT_TRANSIENTWINDOW = 3   # Acrylic（磨砂玻璃）
DWMSBT_TABBEDWINDOW = 4      # Mica Alt

# ---- SetWindowCompositionAttribute（Win10 1803+ 的亚克力）----
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

WCA_ACCENT_POLICY = 19

# ---- 缩放命中测试 ----
HTCLIENT = 1
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDBLCLK = 0x00A3

_dwmapi: Optional[ctypes.WinDLL] = None
_user32: Optional[ctypes.WinDLL] = None
_dwm_ready = False


def _load() -> bool:
    """惰性加载两个 DLL，失败就整体禁用。"""
    global _dwmapi, _user32, _dwm_ready
    if _dwm_ready:
        return True
    if not IS_WINDOWS:
        return False
    try:
        _dwmapi = ctypes.WinDLL("dwmapi")
        _user32 = ctypes.WinDLL("user32")
        _dwm_ready = True
    except OSError:
        _dwm_ready = False
    return _dwm_ready


def windows_build() -> int:
    """返回 CurrentBuildNumber；拿不到返回 0。"""
    if not IS_WINDOWS:
        return 0
    return _build()


def _build() -> int:
    class OSVERSIONINFOEXW(ctypes.Structure):
        _fields_ = [
            ("dwOSVersionInfoSize", wintypes.DWORD),
            ("dwMajorVersion", wintypes.DWORD),
            ("dwMinorVersion", wintypes.DWORD),
            ("dwBuildNumber", wintypes.DWORD),
            ("dwPlatformId", wintypes.DWORD),
            ("szCSDVersion", wintypes.WCHAR * 128),
            ("wServicePackMajor", wintypes.WORD),
            ("wServicePackMinor", wintypes.WORD),
            ("wSuiteMask", wintypes.WORD),
            ("wProductType", wintypes.BYTE),
            ("wReserved", ctypes.c_byte),
        ]

    info = OSVERSIONINFOEXW()
    info.dwOSVersionInfoSize = ctypes.sizeof(info)
    # RtlGetVersion 不受 manifest 的兼容性谎言影响（GetVersionEx 会撒谎）
    try:
        if ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info)) != 0:
            return 0
    except Exception:
        return 0
    return int(info.dwBuildNumber)


def supports_mica() -> bool:
    return IS_WINDOWS and _build() >= 22000


def supports_backdrop_type() -> bool:
    return IS_WINDOWS and _build() >= 22621


def _dwm_set(hwnd: int, attribute: int, value: int) -> bool:
    if not _load():
        return False
    try:
        ptr = ctypes.c_int(value)
        result = _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attribute),
            ctypes.byref(ptr), ctypes.sizeof(ptr),
        )
        return result == 0
    except Exception:
        return False


def _dwm_set_old(hwnd: int, value: int) -> bool:
    """老编号（19）也试一次，覆盖 1809~1903 的系统。"""
    return _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, value)


def set_dark_title_bar(hwnd: int, dark: bool = True) -> bool:
    """让系统绘制的部分（标题栏、边框、右键菜单）跟随暗色主题。"""
    if _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0):
        return True
    return _dwm_set_old(hwnd, 1 if dark else 0)


def set_rounded_corners(hwnd: int, rounded: bool = True) -> bool:
    """Win11 圆角。最大化时 Windows 会自动变直角。"""
    return _dwm_set(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                    DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)


def set_backdrop(hwnd: int, kind: int = DWMSBT_TRANSIENTWINDOW) -> bool:
    """Win11 22H2+ 的系统背景材质。"""
    return _dwm_set(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, kind)


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),   # 0xAABBGGRR
        ("AnimationId", ctypes.c_uint),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_uint),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def set_acrylic(hwnd: int, tint: int = 0xCC1A1614) -> bool:
    """Win10 1803+ 的亚克力模糊（磨砂玻璃）。

    `tint` 是 0xAABBGGRR：AA=不透明度，BB/GG/RR 是蓝绿红分量。
    默认给一层偏暗的蓝灰底色，保证上面白色文字始终可读。

    这是未公开 API，Win11 上有时会被系统忽略；返回 False 不代表出错，
    调用方应把它当作「尽力而为」。
    """
    if not _load():
        return False
    try:
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 0x20 | 0x40 | 0x80 | 0x100  # 四边都画
        accent.GradientColor = tint

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)

        func = _user32.SetWindowCompositionAttribute
        func.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        func.restype = wintypes.BOOL
        return bool(func(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def clear_acrylic(hwnd: int) -> None:
    if not _load():
        return
    try:
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_DISABLED
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        _user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data))
    except Exception:
        pass


def apply_glass(hwnd: int, enabled: bool = True, rounded: bool = True,
                dark: bool = True, tint: int = 0xE81A1613) -> dict:
    """一次性把外观设置应用到窗口，返回每项是否成功（便于日志/自检）。

    **只用亚克力一套机制**。实测（tools\\_probe_glass.py）在 22631 上
    DWMWA_SYSTEMBACKDROP_TYPE 与 SetWindowCompositionAttribute 都能返回成功，
    但两者同时开会互相打架（整块不透明或闪烁），所以这里只走亚克力：
    它才是真正的「磨砂玻璃」，而 Mica 只是把桌面壁纸调暗后当作静态底色。

    `tint` 是 0xAABBGGRR，默认 0xE81A1613 ≈ 91% 不透明度的暗蓝灰，
    保证压在上面的白字始终可读。
    """
    if not IS_WINDOWS:
        return {"supported": False}
    result = {
        "supported": True,
        "build": _build(),
        "dark_title": set_dark_title_bar(hwnd, dark),
        "rounded": set_rounded_corners(hwnd, rounded),
        "acrylic": False,
    }
    if enabled:
        result["acrylic"] = set_acrylic(hwnd, tint)
    else:
        set_backdrop(hwnd, DWMSBT_NONE)
        clear_acrylic(hwnd)
    return result


def enable_dark_mode_for_window(hwnd: int) -> bool:
    """暗色标题栏的别名，便于语义化调用。"""
    return set_dark_title_bar(hwnd, True)
