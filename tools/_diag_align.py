"""对位检查：应用运行时，整屏截图在区域坐标处的裁切，是否等于区域抓图。

如果两者不一致，说明 `QScreen.grabWindow(0, x, y, w, h)` 的偏移量在
某些窗口组合下不可靠 —— 那才是「识别不到屏幕文字」的真因。
"""
from __future__ import annotations

import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from _sandbox import install  # noqa: E402


def pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def to_array(pixmap):
    import numpy as np
    image = pixmap.toImage().convertToFormat(pixmap.toImage().Format.Format_RGB888)
    ptr = image.constBits()
    buf = np.frombuffer(ptr, dtype=np.uint8)
    row = image.bytesPerLine()
    arr = buf[: row * image.height()].reshape(image.height(), row)
    return arr[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def main() -> int:
    import core.capture as capture_mod
    import main as app_main
    from core.config import get_config

    config = install()
    region = config.get("region")
    print(f"region={region}")

    instance = app_main.TranslatorApp()
    app = instance.app
    instance.panel.move(40, 400)
    pump(app, 1.5)

    screen = QGuiApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio() or 1.0)
    full = screen.grabWindow(0)          # 整屏（物理像素）
    print(f"整屏 {full.width()}x{full.height()} dpr={dpr}")
    full.toImage().save("tools/_align_full.png")

    left, top, width, height = [int(v) for v in region]
    area = capture_mod.grab_region(tuple(region))
    from PIL import Image
    Image.fromarray(area).save("tools/_align_area.png")

    crop = to_array(full)[top:top + height, left:left + width]
    Image.fromarray(crop).save("tools/_align_crop.png")
    print(f"区域抓图 shape={area.shape} 整屏裁切 shape={crop.shape}")
    if crop.shape == area.shape:
        diff = capture_mod.mean_abs_diff(area, crop)
        print(f"整屏裁切 vs 区域抓图 平均绝对差 = {diff:.5f}"
              f"  （接近 0 表示坐标一致）")
        # 再横向找最佳对齐偏移
        import numpy as np
        best = None
        for shift in range(-80, 81, 2):
            if shift < 0:
                a = area[:, :shift]
                b = crop[:, -shift:]
            elif shift > 0:
                a = area[:, shift:]
                b = crop[:, : crop.shape[1] - shift]
            else:
                a, b = area, crop
            d = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
            if best is None or d < best[1]:
                best = (shift, d)
        print(f"最佳横向对齐偏移 = {best[0]} 物理像素（差值 {best[1]:.2f}）")

    instance.panel.close()
    instance.shutdown()
    config.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
