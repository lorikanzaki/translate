"""决定性检查：用用户真实的区域，把一个英文窗口放进这个区域，看面板是否刷新。

如果面板能稳定显示英文原文 + 中文译文，说明抓屏/OCR/翻译/实时循环都没坏，
用户看到「没反应」的原因是区域里没有可翻译的外语。
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from _sandbox import install  # noqa: E402

ROUNDS = [
    ["File", "Edit", "View", "Export project as PDF"],
    ["Sign in to continue", "Email address", "Forgot your password?"],
    ["Update available", "Restart now", "Remind me later"],
]

COLORS = ["#ffffff", "#f5f7fa", "#fffdf5"]


class Target(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.FramelessWindowHint)
        self.round = 0

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS[self.round % len(COLORS)]))
        painter.setFont(QFont("Segoe UI", 12))
        painter.setPen(QColor("#101010"))
        y = 40
        for line in ROUNDS[self.round % len(ROUNDS)]:
            painter.drawText(20, y, line)
            y += 60


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    import main as app_main
    from core.config import get_config

    config = install()
    region = config.get("region")
    print(f"用户 region = {region}")
    if not region:
        return 1

    instance = app_main.TranslatorApp()
    app = instance.app

    screen = QGuiApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio() or 1.0)
    left, top, width, height = [int(v) for v in region]

    target = Target()
    target.setGeometry(int(left / dpr), int(top / dpr),
                       int(width / dpr), int(height / dpr))
    target.show()
    target.raise_()
    pump(app, 1.0)

    instance.panel.move(700, 60)
    instance.toggle(True)

    for step in range(6):
        target.round += 1
        target.update()
        pump(app, 3.0)
        src = instance.panel.source_view.toPlainText().strip().replace("\n", " | ")
        dst = instance.panel.target_view.toPlainText().strip().replace("\n", " | ")
        print(f"[{step}] busy={instance._busy} frames={instance._frame_count}")
        print(f"      原文={src[:150]!r}")
        print(f"      译文={dst[:150]!r}")

    instance.timer.stop()
    instance.panel.close()
    instance.shutdown()
    config.save()
    target.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
