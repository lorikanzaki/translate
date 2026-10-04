"""测源语言的影响：同一段日文/英文，from= 不同时结果差多少。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

BASE = "https://edge.microsoft.com/translate/translatetext?to=zh-Hans&isEnterpriseClient=false"
HEAD = {"Content-Type": "application/json"}

SAMPLES = {
    "日文歌词": ["溢れ出した君の涙が", "心の奥に響いている"],
    "日文界面": ["設定", "保存に失敗しました", "もう一度お試しください"],
    "英文界面": ["Now playing", "Shuffle all", "Load last save"],
    "英文句子": ["Failed to connect to the server. Retrying in 5 seconds."],
}


def call(texts, src):
    url = BASE + (f"&from={src}" if src else "")
    r = requests.post(url, headers=HEAD, json=texts, timeout=9)
    data = r.json()
    return [item["translations"][0]["text"] for item in data]


def main() -> int:
    for label, texts in SAMPLES.items():
        print(f"\n=== {label} ===")
        for src in ("", "en", "ja", "auto"):
            try:
                out = call(texts, src)
            except Exception as exc:
                out = [f"<{type(exc).__name__}: {exc}>"]
            print(f"  from={src or '(空)':6} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
