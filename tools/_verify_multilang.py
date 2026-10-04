"""端到端验收：多语言 → 多语言 真的能用。

做四件事，全部走真实代码路径（真 OCR 引擎 + 真在线接口）：
  1. 用 PIL 把各语言的界面文本渲染成图片；
  2. 用 `OcrEngine(source_lang=...)` 识别 —— 非拉丁语言会加载对应识别模型
     （首次使用自动从 modelscope 下载）；
  3. 用 `core.worker` 里同一套过滤逻辑（`looks_like_translatable(..., target=)`）
     决定哪些行要翻；
  4. 交给 `Translator.translate()` 翻成目标语言，打印结果。

用法：
  .venv\\Scripts\\python.exe tools\\_verify_multilang.py
  .venv\\Scripts\\python.exe tools\\_verify_multilang.py offline   # 只做识别，不调接口
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("RAPIDOCR_LOG_LEVEL", "error")

from _probe_ocr_langs import render  # noqa: E402

from core.ocr import OcrEngine, looks_like_translatable, merge_close_blocks  # noqa: E402
from core.translate import Translator  # noqa: E402
from core.worker import PipelineSettings  # noqa: E402

# (说明, 源语言设置, 图上文字, 字体, 目标语言)
CASES = [
    ("日语 -> 中文", "auto", "設定\nこんにちは世界\nダウンロードに失敗しました",
     "YuGothR.ttc", "zh-CN"),
    ("德语 -> 中文", "auto", "Einstellungen\nHerunterladen fehlgeschlagen",
     "msyh.ttc", "zh-CN"),
    ("韩语 -> 中文", "ko", "설정\n다운로드 실패", "malgun.ttf", "zh-CN"),
    ("俄语 -> 中文", "ru", "Настройки\nНе удалось загрузить", "msyh.ttc", "zh-CN"),
    ("英语 -> 中文", "auto", "Settings\nDownload failed", "msyh.ttc", "zh-CN"),
    ("中文 -> 英语", "zh-CN", "设置\n下载失败", "msyh.ttc", "en"),
    ("英语 -> 日语", "auto", "Settings\nDownload failed", "msyh.ttc", "ja"),
    ("日语 -> 英语", "auto", "設定\nこんにちは世界", "YuGothR.ttc", "en"),
    ("中文屏 + 目标是中文（应全部跳过）", "auto", "设置\n下载失败", "msyh.ttc", "zh-CN"),
]


def main() -> int:
    offline = "offline" in sys.argv
    translator = Translator() if not offline else None
    engines: dict = {}
    failures: list = []

    for note, source_lang, text, font, target in CASES:
        print(f"\n{'=' * 66}\n{note}   （source_lang={source_lang!r} → target={target!r}）")
        image = render(text, font)

        key = source_lang
        if key not in engines:
            engines[key] = OcrEngine(use_dml=True, source_lang=source_lang)
        engine = engines[key]
        started = time.perf_counter()
        ocr = engine.run(image)
        recognize_ms = (time.perf_counter() - started) * 1000

        print(f"  识别模型={engine.pack_note}  后端={engine.backend}  "
              f"{len(ocr.blocks)} 段  {recognize_ms:.0f}ms")
        for block in ocr.blocks:
            print(f"    [{block.score:.2f}] {block.text!r}")

        # 与 core/worker.py 完全一样的过滤
        settings = PipelineSettings(target_lang=target, source_lang=source_lang,
                                    min_score=0.5, filter_cjk=True)
        seen = [b for b in ocr.blocks if b.score >= settings.min_score]
        blocks = [b for b in seen
                  if looks_like_translatable(b.text, target=target,
                                             source=source_lang)]
        lines = [line for line in merge_close_blocks(blocks)
                 if looks_like_translatable(line, target=target,
                                            source=source_lang)]

        expected = [l for l in text.split("\n") if l.strip()]
        if not lines:
            if target.startswith("zh") and all(
                    any("\u4e00" <= c <= "\u9fff" for c in l) for l in expected):
                print("  → 全部跳过（已经是目标语言），符合预期 ✅")
            else:
                print("  → ❌ 一行都没送翻译（不该发生）")
                failures.append(f"{note}: 过滤后为空")
            continue

        print(f"  送翻译 {len(lines)} 行：{lines!r}")
        if offline:
            continue
        assert translator is not None
        outcome = translator.translate(lines, target, source_lang)
        print(f"  接口={outcome.provider}  {outcome.elapsed_ms:.0f}ms  "
              f"错误={outcome.error or '无'}")
        for src, dst in zip(lines, outcome.translations):
            print(f"    {src!r}  ->  {dst!r}")
        blank = [i for i, t in enumerate(outcome.translations) if not t.strip()]
        if blank:
            failures.append(f"{note}: 第 {blank} 行没拿到译文")
        elif outcome.error:
            failures.append(f"{note}: {outcome.error}")

    print(f"\n{'=' * 66}")
    if failures:
        print("有问题：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
