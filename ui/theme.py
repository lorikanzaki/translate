"""界面配色、背景图与框选边框的样式定义。

所有可调外观都集中在这里：

* 颜色（面板底色 / 正文底色 / 强调色 / 文字 / 边框）
* 背景图（路径 / 填充方式 / 显示强度）
* 框选区域边框（开关 / 颜色 / 粗细）

设置对话框改的是 ``config["theme"]`` 这一个字典，面板调用
``panel_style(theme(config))`` 重新生成样式表即可生效，不需要重启。

为什么把样式表做成模板：QSS 里全是 ``{}``，用 ``str.format`` 会被当成
占位符报错，所以这里用 ``string.Template`` 的 ``$名字`` 语法。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter
from string import Template

# --------------------------------------------------------------------- 默认值
DEFAULTS: Dict[str, object] = {
    # 面板（窗口面：标题栏 / 状态栏 / 边距）
    "panel_color": "#12141a",
    "panel_alpha": 56,          # 0-255
    # 正文区（左右两个文本框的底色）
    "content_color": "#0d0f12",
    "content_alpha": 235,       # 0-255
    # 强调色（开始/暂停按钮、选中、滚动条…）
    "accent": "#2e7cd6",
    # 文字
    "text": "#e6eaef",
    "muted": "#b9c0c8",
    # 窗口描边
    "border": "#ffffff",
    "border_alpha": 184,        # 0-255，默认约等于 0.72
    # 背景图
    "bg_image": "",
    "bg_fit": "cover",          # cover / contain / stretch / tile
    "bg_alpha": 255,            # 0-255
    # 框选区域边框
    "region_enabled": True,
    "region_color": "#4da3ff",
    "region_width": 2,
    "region_label": True,
}

BG_FITS = [
    ("cover", "铺满（裁掉多余部分）"),
    ("contain", "完整显示（留边）"),
    ("stretch", "拉伸填满（会变形）"),
    ("tile", "平铺"),
]

# 预设。用户点一下就能整体换配色，再在下面微调。
PRESETS: Dict[str, Dict[str, object]] = {
    "深色（默认）": {},
    "浅色": {
        "panel_color": "#f2f4f7",
        "panel_alpha": 232,
        "content_color": "#ffffff",
        "content_alpha": 240,
        "accent": "#1f6fd0",
        "text": "#1b1f24",
        "muted": "#5b636d",
        "border": "#ffffff",
        "border_alpha": 220,
        "region_color": "#d0342c",
    },
    "午夜蓝": {
        "panel_color": "#0b1c33",
        "panel_alpha": 92,
        "content_color": "#08152a",
        "content_alpha": 232,
        "accent": "#3d8bfd",
        "text": "#dfe9f7",
        "muted": "#9fb3cc",
        "border": "#8fc4ff",
        "border_alpha": 170,
        "region_color": "#57d9ff",
    },
    "暗紫": {
        "panel_color": "#1b1230",
        "panel_alpha": 92,
        "content_color": "#140d24",
        "content_alpha": 234,
        "accent": "#8b5cf6",
        "text": "#ece7fb",
        "muted": "#b6a9d6",
        "border": "#c4b5fd",
        "border_alpha": 170,
        "region_color": "#c084fc",
    },
    "护眼绿": {
        "panel_color": "#16241c",
        "panel_alpha": 100,
        "content_color": "#101b14",
        "content_alpha": 232,
        "accent": "#35a06a",
        "text": "#e2efe6",
        "muted": "#a8c2b2",
        "border": "#9fe0b8",
        "border_alpha": 150,
        "region_color": "#4ade80",
    },
}


# --------------------------------------------------------------------- 颜色工具
def hex_to_rgb(value: str, fallback: Tuple[int, int, int] = (255, 255, 255)
               ) -> Tuple[int, int, int]:
    """把 #rgb / #rrggbb / rrggbb 解析成三元组，失败返回 fallback。"""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return fallback
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return fallback


