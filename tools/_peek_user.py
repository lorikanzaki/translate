"""看一眼用户当前框选的区域里到底是什么，以及原生 OCR 读到了什么。"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.capture import grab_region  # noqa: E402
from core.config import get_config  # noqa: E402
from core.ocr import OcrEngine, looks_like_translatable  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    config = get_config()
    region = config.get("region")
    print(f"region={region}")
    if not region:
        return 1

    engine = OcrEngine(use_dml=True, use_cls=False,
                       max_side=config.get("ocr_max_side"))
    for attempt in range(3):
        frame = grab_region(tuple(region))
        if frame is None:
            print("抓屏失败")
            return 1
        from PIL import Image
        path = f"tools/_peek_user_{attempt}.png"
        Image.fromarray(frame).save(path)
        print(f"已存 {path}  shape={frame.shape}  "
              f"均值={frame.reshape(-1, 3).mean(axis=0).round(1)}")
        result = engine.run(frame)
        print(f"  原生 OCR {len(result.blocks)} 段 后端={result.backend} "
              f"{result.elapsed_ms:.0f}ms")
        for b in result.blocks:
            keep = b.score >= float(config.get("ocr_min_score") or 0.5)
            translatable = looks_like_translatable(b.text)
            print(f"    score={b.score:.3f} min_ok={keep} "
                  f"translatable={translatable} text={b.text!r}")
        time.sleep(1.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
