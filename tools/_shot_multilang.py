"""多语言互译的目视验收：真窗口 + 真 OCR + 真翻译，抓两张面板图。

    .venv\\Scripts\\python.exe tools\\_shot_multilang.py

存 tools\\shot_multilang_ja2zh.png（日语界面 -> 中文）
与 tools\\shot_multilang_zh2en.png（中文界面 -> 英语）。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication  # noqa: E402

from _sandbox import install  # noqa: E402
from core.translate import Translator  # noqa: E402
from core.worker import PipelineSettings, TranslateWorker  # noqa: E402
from tools._probe_ocr_langs import render  # noqa: E402
from ui.panel import MainPanel  # noqa: E402

CASES = [
    ("shot_multilang_ja2zh.png",
     "日语界面 → 中文",
     "設定\nダウンロードに失敗しました\n保存できませんでした",
     "YuGothR.ttc", "ja", "zh-CN"),
    ("shot_multilang_zh2en.png",
     "中文界面 → 英语",
     "设置\n下载失败\n无法保存文件",
     "msyh.ttc", "zh-CN", "en"),
]


def save(window, path: str) -> None:
    from _winpix import grab_window_rgb

    image = grab_window_rgb(int(window.winId()))
    image.save(path)
    print(f"  已存 {path}  {image.size}")


def main() -> int:
    app = QApplication(sys.argv)
    config = install()
    translator = Translator(timeout=15)
    worker = TranslateWorker(translator)

    panel = MainPanel(config, translator)
    panel.resize(760, 620)
    panel.move(80, 40)
    panel.show()
    panel.raise_()
    panel.activateWindow()

    def pump(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(1.2)

    for filename, title, text, font, source_lang, target in CASES:
        print(f"\n=== {title} ===")
        image = render(text, font)
        settings = PipelineSettings(target_lang=target, source_lang=source_lang,
                                    use_dml=True, max_side=1600)
        worker._handle(image, settings)
        results = [payload for kind, payload in worker.drain() if kind == "result"]
        if not results:
            print("  没有结果")
            continue
        result = results[-1]
        print(f"  识别模型={result.ocr_model}  provider={result.provider}")
        for src, dst in zip(result.lines, result.translations):
            print(f"    {src!r}  ->  {dst!r}")
        panel.set_region_text(f"{title}   区域 900×260 @ (0, 0)")
        panel.update_result(result)
        pump(0.8)
        save(panel, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))

    panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