def _clamp_byte(value, fallback: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(0, min(255, number))


def qcolor(value: str, alpha: int = 255) -> QColor:
    red, green, blue = hex_to_rgb(value)
    color = QColor(red, green, blue)
    color.setAlpha(_clamp_byte(alpha, 255))
    return color


def rgba(value: str, alpha01: float) -> str:
    """给 QSS 用的 rgba(...) 字符串，alpha01 是 0-1。"""
    red, green, blue = hex_to_rgb(value)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha01)):.3f})"


def _ink_overlay(value: str, alpha01: float) -> str:
    """在底色上叠一层对比较强的白或黑，用于按钮/胶囊的半透明填充。

    深色底叠白、浅色底叠黑 —— 否则浅色主题里按钮会看不见。
    """
    ink = "0, 0, 0" if is_light(value) else "255, 255, 255"
    return f"rgba({ink}, {alpha01:.3f})"


def is_light(value: str) -> bool:
    """按相对亮度判断是不是浅色，决定叠加层用黑还是白。"""
    red, green, blue = hex_to_rgb(value)
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0
    return luminance > 0.5


def mix(value: str, other: str = "#ffffff", ratio: float = 0.5) -> str:
    """把两种颜色按比例混合，返回 #rrggbb。"""
    ar, ag, ab = hex_to_rgb(value)
    br, bg, bb = hex_to_rgb(other)
    ratio = max(0.0, min(1.0, ratio))
    return "#{:02x}{:02x}{:02x}".format(
        int(round(ar + (br - ar) * ratio)),
        int(round(ag + (bg - ag) * ratio)),
        int(round(ab + (bb - ab) * ratio)),
    )


# --------------------------------------------------------------------- 主题读取
def theme(config=None) -> Dict[str, object]:
    """把配置里的 ``theme`` 字典跟默认值合并、并把数值字段夹到合法范围。

    三种入参都认：

    * ``Config`` 对象（读它的 ``theme`` 键）
    * ``{"theme": {...}}`` 这种包了一层的字典
    * **直接一张平铺的主题字典** —— 设置对话框的实时预览就是这么传的。
      这一条必须认，否则预览会静默退回默认配色（实测踩过：调颜色时窗口不变）。
    """
    stored: Dict[str, object] = {}
    if isinstance(config, dict):
        nested = config.get("theme")
        if isinstance(nested, dict):
            stored = nested
        else:
            stored = {key: value for key, value in config.items() if key in DEFAULTS}
    elif config is not None and hasattr(config, "get"):
        nested = config.get("theme")
        if isinstance(nested, dict):
            stored = nested
    merged = dict(DEFAULTS)
    for key, value in stored.items():
        if key in DEFAULTS:
            merged[key] = value
    for key, fallback in (("panel_alpha", 56), ("content_alpha", 235),
                          ("bg_alpha", 255), ("border_alpha", 184),
                          ("region_width", 2)):
        merged[key] = _clamp_byte(merged.get(key), fallback)
    merged["region_width"] = max(1, min(10, int(merged["region_width"])))
    merged["region_enabled"] = bool(merged["region_enabled"])
    merged["region_label"] = bool(merged["region_label"])
    if merged.get("bg_fit") not in {key for key, _ in BG_FITS}:
        merged["bg_fit"] = "cover"
    return merged


def apply_preset(stored: Dict[str, object], name: str) -> Dict[str, object]:
    """把预设整体套到已有设置上（返回新字典，不改原对象）。"""
    result = dict(stored or {})
    preset = PRESETS.get(name)
    if preset is not None:
        result.update(preset)
    return result


