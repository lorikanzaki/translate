"""诊断：框选边框窗口会不会干扰抓屏 + OCR（模拟真实使用）。

流程：放一个白底黑字的英文窗口 → 在它上面盖一圈框选边框 → 抓屏比对
「有边框 / 没边框」两种情况下 OCR 拿到的文本，并把抓到的图存下来看。
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from core.capture import grab_region  # noqa: E402
from core.ocr import OcrEngine  # noqa: E402
from ui import theme as theme_mod  # noqa: E402
from ui.regionbox import RegionFrame  # noqa: E402

LINES = [
    "Download failed",
    "Are you sure you want to delete this file?",
    "The installer was unable to write to the selected directory.",
    "Settings",
]


class Target(QWidget):
    """一块白底黑字的假界面，用来当被翻译的对象。"""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Target")
        self.setStyleSheet("background: #ffffff;")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        font = QFont("Segoe UI", 11)
        painter.setFont(font)
        painter.setPen(QColor("#000000"))
        y = 30
        for line in LINES:
            painter.drawText(16, y, line)
            y += 44


def main() -> int:
    app = QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio() or 1.0)
    print(f"screen={screen.geometry().getRect()} dpr={dpr}")

    target = Target()
    target.setGeometry(200, 200, 640, 220)
    target.show()
    target.raise_()
    target.activateWindow()
    app.processEvents()
    time.sleep(1.2)

    g = target.geometry()   # 逻辑像素
    region = (int(g.x() * dpr), int(g.y() * dpr),
              int(g.width() * dpr), int(g.height() * dpr))
    print(f"target logical={g.getRect()}  region(physical)={region}")

    engine = OcrEngine(use_dml=True)

    def shot(tag: str):
        frame = grab_region(region)
        if frame is None:
            print(f"[{tag}] 抓屏失败")
            return None
        from PIL import Image
        Image.fromarray(frame).save(f"tools/_diag_{tag}.png")
        result = engine.run(frame)
        texts = [b.text for b in result.blocks]
        print(f"[{tag}] OCR {len(texts)} 段 (backend={result.backend}, "
              f"{result.elapsed_ms:.0f}ms): {texts}")
        return frame

    print("\n--- 1) 不盖边框 ---")
    shot("noframe")

    print("\n--- 2) 盖上框选边框 ---")
    frame = RegionFrame()
    frame.apply_theme(theme_mod.theme(None))
    frame.set_region(region)
    app.processEvents()
    time.sleep(0.6)
    print(f"    frame visible={frame.isVisible()} geometry={frame.geometry().getRect()}")
    shot("frame")

    print("\n--- 3) 边框改成红色 6px + 标签 ---")
    values = dict(theme_mod.theme(None))
    values.update({"region_color": "#ff0000", "region_width": 6, "region_label": True})
    frame.apply_theme(values)
    app.processEvents()
    time.sleep(0.6)
    shot("frame_red")

    frame.clear()
    target.close()
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
