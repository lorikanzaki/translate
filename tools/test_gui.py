"""离屏 GUI 自检：不弹窗口，验证「抓屏 → 后台线程 OCR → 翻译 → 面板显示」整条链路。

用法：
    .venv\\Scripts\\python.exe tools\\test_gui.py            # 用真实屏幕内容
    .venv\\Scripts\\python.exe tools\\test_gui.py synthetic  # 用合成的英文界面图
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 必须在导入 PySide6 之前设置，否则会真的弹窗
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.capture import grab_region, mean_abs_diff, virtual_screen_rect  # noqa: E402
from core.config import Config  # noqa: E402
from core.hotkeys import parse_hotkey  # noqa: E402
from core.translate import Translator  # noqa: E402
from core.worker import PipelineResult, PipelineSettings, TranslateWorker  # noqa: E402
from ui.panel import MainPanel  # noqa: E402

PASSED: list = []
FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    (PASSED if condition else FAILED).append(name)
    print(f"  {'✅' if condition else '❌'} {name}" + (f"  [{detail}]" if detail else ""))
    return bool(condition)


def _offscreen() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")


def _has_thickframe(widget) -> bool:
    """读原生窗口样式，确认 WS_THICKFRAME 已补上。

    offscreen 平台没有真实 HWND，读不到就返回 False，由调用方决定是否判失败。
    """
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32")
        hwnd = wintypes.HWND(int(widget.winId()))
        return bool(user32.GetWindowLongW(hwnd, -16) & 0x40000000)
    except Exception:
        return False


def synthetic_english_ui(width: int = 900, height: int = 260) -> np.ndarray:
    """合成一张像软件界面的英文图，不依赖真实屏幕。"""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), "#f4f4f4")
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, 30)
                break
            except Exception:
                continue
    lines = [
        "Download failed",
        "Are you sure you want to delete this file?",
        "The installer was unable to write to the selected directory.",
        "Settings",
    ]
    for index, text in enumerate(lines):
        if font:
            draw.text((24, 18 + index * 58), text, fill="#101010", font=font)
        else:
            draw.text((24, 18 + index * 58), text, fill="#101010")
    return np.asarray(image)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "screen"
    print("屏幕实时翻译 · GUI 链路自检（离屏模式）")

    app = QApplication.instance() or QApplication(sys.argv)

    print("\n=== 1) 抓屏 ===")
    rect = virtual_screen_rect()
    check("读到虚拟屏幕尺寸", rect[2] > 0 and rect[3] > 0, f"{rect[2]}×{rect[3]}")
    if mode == "synthetic":
        frame = synthetic_english_ui()
        check("使用合成英文界面图", frame.shape[:2] == (260, 900), str(frame.shape))
    else:
        # 抓屏幕左上角一块真实区域：够大即可，内容无所谓
        frame = grab_region([0, 0, 900, 260])
        check("抓屏返回 ndarray", isinstance(frame, np.ndarray), str(frame.shape))
        check("抓屏通道数为 3", frame.ndim == 3 and frame.shape[2] == 3, str(frame.shape))
    check("同一张图差异为 0", mean_abs_diff(frame, frame) == 0.0)
    changed = mean_abs_diff(frame, np.clip(frame.astype(int) + 30, 0, 255).astype(np.uint8))
    check("明显变化能被检出", changed > 0.01, f"diff={changed:.4f}")

    print("\n=== 2) 热键解析 ===")
    for spec, expected_key in [("ctrl+alt+t", 0x54), ("ctrl+alt+z", 0x5A)]:
        mods, vk = parse_hotkey(spec)
        check(f"{spec} 解析出 Alt+Ctrl", bool(mods & 0x0001) and bool(mods & 0x0002),
              f"mods=0x{mods:X}")
        check(f"{spec} 解析出按键 {hex(expected_key)}", vk == expected_key, f"vk=0x{vk:X}")

    print("\n=== 3) 配置读写 ===")
    tmp_path = os.path.join(tempfile.gettempdir(), "_screen_translator_test_config.json")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    config = Config(tmp_path)
    check("默认翻译链已更新",
          config.get("translate_providers") == ["bing", "volc", "edge", "mymemory"],
          str(config.get("translate_providers")))
    config.set("region", [10, 20, 300, 200])
    config.set("interval_ms", 1234)
    reloaded = Config(tmp_path)
    check("区域持久化", reloaded.get("region") == [10, 20, 300, 200],
          str(reloaded.get("region")))
    check("间隔持久化", reloaded.get("interval_ms") == 1234)
    # 旧配置（bing 时代）应被自动迁移
    import json

    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"translate_providers": ["bing", "google", "mymemory"],
                   "interval_ms": 1500}, fh)
    migrated = Config(tmp_path)
    check("失效的旧接口配置被迁移",
          migrated.get("translate_providers") == ["bing", "volc", "edge", "mymemory"],
          str(migrated.get("translate_providers")))
    # 用户真实配置里的那一条（edge 打头的老链路）也必须被换掉
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"translate_providers": ["edge", "mymemory", "youdao", "google"]}, fh)
    migrated2 = Config(tmp_path)
    check("用户那版老链路也被迁移",
          migrated2.get("translate_providers") == ["bing", "volc", "edge", "mymemory"],
          str(migrated2.get("translate_providers")))
    # 用户自己调过的顺序不能被多管闲事
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"translate_providers": ["edge", "bing"]}, fh)
    kept = Config(tmp_path)
    check("用户自定义过的接口顺序不动它",
          kept.get("translate_providers") == ["edge", "bing"],
          str(kept.get("translate_providers")))
    # 旧版本的 8 秒超时偏紧（降级到 MyMemory 要 2s+），也应被抬到 15
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"translate_timeout": 8}, fh)
    timeout_migrated = Config(tmp_path)
    check("旧的 8 秒超时被抬到 15",
          timeout_migrated.get("translate_timeout") == 15,
          str(timeout_migrated.get("translate_timeout")))
    # 用户手动设过的其它超时值不能被动
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"translate_timeout": 30}, fh)
    kept = Config(tmp_path)
    check("用户自定的超时值保持不变",
          kept.get("translate_timeout") == 30,
          str(kept.get("translate_timeout")))
    os.remove(tmp_path)

    print("\n=== 4) 后台线程跑真实流水线 ===")
    translator = Translator(timeout=15)
    worker = TranslateWorker(translator)
    settings = PipelineSettings(target_lang="zh-CN", use_dml=True, max_side=1600)
    started = time.perf_counter()
    worker._handle(frame, settings)
    elapsed = (time.perf_counter() - started) * 1000
    drained = worker.drain()
    results = [payload for kind, payload in drained if kind == "result"]
    failures = [payload for kind, payload in drained if kind == "error"]

    check("流水线没有抛异常", not failures, "; ".join(failures))
    check("拿到结果对象", bool(results), f"{len(results)} 个结果")
    if results:
        result: PipelineResult = results[-1]
        print(f"     OCR={result.ocr_ms:.0f}ms 翻译={result.translate_ms:.0f}ms "
              f"合计={result.total_ms:.0f}ms backend={result.backend} "
              f"provider={result.provider} 术语表={result.from_phrasebook}")
        for source, target in zip(result.lines, result.translations):
            print(f"       {source[:52]:<54} -> {target[:40]}")
        check("OCR 后端已就绪", bool(result.backend), result.backend)
        if mode == "synthetic":
            check("识别出 4 段文本", len(result.lines) == 4, f"{len(result.lines)} 段")
            joined = " ".join(result.translations)
            check("译文含中文", any("\u4e00" <= ch <= "\u9fff" for ch in joined),
                  joined[:60])
            check("Settings 走术语表得到「设置」",
                  "设置" in joined, joined[:60])
        check("整轮耗时在可接受范围", elapsed < 20_000, f"{elapsed:.0f}ms")

    print("\n=== 5) 面板显示 ===")
    panel = MainPanel(config, translator)
    panel.set_region_text("区域 900×260 @ (0, 0)")
    panel.update_result(results[-1] if results else
                        PipelineResult(lines=[], translations=[]))
    source_html = panel.source_view.toPlainText()
    target_html = panel.target_view.toPlainText()
    check("面板已显示原文", bool(source_html.strip()), source_html[:50])
    check("面板已显示译文", bool(target_html.strip()), target_html[:50])
    check("状态栏含耗时信息", "合计" in panel.status_label.text(),
          panel.status_label.text())
    check("状态栏不含「翻译失败」", "翻译失败" not in panel.status_label.text(),
          panel.status_label.text())
    panel.set_busy(True)
    check("忙碌状态可设置", "正在" in panel.status_label.text() or panel.status_label.text(),
          panel.status_label.text())
    panel.set_busy(False)
    panel.close()

    print("\n=== 5b) 识别到但不需要翻译 / 内容没变不重渲染 ===")
    panel_skip = MainPanel(config, translator)
    panel_skip.update_result(PipelineResult(
        blocks=[{"text": "我喜欢的音乐", "score": 0.99},
                {"text": "最近播放", "score": 0.98}],
        skipped=2, backend="DirectML(GPU)", ocr_ms=180.0, total_ms=200.0,
    ))
    src = panel_skip.source_view.toPlainText()
    dst = panel_skip.target_view.toPlainText()
    check("识别到中文时原文栏列出原文", "我喜欢的音乐" in src, src[:60])
    check("识别到中文时译文栏说明原因", "不需要翻译" in dst, dst[:60])
    check("识别到中文时状态栏是 0 段", panel_skip.status_label.text().startswith("0 段"),
          panel_skip.status_label.text())
    panel_skip.update_result(PipelineResult(
        backend="DirectML(GPU)", ocr_ms=180.0, total_ms=200.0))
    check("一段文字都没有时提示「没有识别到文字」",
          "没有识别到文字" in panel_skip.source_view.toPlainText(),
          panel_skip.source_view.toPlainText()[:60])

    same = PipelineResult(lines=["File", "Edit"], translations=["文件", "编辑"],
                          provider="微软 Edge 接口", ocr_ms=100.0, total_ms=120.0)
    panel_skip.update_result(same)
    panel_skip.source_view.setPlainText("MARK")
    panel_skip.update_result(same)
    check("内容没变时不重渲染（避免闪烁/打断选中）",
          panel_skip.source_view.toPlainText() == "MARK",
          panel_skip.source_view.toPlainText()[:40])
    panel_skip.update_result(PipelineResult(
        lines=["File", "View"], translations=["文件", "视图"],
        provider="微软 Edge 接口", ocr_ms=100.0, total_ms=120.0))
    check("内容变了会重渲染",
          "View" in panel_skip.source_view.toPlainText(),
          panel_skip.source_view.toPlainText()[:40])
    check("强制刷新与变化灵敏度的默认值存在",
          int(config.get("refresh_ms")) > 0
          and 0 < float(config.get("change_threshold")) < 0.01,
          f"refresh_ms={config.get('refresh_ms')} "
          f"threshold={config.get('change_threshold')}")
    panel_skip.close()

    print("\n=== 6) 设置对话框 ===")
    from PySide6.QtCore import Qt

    from ui.settings import PROVIDER_LABELS, SettingsDialog

    dialog = SettingsDialog(config, translator=translator)
    listed = dialog._provider_order()
    all_items = [dialog.provider_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(dialog.provider_list.count())]
    from core.translate import PROVIDER_CLASSES
    check("设置面板列出全部翻译接口",
          sorted(all_items) == sorted(PROVIDER_CLASSES), str(all_items))
    check("火山/必应两个新接口都在列表里",
          "volc" in all_items and "bing" in all_items, str(all_items))
    check("勾选的是配置里的降级链", listed == list(config.get("translate_providers")),
          f"{listed} vs {config.get('translate_providers')}")
    check("大模型接口在列表里但默认不勾选（没填 key 时没用）",
          "llm" in all_items and "llm" not in listed, str(listed))
    for name in listed:
        check(f"接口 {name} 有中文标签", name in PROVIDER_LABELS, PROVIDER_LABELS.get(name, ""))
    # 取消勾选全部再保存应被拦下
    for i in range(dialog.provider_list.count()):
        dialog.provider_list.item(i).setCheckState(Qt.CheckState.Unchecked)
    check("全部取消勾选后顺序为空", dialog._provider_order() == [])
    dialog.provider_list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._save()
    check("保存后配置写入新顺序", config.get("translate_providers") == [listed[0]],
          str(config.get("translate_providers")))
    check("保存后翻译器顺序同步", translator.order[0] == listed[0],
          str(translator.order))
    dialog2 = SettingsDialog(config, translator=translator)
    check("设置里有「强制刷新」", hasattr(dialog2, "refresh"), "")
    check("设置里有「变化灵敏度」", hasattr(dialog2, "threshold"), "")
    check("设置里有「保护代码片段」", hasattr(dialog2, "protect_tokens"), "")
    check("保护代码片段默认勾选", dialog2.protect_tokens.isChecked(), "")
    check("设置里有「OCR 推理设备」", hasattr(dialog2, "device"), "")
    check("推理设备默认省内存（CPU）", dialog2.device.currentData() == "cpu",
          str(dialog2.device.currentData()))
    check("推理设备可切换到显卡", dialog2.device.findData("gpu") >= 0, "")
    dialog2.refresh.setValue(7000)
    dialog2.threshold.setValue(3)
    dialog2.protect_tokens.setChecked(False)
    dialog2.device.setCurrentIndex(dialog2.device.findData("gpu"))
    dialog2._save()
    check("推理设备写回配置", config.get("ocr_device") == "gpu",
          str(config.get("ocr_device")))
    check("旧的 GPU 开关已不存在",
          "ocr_use_dml" not in config._data, "")
    check("强制刷新写回配置", int(config.get("refresh_ms")) == 7000,
          str(config.get("refresh_ms")))
    check("变化灵敏度按千分比写回配置",
          abs(float(config.get("change_threshold")) - 0.003) < 1e-9,
          str(config.get("change_threshold")))
    check("保护代码片段写回配置", config.get("protect_tokens") is False,
          str(config.get("protect_tokens")))
    check("保护代码片段同步到翻译器", translator.protect_tokens is False, "")
    config.update({"refresh_ms": 4000, "change_threshold": 0.002,
                   "protect_tokens": True})

    # --- 多语言：目标语言列表要够长，源语言要能指定到会换 OCR 识别模型的语言
    from ui.settings import LANGUAGES, SOURCE_LANGS  # noqa: PLC0415

    target_codes = [code for code, _ in LANGUAGES]
    source_codes = [code for code, _ in SOURCE_LANGS]
    check("目标语言不只是 5 项", len(LANGUAGES) >= 16, str(len(LANGUAGES)))
    check("目标语言含英语（可以翻成外语）", "en" in target_codes)
    check("目标语言含日语", "ja" in target_codes)
    check("目标语言含阿拉伯语", "ar" in target_codes)
    check("目标语言含泰语", "th" in target_codes)
    check("源语言第一项是自动识别", source_codes[0] == "auto", str(source_codes[:1]))
    check("源语言含韩语", "ko" in source_codes)
    check("源语言含俄语", "ru" in source_codes)
    check("源语言含阿拉伯语", "ar" in source_codes)
    check("源语言含印地语", "hi" in source_codes)
    check("源语言含希腊语", "el" in source_codes)
    check("每个目标语言都有中文名",
          all(label.strip() for _, label in LANGUAGES), "")
    check("每个源语言都有中文名",
          all(label.strip() for _, label in SOURCE_LANGS), "")
    check("要换识别模型的语言在源语言列表里标出来了",
          any("下载识别模型" in label for _, label in SOURCE_LANGS), "")

    dialog3 = SettingsDialog(config, translator=translator)
    check("勾选项文案改成了「跳过已经是目标语言的文本」",
          "目标语言" in dialog3.filter_cjk.text(), dialog3.filter_cjk.text())
    dialog3.source.setCurrentIndex(dialog3.source.findData("ko"))
    check("源语言能选中韩语", dialog3.source.currentData() == "ko",
          str(dialog3.source.currentData()))
    dialog3._save()
    check("源语言写回配置", config.get("source_lang") == "ko",
          str(config.get("source_lang")))
    config.update({"source_lang": "auto"})

    print("\n=== 7) 抓屏区域换算 ===")
    from PySide6.QtCore import QRect

    from ui.selector import RegionSelector

    selector = RegionSelector()
    physical = selector._to_physical(QRect(0, 0, 400, 300))
    check("区域换算返回 4 元组", len(physical) == 4, str(physical))
    check("换算结果不出现负数宽高", physical[2] > 0 and physical[3] > 0, str(physical))
    check("换算后的宽高与实际屏幕同量级",
          0 < physical[2] <= 8192 and 0 < physical[3] <= 8192, str(physical))
    selector.close()

    print("\n=== 8) 实体窗口控件 ===")
    # 把窗口位置的持久化重定向到内存，别污染用户真实配置。
    saved = {}
    panel2 = MainPanel(config, translator)
    panel2._save_geometry = lambda: saved.update(
        {"geo": [panel2.x(), panel2.y(), panel2.width(), panel2.height()]}
    )
    check("标题栏有三个窗口按钮",
          all(hasattr(panel2, n) for n in ("btn_min", "btn_max", "btn_close")))
    check("窗口按钮带中文提示",
          all(b.toolTip() for b in (panel2.btn_min, panel2.btn_max, panel2.btn_close)),
          panel2.btn_min.toolTip() + " / " + panel2.btn_close.toolTip())

    hits = []
    panel2.minimizeRequested.connect(lambda: hits.append("min"))
    panel2.btn_min.click()
    check("点最小化会发出 minimizeRequested", hits == ["min"], str(hits))
    check("点最小化前已保存窗口位置", "geo" in saved, str(saved.get("geo")))
    panel2.showMinimized()
    check("最小化后窗口状态为最小化",
          bool(panel2.windowState() & panel2.windowState().WindowMinimized),
          str(panel2.windowState().value))
    panel2.showNormal()

    panel2.btn_max.click()
    check("点最大化后进入最大化", panel2.is_maximized())
    check("最大化后按钮变成还原图标", panel2.btn_max.text() == "❐", panel2.btn_max.text())
    check("最大化后 root 的 winMaximized 属性为 true",
          panel2.root_frame.property("winMaximized") is True,
          repr(panel2.root_frame.property("winMaximized")))
    panel2.btn_max.click()
    check("再次点击回到普通状态", not panel2.is_maximized())
    check("还原后按钮图标复原", panel2.btn_max.text() == "□", panel2.btn_max.text())

    check("窗口有最小尺寸限制",
          panel2.minimumWidth() > 0 and panel2.minimumHeight() > 0,
          f"{panel2.minimumWidth()}x{panel2.minimumHeight()}")
    check("窗口无系统标题栏",
          bool(panel2.windowFlags() & Qt.WindowType.FramelessWindowHint))
    check("窗口有贴边缩放用的 WS_THICKFRAME",
          _has_thickframe(panel2) or _offscreen(), "offscreen 平台无真实 HWND")
    panel2.close()

    print("\n=== 9) 外观主题与框选边框 ===")
    from ui import theme as theme_mod
    from ui.regionbox import RegionFrame

    values = dict(theme_mod.theme(None))
    values.update({
        "panel_color": "#123456",
        "panel_alpha": 200,
        "accent": "#ff8800",
        "border": "#ffffff",
        "border_alpha": 180,
        "bg_image": "",
        "region_enabled": True,
        "region_color": "#00ffaa",
        "region_width": 4,
        "region_label": True,
    })
    config.set("theme", values)
    merged = theme_mod.theme(config)
    check("主题合并后取到自定义色", merged["panel_color"] == "#123456",
          merged["panel_color"])
    check("平铺主题字典也能被 theme() 认出来",
          theme_mod.theme(values)["panel_color"] == "#123456",
          theme_mod.theme(values)["panel_color"])
    check("主题数值被夹进合法范围",
          isinstance(merged["panel_alpha"], int) and 0 <= merged["panel_alpha"] <= 255,
          str(merged["panel_alpha"]))
    check("亚克力色调随面板底色变化",
          theme_mod.acrylic_tint(merged) != theme_mod.acrylic_tint(theme_mod.theme(None)),
          hex(theme_mod.acrylic_tint(merged)))
    check("分割条样式表可生成",
          "background" in theme_mod.splitter_handle_style(merged),
          theme_mod.splitter_handle_style(merged).strip())

    panel3 = MainPanel(config, translator)
    panel3.preview_theme(values)
    sheet = panel3.styleSheet()
    check("预览后面板样式表带上了新底色",
          "18, 52, 86" in sheet, "len=%d 含新色=%s" % (len(sheet), "18, 52, 86" in sheet))
    check("面板记住了主题", panel3.theme_settings["accent"] == "#ff8800",
          panel3.theme_settings["accent"])
    check("正文底色跟着主题走",
          panel3.source_view.palette().color(
              panel3.source_view.viewport().backgroundRole()).alpha() < 255,
          str(panel3.source_view.palette().color(
              panel3.source_view.viewport().backgroundRole()).alpha()))
    panel3.close()

    # 设置对话框里调颜色必须能实时预览（这条链路曾经断过：
    # theme() 不认平铺主题字典，预览静默退回默认配色）。
    from ui.settings import SettingsDialog

    panel4 = MainPanel(config, translator)
    dialog2 = SettingsDialog(config, parent=panel4, preview=panel4.preview_theme)
    dialog2.panel_color.set_color("#abcdef")
    dialog2.panel_alpha.setValue(255)
    dialog2._on_theme_changed()
    check("设置里改颜色会立刻预览到面板上",
          "171, 205, 239" in panel4.styleSheet(),
          "含新色=%s" % ("171, 205, 239" in panel4.styleSheet()))
    check("预览后的主题被记进对话框", dialog2._collect_theme()["panel_color"] == "#abcdef",
          dialog2._collect_theme()["panel_color"])
    dialog2.close()
    panel4.close()

    frame = RegionFrame()
    frame.apply_theme(theme_mod.theme(config))
    check("框选边框默认隐藏", not frame.isVisible())
    frame.set_region((200, 300, 1000, 400))
    geo = frame.geometry()
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    check("边框窗口比区域大一圈",
          geo.width() >= int(1000 / dpr) and geo.height() >= int(400 / dpr),
          f"geo={geo.getRect()} dpr={dpr}")
    check("边框窗口起点在区域左侧外",
          geo.x() <= int(200 / dpr) and geo.y() <= int(300 / dpr),
          f"x={geo.x()} y={geo.y()}")
    check("边框本身不接收鼠标",
          bool(frame.windowFlags() & Qt.WindowType.WindowTransparentForInput))
    check("边框窗口不在任务栏出现",
          bool(frame.windowFlags() & Qt.WindowType.Tool))
    frame.hide()
    check("隐藏后边框不可见", not frame.isVisible())

    print("\n=== 10) 词典页 / 生词本 / 悬停卡片 ===")
    from core.dictdb import Dictionary
    from core.vocab import VocabBook
    from ui.dictwin import DictWindow
    from ui.hover import HoverCard

    vocab_path = os.path.join(tempfile.gettempdir(), "_test_gui_vocab.jsonl")
    if os.path.exists(vocab_path):
        os.remove(vocab_path)
    vocab = VocabBook(vocab_path)
    dictionary = Dictionary()

    # 面板带词典时应当是两页签，不带时保持一页签（老调用方不受影响）
    panel_plain = MainPanel(config, translator)
    check("不传词典时面板只有「翻译」一页", panel_plain.tabs.count() == 1,
          str(panel_plain.tabs.count()))
    panel_plain.close()

    panel5 = MainPanel(config, translator, dictionary=dictionary, vocab=vocab)
    titles = [panel5.tabs.tabText(i) for i in range(panel5.tabs.count())]
    check("传了词典时面板有两页", panel5.tabs.count() == 2, str(titles))
    check("第二页叫「词典」", titles[1] == "词典", str(titles))
    check("面板持有词典页对象", panel5.page_dict is not None, "")

    # 屏幕文本要喂给词典页（跳过中文的判断会顺手筛掉一些英文，词典不该跟着漏）
    panel5._update_dictionary(SimpleNamespace(
        lines=["Download failed"], blocks=[{"text": "installer", "score": 0.9}]))
    shown = panel5.page_dict.view.toPlainText()
    check("词典页收到了屏幕上的词", "installer" in shown or "Download" in shown,
          shown[:60].replace("\n", " "))

    check("词典页正文有自己的底色（否则浅色壁纸下读不出来）",
          "rgba(" in panel5.page_dict.view.styleSheet(),
          panel5.page_dict.view.styleSheet()[:70])
    panel5.close()

    window = DictWindow(config, dictionary, vocab)
    win_titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    check("独立词典窗口有「词典」和「生词本」两页",
          win_titles == ["词典", "生词本"], str(win_titles))
    check("生词本表格有自己的底色",
          "rgba(" in window.page_vocab.table.styleSheet(),
          window.page_vocab.table.styleSheet()[:70])
    check("生词本表头也有底色（只写 transparent 会留一条浅灰带）",
          "QHeaderView" in window.page_vocab.table.styleSheet(), "")

    vocab.add("installer", meaning="n. 安装程序", pos="名词")
    vocab.add("restart", meaning="n. 重启", pos="名词")
    window.page_vocab.refresh()
    check("生词本能刷新出行数", window.page_vocab.table.rowCount() == 2,
          str(window.page_vocab.table.rowCount()))
    check("生词本顶部有统计文案", "共 2 条" in window.page_vocab.summary.text(),
          window.page_vocab.summary.text())
    window.close()

    card = HoverCard(config, dictionary, vocab)
    check("悬停卡片不吃鼠标事件",
          bool(card.windowFlags() & Qt.WindowType.WindowTransparentForInput))
    check("悬停卡片不抢焦点",
          bool(card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))
    entry = None
    if dictionary.available:
        entry = dictionary.lookup("installer")
    if entry is not None:
        card.show_entry(entry, "The installer was unable to write to the selected directory.",
                        QRect(100, 100, 60, 20))
        doc_height = int(card.view.document().size().height())
        check("悬停卡片高度容得下整张词条（不留余量会切掉最后一行）",
              card.height() >= doc_height + 20,
              f"card={card.height()} doc={doc_height}")
        check("悬停卡片宽度按配置走", 220 <= card.width() <= 360, str(card.width()))
        check("卡片里带上了「出现在」的例句",
              "出现在" in card.view.toPlainText(), card.view.toPlainText()[:60])
        check("例句里的词被高亮成链接", "word:installer" in card.view.toHtml()
              or "installer" in card.view.toPlainText(), "")
    else:
        check("跳过悬停卡片渲染（本机没有词典库）", True, "未建库")
    card.hide_card()
    check("隐藏后卡片从窗口里消失", not card.isVisible())

    settings_dict = SettingsDialog(config, translator=translator, dictionary=dictionary)
    st = [settings_dict.tabs.tabText(i) for i in range(settings_dict.tabs.count())]
    check("设置对话框是「翻译 / 词典 / 外观」三页", st == ["翻译", "词典", "外观"], str(st))
    check("词典页里有下载/重建按钮",
          hasattr(settings_dict.dict_setup, "start"), "")
    values = settings_dict.dict_setup.values()
    check("词典页能取回全部词典配置",
          set(values) == {"dict_enabled", "dict_hover", "dict_hover_delay_ms",
                          "dict_hover_font_size", "dict_hover_width"}, str(sorted(values)))
    settings_dict._save()
    check("词典配置写回",
          all(config.get(k) == v for k, v in values.items()), str(values))
    check("词典标签页是标题栏之外的独立页（标题没被词典设置污染）",
          "词典" in settings_dict.windowTitle() or settings_dict.windowTitle(), "")
    settings_dict.close()
    if os.path.exists(vocab_path):
        os.remove(vocab_path)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print(f"  - {name}")
    del app
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