# --------------------------------------------------------------------- 样式表
_QSS = Template("""
#panelRoot {
    background: $panel_bg;
    border: 1px solid $border;
    border-radius: 10px;
}
/* 注意：属性名不能叫 maximized —— 那是 QWidget 自带的内建属性，setProperty
   会去调它的内建 setter，写入被静默丢弃，样式表永远匹配不到（实测 property()
   读回始终是 False）。换成一个自定义动态属性名才生效。

   另外：样式表里的注释只能用斜杠星号成对包裹，**不能用井号开头**。井号
   在 CSS 里是 ID 选择器，解析器会把它当成一个畸形选择器，并丢掉它之后
   的整段样式表 —— 实测这一条曾让 #titleBar QLabel / QLabel#pill /
   QPushButton 全部失效，界面退回系统浅色主题。 */
#panelRoot[winMaximized="true"] { border-radius: 0px; }
#titleBar QLabel { color: $text; }
#titleLabel { font-size: 13px; font-weight: 600; letter-spacing: 0.3px; }
#hintLabel { color: $muted; font-size: 11px; }
QLabel#pill {
    padding: 1px 8px; border-radius: 8px; font-size: 11px;
    background: $pill_bg; color: $text;
}
QLabel#pillOn {
    padding: 1px 8px; border-radius: 8px; font-size: 11px;
    background: rgba(60, 190, 120, 0.26); color: #7bf0ad;
}
QLabel#pillBusy {
    padding: 1px 8px; border-radius: 8px; font-size: 11px;
    background: rgba(240, 180, 94, 0.24); color: #f7c877;
}
QLabel#pillErr {
    padding: 1px 8px; border-radius: 8px; font-size: 11px;
    background: rgba(240, 120, 110, 0.26); color: #ffb0a8;
}
QPushButton {
    background: $btn_bg;
    color: $text;
    border: 1px solid $btn_border;
    border-radius: 7px; padding: 4px 12px; font-size: 12px;
}
QPushButton:hover { background: $btn_hover; border-color: $btn_border_hover; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton#primary {
    background: $accent_bg;
    border-color: $accent_border;
    color: $accent_text; font-weight: 600;
}
QPushButton#primary:hover { background: $accent_bg_hover; }
QPushButton#caption {
    background: transparent; border: none; padding: 2px 8px;
    border-radius: 6px; font-size: 13px; color: $muted;
}
QPushButton#caption:hover { background: $btn_hover; }
QPushButton#winBtn {
    background: transparent; border: none; border-radius: 6px;
    padding: 0px; font-size: 13px; color: $text;
}
QPushButton#winBtn:hover { background: $btn_hover; }
QPushButton#winClose {
    background: transparent; border: none; border-radius: 6px;
    padding: 0px; font-size: 13px; color: $text;
}
QPushButton#winClose:hover { background: rgba(232, 72, 62, 0.85); color: #ffffff; }
/* QTextBrowser 的底色与文字颜色在代码里用调色板设置（见 _make_text_view）：
   Qt 样式表染不到 QAbstractScrollArea 的 viewport，靠样式表会露出默认白底。 */
QTextBrowser {
    border: 1px solid $line;
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: $accent;
}
/* QSplitter 的分割条不在这里上色。
   实测：面板级样式表里的 QSplitter::handle 规则会被静默丢弃
   （无论写 rgba 还是 #RRGGBB、有没有 :horizontal、后面有没有别的规则，读回
   都是调色板的白底 RGB(243,243,243)，面板正中一条 4px 白缝）；
   而把同一条规则用 setStyleSheet 直接设在 handle 控件上就立刻生效。
   所以分割条的样式在 _style_splitter_handles() 里单独设置。 */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $scroll; border-radius: 4px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $scroll; border-radius: 4px; min-width: 24px;
}
/* QTabWidget：默认样式会画一条不透明的 tab 底槽，跟磨砂玻璃打架。
   这里把底槽和分隔线都去掉，只留下文字，选中项用强调色下划线标出。 */
QTabWidget::pane { border: none; background: transparent; }
QTabWidget::tab-bar { left: 0; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; color: $muted;
    padding: 5px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent; }
QTabBar::tab:hover:!selected { color: $text; }
#statusBar { color: $muted; font-size: 11px; }
""")


