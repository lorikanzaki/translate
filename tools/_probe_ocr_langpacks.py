"""验证 RapidOCR 能否按语言自动下载并加载额外的识别模型。

内置的 PP-OCRv6 只认「拉丁字母 + 日语 + 中文」，俄语/韩语/阿拉伯语/泰语/印地语
完全识别不出来（见 tools\\_probe_ocr_langs.py 的实测输出）。
RapidOCR 的 default_models.yaml 里列着这些语言的专用识别模型（托管在 modelscope），
这个脚本验证「改 lang_type 就能自动下载 + 正确识别」这条路走不走得通。

用法：.venv\\Scripts\\python.exe tools\\_probe_ocr_langpacks.py [语言...]
      .venv\\Scripts\\python.exe tools\\_probe_ocr_langpacks.py korean eslav
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._probe_ocr_langs import render  # noqa: E402

# lang_type -> (显示名, 文本, 字体, Rec.lang_type, Rec.ocr_version)
PACKS = {
    "korean": ("韩语", "설정\n다운로드 실패", "malgun.ttf", "KOREAN", "PPOCRV5"),
    "eslav": ("俄语", "Настройки\nНе удалось загрузить", "msyh.ttc", "ESLAV", "PPOCRV5"),
    "cyrillic": ("俄语(cyrillic)", "Настройки", "msyh.ttc", "CYRILLIC", "PPOCRV5"),
    "arabic": ("阿拉伯语", "الإعدادات\nفشل التنزيل", "tahoma.ttf", "ARABIC", "PPOCRV4"),
    "th": ("泰语", "การตั้งค่า\nดาวน์โหลดล้มเหลว", "LeelawUI.ttf", "TH", "PPOCRV5"),
    "devanagari": ("印地语", "सेटिंग्स\nडाउनलोड विफल", "Nirmala.ttf", "DEVANAGARI", "PPOCRV5"),
    "el": ("希腊语", "Ρυθμίσεις", "msyh.ttc", "EL", "PPOCRV5"),
    "latin": ("法语", "Paramètres\nÉchec du téléchargement", "msyh.ttc", "LATIN", "PPOCRV5"),
}


def main(argv: list[str]) -> int:
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    names = [a for a in argv if a in PACKS] or ["korean", "eslav"]
    print(f"要验证的语言包：{names}\n")

    for name in names:
        label, text, font_file, lang_type, version = PACKS[name]
        print("=" * 70)
        print(f"{label}（lang_type={lang_type}, ocr_version={version}）")
        started = time.perf_counter()
        try:
            engine = RapidOCR(params={
                "Rec.lang_type": getattr(LangRec, lang_type),
                "Rec.ocr_version": getattr(OCRVersion, version),
                "Rec.model_type": ModelType.MOBILE,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 加载失败：{type(exc).__name__}: {exc}")
            continue
        load_ms = (time.perf_counter() - started) * 1000
        model_path = getattr(engine.cfg.Rec, "model_path", None)
        print(f"  加载 {load_ms:.0f}ms  模型={model_path}")

        img = render(text, font_file)
        started = time.perf_counter()
        try:
            result = engine(img)
            txts = list(result.txts or [])
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 识别异常：{type(exc).__name__}: {exc}")
            continue
        infer_ms = (time.perf_counter() - started) * 1000
        want = [line for line in text.split("\n") if line.strip()]
        hits = sum(
            1 for line in want
            if any(line.replace(" ", "") == (t or "").replace(" ", "") for t in txts)
        )
        mark = "✅" if hits == len(want) else "❌"
        print(f"  {mark} {hits}/{len(want)}  {infer_ms:.0f}ms  识别={txts!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
