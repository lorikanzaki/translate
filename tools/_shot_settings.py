"""把设置对话框拍下来，用来目视检查「翻译」页（含新加的大模型接口段）。"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from _sandbox import install  # noqa: E402
from core.translate import Translator  # noqa: E402
from ui.settings import SettingsDialog  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    config = install()
    translator = Translator(timeout=15)
    dialog = SettingsDialog(config, translator=translator)
    dialog.move(80, 40)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()

    end = time.perf_counter() + 1.5
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)

    dialog.tabs.setCurrentIndex(0)
    from _winpix import grab_window_rgb

    base = os.path.dirname(os.path.abspath(__file__))
    dialog.raise_()
    dialog.activateWindow()
    end = time.perf_counter() + 0.6
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)
    # 用 Qt 自己渲染（不受别的窗口遮挡影响）；抓屏版本只在没被挡住时才好看
    pixmap = dialog.grab()
    pixmap.toImage().save(os.path.join(base, "shot_settings_translate.png"))
    image = grab_window_rgb(int(dialog.winId()))
    image.save(os.path.join(base, "shot_settings_translate_screen.png"))
    print(f"翻译页已存 shot_settings_translate.png  {image.size}")
    print(f"对话框尺寸 {dialog.width()}x{dialog.height()}  "
          f"（屏幕可用高度参考 816）")

    dialog.tabs.setCurrentIndex(1)
    dialog.raise_()
    dialog.activateWindow()
    end = time.perf_counter() + 1.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)
    image = grab_window_rgb(int(dialog.winId()))
    image.save(os.path.join(base, "shot_settings_appearance.png"))
    print(f"外观页已存 shot_settings_appearance.png  {image.size}")

    dialog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
