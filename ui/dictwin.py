"""独立的词典窗口：左「词典」右「生词本」，可以一直摆在屏幕角落。

跟主面板一样用无边框窗口 + 自绘标题栏，配色跟随同一套主题。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QFrame, QTabWidget, QVBoxLayout, QWidget,
)

from core.dictdb import Dictionary
from core.vocab import VocabBook
from ui import theme as theme_mod
from ui.dictpage import DictPage
from ui.frameless import FramelessWindow
from ui.vocabpage import VocabPage


class DictWindow(FramelessWindow):
    """词典 + 生词本的独立窗口。"""

    vocabChanged = Signal()

    def __init__(self, config, dictionary: Dictionary, vocab: VocabBook) -> None:
        super().__init__(None)
        self.config = config
        self.dictionary = dictionary
        self.vocab = vocab
        self._theme = theme_mod.theme(config)
        self.setMinimumSize(520, 360)
        self.resize(760, 560)
        self.setStyleSheet(theme_mod.panel_style(self._theme))
        self._build()
        self._restore_geometry()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = QFrame(self)
        root.setObjectName("panelRoot")
        self.root_frame = root
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(30)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        title = QLabel("词典")
        title.setObjectName("titleLabel")
        bar_layout.addWidget(title)
        bar_layout.addStretch(1)
        for text, tip, slot, name in (
            ("—", "最小化", self.showMinimized, "winBtn"),
            ("□", "最大化 / 还原", self.toggle_maximize, "winBtn"),
            ("✕", "关闭窗口", self.close, "winClose"),
        ):
            button = QPushButton(text)
            button.setObjectName(name)
            button.setToolTip(tip)
            button.setFixedSize(32, 24)
            button.setFlat(True)
            button.clicked.connect(slot)
            bar_layout.addWidget(button)
        layout.addWidget(bar)

        self.tabs = QTabWidget()
        self.page_dict = DictPage(self.config, self.dictionary, self.vocab,
                                  theme=self._theme)
        self.page_vocab = VocabPage(self.vocab, self.config)
        self.page_vocab.set_theme(self._theme)
        self.tabs.addTab(self.page_dict, "词典")
        self.tabs.addTab(self.page_vocab, "生词本")
        layout.addWidget(self.tabs, 1)

        self.status = QLabel("把鼠标停在屏幕上的英文单词上，或在上面输入要查的词")
        self.status.setObjectName("statusBar")
        layout.addWidget(self.status)

        self.page_dict.statusMessage.connect(self.status.setText)
        self.page_vocab.statusMessage.connect(self.status.setText)
        self.page_vocab.changed.connect(self.vocabChanged.emit)
        self.tabs.currentChanged.connect(self._on_tab)

    def _on_tab(self, index: int) -> None:
        if index == 1:
            self.page_vocab.refresh()

    # ------------------------------------------------------------ 外观
    def _corner_radius(self) -> int:
        return 0 if self.is_maximized() else 10

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._corner_radius(),
                            self._corner_radius())
        painter.setClipPath(path)
        theme_mod.draw_background(painter, self.rect(), self._theme)
        painter.end()
        super().paintEvent(event)

    def apply_appearance(self) -> None:
        self._theme = theme_mod.theme(self.config)
        self.setStyleSheet(theme_mod.panel_style(self._theme))
        self.page_dict.set_theme(self._theme)
        self.page_vocab.set_theme(self._theme)
        self.update()

    # ------------------------------------------------------------ 位置
    def _restore_geometry(self) -> None:
        stored = self.config.get("dictwin_geometry")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        if isinstance(stored, list) and len(stored) == 4:
            rect = QRect(*[int(v) for v in stored])
            if rect.intersects(available):
                self.setGeometry(rect)
                return
        self.move(available.left() + 60, available.top() + 80)

    def _save_geometry(self) -> None:
        if self.is_maximized():
            return
        geo = self.geometry()
        self.config.set("dictwin_geometry",
                        [geo.x(), geo.y(), geo.width(), geo.height()])

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        super().closeEvent(event)

    # ------------------------------------------------------------ 内容
    def show_screen(self, text: str, entries) -> None:
        self.page_dict.set_screen(text, entries)
        self.tabs.setCurrentIndex(0)

    def show_word(self, word: str, sentence: str = "") -> bool:
        ok = self.page_dict.show_word(word, sentence)
        self.show()
        self.raise_()
        self.activateWindow()
        return ok
