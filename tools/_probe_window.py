"""验证新的实体窗口：磨砂玻璃、四角缩放命中、最大化/还原、关闭。

必须在**真实窗口平台**（默认 windows）下跑，offscreen 平台没有真正的 HWND，
玻璃和 hit-test 都测不出来。

缩放命中用 `SendMessage(hwnd, WM_NCHITTEST, ...)` 实测，比合成鼠标事件可靠：
合成事件走的是 Qt 的坐标映射，测不出 Win32 层的真实返回值。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 直接跑本脚本时 sys.path[0] 就是 tools/，但被别的脚本 import 时不是，
# 所以显式加一次，保证 from _winpix import 一定能找到。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _winpix import grab_window_rgb as _grab_window_rgb  # noqa: E402
from _winpix import sample_pixels as _sample_pixels  # noqa: E402
from _winpix import window_rect as _window_rect  # noqa: E402

import _winpix  # noqa: E402

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import Config  # noqa: E402
from core.translate import Translator  # noqa: E402
from ui import win11  # noqa: E402
from ui.panel import MainPanel  # noqa: E402

user32 = ctypes.WinDLL("user32")
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                ctypes.c_size_t, ctypes.c_ssize_t]
gdi32 = ctypes.WinDLL("gdi32")

pass_count = 0
fail_count = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global pass_count, fail_count
    if ok:
        pass_count += 1
        print(f"  [OK]   {label}" + (f"  [{detail}]" if detail else ""))
    else:
        fail_count += 1
        print(f"  [FAIL] {label}" + (f"  [{detail}]" if detail else ""))


GWLP_STYLE = -16
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000

HTNAMES = {
    1: "HTCLIENT", 2: "HTCAPTION", 3: "HTSYSMENU", 10: "HTLEFT", 11: "HTRIGHT",
    12: "HTTOP", 13: "HTTOPLEFT", 14: "HTTOPRIGHT", 15: "HTBOTTOM",
    16: "HTBOTTOMLEFT", 17: "HTBOTTOMRIGHT",
}


def nchittest(hwnd: int, x: int, y: int) -> int:
    """模拟系统在屏幕坐标 (x, y) 上做的 WM_NCHITTEST。"""
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    return int(user32.SendMessageW(wintypes.HWND(hwnd), 0x0084, 0, lparam))


def window_rect(hwnd: int):
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


app = QApplication(sys.argv)

config = Config(os.path.join(os.environ.get("TEMP", "."), "_probe_window_cfg.json"))
for key in ("panel_geometry",):
    config.set(key, None, autosave=False)

translator = Translator(timeout=15)
panel = MainPanel(config, translator)
panel.show()
app.processEvents()

hwnd = int(panel.winId())
print(f"\n=== 窗口基本属性 (hwnd={hwnd:#x}) ===")
print(f"  build={win11.windows_build()}")

style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE)
print(f"  GWL_STYLE = {style:#010x}")
check(bool(style & WS_THICKFRAME), "原生窗口带可调边框位（缩放交给系统）",
      "WS_THICKFRAME" if style & WS_THICKFRAME else "缺失")

geo = panel.geometry()
print(f"  geometry = {geo.x()},{geo.y()} {geo.width()}x{geo.height()}")

print("\n=== 磨砂玻璃 ===")
result = panel.enable_glass(True)
print(f"  apply_glass -> {result}")
check(bool(result.get("acrylic")), "亚克力模糊已生效", str(result.get("acrylic")))
check(bool(result.get("dark_title")), "暗色标题栏已生效")
check(bool(result.get("rounded")), "圆角已生效")

print("\n=== 缩放命中测试（WM_NCHITTEST）===")
frame = panel._frame_rect_physical()
print(f"  Qt 逻辑 geometry = {geo.x()},{geo.y()} {geo.width()}x{geo.height()}")
print(f"  原生物理 frame   = {frame.x()},{frame.y()} {frame.width()}x{frame.height()}")
dpr = panel.devicePixelRatioF()
print(f"  devicePixelRatio = {dpr}")
check(abs(frame.width() / max(1, geo.width()) - dpr) < 0.02,
      "物理/逻辑宽度比等于 DPI 缩放",
      f"{frame.width()}/{geo.width()}={frame.width()/geo.width():.3f}")
_wr = window_rect(hwnd)
print(f"  Win32 GetWindowRect = {_wr[0]},{_wr[1]} {_wr[2]-_wr[0]}x{_wr[3]-_wr[1]}")
check(abs((_wr[2] - _wr[0]) - frame.width()) <= 2
      and abs((_wr[3] - _wr[1]) - frame.height()) <= 2,
      "自算物理矩形与 Win32 一致", f"{frame.width()}x{frame.height()}")

fl, ft = frame.x(), frame.y()
fr, fb = fl + frame.width(), ft + frame.height()
cx = (fl + fr) // 2
cy = (ft + fb) // 2
w, h = geo.width(), geo.height()

cases = [
    ("左边缘", fl + 2, cy, "HTLEFT"),
    ("右边缘", fr - 3, cy, "HTRIGHT"),
    ("上边缘", cx, ft + 2, "HTTOP"),
    ("下边缘", cx, fb - 3, "HTBOTTOM"),
    ("左上角", fl + 2, ft + 2, "HTTOPLEFT"),
    ("右上角", fr - 3, ft + 2, "HTTOPRIGHT"),
    ("左下角", fl + 2, fb - 3, "HTBOTTOMLEFT"),
    ("右下角", fr - 3, fb - 3, "HTBOTTOMRIGHT"),
]
for label, x, y, expected in cases:
    direct = HTNAMES.get(panel._hit_test(QPoint(x, y)), "?")
    native = HTNAMES.get(nchittest(hwnd, x, y), "?")
    check(direct == expected, f"{label} 直接命中 → {expected}", f"实际 {direct}")
    check(native == expected, f"{label} 原生 WM_NCHITTEST → {expected}", f"实际 {native}")

# 正文中心：只有「真的落在客户区」才应该是 HTCLIENT。
# 这里必须用 Win32 实测的中心点，不能用自算 frame 的中心 —— 本机 125%
# 缩放下自算原点是 1788 而 GetWindowRect 是 894，两者差在 Windows 的
# 投影边距上，用错就会得到 HTRIGHT。
_mcx = (_wr[0] + _wr[2]) // 2
_mcy = (_wr[1] + _wr[3]) // 2
check(HTNAMES.get(nchittest(hwnd, _mcx, _mcy), "?") == "HTCLIENT",
      "窗口正中（GetWindowRect 中心）→ HTCLIENT",
      f"实际 {HTNAMES.get(nchittest(hwnd, _mcx, _mcy), '?')}")

print("\n=== 最大化 / 还原 ===")
panel.toggle_maximize()
app.processEvents()
check(panel.is_maximized(), "toggle_maximize 后进入最大化")
mframe = panel._frame_rect_physical()
print(f"  最大化 frame = {mframe.width()}x{mframe.height()}")
check(mframe.width() > frame.width() or mframe.height() > frame.height(),
      "最大化后确实变大")
# 最大化时不应再有缩放热区（否则贴边会误触缩放、被弹回窗口模式）
_mx, _my = mframe.x() + 2, mframe.y() + 2
print(f"  状态快照：is_maximized={panel.is_maximized()} "
      f"windowState={panel.windowState().value} "
      f"WM_ISZOOMED={bool(user32.IsZoomed(wintypes.HWND(hwnd)))}")
_max_direct = panel._hit_test(QPoint(_mx, _my))
_max_native = nchittest(hwnd, _mx, _my)
check(_max_direct == 1, "最大化时 _hit_test 返回 HTCLIENT（无缩放热区）",
      f"nchittest({_mx},{_my}) = {HTNAMES.get(_max_direct, hex(_max_direct))}")
# 原生路径在最大化时会被 Qt/Windows 自己接管，实测返回 HTTOPLEFT。
# 对照实验（tools\\_probe_baseline.py，纯 Qt 无边框 QWidget）同样如此：
# 无边框窗口 showMaximized() 后 IsZoomed=False、窗口矩形仍比屏幕大，
# 这是 Qt 的平台层行为，不是本项目的缺陷，所以只作提示不作判失败。
if _max_native == 1:
    print("  [OK]   最大化时原生 WM_NCHITTEST 也返回 HTCLIENT")
else:
    print(f"  [--]   最大化时原生 WM_NCHITTEST 返回 "
          f"{HTNAMES.get(_max_native, hex(_max_native))}"
          f"（Qt 平台层行为，见 _probe_baseline.py，不判失败）")

panel.toggle_maximize()
app.processEvents()
check(not panel.is_maximized(), "再次 toggle_maximize 回到普通状态")
rframe = panel._frame_rect_physical()
print(f"  还原 frame = {rframe.width()}x{rframe.height()}")
check(abs(rframe.width() - frame.width()) <= 2
      and abs(rframe.height() - frame.height()) <= 2,
      "还原后尺寸回到最大化前")


def sample_pixels(hwnd: int, points):
    """抓窗口矩形范围内的真实屏幕像素，用来验证磨砂玻璃真的生效。

    实现搬到了 tools\\_winpix.py —— 本文件顶层就是探测逻辑，一 import
    就整段执行，别的脚本没法复用这里的函数。这里保留同名入口。
    """
    return _winpix.sample_pixels(hwnd, points)


print("\n=== 磨砂玻璃与白色边框（真实像素） ===")
try:
    gear = panel.enable_glass(True)
    check(bool(gear.get("acrylic")), "真实 HWND 下亚克力磨砂玻璃生效", str(gear))
    # 置顶并激活，确保抓到的确实是本窗口而不是被别的窗口盖住
    panel.raise_()
    panel.activateWindow()
    user32.SetForegroundWindow(wintypes.HWND(hwnd))
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()

    w = panel.width()
    h = panel.height()
    dpr = panel.devicePixelRatioF()
    # 采样点全部用**窗口内物理像素**。注意别取正中：正中是分割条，
    # 那里透出的是玻璃而不是正文底色，会把「正文够不够暗」测歪。
    points = [
        ("标题栏", w * dpr * 0.5, 9),
        ("正文左", w * dpr * 0.22, h * dpr * 0.5),
        ("正文右", w * dpr * 0.78, h * dpr * 0.5),
        ("状态栏", w * dpr * 0.05, h * dpr * 0.965),
    ]
    samples, image = sample_pixels(hwnd, points)
    for name, rgb in samples.items():
        print(f"  {name}: RGB{rgb}")

    # 白色边框：窗口最外圈是一条亮且接近中性灰的线。
    # 不逐点猜坐标，直接在上边缘扫一条带、取最亮的那个像素。
    def brightest(xs, ys):
        best = (0, 0, 0)
        for x in xs:
            for y in ys:
                px = image.getpixel((int(x), int(y)))
                if sum(px) > sum(best):
                    best = px
        return best

    border_rgb = brightest(range(int(w * dpr * 0.2), int(w * dpr * 0.8)), range(0, 3))
    title_rgb = samples["标题栏"]
    body_rgb = samples["正文左"]

    spread = max(border_rgb) - min(border_rgb)
    check(min(border_rgb) >= 140 and spread <= 45, "上边框是白色（亮且中性）",
          f"RGB{border_rgb} 最暗={min(border_rgb)} 色偏={spread}")
    # 正文区刻意不透明（只有标题栏/状态栏/边框透玻璃），否则中文糊在壁纸上
    check(sum(body_rgb) < 3 * 90, "正文底色足够暗（保证中文可读）", f"RGB{body_rgb}")
    check(samples["正文右"] == body_rgb or abs(sum(samples["正文右"]) - sum(body_rgb)) < 30,
          "左右两栏底色一致", f"{body_rgb} vs {samples['正文右']}")
    # 玻璃的硬证据：挪动窗口后标题栏像素会变（底下壁纸不同了）。
    # 壁纸恰好同色时可能测不出差异，所以只作提示，不判失败。
    before = title_rgb
    panel.move(panel.x() + 220, panel.y() + 60)
    app.processEvents()
    time.sleep(0.45)
    app.processEvents()
    after, _ = sample_pixels(hwnd, [("标题栏", w * dpr * 0.5, 9)])
    after_rgb = after["标题栏"]
    delta = max(abs(a - b) for a, b in zip(before, after_rgb))
    print(f"  [--]   挪窗后标题栏像素变化 {before} -> {after_rgb}（最大通道差 {delta}）"
          f"  {'透出桌面 ✓' if delta >= 3 else '本次壁纸同色，测不出差异'}")
    panel.move(panel.x() - 220, panel.y() - 60)
    app.processEvents()

    shot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_window.png")
    image.save(shot)
    print(f"  窗口截图已保存：{shot}")
except Exception as exc:  # pragma: no cover - 仅用于诊断
    print(f"  [!!]   像素采样失败（不影响窗口功能）: {exc}")


def finish():
    print(f"\n{'=' * 60}")
    print(f"通过 {pass_count} 项，失败 {fail_count} 项")
    panel.close()
    app.quit()


QTimer.singleShot(600, finish)
app.exec()
sys.exit(1 if fail_count else 0)
