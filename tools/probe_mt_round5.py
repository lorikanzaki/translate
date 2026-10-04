"""第五轮：深度验证 cn.bing.com /ttranslatev3（token+key 流程）——质量、批量、限流、token 寿命。"""
from __future__ import annotations

import re
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = (4, 10)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
H = {"User-Agent": UA}
HOST = "cn.bing.com"

TESTS = [
    "hello world", "Settings", "Are you sure you want to delete this file?",
    "Copy", "Paste", "Save", "Zoom in", "Trash", "Sign in", "Free up space",
    "Sync now", "Are you sure you want to delete this file? This action cannot be undone.",
]


class Bing:
    def __init__(self, host=HOST):
        self.host = host
        self.s = requests.Session()
        self.s.headers.update(H)
        self.ig = self.iid = self.key = self.token = None

    def boot(self):
        t0 = time.perf_counter()
        page = self.s.get(f"https://{self.host}/translator", timeout=TIMEOUT)
        ms = int((time.perf_counter() - t0) * 1000)
        text = page.text
        self.ig = re.search(r'IG:"([^"]+)"', text).group(1)
        self.iid = re.search(r'data-iid="([^"]+)"', text).group(1)
        parts = [p.strip().strip('"') for p in
                 re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", text)
                 .group(1).split(",")]
        self.key, self.token = parts[0], parts[1]
        print(f"  boot {page.status_code} {ms}ms IG={self.ig[:12]}... IID={self.iid} "
              f"key={self.key} token={self.token[:16]}... exp={parts[2] if len(parts) > 2 else '?'}")
        return page.status_code == 200

    def tr(self, texts, frm="en", to="zh-Hans", iid_suffix=".1"):
        data = [("fromLang", frm)] + [("text", t) for t in texts] + [
            ("to", to), ("token", self.token), ("key", self.key)]
        t0 = time.perf_counter()
        r = self.s.post(f"https://{self.host}/ttranslatev3",
                        params={"isVertical": "1", "IG": self.ig,
                                "IID": self.iid + iid_suffix},
                        data=data,
                        headers={"Referer": f"https://{self.host}/translator",
                                 "Origin": f"https://{self.host}",
                                 "Content-Type": "application/x-www-form-urlencoded"},
                        timeout=TIMEOUT)
        return r, int((time.perf_counter() - t0) * 1000)


print("=" * 72)
print("A. cn.bing.com 质量对比（12 条）")
print("=" * 72)
b = Bing()
b.boot()
r, ms = b.tr(TESTS)
print(f"  批量 12 条一次发: status={r.status_code} {ms}ms")
try:
    j = r.json()
    if isinstance(j, list):
        for src, item in zip(TESTS, j):
            t = (item.get("translations") or [{}])[0].get("text")
            print(f"    {src!r:72} -> {t!r}   usedLLM={item.get('usedLLM')}")
    else:
        print(f"    {j}")
except Exception as exc:
    print(f"  parse error {exc} raw={r.text[:300]!r}")

print()
print("=" * 72)
print("B. 逐条发（对照）")
print("=" * 72)
for i, t in enumerate(TESTS[:6]):
    r, ms = b.tr([t], iid_suffix=f".{i+2}")
    out = ""
    try:
        out = (r.json()[0]["translations"][0]["text"]) if r.status_code == 200 and r.text else r.text[:60]
    except Exception:
        out = r.text[:60]
    print(f"    {t!r:60} -> {out!r}  ({r.status_code}, {ms}ms)")

print()
print("=" * 72)
print("C. 限流：连打 15 次")
print("=" * 72)
lat, ok = [], 0
for i in range(15):
    r, ms = b.tr(["hello world"], iid_suffix=f".{100+i}")
    lat.append(ms)
    good = r.status_code == 200 and "你" in r.text
    ok += 1 if good else 0
    if not good:
        print(f"    #{i} 失败 {r.status_code} {r.text[:120]!r}")
lat.sort()
print(f"  成功 {ok}/15  min={lat[0]}ms p50={lat[len(lat)//2]}ms max={lat[-1]}ms")

print()
print("=" * 72)
print("D. 老 token 复用 & from=auto")
print("=" * 72)
print("  等 8 秒后用同一个 token 再发 3 次 ...")
time.sleep(8)
for i in range(3):
    r, ms = b.tr(["Settings"], iid_suffix=f".{200+i}")
    print(f"    复用 #{i} {r.status_code} {ms}ms {r.text[:90]!r}")
r, ms = b.tr(["Bonjour tout le monde"], frm="auto", iid_suffix=".301")
print(f"    from=auto: {r.status_code} {ms}ms {r.text[:160]!r}")
r, ms = b.tr(["hello world"], to="zh-Hant", iid_suffix=".302")
print(f"    to=zh-Hant: {r.status_code} {ms}ms {r.text[:160]!r}")

print()
print("=" * 72)
print("E. 只用 IG/IID 不带 token/key（确认必须带）")
print("=" * 72)
save = (b.token, b.key)
b.token = b.key = ""
r, ms = b.tr(["hello world"], iid_suffix=".401")
print(f"    无 token/key: {r.status_code} {ms}ms {r.text[:160]!r}")
b.token, b.key = save

print()
print("=" * 72)
print("F. GET /translator 一次能刷多少 token（token 过期后的续期成本）")
print("=" * 72)
for i in range(2):
    t0 = time.perf_counter()
    b2 = Bing()
    b2.boot()
    print(f"    第 {i+1} 次 boot 共用 {int((time.perf_counter()-t0)*1000)}ms")
