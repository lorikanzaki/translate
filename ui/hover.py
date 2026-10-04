"""鼠标悬停取词浮窗：鼠标停在屏幕上的英文单词上时弹出词条卡片。

刻意做成**不接受焦点、不激活**的顶层窗口（`WindowDoesNotAcceptFocus` +
`WA_ShowWithoutActivating`），否则鼠标一移到浮窗上，桌面上正在用的那个窗口
就失焦了，用户会明显感觉到「一悬停就抢焦点」。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSizeF, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QTextBrowser, QVBoxLayout, QWidget

from core.dictdb import DictEntry
from core.vocab import VocabBook
from ui import dicthtml
from ui import theme as theme_mod

MARGIN = 14


class HoverCard(QWidget):
    """悬停时弹出的词条小卡片。"""

    vocabToggled = Signal(str)

    def __init__(self, config, dictionary, vocab: VocabBook,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(None)
        self.config = config
        self.dictionary = dictionary
        self.vocab = vocab
        self._theme = theme_mod.theme(config)
        self.word = ""
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._build()
        self.hide()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("hoverRoot")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(11, 9, 11, 9)
        self.view = QTextBrowser()
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.view)
        outer.addWidget(self.frame)
        self._apply_theme()

    def _apply_theme(self) -> None:
        theme = self._theme
        background = theme_mod.qcolor(str(theme.get("panel_color") or "#121f2e"),
                                      max(200, int(theme.get("panel_alpha") or 235)))
        text = theme_mod.qcolor(str(theme.get("text") or "#e8eef5"))
        border = theme_mod.qcolor(str(theme.get("border") or "#3a4450"))
        self.frame.setStyleSheet(
            "#hoverRoot {"
            f" background: rgba({background.red()},{background.green()},"
            f"{background.blue()},{background.alpha()});"
            f" border: 1px solid rgba({border.red()},{border.green()},"
            f"{border.blue()},{border.alpha()});"
            " border-radius: 8px; }"
        )
        size = max(9, int(self.config.get("dict_hover_font_size") or 10))
        font = self.view.font()
        font.setPointSize(size)
        self.view.setFont(font)
        self.view.setStyleSheet("QTextBrowser { background: transparent; border: none;"
                                f" color: rgba({text.red()},{text.green()},"
                                f"{text.blue()},{text.alpha()}); }}")
        self.view.document().setDefaultStyleSheet("p { margin: 0 0 3px 0; }")

    def set_theme(self, theme: dict) -> None:
        self._theme = theme
        self._apply_theme()

    # ------------------------------------------------------------ 显示
    def show_entry(self, entry: DictEntry, sentence: str = "",
                   anchor: Optional[QRect] = None) -> None:
        self.word = entry.word
        if sentence:
            entry.senses = entry.ordered_senses(sentence)
        self.view.setHtml(dicthtml.entry_html(
            entry, self._theme,
            sentence=dicthtml.sentence_of(sentence, entry.word) if sentence else "",
            in_vocab=self.vocab.has(entry.word),
            compact=True,
        ))
        width = max(220, min(360, int(self.config.get("dict_hover_width") or 280)))
        # 量高度不能自己拍一个宽度去量，也不能在窗口显示之前量：文本视图真正的
        # 排版宽度要等窗口显示、布局跑完才定下来（实测显示前 viewport 是 640、
        # 显示后才是 256）。宽度估错会少折一行，最后一行就被裁掉
        # （「1. 名词 安装者」就是这么被切的）。所以先摆出来、再用**实际
        # viewport 宽度**量文档高度，量完立刻改成最终高度。中间那次高度不会被
        # 用户看到：Qt 要等控制权回到事件循环才重绘。
        document = self.view.document()
        self.setFixedSize(width, 200)
        self.show()
        layout = self.frame.layout()
        if layout is not None:
            layout.activate()
        viewport_width = self.view.viewport().width()
        if viewport_width <= 0:
            viewport_width = width - 24
        document.setPageSize(QSizeF(viewport_width, -1))
        document.adjustSize()
        # 文档高度不含最后一段的下外边距，再留几个像素余量。
        height = int(document.size().height()) + 24
        self.setFixedSize(width, max(70, min(height, 340)))
        self._place(anchor)
        self.raise_()

    def _place(self, anchor: Optional[QRect]) -> None:
        """优先放在锚点右边；右边不够就放左边；上下都夹在屏幕内。"""
        screen = QGuiApplication.screenAt(
            anchor.center() if anchor else self.pos()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        if anchor is None:
            self.move(QGuiApplication.primaryScreen().geometry().center())
            return
        x = anchor.right() + MARGIN
        if x + self.width() > available.right():
            x = anchor.left() - MARGIN - self.width()
        if x < available.left():
            x = available.left() + 8
        y = anchor.top()
        if y + self.height() > available.bottom():
            y = available.bottom() - self.height() - 8
        y = max(available.top() + 8, y)
        self.move(QPoint(x, y))

    def hide_card(self) -> None:
        self.word = ""
        self.hide()

    # ------------------------------------------------------------ 交互
    def _on_anchor(self, url) -> None:
        text = url.toString()
        if text.startswith("vocab:"):
            self.vocabToggled.emit(text[len("vocab:"):])
            self.hide_card()