def panel_style(settings: Dict[str, object]) -> str:
    """按主题生成面板样式表。"""
    settings = settings or DEFAULTS
    panel = str(settings.get("panel_color") or DEFAULTS["panel_color"])
    accent = str(settings.get("accent") or DEFAULTS["accent"])
    border = str(settings.get("border") or DEFAULTS["border"])
    text = str(settings.get("text") or DEFAULTS["text"])
    muted = str(settings.get("muted") or DEFAULTS["muted"])
    panel_alpha = _clamp_byte(settings.get("panel_alpha"), 56)
    border_alpha = _clamp_byte(settings.get("border_alpha"), 184)
    light = is_light(panel)
    return _QSS.substitute(
        panel_bg=rgba(panel, panel_alpha / 255.0),
        border=rgba(border, border_alpha / 255.0),
        text=text,
        muted=muted,
        pill_bg=_ink_overlay(panel, 0.16),
        btn_bg=_ink_overlay(panel, 0.13),
        btn_hover=_ink_overlay(panel, 0.22),
        btn_pressed=_ink_overlay(panel, 0.09),
        btn_border=_ink_overlay(panel, 0.30),
        btn_border_hover=_ink_overlay(panel, 0.50),
        line=_ink_overlay(panel, 0.22),
        scroll=_ink_overlay(panel, 0.28),
        scroll_hover=_ink_overlay(panel, 0.42),
        accent=accent,
        accent_bg=rgba(accent, 0.72),
        accent_bg_hover=rgba(mix(accent, "#ffffff", 0.18), 0.82),
        accent_border=rgba(mix(accent, "#ffffff", 0.55), 0.72),
        # 浅色主题下按钮里的文字用白色，深色主题也用白色 —— 强调色本身够深
        accent_text="#ffffff" if not light else "#ffffff",
    )


def splitter_handle_style(settings: Dict[str, object]) -> str:
    """分割条样式。必须直接设在 handle 控件上才生效（原因见上面的注释）。"""
    panel = str((settings or {}).get("panel_color") or DEFAULTS["panel_color"])
    return f"QSplitterHandle {{ background: {_ink_overlay(panel, 0.16)}; }}"


def acrylic_tint(settings: Dict[str, object]) -> int:
    """把面板底色换算成 win11 亚克力用的 0xAABBGGRR 色值。"""
    panel = str((settings or {}).get("panel_color") or DEFAULTS["panel_color"])
    red, green, blue = hex_to_rgb(panel)
    # 亚克力的 alpha 越高越不透明；用面板底色决定整体色调，
    # 再留一点透明度让底下的壁纸透出来一点，磨砂感才明显。
    return (0xE8 << 24) | (blue << 16) | (green << 8) | red


def dark_title_bar(settings: Dict[str, object]) -> bool:
    """浅色主题用亮色标题栏，深色主题用暗色（也影响亚克力渲染）。"""
    panel = str((settings or {}).get("panel_color") or DEFAULTS["panel_color"])
    return not is_light(panel)


# --------------------------------------------------------------------- 背景图
_image_cache: Dict[str, Tuple[float, QImage]] = {}


def load_image(path: str) -> Optional[QImage]:
    """读背景图（带一次缓存，文件改动后自动失效）。"""
    if not path:
        return None
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    cached = _image_cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    image = QImage(path)
    if image.isNull():
        return None
    _image_cache.clear()          # 只缓存一张，避免多张大图堆内存
    _image_cache[path] = (stamp, image)
    return image


def draw_background(painter: QPainter, rect: QRect,
                    settings: Dict[str, object]) -> bool:
    """把背景图画到 rect 里；没有图或全透明时返回 False。"""
    settings = settings or DEFAULTS
    image = load_image(str(settings.get("bg_image") or ""))
    if image is None or rect.isEmpty():
        return False
    alpha = _clamp_byte(settings.get("bg_alpha"), 255)
    if alpha <= 0:
        return False
    fit = str(settings.get("bg_fit") or "cover")
    painter.save()
    painter.setOpacity(alpha / 255.0)
    if fit == "tile":
        painter.fillRect(rect, QBrush(image))
    elif fit == "stretch":
        painter.drawImage(rect, image)
    elif fit == "contain":
        scaled = image.scaled(
            rect.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(
            rect.left() + (rect.width() - scaled.width()) // 2,
            rect.top() + (rect.height() - scaled.height()) // 2,
            scaled,
        )
    else:  # cover
        scaled = image.scaled(
            rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        source = QRect(
            max(0, (scaled.width() - rect.width()) // 2),
            max(0, (scaled.height() - rect.height()) // 2),
            min(scaled.width(), rect.width()),
            min(scaled.height(), rect.height()),
        )
        painter.drawImage(rect, scaled, source)
    painter.restore()
    return True
