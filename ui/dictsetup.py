"""设置里的「词典」页：开关、悬停取词参数、以及下载并建立本地词库。

词库是 ECDICT 的 `ecdict.csv`（约 66 MB），下载完还要建成 SQLite（约 53 MB）。
两件事加起来要几分钟，而且这个下载实测会卡住，所以：

* 整个流程跑在**后台线程**里（`threading.Thread`），界面线程只负责显示进度；
* 进度用 `queue.Queue` 传出来，再靠一个 200ms 的 `QTimer` 搬到界面上 ——
  后台线程**绝对不能直接碰 Qt 控件**，否则随机崩；
* 下载支持断点续传（见 `core.dictdb.download_csv`），所以「下载失败」不是
  白下的，再点一次会从断掉的地方继续。
"""
from __future__ import annotations

import queue
import re
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import dictdb

_MB_RE = re.compile(r"已下载\s*([\d.]+)\s*MB\s*/\s*([\d.]+)\s*MB")


def _human(size: int) -> str:
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.0f} KB"
    return f"{size} 字节"


class DictSetupPanel(QWidget):
    """「词典」设置页。"""

    statusMessage = Signal(str)
    libraryChanged = Signal()

    def __init__(self, config, dictionary=None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.dictionary = dictionary
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._build()
        self._reload()
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        switch = QGroupBox("词典")
        form = QFormLayout(switch)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.enabled = QCheckBox("启用词典功能（关闭后不建词库、不查词）")
        self.hover = QCheckBox("鼠标停在屏幕上的单词上时，弹一张词条卡片")
        self.delay = QSpinBox()
        self.delay.setRange(0, 5000)
        self.delay.setSingleStep(50)
        self.delay.setSuffix(" ms")
        self.font_size = QSpinBox()
        self.font_size.setRange(7, 24)
        self.font_size.setSuffix(" pt")
        self.width = QSpinBox()
        self.width.setRange(180, 600)
        self.width.setSingleStep(20)
        self.width.setSuffix(" px")
        form.addRow("", self.enabled)
        form.addRow("", self.hover)
        form.addRow("停留多久才弹", self.delay)
        form.addRow("卡片字号", self.font_size)
        form.addRow("卡片宽度", self.width)
        hint = QLabel(
            "取词只在**框选区域内**生效（鼠标坐标要能对上 OCR 认出来的那一行）。"
            "悬停卡片不接受焦点、鼠标能穿透，移上去不会把当前窗口切成别的。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8b939c;")
        form.addRow("", hint)
        layout.addWidget(switch)

        library = QGroupBox("本地词库")
        inner = QVBoxLayout(library)
        self.status = QLabel("正在检查……")
        self.status.setWordWrap(True)
        inner.addWidget(self.status)

        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        row.addWidget(self.progress, 1)
        self.button = QPushButton("下载并建立词典库")
        self.button.clicked.connect(self.start)
        row.addWidget(self.button)
        self.rebuild = QPushButton("重建")
        self.rebuild.setToolTip("删掉现有词典库，按当前词典数据重新生成一遍")
        self.rebuild.clicked.connect(lambda: self.start(force=True))
        row.addWidget(self.rebuild)
        inner.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setStyleSheet(
            "QPlainTextEdit { background: #12151a; color: #93a0ad;"
            " border: 1px solid #2c3138; border-radius: 6px; }"
        )
        inner.addWidget(self.log)

        note = QLabel(
            "词库来自 ECDICT（MIT 许可，https://github.com/skywind3000/ECDICT），"
            "首次要下载约 63 MB 的词典数据，再建成约 53 MB 的本地库，"
            f"存放在 {dictdb.dict_dir()}。全部离线查询，不上传任何东西。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8b939c;")
        inner.addWidget(note)
        layout.addWidget(library)
        layout.addStretch(1)

    # ------------------------------------------------------------ 配置
    def _reload(self) -> None:
        self.enabled.setChecked(bool(self.config.get("dict_enabled", True)))
        self.hover.setChecked(bool(self.config.get("dict_hover", True)))
        self.delay.setValue(int(self.config.get("dict_hover_delay_ms") or 450))
        self.font_size.setValue(int(self.config.get("dict_hover_font_size") or 10))
        self.width.setValue(int(self.config.get("dict_hover_width") or 280))
        self._refresh_status()

    def values(self) -> dict:
        return {
            "dict_enabled": self.enabled.isChecked(),
            "dict_hover": self.hover.isChecked(),
            "dict_hover_delay_ms": self.delay.value(),
            "dict_hover_font_size": self.font_size.value(),
            "dict_hover_width": self.width.value(),
        }

    def _refresh_status(self) -> None:
        info = dictdb.library_status()
        if info["ready"]:
            self.status.setText(
                f"词库已就绪：<b>{info['entries']:,}</b> 条，"
                f"{_human(int(info['db_bytes']))}"
                f"（词典数据 {_human(int(info['csv_bytes']))}）"
            )
            self.button.setText("重新下载词典数据")
            self.progress.setValue(100)
        elif info["csv_bytes"]:
            self.status.setText(
                f"词典数据已下载 {_human(int(info['csv_bytes']))}，"
                f"但还没建库 —— 点右边的按钮建库（约几秒）。"
            )
            self.button.setText("建立词典库")
            self.progress.setValue(0)
        else:
            self.status.setText(
                "还没有本地词库。点右边的按钮下载并建立"
                "（约 63 MB，网速不稳时要几分钟，中途卡住再点一次会续传）。"
            )
            self.button.setText("下载并建立词典库")
            self.progress.setValue(0)
        self.rebuild.setEnabled(bool(info["csv_bytes"]))

    # ------------------------------------------------------------ 下载
    def start(self, force: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.statusMessage.emit("词典正在下载，请等它跑完")
            return
        self.progress.setValue(0)
        self.log.clear()
        self.button.setEnabled(False)
        self.rebuild.setEnabled(False)
        self._log("开始：下载词典数据（支持断点续传）")
        self._thread = threading.Thread(
            target=self._worker, args=(force,), daemon=True)
        self._thread.start()

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _worker(self, force: bool) -> None:
        """后台线程：下载 + 建库。只往队列里塞字符串，不碰任何控件。"""
        def report(message: str) -> None:
            self._queue.put(message)

        try:
            report("下载词典数据……")
            dictdb.download_csv(progress=report, force=force)
            report("开始建库……")
            entries, forms = dictdb.build_db(progress=report, force=True)
            report(f"完成：收录 {entries} 条，词形 {forms} 条")
            self._queue.put("__done__")
        except Exception as exc:  # noqa: BLE001 - 网络什么都可能抛
            self._queue.put(f"失败：{type(exc).__name__}: {exc}")
            self._queue.put("__done__")

    def _drain(self) -> None:
        done = False
        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                break
            if message == "__done__":
                done = True
                continue
            self._log(message)
            match = _MB_RE.search(message)
            if match:
                got = float(match.group(1))
                self.progress.setValue(min(99, int(got * 100 / 63.0)))
        if done:
            self.button.setEnabled(True)
            self._refresh_status()
            self.libraryChanged.emit()
            self.statusMessage.emit("词典库已更新")
