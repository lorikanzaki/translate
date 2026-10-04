"""词典页：把屏幕上的词做成词条卡片，可点星收录进生词本。

三处共用同一种卡片 HTML（见 `ui/dicthtml.py`）：面板里的「词典」页、
悬停取词浮窗、独立词典窗口。
"""
from __future__ import annotations

import html
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
    QWidget,
)

from core.dictdb import DictEntry, Dictionary
from core.vocab import VocabBook
from ui import dicthtml
from ui import theme as theme_mod

MAX_CARDS = 10


class DictPage(QWidget):
    """「词典」页。"""

    vocabToggled = Signal(str)
    openWindowRequested = Signal()
    statusMessage = Signal(str)

    def __init__(self, config, dictionary: Dictionary, vocab: VocabBook,
                 theme: Optional[dict] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.dictionary = dictionary
        self.vocab = vocab
        self._theme = theme or theme_mod.theme(config)
        self._entries: List[DictEntry] = []
        self._sentence = ""
        self._render_key: object = None
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("查一个单词（回车）")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._on_search)
        self.btn_search = QPushButton("查词")
        self.btn_search.clicked.connect(self._on_search)
        self.btn_vocab = QPushButton("生词本")
        self.btn_vocab.setToolTip("打开生词本（收录、导出 CSV / Anki）")
        self.btn_vocab.clicked.connect(self._on_vocab)
        self.btn_window = QPushButton("独立窗口")
        self.btn_window.setToolTip("把词典单独开一个窗口，可以一直摆在旁边")
        self.btn_window.clicked.connect(self.openWindowRequested.emit)

        bar.addWidget(self.search, 1)
        bar.addWidget(self.btn_search)
        bar.addWidget(self.btn_vocab)
        bar.addWidget(self.btn_window)
        layout.addLayout(bar)

        self.hint = QLabel("")
        self.hint.setObjectName("hintLabel")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.view, 1)

        self._apply_theme()

    def set_theme(self, theme: dict) -> None:
        self._theme = theme
        self._apply_theme()
        self.render()

    def _apply_theme(self) -> None:
        theme = self._theme
        # 正文必须自己有底色。背景图一亮（比如浅色天空），透明底的浅色文字
        # 就完全看不见了 —— 翻译页的正文视图一直是这么处理的，词典页照抄。
        content = theme_mod.rgba(
            str(theme.get("content_color") or "#0e1a26"),
            int(theme.get("content_alpha") or 200) / 255.0)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {content}; border: none;"
            f" border-radius: 8px; color: {theme.get('text') or '#e8eef5'};"
            f" padding: 8px 10px; }}"
        )
        size = int(self.config.get("panel_font_size") or 12)
        font = self.view.font()
        font.setPointSize(size)
        self.view.setFont(font)
        self.view.document().setDefaultStyleSheet(
            "p { margin: 0 0 4px 0; line-height: 150%; } a { text-decoration: none; }"
        )

    # ------------------------------------------------------------ 内容
    @property
    def count(self) -> int:
        return len(self._entries)

    def set_screen(self, text: str, entries: List[DictEntry]) -> None:
        """换一屏内容：`entries` 是这一屏里查得到的词。"""
        key = (text, tuple(entry.word for entry in entries))
        self._entries = entries
        self._sentence = text or ""
        if key == self._render_key:
            return          # 内容没变就别重渲染，免得打断用户选中文字
        self._render_key = key
        self.render()

    def show_word(self, word: str, sentence: str = "") -> bool:
        """查一个词并显示它的卡片。找到返回 True。"""
        entry = self.dictionary.lookup(word)
        if entry is None:
            self._entries = []
            self._render_key = None
            self.hint.setText(f"词典里没有「{word}」"
                              + ("" if self.dictionary.available
                                 else "（本地词典还没建好）"))
            self.render()
            return False
        if sentence:
            entry.senses = entry.ordered_senses(sentence)
        self._sentence = sentence
        self._entries = [entry]
        self._render_key = None
        self.hint.setText("")
        self.render()
        return True

    def render(self) -> None:
        theme = self._theme
        muted = f"color:{theme.get('muted') or '#8b939c'};font-size:92%"
        if not self.dictionary.available:
            self.view.setHtml(
                f'<p style="{muted}">本地词典还没建好。到「设置 → 词典」点一下'
                f"「下载并建立词典库」，第一次约 66 MB，之后离线可用。</p>")
            return
        if not self._entries:
            self.view.setHtml(
                f'<p style="{muted}">这一屏没有查到词典里收录的英文单词。</p>')
            return
        blocks = []
        for entry in self._entries[:MAX_CARDS]:
            blocks.append(dicthtml.entry_html(
                entry, theme,
                sentence=dicthtml.sentence_of(self._sentence, entry.word)
                if len(self._entries) == 1 else "",
                in_vocab=self.vocab.has(entry.word),
            ))
            blocks.append('<hr style="border:none;border-top:1px solid '
                          + (theme.get("border") or "#3a4450")
                          + ';margin:10px 0">')
        self.view.setHtml("".join(blocks))

    def words(self) -> List[str]:
        return [entry.word for entry in self._entries]

    # ------------------------------------------------------------ 交互
    def _on_search(self) -> None:
        word = self.search.text().strip()
        if not word:
            return
        if not self.show_word(word):
            self.statusMessage.emit(f"词典里没有「{word}」")

    def _on_vocab(self) -> None:
        self.statusMessage.emit(f"生词本共 {self.vocab.count()} 条")

    # ------------------------------------------------------------ 生词本
    def toggle_vocab(self, word: str) -> bool:
        """把 `word` 收录进生词本 / 从生词本里删掉。返回「现在在生词本里吗」。"""
        if not word:
            return False
        if self.vocab.has(word):
            self.vocab.remove(word)
            self.statusMessage.emit(f"已从生词本删掉「{word}」")
            added = False
        else:
            entry = next((item for item in self._entries if item.word == word), None)
            if entry is None:
                entry = self.dictionary.lookup(word)
            if entry is None:
                self.statusMessage.emit(f"词典里没有「{word}」，无法收录")
                added = False
            else:
                sentence = dicthtml.sentence_of(self._sentence, word)
                self.vocab.add(
                    word=word,
                    meaning=dicthtml.vocab_meaning(entry, sentence),
                    pos=(entry.pos_mix[0][0] if entry.pos_mix else ""),
                    phonetic=entry.phonetic,
                    exam=" ".join(entry.tags),
                    sentence=sentence,
                )
                self.statusMessage.emit(f"已收录「{word}」到生词本")
                added = True
        self.vocabToggled.emit(word)
        self._render_key = None
        self.render()
        return added

    def _on_anchor(self, url) -> None:
        text = url.toString()
        if text.startswith("vocab:"):
            self.toggle_vocab(text[len("vocab:"):])
        elif text.startswith("word:"):
            word = text[len("word:"):]
            self.show_word(word)
            self.search.setText(word)
