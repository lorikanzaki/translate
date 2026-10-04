"""框选区域控件。

两种用法：
- RegionSelector：全屏半透明遮罩，鼠标拖拽框出矩形，Esc 取消。
- WindowPicker：全屏十字光标，点一下选中鼠标下的窗口（取窗口矩形）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Optional, Tuple

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.capture import get_window_rect, window_title

user32 = ctypes.windll.user32
GA_ROOT = 2
DWMWA_CLOAKED = 14


class _FullscreenOverlay(QWidget):
    """带提示文字的全屏遮罩基类。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowOpacity(0.35)
        self.setStyleSheet("background-color: #000000;")
        self._hint = ""
        self._hint_font = QFont("Microsoft YaHei UI", 20)
        self._hint_font.setBold(True)

    def _draw_hint(self, painter: QPainter, text: str) -> None:
        painter.setOpacity(1.0)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(self._hint_font)
        rect = self.rect()
        painter.drawText(
            QRect(rect.left(), rect.top() + 40, rect.width(), 60),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            text,
        )

    def _cover_virtual_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        union = QRect(screens[0].geometry())
        for screen in screens[1:]:
            union = union.united(screen.geometry())
        self.setGeometry(union)

    @staticmethod
    def _to_physical(rect: QRect) -> Tuple[int, int, int, int]:
        screen = QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio() or 1.0) if screen else 1.0
        return (
            int(round(rect.x() * dpr)),
            int(round(rect.y() * dpr)),
            int(round(rect.width() * dpr)),
            int(round(rect.height() * dpr)),
        )


class RegionSelector(_FullscreenOverlay):
    """拖拽框选矩形区域。"""

    selected = Signal(int, int, int, int)  # 物理像素 left, top, width, height
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._start = None
        self._current = None
        self._hint = "拖拽框选要翻译的区域    ·    Esc 取消    ·    圈住需要翻译的界面范围即可"

    def start(self) -> None:
        self._start = None
        self._current = None
        self._cover_virtual_screen()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        self._draw_hint(painter, self._hint)
        if self._start is not None and self._current is not None:
            rect = QRect(self._start, self._current).normalized()
            painter.setOpacity(1.0)
            painter.fillRect(rect, QColor(30, 144, 255, 60))
            painter.setPen(QPen(QColor("#4da3ff"), 2))
            painter.drawRect(rect)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setFont(QFont("Consolas", 12))
            label = f"{rect.width()} × {rect.height()}"
            painter.drawText(rect.adjusted(6, -26, 0, 0).topLeft(), label)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._current = self._start
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        rect = QRect(self._start, event.position().toPoint()).normalized()
        self._start = None
        self._current = None
        if rect.width() < 24 or rect.height() < 24:
            self.cancelled.emit()
            self.close()
            return
        self.hide()
        self.selected.emit(*self._to_physical(rect))
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()


class WindowPicker(_FullscreenOverlay):
    """点选一个窗口，取其矩形。"""

    selected = Signal(int, int, int, int)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowOpacity(0.18)
        self._hint = "点击你要翻译的窗口    ·    Esc 取消"
        self._highlight: Optional[QRect] = None

    def start(self) -> None:
        self._cover_virtual_screen()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.setMouseTracking(True)

    def _window_at(self, global_pos) -> int:
        point = wintypes.POINT(int(global_pos.x()), int(global_pos.y()))
        hwnd = user32.WindowFromPoint(point)
        if not hwnd:
            return 0
        root = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
        return int(root or hwnd)

    def _rect_for(self, hwnd: int) -> Optional[QRect]:
        physical = get_window_rect(hwnd)
        if physical is None:
            return None
        screen = QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio() or 1.0) if screen else 1.0
        return QRect(
            int(round(physical[0] / dpr)), int(round(physical[1] / dpr)),
            int(round(physical[2] / dpr)), int(round(physical[3] / dpr)),
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hwnd = self._window_at(event.globalPosition())
        rect = self._rect_for(hwnd) if hwnd else None
        # 换算成本控件本地坐标
        offset = self.geometry().topLeft()
        self._highlight = rect.translated(-offset) if rect else None
        title = window_title(hwnd) if hwnd else ""
        self._hint = (
            f"点击选中的窗口：{title[:48]}" if title else "点击你要翻译的窗口    ·    Esc 取消"
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
        if self._highlight is not None:
            painter.setOpacity(1.0)
            painter.fillRect(self._highlight, QColor(30, 144, 255, 50))
            painter.setPen(QPen(QColor("#4da3ff"), 2))
            painter.drawRect(self._highlight)
        self._draw_hint(painter, self._hint)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hwnd = self._window_at(event.globalPosition())
        physical = get_window_rect(hwnd) if hwnd else None
        if physical is None or physical[2] < 24 or physical[3] < 24:
            self.cancelled.emit()
            self.close()
            return
        self.hide()
        self.selected.emit(*physical)
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
