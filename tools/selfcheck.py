"""自检：依赖、配置目录、OCR 引擎、翻译接口。

用法（带控制台输出）：
    .venv\\Scripts\\python.exe tools\\selfcheck.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def line(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def check_python() -> bool:
    line("1. Python 与依赖")
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    ok = True
    for module, label in [
        ("PySide6", "PySide6 (界面)"),
        ("numpy", "numpy"),
        ("requests", "requests"),
        ("rapidocr", "rapidocr (OCR)"),
    ]:
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "?")
            print(f"  [OK]   {label:<22} {version}")
        except Exception as exc:
            print(f"  [缺失] {label:<22} {type(exc).__name__}: {exc}")
            ok = False
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        print(f"  [信息] onnxruntime {ort.__version__} providers={providers}")
        if "DmlExecutionProvider" in providers:
            print("  [OK]   支持 DirectML（GPU）推理")
        else:
            print("  [信息] 没有 DirectML，将使用 CPU 推理（功能正常，稍慢）")
    except Exception as exc:
        print(f"  [缺失] onnxruntime            {exc}")
        ok = False
    return ok


def check_config() -> None:
    line("2. 配置文件")
    from core.config import config_path, get_config

    cfg = get_config()
    print(f"配置路径: {config_path()}")
    print(f"  抄取区域 region = {cfg.get('region')}")
    print(f"  刷新间隔 = {cfg.get('interval_ms')} ms")
    print(f"  翻译顺序 = {cfg.get('translate_providers')}")
    print(f"  热键 = {cfg.get('hotkey_toggle')} / {cfg.get('hotkey_mark')}")


def check_translate() -> None:
    line("3. 翻译接口")
    from core.translate import DEFAULT_PROVIDER_ORDER, Translator, has_proxy_configured

    if has_proxy_configured():
        print("  [信息] 检测到 HTTP 代理，Google 接口会被加入降级链末尾")
    else:
        print("  [信息] 未检测到 HTTP 代理，Google 接口不参与（中国大陆直连会挂起）")
    translator = Translator(timeout=15)
    for name in translator.order:
        if name not in DEFAULT_PROVIDER_ORDER:
            print(f"  [信息] 额外启用 {name}")
    for entry in translator.probe():
        if entry["ok"]:
            print(f'  [OK]   {entry["label"]:<24} {entry["ms"]:>5}ms  「{entry["sample"]}」')
        else:
            print(f'  [失败] {entry["label"]:<24} {entry["error"]}')

    from core.translate import load_phrasebook

    book = load_phrasebook()
    print(f"  [信息] 界面术语表 {len(book)} 条（短词直接查表，不走接口）")
    outcome = translator.translate(["Copy", "Paste", "Settings", "Bluetooth"],
                                   "zh-CN")
    print(f"  [信息] 端到端 {outcome.elapsed_ms:.0f}ms  via {outcome.provider or '（无）'}"
          f"  术语表命中 {outcome.from_phrasebook} 条")
    for source, target in zip(["Copy", "Paste", "Settings", "Bluetooth"],
                              outcome.translations):
        print(f'         {source:<12} -> {target}')


def check_ocr() -> None:
    line("4. OCR 引擎")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (760, 220), "white")
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
    ):
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, 34)
                break
            except Exception:
                continue
    text_a = "Settings"
    text_b = "Download failed"
    if font:
        draw.text((24, 30), text_a, fill="black", font=font)
        draw.text((24, 110), text_b, fill="black", font=font)
    else:
        draw.text((24, 30), text_a, fill="black")
        draw.text((24, 110), text_b, fill="black")
    import numpy as np

    array = np.asarray(image)

    from core.config import device_uses_dml, get_config
    from core.ocr import OcrEngine

    try:
        current = "DirectML(GPU)" if device_uses_dml(get_config()) else "CPU"
    except Exception:
        current = "CPU"
    print(f"  当前配置选的是：{current}（设置 → 翻译 → OCR 推理设备）")

    for use_dml in (True, False):
        label = "DirectML(GPU)" if use_dml else "CPU"
        engine = OcrEngine(use_dml=use_dml)
        started = time.perf_counter()
        try:
            engine.ensure_loaded()
        except Exception as exc:
            print(f"  [失败] {label}: 加载失败 {type(exc).__name__}: {exc}")
            continue
        load_ms = (time.perf_counter() - started) * 1000
        try:
            result = engine.run(array)
            engine.run(array)  # 第二次跳过预热
            second = engine.run(array)
        except Exception as exc:
            print(f"  [失败] {label}: 推理失败 {type(exc).__name__}: {exc}")
            continue
        print(
            f"  [OK]   {label:<14} backend={result.backend:<22} "
            f"加载 {load_ms:.0f}ms  推理 {second.elapsed_ms:.0f}ms/次"
        )
        for block in second.blocks:
            print(f"         · 「{block.text}」 score={block.score:.2f}")


def check_language_packs() -> None:
    line("5. OCR 识别语言包（多语言源语言用）")
    from core.ocr import LANGUAGE_PACKS, PACK_LABELS, pack_file_name, pack_is_downloaded

    print("  内置模型认得：拉丁字母（英/德/法/西/葡/意/波…）、日语（假名+汉字）、中文")
    print("  下面这些语言要另下识别模型，在「设置 → 翻译 → 源语言」里选中即自动下载：")
    grouped: dict = {}
    for code, pack in LANGUAGE_PACKS.items():
        grouped.setdefault(pack, []).append(code)
    for pack, codes in grouped.items():
        name = pack_file_name(pack)
        state = "[已下载]" if pack_is_downloaded(pack) else "[未下载]"
        tail = "" if pack_is_downloaded(pack) else "  （约 8-14 MB）"
        print(f"  {state} {PACK_LABELS.get(pack, pack[0]):<10} "
              f"{'/'.join(sorted(codes)):<22} {name}{tail}")


def check_dictionary() -> None:
    line("6. 离线词典（ECDICT）")
    from core.dictdb import Dictionary, library_status

    info = library_status()
    print(f"  词典数据: {info['csv_path']}")
    print(f"  词典库  : {info['db_path']}")
    if not info["csv_bytes"] and not info["db_bytes"]:
        print("  [未安装] 还没有词典数据。到「设置 → 词典」点「下载并建立词典库」")
        print("           （约 63 MB，全离线，不上传任何东西）")
        return
    print(f"  词典数据 {info['csv_bytes'] / 1048576:.1f} MB，"
          f"词典库 {info['db_bytes'] / 1048576:.1f} MB")
    if not info["ready"]:
        print("  [待建库] 词典数据已下载但还没建库，"
              "到「设置 → 词典」点一下「建立词典库」（几秒）")
        return
    print(f"  [就绪] 收录 {info['entries']:,} 条")
    book = Dictionary()
    probes = ["abandon", "settings", "runtime", "screenshot", "cached", "mutex"]
    missing = [word for word in probes if book.lookup(word) is None]
    print(f"  抽查 {len(probes)} 个词，"
          f"{'全部命中' if not missing else '缺失 ' + ', '.join(missing)}")
    entry = book.lookup("abandon")
    if entry is not None:
        tags = "、".join(entry.exam_labels) or "（无）"
        print(f"  abandon 考试标签：{tags}；{entry.freq_label}")
    cached = book.lookup("cached")
    if cached is not None:
        print(f"  cached → 原形 {cached.word}（变体占位会退回原形）")


def main() -> int:
    print("屏幕实时翻译 · 环境自检")
    deps_ok = check_python()
    check_config()
    check_translate()
    if deps_ok:
        check_ocr()
        check_language_packs()
    check_dictionary()
    line("结论")
    print("依赖齐全，可以双击 run.bat 启动。" if deps_ok
          else "有依赖缺失，请先运行 setup.bat。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
