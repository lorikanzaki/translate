"""给「窗口实体化」改造留一张目视验收截图。

跑真实的 TranslatorApp（合成英文界面图，不碰用户真实框选区域），
等面板真的显示出译文后，用 Win32 BitBlt 抓窗口所在的**屏幕像素**
存成 tools\\shot_demo.png —— 抓屏幕而不是 QWidget.grab()，因为
grab() 会把透明区域画成白色，判读配色会误导。

用法：
    $env:QT_QPA_PLATFORM="windows"
    .venv\\Scripts\\python.exe tools\\_shot_demo.py
"""
from __future__ import annotations

import os
import sys
import time

# 必须在导入 Qt 之前设置：这里要的是真实窗口，不是 offscreen
os.environ["QT_QPA_PLATFORM"] = "windows"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PySide6.QtCore import QTimer  # noqa: E402

from _winpix import sample_pixels  # noqa: E402
from test_smoke import synthetic_frame  # noqa: E402


def main() -> int:
    from core.capture import grab_region
    import main as app_main

    from _sandbox import install

    install()  # 别让窗口几何写回用户真配置

    app = app_main.TranslatorApp()
    saved_region = app.config.get("region")
    frame = synthetic_frame()
    app.config.set("region", [0, 0, frame.shape[1], frame.shape[0]])
    app_main.grab_region = lambda rect: frame  # 注入合成帧

    state = {"frames": 0, "shot": None}
    original_drain = app._drain_results

    def counting_drain() -> None:
        before = app.worker.results.qsize()
        original_drain()
        if before:
            state["frames"] += 1

    app.result_timer.timeout.disconnect()
    app.result_timer.timeout.connect(counting_drain)

    app.panel.show()
    app.toggle(True)
    # 恢复成默认大小，截图里能看到完整的标题栏 / 正文 / 状态栏
    app.panel.resize(900, 620)
    app.panel.move(120, 90)

    deadline = time.time() + 30.0
    shot_done = {"v": False}

    def maybe_shot() -> None:
        if shot_done["v"] or not state["frames"] or app._busy:
            return
        # 再等 1.2 秒让译文渲染完、让玻璃有时间稳定
        shot_done["v"] = True
        QTimer.singleShot(1200, do_shot)

    def do_shot() -> None:
        hwnd = int(app.panel.winId())
        _, image = sample_pixels(hwnd, [])
        out = os.path.join(ROOT, "tools", "shot_demo.png")
        image.save(out)
        print(f"[shot] 已保存 {out}  尺寸={image.size}")
        print(f"[shot] 原文={app.panel.source_view.toPlainText()!r}")
        print(f"[shot] 译文={app.panel.target_view.toPlainText()!r}")
        print(f"[shot] 状态栏={app.panel.status_label.text()!r}")
        app.app.quit()

    probe = QTimer()
    probe.setInterval(300)
    probe.timeout.connect(maybe_shot)
    probe.start()

    def give_up() -> None:
        if not shot_done["v"]:
            print(f"[shot] 超时：frames={state['frames']} busy={app._busy}")
            app.app.quit()

    QTimer.singleShot(int(max(1.0, deadline - time.time()) * 1000), give_up)

    code = app.app.exec()
    app.shutdown()
    app.config.set("region", saved_region)
    app.config.save()
    print(f"[shot] 用户区域已还原：{saved_region}")
    return code


if __name__ == "__main__":
    sys.exit(main())
