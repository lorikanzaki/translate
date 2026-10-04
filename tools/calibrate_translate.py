"""翻译校准对照：跑一批"真实界面上最容易译错"的短文本，看校准前后的差别。

用法：
    .venv\\Scripts\\python.exe tools\\calibrate_translate.py          # 只跑离线部分
    .venv\\Scripts\\python.exe tools\\calibrate_translate.py online   # 联网跑真实接口

联网部分会真的打接口（edge / mymemory / youdao），但只发几十条短文本，
不会消耗多少配额。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import (  # noqa: E402
    Translator,
    _phrasebook_candidates,
    load_phrasebook,
    looks_like_proper_noun,
    phrasebook_lookup,
    sentence_lookup,
)

# 一组实测过的"坑"：孤立短词、专名、带快捷键尾巴的菜单项、长句
CASES = [
    # (原文, 期望的定性结果)
    ("Copy", "短词"),
    ("Paste", "短词"),
    ("Settings", "短词"),
    ("Access denied", "短词"),
    ("Check for updates", "短词"),
    ("Restart required", "短词"),
    ("Zoom out", "短词"),
    # 带尾巴的菜单项 —— 校准前这些因为含标点直接被排除在术语表之外
    ("Save As...", "带尾巴"),
    ("Copy (Ctrl+C)", "带尾巴"),
    ("Settings\tCtrl+,", "带尾巴"),
    ("Undo [Ctrl+Z]", "带尾巴"),
    ("\u2022 Paste", "带尾巴"),
    ("File > Save", "带尾巴"),
    ("Open Recent...", "带尾巴"),
    # 专名 —— 校准前会被送给接口乱翻
    ("GitHub", "专名"),
    ("Python", "专名"),
    ("PDF", "专名"),
    ("C++", "专名"),
    ("onnxruntime", "专名"),
    ("DirectML", "专名"),
    ("v1.2.3", "专名"),
    ("setup.exe", "专名"),
    # 整句 —— 应当照常走接口，翻译质量本来就还行
    ("Are you sure you want to delete this file?", "整句"),
    ("The installer was unable to write to the selected directory.", "整句"),
    ("Do you want to save your changes before closing?", "整句"),
    ("Open in GitHub", "混合"),
]


def describe(text: str, book) -> str:
    if not text.strip():
        return "空"
    hit = phrasebook_lookup(text, book)
    if hit is not None:
        return f"术语表 -> {hit}"
    hit = sentence_lookup(text)
    if hit is not None:
        return f"整句对照表 -> {hit}"
    if looks_like_proper_noun(text):
        return "专名保留原文"
    return "走接口"


def offline_report() -> None:
    book = load_phrasebook()
    print(f"术语表 {len(book)} 条\n")
    print(f"{'原文':<46} {'类别':<8} {'处理方式'}")
    print("-" * 92)
    for text, kind in CASES:
        display = text.replace("\t", "\\t").replace("\u2022", "*")
        print(f"{display:<46} {kind:<8} {describe(text, book)}")

    print("\n候选键逐步剥壳演示：")
    for sample in ("Settings\tCtrl+,", "Copy (Ctrl+C)", "Save As...",
                   "\u2022 File > Save", "Open Recent..."):
        print(f"  {sample!r} -> {_phrasebook_candidates(sample)}")


def online_report() -> None:
    book = load_phrasebook()
    translator = Translator(timeout=15, use_glossary=True, keep_proper_nouns=True)
    texts = [text for text, _ in CASES]

    # 第一遍：走完整流程（术语表 + 专名 + 接口）
    started = time.perf_counter()
    outcome = translator.translate(texts, "zh-CN")
    elapsed = (time.perf_counter() - started) * 1000

    print(f"\n=== 联网结果（provider={outcome.provider or '无'} "
          f"{elapsed:.0f}ms，术语表命中 {outcome.from_phrasebook}，"
          f"专名保留 {translator.stats['proper_noun_kept']}）===")
    if outcome.error:
        print(f"error: {outcome.error}")
    print(f"{'原文':<46} {'译文'}")
    print("-" * 92)
    for text, translated in zip(texts, outcome.translations):
        display = text.replace("\t", "\\t").replace("\u2022", "*")
        print(f"{display:<46} {translated}")

    print("\n=== 指定源语言 en 的效果（同一批）===")
    translator.clear_cache()
    outcome_en = translator.translate(texts, "zh-CN", source="en")
    print(f"provider={outcome_en.provider or '无'}  error={outcome_en.error or '无'}")
    for text, translated in zip(texts, outcome_en.translations):
        display = text.replace("\t", "\\t").replace("\u2022", "*")
        print(f"{display:<46} {translated}")

    # 空串 / 纯空白不应消耗配额
    translator.clear_cache()
    empty = translator.translate(["", "   ", "Settings"], "zh-CN")
    print(f"\n空串检查：{empty.translations} provider={empty.provider}")

    # 原样退回检测：故意传一个接口大概率会原样退回的东西
    translator.clear_cache()
    same = translator.translate(["zzzqqqxxx", "GitHub"], "zh-CN")
    print(f"无法翻译的字符串：{same.translations} provider={same.provider or '无'} "
          f"error={same.error or '无'}")


def main() -> int:
    offline_report()
    if len(sys.argv) > 1 and sys.argv[1] == "online":
        online_report()
    else:
        print("\n（加 online 参数可联网对照真实接口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
