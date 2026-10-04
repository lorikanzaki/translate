"""第八轮：Bing 长度上限 + 分隔符合并批量（对 volcengine / bing 的可行性与对齐可靠性）。"""
from __future__ import annotations

import re
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = (4, 15)
H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")}

s = requests.Session()
s.headers.update(H)
page = s.get("https://cn.bing.com/translator", timeout=TIMEOUT)
IG = re.search(r'IG:"([^"]+)"', page.text).group(1)
IID = re.search(r'data-iid="([^"]+)"', page.text).group(1)
P = [p.strip().strip('"') for p in
     re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", page.text)
     .group(1).split(",")]
KEY, TOKEN = P[0], P[1]
N = [0]


def bing(text):
    N[0] += 1
    t0 = time.perf_counter()
    r = s.post("https://cn.bing.com/ttranslatev3",
               params={"isVertical": "1", "IG": IG, "IID": f"{IID}.{N[0]}"},
               data={"fromLang": "en", "text": text, "to": "zh-Hans",
                     "token": TOKEN, "key": KEY},
               headers={"Content-Type": "application/x-www-form-urlencoded"},
               timeout=TIMEOUT)
    ms = int((time.perf_counter() - t0) * 1000)
    try:
        return r.json()[0]["translations"][0]["text"], ms
    except Exception:
        return f"<{r.status_code} {r.text[:60]}>", ms


def volc(text):
    t0 = time.perf_counter()
    r = requests.post("https://translate.volcengine.com/crx/translate/v1",
                      json={"source_language": "en", "target_language": "zh", "text": text},
                      headers={**H, "Content-Type": "application/json"}, timeout=TIMEOUT)
    ms = int((time.perf_counter() - t0) * 1000)
    return r.json().get("translation", ""), ms, r.status_code


print("=" * 72)
print("A. cn.bing.com 单条长度上限")
print("=" * 72)
for n in (200, 500, 900, 1100, 1600, 2500):
    src = ("word " * (n // 5))[:n]
    out, ms = bing(src)
    print(f"  源 {len(src):5} 字符 -> 译文 {len(out):5} 字符  {ms}ms  "
          f"完整={len(out) >= len(src) * 0.3}  尾部={out[-24:]!r}")

print()
print("=" * 72)
print("B. 分隔符合并批量：cn.bing.com")
print("=" * 72)
items = ["Settings", "Copy", "Paste", "Save", "Zoom in", "Trash", "Sign in",
         "Free up space", "Sync now", "Download"]
for label, sep in [("单个换行 \\n", "\n"), ("SEP \\n\\u241e\\n", "\n\u241e\n")]:
    joined = sep.join(items)
    out, ms = bing(joined)
    back = [x.strip() for x in out.split("\u241e")] if "\u241e" in sep else [
        x.strip() for x in out.split("\n")]
    print(f"  --- {label} ---  源 {len(joined)} 字符 {ms}ms")
    print(f"    译文原始 = {out!r}")
    print(f"    切回 {len(back)} 段: {back}  对齐={back == [i for i in items]}  "
          f"数量相等={len(back) == len(items)}")

print()
print("=" * 72)
print("C. 分隔符合并批量：volcengine crx")
print("=" * 72)
for label, sep in [("单个换行 \\n", "\n"), ("SEP \\n\\u241e\\n", "\n\u241e\n"),
                   ("SEP |||", "|||")]:
    joined = sep.join(items)
    out, ms, code = volc(joined)
    if "\u241e" in sep:
        back = [x.strip() for x in out.split("\u241e")]
    elif sep == "|||":
        back = [x.strip() for x in out.split("|||")]
    else:
        back = [x.strip() for x in out.split("\n") if x.strip()]
    print(f"  --- {label} ---  源 {len(joined)} 字符 {code} {ms}ms")
    print(f"    译文原始 = {out[:200]!r}")
    print(f"    切回 {len(back)} 段: {back}")
