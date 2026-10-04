"""DirectML(GPU) 和 CPU 两种 OCR 推理设备的真实对比：延迟 + 内存。

像素尺寸用用户实际的框选区域（281×412）和一张更满的界面图各测一遍，
每个后端先跑 2 次热身、再跑 8 次取中位数。

用法：
    .venv\\Scripts\\python.exe tools\\_bench_ocr.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gc
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_K32 = ctypes.windll.kernel32.K32GetProcessMemoryInfo
_K32.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
_K32.restype = wt.BOOL


def working_set() -> float:
    c = PMC()
    c.cb = ctypes.sizeof(c)
    _K32(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 1048576


LINES = [
    "Download failed",
    "The installer was unable to write to the selected directory.",
    "Settings", "Export project as PDF", "Are you sure you want to delete this file?",
    "Check for updates", "Sign in to continue", "Restart required",
]


def make_image(width: int, height: int):
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    y = 12
    for line in LINES:
        if y > height - 24:
            break
        draw.text((10, y), line, fill=(0, 0, 0))
        y += 26
    import numpy as np

    return np.ascontiguousarray(np.asarray(image)[:, :, ::-1].copy())


def bench(use_dml: bool, frame) -> tuple:
    from core.ocr import OcrEngine

    before = working_set()
    engine = OcrEngine(use_dml=use_dml)
    engine.run(frame)  # 触发真正的初始化
    loaded = working_set()
    times = []
    for _ in range(8):
        t0 = time.perf_counter()
        result = engine.run(frame)
        times.append((time.perf_counter() - t0) * 1000.0)
    after = working_set()
    del engine
    gc.collect()
    freed = working_set()
    return engine_backend(use_dml), result, times, before, loaded, after, freed


def engine_backend(use_dml: bool) -> str:
    return "DirectML(GPU)" if use_dml else "CPU(onnxruntime)"


def main() -> int:
    print("=" * 84)
    print("OCR 推理设备对比（每种设备先热身 1 次，再跑 8 次取中位数）")
    print("=" * 84)
    for label, frame in (("用户实际区域 281 x 412", make_image(281, 412)),
                         ("整块界面 900 x 600", make_image(900, 600))):
        print(f"\n### {label}")
        for use_dml in (True, False):
            backend, result, times, before, loaded, after, freed = bench(use_dml, frame)
            lines = len([b for b in result.blocks if b.text.strip()])
            print(f"  {backend:<20} 中位 {statistics.median(times):6.0f} ms"
                  f" | 最快 {min(times):5.0f} | 最慢 {max(times):5.0f}"
                  f" | 识别 {lines} 行"
                  f" | 引擎加载 +{loaded - before:5.0f} MB"
                  f" | 推理后 +{after - before:5.0f} MB"
                  f" | 释放后 +{freed - before:5.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
