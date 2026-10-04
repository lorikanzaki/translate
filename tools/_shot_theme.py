"""给「自定义配色 + 背景图 + 框选边框」留目视验收截图。

跑真实的 TranslatorApp（注入合成英文界面图），临时套一套自定义主题
（深色面板 + 暖色强调 + 一张生成的渐变背景图），并让框选边框显形，
然后用 Win32 BitBlt 抓**屏幕像素**存图。

用法：
    $env:QT_QPA_PLATFORM="windows"
    .venv\\Scripts\\python.exe tools\\_shot_theme.py

跑完会把用户真实的 region / theme 配置原样还原。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "windows"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from _winpix import sample_pixels  # noqa: E402
from test_smoke import synthetic_frame  # noqa: E402

THEME = {
    "preset": "自定义",
    "panel_color": "#1b1430",
    "panel_alpha": 214,
    "content_color": "#241a3d",
    "content_alpha": 236,
    "accent_color": "#e0762a",
    "text_color": "#f6f1e8",
    "muted_color": "#b0a6c4",
    "border_color": "#f4e2c8",
    "border_alpha": 210,
    "bg_image": "",
    "bg_fit": "cover",
    "bg_alpha": 190,
    "glass": True,
    "region_enabled": True,
    "region_color": "#ff9d3c",
    "region_width": 3,
    "region_label": True,
}


def make_background() -> str:
    """生成一张渐变 + 光斑的背景图，用来肉眼判断"壁纸确实铺上去了"。"""
    path = os.path.join(tempfile.gettempdir(), "dsh_theme_demo_bg.png")
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(int(24 + 90 * ratio), int(18 + 40 * ratio), int(52 + 120 * ratio)),
        )
    for cx, cy, radius, color in (
        (260, 220, 300, (240, 140, 60)),
        (1250, 760, 420, (70, 150, 210)),
        (900, 180, 220, (200, 90, 150)),
    ):
        for step in range(radius, 0, -12):
            alpha = (radius - step) / radius
            blend = tuple(
                int(channel * alpha + 30 * (1 - alpha)) for channel in color
            )
            draw.ellipse(
                [cx - step, cy - step, cx + step, cy + step],
                fill=blend,
            )
    image.save(path)
    return path


def main() -> int:
    import main as app_main

    from _sandbox import install

    install()  # 别让窗口几何写回用户真配置
    from ui import theme as theme_mod

    app = app_main.TranslatorApp()

    saved_region = app.config.get("region")
    saved_theme = app.config.get("theme")
    bg_path = make_background()
    theme_values = dict(THEME)
    theme_values["bg_image"] = bg_path

    frame = synthetic_frame()
    region = [120, 120, frame.shape[1], frame.shape[0]]
    app.config.set("region", region)
    app.config.set("theme", theme_values)
    app_main.grab_region = lambda rect: frame

    app.panel.apply_appearance()
    app.region_frame.apply_theme(theme_mod.theme({"theme": theme_values}))
    app.region_frame.set_region(region)
    app._report_region()

    state = {"frames": 0}
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
    app.panel.resize(900, 620)
    app.panel.move(90, 150)

    done = {"v": False}

    def maybe_shot() -> None:
        if done["v"] or not state["frames"] or app._busy:
            return
        done["v"] = True
        QTimer.singleShot(1500, do_shot)

    def do_shot() -> None:
        for widget, name in ((app.panel, "shot_theme_panel.png"),
                             (app.region_frame, "shot_region_frame.png")):
            hwnd = int(widget.winId())
            if not hwnd:
                print(f"[shot] {name}: 没有原生窗口句柄，跳过")
                continue
            _, image = sample_pixels(hwnd, [])
            out = os.path.join(ROOT, "tools", name)
            image.save(out)
            print(f"[shot] 已保存 {out}  尺寸={image.size}  "
                  f"geometry={widget.geometry().getRect()}")
        print(f"[shot] 译文={app.panel.target_view.toPlainText()!r}")
        print(f"[shot] 状态栏={app.panel.status_label.text()!r}")
        print(f"[glass] {app.panel.enable_glass(True)}")
        app.app.quit()

    probe = QTimer()
    probe.setInterval(300)
    probe.timeout.connect(maybe_shot)
    probe.start()

    def give_up() -> None:
        if not done["v"]:
            print(f"[shot] 超时：frames={state['frames']} busy={app._busy}")
            app.app.quit()

    QTimer.singleShot(30000, give_up)

    code = app.app.exec()
    app.shutdown()
    app.config.set("region", saved_region)
    app.config.set("theme", saved_theme)
    app.config.save()
    print(f"[shot] 用户配置已还原 region={saved_region} theme_keys="
          f"{sorted((saved_theme or {}).keys())}")
    try:
        os.remove(bg_path)
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
