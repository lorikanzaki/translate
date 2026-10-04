"""诊断：用用户真实配置的区域跑一遍，逐步记录差异值 / OCR / 译文。

不做任何伪造：region 直接取 %APPDATA%\\ScreenTranslator\\config.json 里的值，
回答「为什么屏幕上内容在变、面板却不刷新」。
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from _sandbox import install  # noqa: E402


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    import core.capture as capture_mod
    import main as app_main
    from core.config import get_config

    config = install()
    region = config.get("region")
    print(f"用户 region = {region}")
    if not region:
        print("配置里没有区域，先让用户框选一次再诊断")
        return 1

    threshold = float(config.get("change_threshold") or 0.004)
    interval = int(config.get("interval_ms") or 1500)
    print(f"interval_ms={interval} change_threshold={threshold}")
    print(f"hide_cjk_blocks={config.get('hide_cjk_blocks')} "
          f"min_score={config.get('ocr_min_score')} "
          f"max_side={config.get('ocr_max_side')} "
          f"source_lang={config.get('source_lang')}")

    logs = {"diffs": [], "ticks": 0}

    def traced_grab(rel):
        got = capture_mod.grab_region(rel)
        logs["ticks"] += 1
        if got is not None and logs.get("saved", 0) < 3:
            from PIL import Image
            logs["saved"] = logs.get("saved", 0) + 1
            Image.fromarray(got).save(f"tools/_diag_user_frame_{logs['saved']}.png")
        if got is not None and logs.get("last") is not None:
            logs["diffs"].append(
                round(capture_mod.mean_abs_diff(got, logs["last"]), 5))
        if got is not None:
            logs["last"] = got
        return got

    app_main.grab_region = traced_grab

    instance = app_main.TranslatorApp()
    app = instance.app

    original_on_result = instance._on_result

    def traced_result(result):
        texts = [getattr(b, "text", "") for b in getattr(result, "blocks", [])]
        print(f"    -> 结果 {len(texts)} 段 provider={result.provider} "
              f"错误={result.error!r}")
        print(f"       原文={texts[:8]}")
        print(f"       译文={(result.translations or [])[:8]}")
        original_on_result(result)

    instance._on_result = traced_result

    instance.toggle(True)
    for step in range(12):
        pump(app, 2.0)
        print(f"[{step}] busy={instance._busy} frames={instance._frame_count} "
              f"grab_calls={logs['ticks']} 最近差异={logs['diffs'][-5:]}")
        src = instance.panel.source_view.toPlainText().strip().replace("\n", " | ")
        dst = instance.panel.target_view.toPlainText().strip().replace("\n", " | ")
        print(f"      面板原文={src[:160]!r}")
        print(f"      面板译文={dst[:160]!r}")

    instance.timer.stop()
    instance.panel.close()
    instance.shutdown()
    config.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
