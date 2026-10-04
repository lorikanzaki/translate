"""诊断：端到端跑真实 TranslatorApp（真屏幕抓取 + 真 OCR + 真翻译）。

放一个白底黑字的英文窗口当被翻译对象，把 config 的 region 指到它上面，
启动翻译，定时打印面板里的原文/译文/状态，看链路上是哪一环断了。

注意：QApplication 是单例，必须先让 TranslatorApp 自己建，再建 Target 窗口。
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

LINES = [
    "Download failed",
    "Are you sure you want to delete this file?",
    "The installer was unable to write to the selected directory.",
    "Settings",
]

# 另一屏完全不同的内容，用来制造「大变化」，验证重跑 OCR 的路径
LINES_B = [
    "Export project as PDF",
    "Choose a folder to store the exported file",
    "Include bookmarks and annotations",
    "Cancel          Export",
]


class Target(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Target")
        self.setStyleSheet("background: #ffffff;")
        self.counter = 0
        self.flip = False

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 11))
        painter.setPen(QColor("#000000"))
        lines = LINES_B if self.flip else LINES
        y = 30
        for line in lines:
            painter.drawText(16, y, line)
            y += 44
        painter.drawText(16, y, f"Round {self.counter} - please wait")


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    import main as app_main
    from core.config import get_config

    config = install()
    saved = config.get("region")
    saved_geom = config.get("panel_geometry")
    config.set("region", None)

    instance = app_main.TranslatorApp()
    app = instance.app

    target = Target()
    target.setGeometry(200, 200, 640, 220)
    target.show()
    target.raise_()
    target.activateWindow()
    pump(app, 1.2)

    screen = QGuiApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio() or 1.0)
    g = target.geometry()
    region = [int(g.x() * dpr), int(g.y() * dpr),
              int(g.width() * dpr), int(g.height() * dpr)]
    print(f"region={region} dpr={dpr} target_visible={target.isVisible()}")
    config.set("region", region)

    instance.panel.move(40, 470)
    instance._report_region()

    # 给抓屏加计数，看 _tick 到底跑了几次、每帧的差异有多大
    import core.capture as capture_mod
    stats = {"calls": 0, "diffs": [], "last": None}

    def traced_grab(region):
        got = capture_mod.grab_region(region)
        stats["calls"] += 1
        if got is not None and stats["last"] is not None:
            stats["diffs"].append(round(capture_mod.mean_abs_diff(got, stats["last"]), 5))
        if got is not None:
            stats["last"] = got
        return got

    app_main.grab_region = traced_grab

    instance.toggle(True)
    pump(app, 0.3)
    print(f"running={instance._running} timer_active={instance.timer.isActive()} "
          f"interval={instance.timer.interval()}")
    print(f"region_frame visible={instance.region_frame.isVisible()} "
          f"geom={instance.region_frame.geometry().getRect()}")

    for step in range(8):
        target.counter += 1
        target.flip = not target.flip
        target.update()
        pump(app, 2.0)
        src = instance.panel.source_view.toPlainText().strip().replace("\n", " | ")
        dst = instance.panel.target_view.toPlainText().strip().replace("\n", " | ")
        print(f"[{step}] busy={instance._busy} frames={instance._frame_count} "
              f"grab_calls={stats['calls']} diffs={stats['diffs'][-6:]} "
              f"status={instance.panel.status_label.text()!r}")
        print(f"      原文={src[:140]!r}")
        print(f"      译文={dst[:140]!r}")

    instance.timer.stop()
    instance.panel.close()
    instance.shutdown()
    config.set("region", saved)
    config.set("panel_geometry", saved_geom)
    config.save()
    target.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
