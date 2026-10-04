"""把 TranslatorApp 的构造函数拆开量：每一步占多少内存。

用来回答「面板 + 框选边框 + 热键 + 托盘」那 90 MB 到底花在哪。
只读，不改配置。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gc
import os
import sys
from _sandbox import install  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def ws() -> float:
    c = PMC()
    c.cb = ctypes.sizeof(c)
    _K32(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 1048576


_last = [0.0]


def stage(label: str) -> None:
    gc.collect()
    value = ws()
    delta = value - (_last[0] or 0.0)
    _last[0] = value
    print(f"{value:8.1f} MB | 本步 {delta:+7.1f} MB | {label}")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    import main as app_main
    from core.config import get_config

    # 借 TranslatorApp 拿到 QApplication，但不让它把整个界面建起来：
    # 先建 QApplication，再逐件手工 new。
    app = QApplication(sys.argv[:1])
    stage("QApplication")

    from core.dictdb import Dictionary
    from core.translate import Translator
    from core.vocab import VocabBook
    import ui.theme as theme_mod

    config = install()
    stage("读配置")

    translator = Translator(
        provider_order=config.get("translate_providers"),
        glossary_path=None,
    )
    stage("Translator（读术语表 + 建 provider）")

    dictionary = Dictionary()
    stage(f"Dictionary（可用={dictionary.available}）")

    vocab = VocabBook()
    stage(f"VocabBook（{vocab.count()} 条）")

    from ui.panel import MainPanel

    panel = MainPanel(config, translator, dictionary=dictionary, vocab=vocab)
    stage("MainPanel（含翻译页 + 词典页）")

    theme = theme_mod.theme(config)
    from ui.regionbox import RegionFrame

    frame = RegionFrame()
    frame.apply_theme(theme)
    stage("RegionFrame（框选边框）")

    from ui.hover import HoverCard

    hover = HoverCard(config, dictionary, vocab)
    stage("HoverCard（悬停取词卡片）")

    from ui.selector import RegionSelector

    selector = RegionSelector()
    stage("RegionSelector（全屏框选遮罩，只在点『标记区域』时才用）")

    try:
        from ui.selector import WindowPicker

        picker = WindowPicker()
        stage("WindowPicker")
    except Exception as exc:  # noqa: BLE001
        print(f"          （WindowPicker 跳过：{exc}）")

    from core.hotkeys import HotkeyManager

    hotkeys = HotkeyManager()
    app.installNativeEventFilter(hotkeys)
    stage("HotkeyManager")

    print("-" * 72)
    print(f"合计工作集 {ws():.1f} MB")
    print(f"背景图设置：{config.get('theme', {}).get('bg_image')!r}")
    bg = config.get("theme", {}).get("bg_image")
    if bg and os.path.isfile(str(bg)):
        size = os.path.getsize(str(bg))
        print(f"背景图文件 {size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
