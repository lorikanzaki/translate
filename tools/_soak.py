"""稳定性压测：连续跑 3 分钟，内容不断变化，看实时循环会不会中途死掉。"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

ROUNDS = [
    ["File", "Edit", "View", "Export project as PDF"],
    ["Sign in to continue", "Email address", "Forgot your password?"],
    ["Update available", "Restart now", "Remind me later"],
    ["Connection lost", "Retry", "Work offline"],
]


class Target(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.FramelessWindowHint)
        self.round = 0

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 12))
        painter.setPen(QColor("#101010"))
        y = 40
        for line in ROUNDS[self.round % len(ROUNDS)]:
            painter.drawText(20, y, line)
            y += 60
        painter.drawText(20, y, f"round {self.round}")


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    import main as app_main
    from core.config import get_config

    config = get_config()
    region = config.get("region")
    if not region:
        print("no region")
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

    last_ok = 0
    failures = 0
    deadline = time.perf_counter() + 180.0
    step = 0
    while time.perf_counter() < deadline:
        step += 1
        target.round = step
        target.update()
        pump(app, 2.0)
        dst = instance.panel.target_view.toPlainText().strip()
        if dst:
            last_ok = step
        else:
            failures += 1
        if step % 10 == 0:
            print(f"t={step * 2}s frames={instance._frame_count} "
                  f"busy={instance._busy} 最近成功步={last_ok} 空步数={failures} "
                  f"译文={dst[:60]!r}", flush=True)

    print(f"压测结束：frames={instance._frame_count} 总步数={step} "
          f"空步数={failures}", flush=True)
    instance.timer.stop()
    instance.panel.close()
    instance.shutdown()
    config.save()
    target.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
