"""屏幕抓取与画面变化检测。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Optional, Tuple

import numpy as np
from PySide6.QtGui import QGuiApplication

user32 = ctypes.windll.user32

Rect = Tuple[int, int, int, int]  # left, top, width, height（物理像素）


def virtual_screen_rect() -> Rect:
    """整个虚拟桌面的物理像素范围。"""
    try:
        left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    except Exception:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 0, 0, 1920, 1080
        geo = screen.geometry()
        return geo.x(), geo.y(), geo.width(), geo.height()
    if width <= 0 or height <= 0:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 0, 0, 1920, 1080
        geo = screen.geometry()
        return geo.x(), geo.y(), geo.width(), geo.height()
    return int(left), int(top), int(width), int(height)


def grab_region(region: Rect) -> Optional[np.ndarray]:
    """抓取物理像素区域，返回 RGB numpy 数组（H, W, 3, uint8）。"""
    left, top, width, height = (int(v) for v in region)
    if width <= 0 or height <= 0:
        return None
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    # QScreen.grabWindow 用的是逻辑坐标，需要按 DPR 换算
    dpr = float(screen.devicePixelRatio() or 1.0)
    logical = (int(round(left / dpr)), int(round(top / dpr)),
               int(round(width / dpr)), int(round(height / dpr)))
    pixmap = screen.grabWindow(0, logical[0], logical[1], logical[2], logical[3])
    if pixmap is None or pixmap.isNull():
        return None
    image = pixmap.toImage()
    if dpr != 1.0 and (image.width() != width or image.height() != height):
        image = image.scaled(width, height)
    image = image.convertToFormat(image.Format.Format_RGB888)
    ptr = image.constBits()
    buffer = np.frombuffer(ptr, dtype=np.uint8)
    row_bytes = image.bytesPerLine()
    expected = row_bytes * image.height()
    if buffer.size < expected:
        return None
    arr = buffer[:expected].reshape(image.height(), row_bytes)[:, : image.width() * 3]
    return arr.reshape(image.height(), image.width(), 3).copy()


def mean_abs_diff(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    """归一化平均绝对差（0-1）；尺寸不同视为完全不同。"""
    if a is None or b is None:
        return 1.0
    if a.shape != b.shape:
        return 1.0
    return float(
        np.abs(a.astype(np.int16) - b.astype(np.int16)).mean() / 255.0
    )


def get_window_rect(hwnd: int) -> Optional[Rect]:
    """取窗口的屏幕矩形（物理像素，含 DWM 阴影外的可视边界）。"""
    try:
        rect = wintypes.RECT()
        # DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS = 9) 更贴近视觉边界
        dwmapi = ctypes.windll.dwmapi
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hr != 0:
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
        return (
            int(rect.left),
            int(rect.top),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
        )
    except Exception:
        return None


def window_title(hwnd: int) -> str:
    try:
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        buf = ctypes.create_unicode_buffer(max(length + 1, 256))
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, len(buf))
        return buf.value
    except Exception:
        return ""


def is_window_valid(hwnd: int) -> bool:
    try:
        return bool(user32.IsWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False
