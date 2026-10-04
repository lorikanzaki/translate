"""内存体检：分阶段打印本进程的工作集（Working Set）与私有字节，找出内存大头。

用法：
    .venv\\Scripts\\python.exe tools\\_mem_report.py            # 全流程（含联网翻译）
    .venv\\Scripts\\python.exe tools\\_mem_report.py nolive     # 跳过联网翻译

只读，不改任何配置。跑的是**真实**的 core / ui 代码路径（真 OCR 引擎、
真词典、真面板），所以数字就是程序运行时的数字。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gc
import os
import sys
import time
from _sandbox import install  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_K32 = ctypes.windll.kernel32.K32GetProcessMemoryInfo
# 必须显式声明 argtypes：不声明的话 64 位句柄会被 ctypes 截成 int，
# 调用静默失败、所有字段读出来都是 0（踩过一次）。
_K32.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wt.DWORD]
_K32.restype = wt.BOOL


def mem() -> tuple:
    """返回 (WorkingSet MB, PrivateUsage MB, PeakWorkingSet MB)。"""
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    _K32(ctypes.windll.kernel32.GetCurrentProcess(),
         ctypes.byref(counters), counters.cb)
    mb = 1024 * 1024
    return (
        counters.WorkingSetSize / mb,
        counters.PrivateUsage / mb,
        counters.PeakWorkingSetSize / mb,
    )


_START = mem()
_LAST = [0.0]


def stage(label: str) -> None:
    gc.collect()
    ws, priv, peak = mem()
    delta = ws - _LAST[0] if _LAST[0] else ws - _START[0]
    _LAST[0] = ws
    print(f"{ws:8.1f} MB 工作集 | {priv:8.1f} MB 私有 | "
          f"峰值 {peak:7.1f} MB | 本步 {delta:+7.1f} MB | {label}")


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    print("=" * 96)
    print("屏幕实时翻译 —— 内存体检（数值为当前进程的工作集 / 私有字节）")
    print(f"参数：{sys.argv[1:] or '（默认：DirectML + 联网翻译）'}")
    print("=" * 96)
    stage("解释器刚起来")

    import numpy as np

    stage("import numpy")

    import PySide6
    from PySide6.QtWidgets import QApplication

    stage(f"import PySide6（{PySide6.__version__}）")

    from core.config import device_uses_dml, get_config
    from core.dictdb import Dictionary
    from core.scripts import primary_script  # noqa: F401
    from core.translate import Translator
    from core.vocab import VocabBook

    config = install()
    stage("import core 全部模块（含 requests / sqlite3）")

    stage("import 完毕（还没有 QApplication）")

    import main as app_main

    instance = app_main.TranslatorApp()
    app = instance.app
    pump(app, 0.6)
    stage("创建 TranslatorApp（面板 + 框选边框 + 热键 + 托盘）")

    translator = instance.translator
    stage(f"翻译器就绪（术语表 {len(getattr(translator, 'phrasebook', {}) or {})} 条）")

    from core.worker import PipelineSettings, TranslateWorker

    worker = TranslateWorker(translator)
    force_cpu = "cpu" in sys.argv
    settings = PipelineSettings(
        source_lang=str(config.get("source_lang") or ""),
        target_lang=str(config.get("target_lang") or "zh-CN"),
        min_score=float(config.get("ocr_min_score") or 0.5),
        max_side=int(config.get("ocr_max_side") or 1600),
        use_dml=device_uses_dml(config) and not force_cpu,
        use_cls=bool(config.get("ocr_use_cls", False)),
        filter_cjk=bool(config.get("hide_cjk_blocks", True)),
    )
    stage("创建 TranslateWorker（还没建 OCR 引擎）")

    engine = worker._ensure_engine(settings)
    stage(f"加载 OCR 引擎（{getattr(engine, 'backend', '?')}）")

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 260), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((16, 20), "Download failed", fill=(0, 0, 0))
    draw.text((16, 60), "The installer was unable to write to the selected directory.",
              fill=(0, 0, 0))
    draw.text((16, 100), "Settings", fill=(0, 0, 0))
    frame = np.asarray(img)[:, :, ::-1].copy()
    frame = np.ascontiguousarray(frame)

    t0 = time.perf_counter()
    worker.push(frame, settings)
    result = None
    for _ in range(200):
        app.processEvents()
        got = worker.drain()
        if got:
            result = got[-1][1]
            break
        time.sleep(0.05)
    stage(f"第一次 OCR 识别完（{(time.perf_counter() - t0) * 1000:.0f} ms）")

    if result is not None and getattr(result, "lines", None):
        print(f"          识别到 {len(result.lines)} 行：{result.lines[:3]}")

    print(f"          词典可用={instance.dictionary.available} "
          f"收录 {instance.dictionary.count():,} 条")
    stage("词典已建立连接并查过一次")

    if "nolive" not in sys.argv:
        outcome = translator.translate(
            ["Download failed", "Settings", "The installer was unable to write "
             "to the selected directory."],
            target="zh-CN",
        )
        print(f"          译文：{list(outcome.translations)}"
              f"（{outcome.provider} {outcome.elapsed_ms:.0f} ms）")
        stage("联网翻译一批文本")

    stage("再跑一轮 OCR + 翻译（模拟实时循环）")
    worker.push(frame, settings)
    for _ in range(200):
        app.processEvents()
        if worker.drain():
            break
        time.sleep(0.05)
    stage("实时循环跑完")

    print("-" * 96)
    ws, priv, peak = mem()
    print(f"最终：工作集 {ws:.1f} MB，私有 {priv:.1f} MB，峰值 {peak:.1f} MB")

    instance.timer.stop()
    instance.panel.close()
    try:
        instance.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
