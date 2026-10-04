"""验证「自动换识别模型」的判定信号是否可靠。

背景：PP-OCRv6 内置模型只认 拉丁+日语+中文，俄语/韩语/阿拉伯语的字符集根本不在里面。
而 RapidOCR 的低分过滤会把识别失败的整行**丢掉**，所以只看 txts 会以为「这屏没有文字」。
于是需要一个信号来判断「检测到了文本、但识别模型认不了」。

本脚本比较两种调用的结果：
  A. engine(img)                        —— 检测 + 识别（正常路径）
  B. engine(img, use_cls=False, use_rec=False) —— 只检测，拿到文本框数量

若某语言的「检测框数 > 识别出行数」，说明有框被识别器丢了，可以据此换语言包重试。

**注意**：所有 A 跑完再跑所有 B。实测发现把 A/B 交错跑、且中间抛过异常时，
引擎内部状态会被污染，后续结果全是错的（第一次实测就是这么被骗的）。

用法：.venv\\Scripts\\python.exe tools\\_probe_ocr_autodetect.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("RAPIDOCR_LOG_LEVEL", "error")

from _probe_ocr_langs import SAMPLES, render  # noqa: E402


def main() -> int:
    from rapidocr import RapidOCR

    print("加载 RapidOCR（默认参数 = 内置 PP-OCRv6_rec_small）...")
    engine = RapidOCR()

    images = [(name, render(text, font)) for name, text, font in SAMPLES]

    # 阶段 A：完整的检测 + 识别
    print("\n=== 阶段 A：检测 + 识别（默认模型）===")
    full: dict[str, list[str]] = {}
    for name, img in images:
        result = engine(img)
        txts = [t for t in (result.txts or []) if t.strip()]
        full[name] = txts
        print(f"{name:8} 识别 {len(txts)} 行  {txts!r}")

    # 阶段 B：只检测，不识别
    print("\n=== 阶段 B：只检测（拿文本框数量）===")
    boxes: dict[str, int] = {}
    for name, img in images:
        try:
            result = engine(img, use_cls=False, use_rec=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:8} ❌ 只检测异常: {type(exc).__name__}: {exc}")
            boxes[name] = -1
            continue
        n = 0 if result.boxes is None else len(result.boxes)
        boxes[name] = n
        print(f"{name:8} 检测框 {n}")

    # 结论：检测到框但识别不出 => 信号成立
    print("\n=== 结论 ===")
    usable, missing = [], []
    for name, _ in images:
        n_box, n_txt = boxes[name], len(full[name])
        if n_box < 0:
            verdict = "只检测不可用"
        elif n_box == 0:
            verdict = "检测不到文字（信号无效）"
        elif n_txt >= n_box:
            verdict = "识别正常"
        else:
            verdict = f"★ 有 {n_box - n_txt} 个框识别失败（信号成立）"
        print(f"{name:8} 框{n_box:3} 行{n_txt:3}  {verdict}")
        if n_txt >= 1:
            usable.append(name)
        else:
            missing.append(name)
    print(f"\n默认模型能识别：{usable}")
    print(f"默认模型识别不了：{missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
