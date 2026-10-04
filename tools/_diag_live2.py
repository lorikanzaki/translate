"""验证两件事：①区域里全是中文时面板会列出识别到的原文；
②画面完全静止时，强制刷新仍会持续推进（不再卡死）。"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _sandbox import install  # noqa: E402


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    import main as app_main

    config = install()
    region = config.get("region")
    print(f"region={region} refresh_ms={config.get('refresh_ms')} "
          f"threshold={config.get('change_threshold')}")
    if not region:
        return 1

    instance = app_main.TranslatorApp()
    app = instance.app
    pump(app, 1.0)
    instance.panel.move(700, 60)
    instance.toggle(True)

    for i in range(3):
        pump(app, 3.0)
        print(f"[{i}] frames={instance._frame_count} busy={instance._busy}")
        print("    原文=", instance.panel.source_view.toPlainText()
              .replace("\n", " | ")[:120])
        print("    译文=", instance.panel.target_view.toPlainText()
              .replace("\n", " | ")[:120])
        print("    状态=", instance.panel.status_label.text())

    # 静止画面：不再动任何窗口，看强制刷新能不能继续推进 frames
    before = instance._frame_count
    time.sleep(0.8)
    pump(app, 12.0)
    after = instance._frame_count
    print(f"静止 12 秒：frames {before} -> {after}（应当至少 +2，"
          f"证明强制刷新在兜底）")

    try:
        from _winpix import grab_window_rgb

        image = grab_window_rgb(int(instance.panel.winId()))
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "shot_live_skip.png")
        image.save(path)
        print(f"截图已存 {path}")
    except Exception as exc:
        print(f"截图失败: {exc}")

    print(f"迁移后 change_threshold={config.get('change_threshold')} "
          f"refresh_ms={config.get('refresh_ms')}")

    instance.timer.stop()
    instance.panel.close()
    instance.shutdown()
    config.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
