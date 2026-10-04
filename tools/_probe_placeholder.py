"""探测：把「绝不能翻译」的片段替换成占位符，接口会不会原样保留？

如果占位符能安全往返，就能在送翻译前把 origin/main、C:\\Users\\x、user_id、
v1.2.3 这类标识符挖出来、翻完再放回去，避免被当成普通单词直译
（实测 "Branch not found: origin/main" 被翻成「未找到分支：起源/主线」）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import Translator  # noqa: E402

CASES = [
    ("菱形占位", "Branch not found: \u2776\u2777\u2778\u2779"),
    ("花括号", "Branch not found: {{0}}"),
    ("双井号", "Branch not found: ##0##"),
    ("尖括号数字", "Branch not found: <0>"),
    ("全角括号", "Branch not found: \uff08\uff100\uff11\uff09"),
    ("private use", "Branch not found: \ue000\ue001"),
    ("% 百分号", "Branch not found: %0%"),
    ("dollar", "Branch not found: $0$"),
]


def main() -> int:
    translator = Translator(timeout=20)
    translator.use_glossary = False
    for name, text in CASES:
        outcome = translator.translate([text], "zh-CN", "")
        print(f"[{name}] {text!r}")
        print(f"    -> {outcome.translations[0] if outcome.translations else '<空>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
