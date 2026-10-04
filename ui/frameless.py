"""无边框窗口外壳：调整大小、拖动、最小化/最大化/关闭、磨砂玻璃。

为什么要自己实现而不是用系统标题栏：
  1. 需要把标题栏做成「状态胶囊 + 操作按钮」的样式，系统标题栏做不到；
  2. 需要磨砂玻璃背景，系统标题栏会残留一条不透明色带。

实现要点：
  * 缩放交给 `startSystemResize()`（Qt 5.15+），由 Windows 自己处理，
    DPI、多屏、贴边吸附、最大化还原全部原生行为，不用手算几何。
  * 边缘命中测试在 `nativeEvent` 的 WM_NCHITTEST 里做，这样鼠标一到
    边缘就变成缩放光标，而且**跨线程/子控件都可靠**。
  * 高 DPI 下 `frameMargins()` 会随缩放变化，不能写死 8px。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ui import win11

# 边缘热区宽度（逻辑像素）。Qt 6 已按 DPI 缩放，用逻辑值即可。
_BORDER = 7
_CORNER = 16

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
HTCLIENT = 1
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000

# WM_NCHITTEST 的返回值必须是「有符号」的，负值（-1 等）会被当成错误，
# 所以这里显式声明结果类型为 LRESULT（c_ssize_t）。
if sys.platform == "win32":
    _LRESULT = ctypes.c_ssize_t
else:  # pragma: no cover
    _LRESULT = ctypes.c_long


class FramelessWindow(QWidget):
    """带玻璃背景和完整窗口操作的基类。

    子类在 `self.body` 里放内容；标题栏请放在 `self.title_bar` 里，
    因为只有标题栏区域才允许拖动窗口。
    """

    minimizeRequested = Signal()
    maximizeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._glass_enabled = True
        self._glass_applied = False
        # 与 ui/win11.py 的默认值保持一致；主题会覆盖（见 ui/theme.py）
        self._glass_tint = 0xE81A1613
        self._glass_dark = True
        self._maximized = False
        self._normal_geometry: Optional[QRect] = None
        self._drag_origin: Optional[QPoint] = None
        self._resizing = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # WA_TranslucentBackground 只声明「窗口可以有透明像素」，并不会把
        # 底色变成透明 —— 窗口自己的背景仍是调色板的 Window 色（本机实测是
        # 不透明白色）。QSS 没画到的地方（分割条缝隙、圆角外沿）就会露出这块
        # 白底，实测面板正中一条 5px 宽的竖缝是 RGB(243,243,243)。
        # 显式把窗口底色设成全透明，磨砂玻璃才透得上来。
        transparent = QColor(0, 0, 0, 0)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, transparent)
        palette.setColor(QPalette.ColorRole.Base, transparent)
        self.setPalette(palette)
        self.setAutoFillBackground(False)
        self.setMinimumSize(420, 300)
        self.setMouseTracking(True)

    # ------------------------------------------------------------ 玻璃
    def enable_glass(self, enabled: bool = True,
                     tint: Optional[int] = None,
                     dark: Optional[bool] = None) -> dict:
        """应用磨砂玻璃。必须在窗口已创建（winId 有效）之后调用。

        ``tint`` 是 0xAABBGGRR 的亚克力色调、``dark`` 决定标题栏明暗，
        两者都由主题给出（ui\\theme.py 的 acrylic_tint / dark_title_bar），
        不传就沿用上一次的值 —— 否则 set_stay_on_top 重建 HWND 后会把主题
        色调悄悄丢掉。
        """
        self._glass_enabled = enabled
        if tint is not None:
            self._glass_tint = int(tint)
        if dark is not None:
            self._glass_dark = bool(dark)
        result = win11.apply_glass(int(self.winId()), enabled=enabled,
                                   tint=self._glass_tint, dark=self._glass_dark)
        self._glass_applied = bool(result.get("acrylic"))
        print(f"[glass] 应用结果 {result}")
        return result

    def set_stay_on_top(self, on_top: bool) -> None:
        """置顶开关。改窗口标志会重建原生窗口，必须重新 show() 并重刷玻璃。"""
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
        if visible:
            self.show()
            # setWindowFlag 会销毁旧 HWND，亚克力是挂在 HWND 上的，
            # 重建后必须重新应用，否则磨砂效果静默消失。
            if self._glass_enabled:
                self.enable_glass(True)

    # ------------------------------------------------------------ 窗口操作
    def toggle_maximize(self) -> None:
        if self._maximized:
            self.showNormal()
        else:
            self._normal_geometry = self.geometry()
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == event.Type.WindowStateChange:
            self._maximized = bool(self.windowState() & Qt.WindowState.WindowMaximized)
            self.maximizeRequested.emit()
        super().changeEvent(event)

    # ------------------------------------------------------------ 拖动
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._in_title_bar(event.position().toPoint()):
                if self._maximized:
                    # 最大化时拖动应先还原，并把窗口移到鼠标下方（Windows 行为）
                    ratio = event.position().x() / max(1, self.width())
                    self.showNormal()
                    if self._normal_geometry:
                        self.resize(self._normal_geometry.size())
                    self.move(int(event.globalPosition().x() - self.width() * ratio),
                              int(event.globalPosition().y() - 16))
                self._drag_origin = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self._save_geometry()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (self._drag_origin is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None:
            self._drag_origin = None
            self._save_geometry()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击标题栏 = 最大化/还原（Windows 习惯）。"""
        if (event.button() == Qt.MouseButton.LeftButton
                and self._in_title_bar(event.position().toPoint())):
            self.toggle_maximize()
            return
        super().mouseDoubleClickEvent(event)

    def _in_title_bar(self, pos: QPoint) -> bool:
        """子类可覆盖。默认把顶部 44px 当作标题栏。"""
        bar = getattr(self, "title_bar", None)
        if bar is not None:
            return bar.geometry().contains(pos)
        return pos.y() < 44

    def _save_geometry(self) -> None:
        """子类覆盖，用于持久化窗口位置。"""

    # ------------------------------------------------------------ 边缘缩放
    def _ensure_resizable_style(self) -> None:
        """给原生窗口补上 WS_THICKFRAME。

        Qt 的 FramelessWindowHint 会去掉 WS_THICKFRAME，某些情况下系统就
        不再提供贴边吸附和原生缩放行为；这里显式补回来（WS_THICKFRAME 只
        声明「这个窗口可调整大小」，绘制仍由我们自己控制）。
        """
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.WinDLL("user32")
            hwnd = wintypes.HWND(int(self.winId()))
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if not style & WS_THICKFRAME:
                user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
        except Exception as exc:
            print(f"[window] 补 WS_THICKFRAME 失败（缩放仍可用）: {exc}")

    def _frame_rect_physical(self) -> Optional[QRect]:
        """原生窗口矩形（**物理像素**），用于和 WM_NCHITTEST 的坐标比较。

        坑 1：`mapToGlobal()` 返回的是**逻辑**像素（geometry 是 Qt 自己的逻辑
        坐标系），而 WM_NCHITTEST 的 lParam 是**物理**屏幕坐标 —— 本机 125%
        缩放下两者差 1.25 倍，直接混用会让左/上边缘热区整体偏移而失效
        （实测右/下边缘正常、左/上边缘永远 HTCLIENT）。
        坑 2：**必须用 frameGeometry().topLeft()，不能用 mapToGlobal(geometry().topLeft())**。
        后者会把已经是全局的坐标再当成本地坐标加一次窗口原点，物理矩形
        整体右移/下移约一个窗口宽度（实测 894 → 1788），于是鼠标在窗口
        正中也会被判成 HTLEFT。
        """
        geo = self.geometry()
        try:
            margins = self.windowHandle().frameMargins()
        except Exception:
            margins = None
        origin = self.frameGeometry().topLeft()
        dpr = float(self.devicePixelRatioF()) or 1.0
        left = int(round(origin.x() * dpr))
        top = int(round(origin.y() * dpr))
        width = int(round(geo.width() * dpr))
        height = int(round(geo.height() * dpr))
        if margins is not None and not margins.isNull():
            left -= margins.left()
            top -= margins.top()
            width += margins.left() + margins.right()
            height += margins.top() + margins.bottom()
        return QRect(left, top, width, height)

    def _hit_test(self, global_pos: QPoint) -> int:
        """按屏幕坐标（物理像素）返回 WM_NCHITTEST 的命中码。"""
        if self.isMaximized() or self.isFullScreen():
            return HTCLIENT
        frame = self._frame_rect_physical()
        if frame is None:
            frame = self.geometry()
        x, y = global_pos.x(), global_pos.y()
        left, top = frame.x(), frame.y()
        right = left + frame.width()
        bottom = top + frame.height()

        left_zone = x < left + _BORDER
        right_zone = x >= right - _BORDER
        top_zone = y < top + _BORDER
        bottom_zone = y >= bottom - _BORDER

        # 四角优先，热区更大，方便抓
        if x < left + _CORNER and y < top + _CORNER:
            return HTTOPLEFT
        if x >= right - _CORNER and y < top + _CORNER:
            return HTTOPRIGHT
        if x < left + _CORNER and y >= bottom - _CORNER:
            return HTBOTTOMLEFT
        if x >= right - _CORNER and y >= bottom - _CORNER:
            return HTBOTTOMRIGHT
        if left_zone:
            return HTLEFT
        if right_zone:
            return HTRIGHT
        if top_zone:
            return HTTOP
        if bottom_zone:
            return HTBOTTOM
        return HTCLIENT

    def nativeEvent(self, event_type, message):  # noqa: N802
        """在 WM_NCHITTEST 里接管边缘命中，让系统给出原生缩放行为。"""
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_NCHITTEST:
                # lParam 里是 16 位有符号的屏幕坐标（物理像素）
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                code = self._hit_test(QPoint(x, y))
                if code != HTCLIENT:
                    return True, code
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_resizable_style()

    # ------------------------------------------------------------ 便利方法
    def is_maximized(self) -> bool:
        return bool(self.windowState() & Qt.WindowState.WindowMaximized)
