"""实测内置的 PP-OCRv6 识别模型到底认不认非拉丁文字。

背景：程序现在只能「英语 → 其它语言」，因为 core/ocr.py 的 looks_like_translatable
要求文本里至少有 2 个 **ASCII 字母**，俄语/日语/韩语/阿拉伯语全部被静默丢掉。
但丢掉之前得先确认一件事：**模型本身认不认**。认得出才值得改过滤，认不出就得
再下多语种模型（几十 MB）。

做法：用 PIL 把各语言的界面文本渲染成图片，喂 RapidOCR，逐条打印识别结果。

用法：.venv\\Scripts\\python.exe tools\\_probe_ocr_langs.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("RAPIDOCR_LOG_LEVEL", "error")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONTS = r"C:\Windows\Fonts"

# 语言 -> (显示名, 要渲染的文本, 覆盖该语言的字体)
SAMPLES = [
    ("英语", "Settings\nDownload failed", "msyh.ttc"),
    ("德语", "Einstellungen\nHerunterladen fehlgeschlagen", "msyh.ttc"),
    ("法语", "Paramètres\nÉchec du téléchargement", "msyh.ttc"),
    ("西语", "Configuración", "msyh.ttc"),
    ("葡语", "Configurações", "msyh.ttc"),
    ("意语", "Impostazioni", "msyh.ttc"),
    ("波兰语", "Ustawienia\nPobieranie nie powiodło się", "msyh.ttc"),
    ("土耳其语", "Ayarlar\nİndirme başarısız", "msyh.ttc"),
    ("越南语", "Cài đặt\nTải xuống thất bại", "arial.ttf"),
    ("希腊语", "Ρυθμίσεις", "msyh.ttc"),
    ("俄语", "Настройки\nНе удалось загрузить", "msyh.ttc"),
    ("乌克兰语", "Налаштування", "msyh.ttc"),
    ("日语", "設定\nこんにちは世界\nダウンロードに失敗しました", "YuGothR.ttc"),
    ("韩语", "설정\n다운로드 실패", "malgun.ttf"),
    ("阿拉伯语", "الإعدادات\nفشل التنزيل", "tahoma.ttf"),
    ("泰语", "การตั้งค่า\nดาวน์โหลดล้มเหลว", "LeelawUI.ttf"),
    ("印地语", "सेटिंग्स\nडाउनलोड विफल", "Nirmala.ttf"),
    ("中文", "设置\n下载失败", "msyh.ttc"),
]


def render(text: str, font_file: str, size: int = 34) -> Image.Image:
    """把多行文本渲染成白底黑字图片。"""
    lines = text.split("\n")
    font = ImageFont.truetype(os.path.join(FONTS, font_file), size)
    pad, gap = 16, 12
    widths, heights = [], []
    probe = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(probe)
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    width = max(widths) + pad * 2
    height = sum(heights) + gap * (len(lines) - 1) + pad * 2
    img = Image.new("RGB", (max(width, 40), max(height, 40)), "white")
    draw = ImageDraw.Draw(img)
    y = pad
    for line, h in zip(lines, heights):
        draw.text((pad, y), line, font=font, fill="black")
        y += h + gap
    return img


def main() -> int:
    from rapidocr import RapidOCR

    print("加载 RapidOCR（默认参数 = 内置 PP-OCRv6_rec_small）...")
    engine = RapidOCR()
    print(f"模型：{os.path.relpath(engine.cfg.Rec.model_path, os.getcwd())}"
          if getattr(engine.cfg.Rec, "model_path", None) else "模型：默认")
    print()

    ok, bad = [], []
    for name, text, font_file in SAMPLES:
        img = render(text, font_file)
        try:
            result = engine(img)
            txts = list(result.txts or [])
        except Exception as exc:  # noqa: BLE001
            print(f"{name:8} ❌ 识别异常: {exc}")
            bad.append(name)
            continue
        want = [line for line in text.split("\n") if line.strip()]
        got_text = "\n".join(txts)
        # 判定：每一行都要能在识别结果里找到（忽略空格）
        hits = 0
        for line in want:
            flat = line.replace(" ", "")
            if any(flat in t.replace(" ", "") or t.replace(" ", "") in flat
                   for t in txts if t.strip()):
                hits += 1
        ratio = hits / len(want) if want else 0
        mark = "✅" if ratio == 1 else ("⚠️" if ratio > 0 else "❌")
        if ratio == 1:
            ok.append(name)
        else:
            bad.append(name)
        print(f"{name:8} {mark} {hits}/{len(want)}  期望={want!r}")
        print(f"{'':9}识别={txts!r}")
        del got_text

    print()
    print(f"完整识别：{ok}")
    print(f"有问题：{bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
