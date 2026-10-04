"""验证 Win11 磨砂玻璃 API 在本机是否真的生效。

不只调 API 看返回值，还用 Win32 桌面 DC 抓真实像素，对比「开玻璃」前后
窗口区域的平均亮度——如果玻璃生效，亚克力会把背后的桌面模糊并混入底色，
像素值应当出现可见变化。
"""
from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ctypes import wintypes  # noqa: E402

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from ui import win11  # noqa: E402

print("=== 系统探测 ===")
print(f"  build = {win11.windows_build()}")
print(f"  supports_mica          = {win11.supports_mica()}")
print(f"  supports_backdrop_type = {win11.supports_backdrop_type()}")


def grab_screen_region(x: int, y: int, w: int, h: int):
    """用 Win32 BitBlt 抓真实屏幕像素（含 DWM 合成后的效果）。"""
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")

    hdesktop = user32.GetDC(0)
    srcdc = gdi32.CreateCompatibleDC(hdesktop)
    bmp = gdi32.CreateCompatibleBitmap(hdesktop, w, h)
    gdi32.SelectObject(srcdc, bmp)

    # SRCCOPY | CAPTUREBLT
    gdi32.BitBlt(srcdc, 0, 0, w, h, hdesktop, x, y, 0x00CC0020 | 0x40000000)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(info)
    info.biWidth = w
    info.biHeight = -h  # 负数 = 自上而下
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(srcdc, bmp, 0, h, buf, ctypes.byref(info), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(srcdc)
    user32.ReleaseDC(0, hdesktop)

    raw = buf.raw
    # BGRA -> 平均亮度
    total = 0
    for i in range(0, len(raw), 4):
        total += raw[i + 2] * 0.299 + raw[i + 1] * 0.587 + raw[i] * 0.114
    return total / (len(raw) / 4)


app = QApplication(sys.argv)
window = QWidget()
window.setWindowFlags(
    Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
)
window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
window.resize(420, 260)
window.move(120, 120)

layout = QVBoxLayout(window)
layout.setContentsMargins(0, 0, 0, 0)
label = QLabel("")
label.setStyleSheet("background: transparent;")
layout.addWidget(label)

window.show()
app.processEvents()

hwnd = int(window.winId())
print(f"\nhwnd = {hwnd:#x}")

geo = window.frameGeometry()
cx, cy, cw, ch = geo.x() + 40, geo.y() + 40, 200, 120

results = {}


def step_measure(tag: str) -> float:
    app.processEvents()
    value = grab_screen_region(cx, cy, cw, ch)
    results[tag] = value
    print(f"  [{tag}] 窗口区域平均亮度 = {value:.1f}")
    return value


QTimer.singleShot(150, lambda: step_measure("before"))
QTimer.singleShot(300, lambda: print(
    "  apply_glass ->", win11.apply_glass(hwnd, enabled=True, rounded=True, dark=True)))
QTimer.singleShot(500, lambda: step_measure("glass_dwm"))
QTimer.singleShot(650, lambda: print(
    "  set_acrylic(强化底色) ->", win11.set_acrylic(hwnd, 0xE0100C0A)))
QTimer.singleShot(850, lambda: step_measure("glass_acrylic"))
QTimer.singleShot(1000, lambda: print(
    "  clear + NONE ->", win11.set_backdrop(hwnd, win11.DWMSBT_NONE),
    win11.clear_acrylic(hwnd)))
QTimer.singleShot(1200, lambda: step_measure("after_clear"))


def finish():
    before = results.get("before")
    dwm = results.get("glass_dwm")
    acr = results.get("glass_acrylic")
    cleared = results.get("after_clear")
    print("\n=== 结论 ===")
    if before is not None and dwm is not None:
        print(f"  DWM 背板改变像素: {abs(dwm - before):.1f}"
              f" → {'有可见效果' if abs(dwm - before) > 3 else '几乎无变化'}")
    if acr is not None:
        print(f"  叠加亚克力后变化: {abs(acr - before):.1f}")
    if cleared is not None and before is not None:
        print(f"  关闭后是否回到原值: {abs(cleared - before):.1f}")
    app.quit()


QTimer.singleShot(1500, finish)
app.exec()
