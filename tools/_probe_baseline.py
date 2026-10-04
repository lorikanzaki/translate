"""基线对照：纯 Qt 无边框窗口的最大化行为到底如何。

用来判断「最大化后 IsZoomed=False / frame 不是全屏」是我们的代码造成的，
还是无边框 + Qt 在这个 DPI 下的固有行为。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

user32 = ctypes.WinDLL("user32")
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

app = QApplication(sys.argv)
widget = QWidget()
widget.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
widget.resize(780, 540)
widget.move(300, 150)
widget.show()
app.processEvents()
hwnd = int(widget.winId())


def win_rect():
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


print("screen      =", app.primaryScreen().geometry().getRect())
print("available   =", app.primaryScreen().availableGeometry().getRect())
print("ui logical  =", widget.geometry().getRect(), "dpr=", widget.devicePixelRatioF())
print("normal  win =", win_rect(), "IsZoomed=", bool(user32.IsZoomed(wintypes.HWND(hwnd))))

widget.showMaximized()
app.processEvents()
print("maximized ui=", widget.geometry().getRect(), "isMaximized=", widget.isMaximized())
print("maximized win=", win_rect(), "IsZoomed=",
      bool(user32.IsZoomed(wintypes.HWND(hwnd))))

widget.showNormal()
app.processEvents()
print("restored  ui=", widget.geometry().getRect())
print("restored win=", win_rect(), "IsZoomed=",
      bool(user32.IsZoomed(wintypes.HWND(hwnd))))
