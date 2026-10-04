"""对比不同调用方式对译文质量的影响（同一批界面文本）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

URL = ("https://edge.microsoft.com/translate/translatetext"
       "?from=en&to=zh-Hans&isEnterpriseClient=false")
HEAD = {"Content-Type": "application/json"}

CASES = [
    "Now playing",
    "Shuffle all",
    "Lyrics not available",
    "Load last save",
    "Controller disconnected",
    "Show less",
    "Build succeeded with 3 warnings.",
    "Branch not found: origin/main",
    "Press any key to continue",
    "Track my order",
]


def call(payload) -> list[str]:
    r = requests.post(URL, headers=HEAD, json=payload, timeout=9)
    data = r.json()
    return [item["translations"][0]["text"] for item in data]


def main() -> int:
    grouped = call(CASES)
    joined = "\n".join(CASES)
    context = call([joined])[0].split("\n")
    print(f"{'原文':38} | {'逐条':26} | 整屏连排（带上下文）")
    print("-" * 110)
    for i, src in enumerate(CASES):
        one = grouped[i] if i < len(grouped) else "?"
        many = context[i] if i < len(context) else "(行数不符)"
        mark = "  <<<" if one.strip() != many.strip() else ""
        print(f"{src:38} | {one:26} | {many}{mark}")

    # 单独发 vs 与相邻行一起发：短词在语境里会不会变好
    print("\n--- 单发 vs 带上相邻行一起发 ---")
    for word, neighbour in [("Build", "Build succeeded with 3 warnings."),
                            ("Play", "Now playing"),
                            ("Save", "Load last save"),
                            ("Origin", "Branch not found: origin/main")]:
        alone = call([word])[0]
        near = call([f"{word}\n{neighbour}"])[0].split("\n")
        print(f"{word!r:12} 单发={alone!r:18} 带上下文={near}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
