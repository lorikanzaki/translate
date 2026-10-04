"""抓「窗口所在的真实屏幕像素」的小工具（Win32 BitBlt + GetDIBits）。

单独成模块的原因：`tools\\_probe_window.py` 的探测逻辑写在模块顶层，
一 import 就会整段跑起来，其它脚本没法复用它里面的函数。

判断窗口外观必须走这里，**不能用 QWidget.grab()** —— grab() 会把透明
区域画成白色（本机实测面板正中一条竖缝显示成 RGB(243,243,243)），
拿它看配色会得出完全错误的结论。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def window_rect(hwnd: int):
    """原生窗口矩形的物理像素 (left, top, width, height)。"""
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def grab_window_rgb(hwnd: int):
    """抓窗口矩形覆盖的那块屏幕像素，返回 PIL.Image(RGB)。"""
    from PIL import Image

    left, top, width, height = window_rect(hwnd)

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    # SRCCOPY = 0x00CC0020，CAPTUREBLT = 0x40000000（带上分层窗口）
    gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc,
                 left, top, 0x00CC0020 | 0x40000000)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height          # 负值 = 自上而下
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0           # BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    image = Image.frombuffer("RGBA", (width, height), buffer.raw,
                             "raw", "BGRA", 0, 1).convert("RGB")

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)
    return image


def sample_pixels(hwnd: int, points):
    """按 (名字, x, y) 采样窗口内的像素。

    points 里给的是**窗口内的物理像素坐标**（调用方自己乘 devicePixelRatioF）。
    只抓一次整块位图再在内存里取点，避免逐点反复 BitBlt。
    返回 (samples_dict, image)。
    """
    image = grab_window_rgb(hwnd)
    samples = {name: image.getpixel((int(x), int(y))) for name, x, y in points}
    return samples, image
