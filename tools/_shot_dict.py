"""把词典功能抓成图，供人目视验收。

    .venv\\Scripts\\python.exe tools\\_shot_dict.py

产出四张图：
  shot_dict_panel.png     面板「词典」页（词条卡片 + 生词本入口）
  shot_dict_hover.png     鼠标悬停取词的浮窗
  shot_dict_window.png     独立词典窗口
  shot_dict_vocab.png      生词本页

截图统一用 `widget.grab()`（Qt 自己渲染），**不看别的窗口脸色** ——
后台常驻着网易云音乐之类的窗口，用抓屏 API 会拍到它们挡在前面的样子。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import Config  # noqa: E402
from core.dictdb import Dictionary  # noqa: E402
from core.translate import Translator  # noqa: E402
from core.vocab import VocabBook  # noqa: E402
from core.worker import PipelineResult  # noqa: E402
from _sandbox import sandbox_config  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SCREEN = (
    "Please select the Export project as PDF option.\n"
    "The installer was unable to write to the selected directory.\n"
    "Download failed. Check your network settings and try again.\n"
    "Restart required to apply the changes.\n"
)


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name: str) -> str:
    """抓图并存盘。

    `widget.grab()` 拿到的是**带透明通道**的图，直接存 PNG 的话半透明的
    面板底色不会和任何东西合成，看起来比真实窗口亮一大截（浅色壁纸下
    几乎全白）。所以先垫一层深灰底再画上去，接近实际观感。
    """
    pixmap = widget.grab()
    canvas = QPixmap(pixmap.size())
    canvas.fill(QColor("#1b1e24"))
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    path = os.path.join(BASE, name)
    canvas.save(path)
    print(f"已存 {name}  {widget.width()}×{widget.height()}")
    return path


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # 沙箱配置：显示窗口会把几何写回配置，绝不能写用户那一份
    config = sandbox_config()
    dictionary = Dictionary()
    # 生词本也用临时文件：万一脚本中途崩了，也不会给用户留测试词
    vocab = VocabBook(os.path.join(tempfile.mkdtemp(prefix="shot-vocab-"), "vocab.jsonl"))
    print(f"词库：{dictionary.count():,} 条")

    # 用一个真的 TranslatorApp：面板、悬停浮窗、独立窗口都在它身上
    from ui.dictwin import DictWindow
    from ui.hover import HoverCard
    from ui.panel import MainPanel

    config.set("region", None)
    panel = MainPanel(config, translator=Translator(timeout=5),
                      dictionary=dictionary, vocab=vocab)
    panel.resize(920, 620)
    panel.show()
    pump(app, 0.5)

    result = PipelineResult(
        lines=[line for line in SCREEN.strip().split("\n")],
        translations=["（翻译略，这里只看词典）"] * 4,
        blocks=[],
        ocr_ms=182.0, translate_ms=0.0, total_ms=182.0,
        backend="DirectML(GPU)", provider="必应翻译中文站",
    )
    panel.update_result(result)
    pump(app, 0.4)

    # 切到「词典」页
    panel.tabs.setCurrentIndex(1)
    panel.page_dict.show_word("installer",
                              "The installer was unable to write to the selected directory.")
    pump(app, 0.5)
    save(panel, "shot_dict_panel.png")

    # 悬停浮窗：把鼠标位置换算成屏幕坐标，锚在 installer 那一行上
    entry = dictionary.lookup_in_context(
        "installer", "The installer was unable to write to the selected directory.")
    if entry is not None:
        hover = HoverCard(config, dictionary, vocab)
        hover.set_theme(panel._theme)
        hover.show_entry(entry, "The installer was unable to write to the selected directory.")
        pump(app, 0.6)
        save(hover, "shot_dict_hover.png")
        hover.hide_card()
        pump(app, 0.2)

    # 生词本
    vocab.add("installer", meaning="n. 安装程序", pos="名词",
              sentence="The installer was unable to write to the selected directory.",
              sentence_translation="安装程序无法写入所选目录。")
    vocab.add("restart", meaning="n. 重启动", pos="名词",
              sentence="Restart required to apply the changes.")
    vocab.add("temporarily", meaning="adv. 暂时", pos="副词")
    vocab.update("temporarily", mastered=True)
    pump(app, 0.3)

    # 独立窗口
    dict_window = DictWindow(config, dictionary, vocab)
    dict_window.resize(760, 560)
    dict_window.show()
    dict_window.raise_()
    dict_window.activateWindow()
    pump(app, 0.8)
    dict_window.show_word("installer",
                          "The installer was unable to write to the selected directory.")
    pump(app, 0.5)
    save(dict_window, "shot_dict_window.png")

    dict_window.tabs.setCurrentIndex(1)
    dict_window.page_vocab.refresh()
    pump(app, 0.6)
    save(dict_window, "shot_dict_vocab.png")

    # 收尾：把刚才写进去的测试词删掉，别污染用户的生词本
    for word in ("installer", "restart", "temporarily"):
        vocab.remove(word)
    print(f"已清理测试生词，剩余 {vocab.count()} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
