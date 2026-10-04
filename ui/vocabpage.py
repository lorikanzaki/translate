"""生词本页：看收录的词、标掌握、导出 CSV / Anki 卡片。"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.vocab import VocabBook
from ui import theme as theme_mod

COLUMNS = ["单词", "音标", "释义", "考试", "来源句子", "查询", "掌握"]


class VocabPage(QWidget):
    """生词本页。"""

    statusMessage = Signal(str)
    changed = Signal()

    def __init__(self, vocab: VocabBook, config=None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.vocab = vocab
        self.config = config
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.summary = QLabel("")
        self.summary.setObjectName("hintLabel")
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.clicked.connect(self.export_csv)
        self.btn_anki = QPushButton("导出 Anki 卡片")
        self.btn_anki.setToolTip("导出成 Anki 可以直接导入的 CSV（正面=单词，"
                                 "背面=音标+释义+例句）")
        self.btn_anki.clicked.connect(self.export_anki)
        self.btn_mastered = QPushButton("标记已掌握")
        self.btn_mastered.clicked.connect(self._toggle_mastered)
        self.btn_remove = QPushButton("删除")
        self.btn_remove.clicked.connect(self._remove_selected)

        bar.addWidget(self.summary, 1)
        for button in (self.btn_csv, self.btn_anki, self.btn_mastered,
                       self.btn_remove):
            bar.addWidget(button)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 260)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 300)
        self.table.setColumnWidth(5, 60)
        layout.addWidget(self.table, 1)

        self._apply_theme()
        self.refresh()

    # ------------------------------------------------------------ 外观
    def set_theme(self, theme: dict) -> None:
        self._theme = dict(theme or {})
        self._apply_theme()

    def _apply_theme(self) -> None:
        """给表格一个自己的底色。

        背景图一亮（浅色天空之类），透明底表格里的黑字就直接糊在图上了，
        完全读不出来。词典页的正文视图一直有底色，表格这里也得有。
        """
        theme = getattr(self, "_theme", None) or {}
        content = theme_mod.rgba(
            str(theme.get("content_color") or "#0e1a26"),
            int(theme.get("content_alpha") or 200) / 255.0)
        line = theme_mod.rgba(str(theme.get("border") or "#3a4450"), 0.45)
        text = str(theme.get("text") or "#e8eef5")
        muted = str(theme.get("muted") or "#8b939c")
        accent = str(theme.get("accent") or "#4da3ff")
        self.table.setStyleSheet(
            "QTableWidget {"
            f" background: {content}; color: {text};"
            f" border: 1px solid {line}; border-radius: 8px;"
            " gridline-color: " + line + "; }"
            # 表头也要有不透明底色：只写 transparent 的话，QHeaderView 的
            # viewport 仍然按系统色画一条浅灰带，表头文字直接看不见。
            f" QHeaderView {{ background: {content}; border: none; }}"
            " QHeaderView::section {"
            f" background: {content}; color: {muted}; border: none;"
            f" border-bottom: 1px solid {line}; padding: 5px 6px; }}"
            f" QTableCornerButton::section {{ background: {content}; border: none; }}"
            " QTableWidget::item { padding: 3px 6px; }"
            " QTableWidget::item:selected {"
            f" background: {theme_mod.rgba(accent, 0.32)}; color: {text}; }}"
        )

    # ------------------------------------------------------------ 内容
    def refresh(self) -> None:
        items = self.vocab.items()
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [
                item.word, item.phonetic, item.meaning, item.exam_text,
                item.sentence, f"查 {item.lookups} / 见 {item.seen}",
                "✓" if item.mastered else "",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.word)
                self.table.setItem(row, column, cell)
        unmastered = sum(1 for item in items if not item.mastered)
        self.summary.setText(f"共 {len(items)} 条，其中 {unmastered} 条还没掌握")
        self.changed.emit()

    def selected_word(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        cell = self.table.item(row, 0)
        return cell.text() if cell is not None else ""

    # ------------------------------------------------------------ 操作
    def _toggle_mastered(self) -> None:
        word = self.selected_word()
        if not word:
            self.statusMessage.emit("先选中一行")
            return
        item = self.vocab.get(word)
        if item is None:
            return
        self.vocab.update(word, mastered=not item.mastered)
        self.refresh()
        self.statusMessage.emit(f"「{word}」标记为"
                                f"{'已掌握' if not item.mastered else '未掌握'}")

    def _remove_selected(self) -> None:
        word = self.selected_word()
        if not word:
            self.statusMessage.emit("先选中一行")
            return
        self.vocab.remove(word)
        self.refresh()
        self.statusMessage.emit(f"已从生词本删掉「{word}」")

    def _ask_path(self, default_name: str, file_filter: str) -> str:
        start = os.path.join(os.path.expanduser("~"), "Desktop", default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出", start, file_filter)
        return path

    def export_csv(self) -> None:
        if not self.vocab.count():
            self.statusMessage.emit("生词本还是空的")
            return
        path = self._ask_path("生词本.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        count = self.vocab.export_csv(path)
        self.statusMessage.emit(f"已导出 {count} 条到 {path}")
        QMessageBox.information(self, "导出完成",
                                f"已导出 {count} 条到：\n{path}")

    def export_anki(self) -> None:
        if not self.vocab.count():
            self.statusMessage.emit("生词本还是空的")
            return
        path = self._ask_path("生词本_anki.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        count = self.vocab.export_anki(path)
        self.statusMessage.emit(f"已导出 {count} 张 Anki 卡片到 {path}")
        QMessageBox.information(
            self, "导出完成",
            f"已导出 {count} 张卡片到：\n{path}\n\n"
            "在 Anki 里：文件 → 导入 → 选这个文件 → 字段对应 "
            "Word / Back，然后导入。")
