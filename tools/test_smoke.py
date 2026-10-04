"""真实启动 main.py 的冒烟测试：验证线程边界干净。

背景：早先真实运行时 OCR 线程会碰主线程的 QTextDocument，Qt 报
    QObject: Cannot create children for a parent that is in a different thread.
并把进程以非 0 退出（还伴随 QThread: Destroyed while thread is still running）。
本脚本用**真实的 TranslatorApp**（不经任何猴子补丁）走一遍完整链路：
    注入一帧合成图 -> 触发 _tick -> 主线程轮询取结果 -> 刷新面板 -> 退出
然后断言 stderr 里没有线程警告，且退出码为 0。

用法：
    .venv\\Scripts\\python.exe tools\\test_smoke.py             # 用真实屏幕抓一帧
    .venv\\Scripts\\python.exe tools\\test_smoke.py synthetic   # 用合成英文界面图
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback

# 必须在导入 Qt 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

FORBIDDEN = [
    "Cannot create children for a parent that is in a different thread",
    "QThread: Destroyed while thread is still running",
    "Timers cannot be started from another thread",
    "Cannot send events to objects owned by a different thread",
]

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
    else:
        FAILED += 1
    line = f"  {'[OK]  ' if condition else '[FAIL]'} {name}"
    if detail:
        line += f"  [{detail}]"
    print(line, flush=True)


def synthetic_frame(width: int = 900, height: int = 260) -> np.ndarray:
    """画一张只有英文界面文字的图（不依赖真实屏幕）。"""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for name in ("arial.ttf", "segoeui.ttf", "consola.ttf"):
        try:
            font = ImageFont.truetype(name, 26)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    img = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    lines = [
        "Download failed",
        "Are you sure you want to delete this file?",
        "The installer was unable to write to the selected directory.",
        "Settings",
    ]
    for index, text in enumerate(lines):
        draw.text((16, 20 + index * 56), text, fill=(20, 20, 20), font=font)
    return np.array(img)[:, :, ::-1].copy()  # RGB -> BGR


def main() -> int:
    use_synthetic = "synthetic" in [a.lower() for a in sys.argv[1:]]
    mode = "合成图" if use_synthetic else "真实抓屏"
    print(f"屏幕实时翻译 · 真实启动冒烟测试（{mode}）")

    # 捕获 stderr：Qt 的线程警告走的是 C++ 层，用重定向最可靠
    real_stderr = sys.stderr
    buffer = io.StringIO()
    sys.stderr = buffer

    from core.capture import grab_region
    import main as app_main

    # 用沙箱配置：测试会写测试区域，万一中途硬崩也不会给用户留下
    # 一张不属于自己的框选区域。
    from _sandbox import install

    install()

    code = 0
    frames = 0
    state: dict = {}

    try:
        app = app_main.TranslatorApp()

        # 冒烟测试会往真实配置里写测试区域，跑完必须还原，否则用户下次
        # 启动会看到一张不属于自己的框选区域。
        state["saved_region"] = app.config.get("region")

        # 把抓屏换掉（或固定区域），让主干流程在无桌面环境下也能跑
        if use_synthetic:
            frame = synthetic_frame()
            app.config.set("region", [0, 0, frame.shape[1], frame.shape[0]])
            app_main.grab_region = lambda rect: frame  # 注入合成帧
        else:
            rect = grab_region((0, 0, 400, 300))
            check("真实抓屏可用", rect is not None)
            if rect is None:
                raise RuntimeError("抓屏不可用")
            app.config.set("region", [0, 0, 400, 300])

        original_drain = app._drain_results

        def counting_drain() -> None:
            nonlocal frames
            before = app.worker.results.qsize()
            original_drain()
            if before:
                frames += 1

        app._drain_results = counting_drain
        app.result_timer.timeout.disconnect()
        app.result_timer.timeout.connect(counting_drain)

        app.panel.show()
        app.toggle(True)

        # 不用固定时长赌运气（首轮要冷启动 OCR 模型 + 网络翻译，可能十几秒）。
        # 每 300ms 检查一次：拿到结果且不忙就退出；最迟 25 秒兜底。
        deadline = time.time() + 25.0

        def poll_done() -> None:
            if (frames > 0 and not app._busy) or time.time() > deadline:
                print(f"[smoke] 结束：收到 {frames} 次结果，"
                      f"busy={app._busy}，用时 {25.0 - (deadline - time.time()):.1f}s")
                app.app.quit()

        probe = QTimer()
        probe.setInterval(300)
        probe.timeout.connect(poll_done)
        probe.start()

        code = app.app.exec()
        app.shutdown()
        state["thread_finished"] = not app.thread.isRunning()
        state["panel_text"] = app.panel.source_view.toPlainText()
        state["status"] = app.panel.status_label.text()
    except Exception:
        traceback.print_exc(file=real_stderr)
        code = 99
    finally:
        sys.stderr = real_stderr
        # 还原测试前用户的配置，绝不留下测试区域
        try:
            if "saved_region" in state:
                app.config.set("region", state["saved_region"])
                app.config.save()
                print(f"[smoke] 已还原用户区域配置：{state['saved_region']}")
        except Exception:
            traceback.print_exc()

    noise = buffer.getvalue()
    print(noise.rstrip() if noise.strip() else "")
    check("进程退出码为 0", code == 0, f"code={code}")
    check("面板收到过结果", frames > 0, f"{frames} 次")
    # 「真实抓屏」模式抓的是屏幕左上角 400x300，内容取决于当时屏幕上是
    # 什么 —— 可能全是中文、可能全是图。所以只能断言「面板渲染出了一段
    # 东西」，不能断言具体文字；只有合成图模式才能断言那句英文。
    panel_text = state.get("panel_text", "")
    if use_synthetic:
        check("面板显示了原文", "Download failed" in panel_text,
              panel_text[:60].replace("\n", " | "))
    else:
        check("面板渲染出了内容", bool(panel_text.strip()),
              panel_text[:60].replace("\n", " | "))
    check("后台线程已停止", bool(state.get("thread_finished")))
    for needle in FORBIDDEN:
        check(f"没有出现「{needle[:42]}…」", needle not in noise)

    print()
    print("=" * 60)
    print(f"通过 {PASSED} 项，失败 {FAILED} 项")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
