"""第六轮：Pollinations 延迟/模型对比；volcengine 连打 30 次；Edge 长时段稳定性。"""
from __future__ import annotations

import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")}
PROMPT = ("Translate the following into Simplified Chinese. Output only the translation, "
          "no explanation:\n")
SAMPLES = ["Settings", "Are you sure you want to delete this file?", "Sync now"]

print("=" * 72)
print("A. Pollinations：模型/延迟")
print("=" * 72)
try:
    r = requests.get("https://text.pollinations.ai/models", headers=H, timeout=(4, 10))
    print(f"  GET /models {r.status_code} {r.text[:400]!r}")
except Exception as exc:
    print(f"  ❌ /models {type(exc).__name__}: {str(exc)[:100]}")

for model in ["openai", "openai-fast", "mistral", "gemini"]:
    for sample in SAMPLES[:1]:
        t0 = time.perf_counter()
        try:
            r = requests.post("https://text.pollinations.ai/openai", headers=H,
                              json={"model": model, "private": True, "seed": 42,
                                    "messages": [{"role": "user",
                                                  "content": PROMPT + sample}]},
                              timeout=(4, 12))
            ms = int((time.perf_counter() - t0) * 1000)
            out = ""
            try:
                out = r.json()["choices"][0]["message"]["content"]
            except Exception:
                out = r.text[:120]
            print(f"  {model:14} {r.status_code} {ms}ms -> {out[:80]!r}")
        except Exception as exc:
            print(f"  {model:14} ❌ {type(exc).__name__}: {str(exc)[:90]}")

print("\n  --- Pollinations 逐条测 3 条（openai, 串行）---")
lat = []
for s in SAMPLES:
    t0 = time.perf_counter()
    try:
        r = requests.post("https://text.pollinations.ai/openai", headers=H,
                          json={"model": "openai", "private": True,
                                "messages": [{"role": "user", "content": PROMPT + s}]},
                          timeout=(4, 12))
        ms = int((time.perf_counter() - t0) * 1000)
        lat.append(ms)
        try:
            out = r.json()["choices"][0]["message"]["content"]
        except Exception:
            out = r.text[:100]
        print(f"    {s!r:60} -> {out[:60]!r} ({r.status_code}, {ms}ms)")
    except Exception as exc:
        print(f"    {s!r:60} ❌ {type(exc).__name__}: {str(exc)[:80]}")
if lat:
    lat.sort()
    print(f"  延迟 min={lat[0]} p50={lat[len(lat)//2]} max={lat[-1]}")

print()
print("=" * 72)
print("B. volcengine crx 连打 30 次（每 ~0.2s 一次）")
print("=" * 72)
lat, ok, errs = [], 0, {}
for i in range(30):
    t0 = time.perf_counter()
    try:
        r = requests.post("https://translate.volcengine.com/crx/translate/v1",
                          json={"source_language": "en", "target_language": "zh",
                                "text": f"Settings {i}"},
                          headers={**H, "Content-Type": "application/json"},
                          timeout=(4, 8))
        ms = int((time.perf_counter() - t0) * 1000)
        lat.append(ms)
        j = {}
        try:
            j = r.json()
        except Exception:
            pass
        good = r.status_code == 200 and "设" in str(j.get("translation", ""))
        ok += 1 if good else 0
        if not good:
            errs[f"{r.status_code}"] = errs.get(f"{r.status_code}", 0) + 1
            print(f"    #{i} 失败 {r.status_code} {r.text[:120]!r}")
    except Exception as exc:
        errs[type(exc).__name__] = errs.get(type(exc).__name__, 0) + 1
lat.sort()
print(f"  成功 {ok}/30  min={lat[0] if lat else '-'} p50={lat[len(lat)//2] if lat else '-'} "
      f"max={lat[-1] if lat else '-'}ms  errors={errs}")

print("\n  --- 一次性 30 条并发（模拟截图翻译场景）---")
from concurrent.futures import ThreadPoolExecutor


def one(i):
    t0 = time.perf_counter()
    try:
        r = requests.post("https://translate.volcengine.com/crx/translate/v1",
                          json={"source_language": "en", "target_language": "zh",
                                "text": f"Option {i}"},
                          headers={**H, "Content-Type": "application/json"},
                          timeout=(4, 8))
        return r.status_code, int((time.perf_counter() - t0) * 1000)
    except Exception as exc:
        return type(exc).__name__, int((time.perf_counter() - t0) * 1000)


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=10) as pool:
    res = list(pool.map(one, range(30)))
total = int((time.perf_counter() - t0) * 1000)
okn = sum(1 for s, _ in res if s == 200)
bad = {str(s): sum(1 for x, _ in res if x == s) for s, _ in res if s != 200}
print(f"  并发 30 条（10 线程）总耗时 {total}ms 成功 {okn}/30 失败分布={bad}")

print()
print("=" * 72)
print("C. Edge 长时段稳定性：每 3 秒一次共 10 次")
print("=" * 72)
lat2 = []
for i in range(10):
    t0 = time.perf_counter()
    try:
        r = requests.post("https://edge.microsoft.com/translate/translatetext",
                          params={"from": "en", "to": "zh-Hans",
                                  "isEnterpriseClient": "false"},
                          json=[f"Update {i} is available now."],
                          headers={**H, "Content-Type": "application/json"},
                          timeout=(4, 10))
        ms = int((time.perf_counter() - t0) * 1000)
        lat2.append(ms)
        out = r.json()[0]["translations"][0]["text"] if r.status_code == 200 else r.text[:60]
        print(f"    #{i} {r.status_code} {ms}ms -> {out!r}")
    except Exception as exc:
        print(f"    #{i} ❌ {type(exc).__name__}: {str(exc)[:80]}")
    time.sleep(3)
if lat2:
    lat2.sort()
    print(f"  延迟 min={lat2[0]} p50={lat2[len(lat2)//2]} max={lat2[-1]}ms")
