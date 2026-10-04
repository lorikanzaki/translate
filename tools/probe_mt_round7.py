"""第七轮：cn.bing.com 逐条 12 串质量 + 多行/HTML 标签/批量语义验证。"""
from __future__ import annotations

import re
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = (4, 10)
H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
     "Referer": "https://cn.bing.com/translator", "Origin": "https://cn.bing.com"}
TESTS = [
    "hello world", "Settings", "Are you sure you want to delete this file?",
    "Copy", "Paste", "Save", "Zoom in", "Trash", "Sign in", "Free up space",
    "Sync now", "Are you sure you want to delete this file? This action cannot be undone.",
]

s = requests.Session()
s.headers.update(H)
page = s.get("https://cn.bing.com/translator", timeout=TIMEOUT)
IG = re.search(r'IG:"([^"]+)"', page.text).group(1)
IID = re.search(r'data-iid="([^"]+)"', page.text).group(1)
parts = [p.strip().strip('"') for p in
         re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", page.text)
         .group(1).split(",")]
KEY, TOKEN = parts[0], parts[1]
counter = [0]


def bing(texts, frm="en", to="zh-Hans", extra=None):
    counter[0] += 1
    data = [("fromLang", frm)] + [("text", t) for t in texts] + [("to", to),
                                                                 ("token", TOKEN), ("key", KEY)]
    if extra:
        data += extra
    t0 = time.perf_counter()
    r = s.post("https://cn.bing.com/ttranslatev3",
               params={"isVertical": "1", "IG": IG, "IID": f"{IID}.{counter[0]}"},
               data=data, headers={"Content-Type": "application/x-www-form-urlencoded"},
               timeout=TIMEOUT)
    return r, int((time.perf_counter() - t0) * 1000)


print("=" * 72)
print("A. cn.bing.com 逐条 12 串")
print("=" * 72)
for t in TESTS:
    r, ms = bing([t])
    try:
        item = r.json()[0]
        out = item["translations"][0]["text"]
        llm = item.get("usedLLM")
    except Exception:
        out, llm = r.text[:80], None
    print(f"  {t!r:72} -> {out!r}  usedLLM={llm} ({r.status_code}, {ms}ms)")

print()
print("=" * 72)
print("B. 多行 / HTML 标签 / 分隔符")
print("=" * 72)
cases = {
    "多行 3 段": "Copy\nPaste\nSave",
    "带 HTML 标签": "<b>Settings</b> and <i>Copy</i>",
    "带 SEP 分隔": "hello world\n\u241e\nSettings",
    "含 URL": "Visit https://example.com/settings to change Copy options",
    "软件长句": "You can change the Copy and Paste behavior in Settings.",
}
for label, text in cases.items():
    r, ms = bing([text])
    try:
        out = r.json()[0]["translations"][0]["text"]
    except Exception:
        out = r.text[:100]
    print(f"  {label:12} {text!r}")
    print(f"               -> {out!r} ({r.status_code}, {ms}ms)")

print()
print("=" * 72)
print("C. 一次带多个 text 参数到底发生什么")
print("=" * 72)
r, ms = bing(["Copy", "Paste", "Save"])
print(f"  发送 3 个 text 参数 -> {r.status_code} {ms}ms")
print(f"  raw = {r.text[:400]!r}")
try:
    j = r.json()
    print(f"  返回条数 = {len(j)}")
except Exception as exc:
    print(f"  解析失败 {exc}")

print()
print("=" * 72)
print("D. 空文本/超长文本")
print("=" * 72)
r, ms = bing([""])
print(f"  空串: {r.status_code} {ms}ms {r.text[:150]!r}")
r, ms = bing(["A word " * 300])
print(f"  1800 字符: {r.status_code} {ms}ms 长度={len(r.text)} 前 120={r.text[:120]!r}")
