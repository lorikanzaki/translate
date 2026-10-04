"""框选区域的常驻边框。

用户反馈：「框选翻译范围后，框选范围是透明的，我无法很清楚地看见翻译范围」。
框选本身是**不可见**的（区域里跑的是别人的界面），所以这里单独做一个
总是置顶、鼠标穿透的小窗口，沿着区域的四边画一圈边框，让范围一目了然。

三条硬约束：

1. **边框必须画在区域外面**。抓屏用的是 ``QScreen.grabWindow``，它抓的是
   合成后的屏幕内容，压在区域上的任何像素都会被 OCR 当成界面文字。
   所以窗口比区域大一圈，只在多出来的那一圈里上色，区域本身留给下面的
   真实界面。
2. **鼠标必须能穿过去**。用 ``WindowTransparentForInput``（映射到
   ``WS_EX_TRANSPARENT | WS_EX_LAYERED``），否则用户点不到边框底下的
   软件。再加 ``WA_ShowWithoutActivating``，免得它抢焦点。
3. **高 DPI 要换算**。区域坐标是**物理像素**（抓屏用），
   ``setGeometry`` 收的是**逻辑像素**，本机 125% 缩放差 1.25 倍。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from ui import theme as theme_mod

# 区域与边框之间留的空隙（逻辑像素）。高 DPI 下物理/逻辑换算会带来最多
# 一个物理像素的取整误差，留一点空隙保证边框绝不会压到被抓屏的区域上。
_GUARD_HIDPI = 2
_GUARD_NORMAL = 1

_LABEL_HEIGHT = 18
_LABEL_FONT = ("Consolas", 9)


class RegionFrame(QWidget):
    """沿框选区域画一圈边框的小窗口（点击穿透、不抢焦点）。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._enabled = True
        self._color = "#4da3ff"
        self._width = 2
        self._label = True
        self._region: Optional[Sequence[int]] = None
        self._hole: Optional[QRect] = None
        self._label_text = ""
        self._label_top = 0
        self._label_bottom = 0

    # ------------------------------------------------------------ 配置
    def apply_theme(self, settings: dict) -> None:
        """从主题设置里取边框颜色/粗细/大小标签开关。"""
        settings = settings or theme_mod.DEFAULTS
        self._enabled = bool(settings.get("region_enabled", True))
        self._color = str(settings.get("region_color") or "#4da3ff")
        self._width = max(1, min(10, int(settings.get("region_width") or 2)))
        self._label = bool(settings.get("region_label", True))
        if self._region is not None:
            self.set_region(self._region)

    # ------------------------------------------------------------ 显示
    def set_region(self, region: Sequence[int]) -> None:
        """按物理像素区域摆放边框窗口。"""
        if not region or len(region) != 4:
            self.clear()
            return
        self._region = [int(v) for v in region]
        if not self._enabled:
            self.hide()
            return

        left, top, width, height = self._region
        dpr = _device_ratio()
        thickness = self._width
        guard = _GUARD_HIDPI if dpr > 1.0 else _GUARD_NORMAL
        wanted_label = _LABEL_HEIGHT if self._label else 0

        rx = int(round(left / dpr))
        ry = int(round(top / dpr))
        rw = max(1, int(round(width / dpr)))
        rh = max(1, int(round(height / dpr)))

        # 大小标签默认放在区域上方；区域贴着屏幕顶端（标题栏那一条）时改放下方，
        # 否则窗口会跑到屏幕外，标签就看不见了。
        if wanted_label and ry - thickness - guard - wanted_label < 0:
            self._label_top, self._label_bottom = 0, wanted_label
        else:
            self._label_top, self._label_bottom = wanted_label, 0

        # 窗口 = 区域向外扩一圈（再加上标签条）
        outer_x = rx - thickness - guard
        outer_y = ry - thickness - guard - self._label_top
        outer_w = rw + 2 * (thickness + guard)
        outer_h = (rh + 2 * (thickness + guard)
                   + self._label_top + self._label_bottom)

        # 区域本身在窗口里的位置：必须精确落在 (rx, ry)
        self._hole = QRect(thickness + guard,
                           thickness + guard + self._label_top, rw, rh)
        self._label_text = f"{width} × {height}"

        self.setGeometry(outer_x, outer_y, outer_w, outer_h)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        """收起边框（没框选、或用户关掉了这个功能）。"""
        self._region = None
        self._hole = None
        self.hide()

    # ------------------------------------------------------------ 绘制
    def _label_band(self) -> QRect:
        rect = self.rect()
        if self._label_top:
            return QRect(0, 0, rect.width(), self._label_top)
        if self._label_bottom:
            return QRect(0, rect.height() - self._label_bottom,
                         rect.width(), self._label_bottom)
        return QRect()

    def _bands(self) -> List[QRect]:
        """把窗口切成「边框四条」（全部在区域之外）。"""
        if self._hole is None:
            return []
        hole = self._hole
        rect = self.rect()
        return [
            QRect(0, self._label_top, rect.width(),
                  hole.top() - self._label_top),
            QRect(0, hole.bottom() + 1, rect.width(),
                  rect.height() - self._label_bottom - hole.bottom() - 1),
            QRect(0, hole.top(), hole.left(), hole.height()),
            QRect(hole.right() + 1, hole.top(),
                  rect.width() - hole.right() - 1, hole.height()),
        ]

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._hole is None or not self._enabled:
            return
        painter = QPainter(self)
        for band in self._bands():
            if not band.isEmpty():
                painter.fillRect(band, QColor(self._color))

        band = self._label_band()
        if self._label and self._label_text and not band.isEmpty():
            painter.fillRect(band, QColor(0, 0, 0, 150))
            painter.setPen(QColor(self._color))
            painter.setFont(QFont(*_LABEL_FONT))
            painter.drawText(band.adjusted(4, 0, -4, 0),
                             Qt.AlignmentFlag.AlignVCenter
                             | Qt.AlignmentFlag.AlignLeft,
                             self._label_text)


def _device_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio() or 1.0) if screen else 1.0
