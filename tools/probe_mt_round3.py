"""第三轮：修正 360、Bing(cn) token 流程、Pollinations 免费 LLM、质量与限流对比。"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = (4, 8)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
H = {"User-Agent": UA}


def show(name, r, ms):
    print(f"{'✅' if r is not None and r.status_code == 200 else '❌'} {name} "
          f"status={r.status_code if r is not None else None} {ms}ms")
    if r is not None:
        print(f"     body[:200]={(r.text or '')[:200]!r}")


# --------------------------------------------------------------- Bing token 流程
def bing_flow(host: str):
    print(f"\n--- Bing token 流程 @ {host} ---")
    s = requests.Session()
    s.headers.update(H)
    t0 = time.perf_counter()
    try:
        page = s.get(f"https://{host}/translator", timeout=TIMEOUT)
    except Exception as exc:
        print(f"  ❌ 首页失败 {type(exc).__name__}: {str(exc)[:100]}")
        return
    print(f"  GET /translator status={page.status_code} {int((time.perf_counter()-t0)*1000)}ms "
          f"len={len(page.text)}")
    text = page.text
    ig = re.search(r'IG:"([^"]+)"', text)
    iid = re.search(r'data-iid="([^"]+)"', text)
    abuse = re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", text)
    print(f"  IG={ig.group(1) if ig else None} IID={iid.group(1) if iid else None} "
          f"abuse={abuse.group(1)[:80] if abuse else None}")
    if not (ig and iid):
        print("  ❌ 页面里找不到 IG/IID，token 流程不可用")
        return
    key = token = None
    if abuse:
        parts = [p.strip().strip('"') for p in abuse.group(1).split(",")]
        if len(parts) >= 2:
            key, token = parts[0], parts[1]
    for extra in ({"token": token, "key": key} if token else {}), {}:
        data = {"fromLang": "en", "text": "hello world", "to": "zh-Hans"}
        data.update({k: v for k, v in extra.items() if v})
        t0 = time.perf_counter()
        try:
            r = s.post(f"https://{host}/ttranslatev3",
                       params={"isVertical": "1", "IG": ig.group(1),
                               "IID": iid.group(1) + ".1"},
                       data=data,
                       headers={"Referer": f"https://{host}/translator",
                                "Origin": f"https://{host}",
                                "Content-Type": "application/x-www-form-urlencoded"},
                       timeout=TIMEOUT)
            show(f"  POST /ttranslatev3 (extra={list(extra)})", r,
                 int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            print(f"  ❌ POST /ttranslatev3 {type(exc).__name__}: {str(exc)[:100]}")


# --------------------------------------------------------------- 360 修正
def fanyi360():
    print("\n--- 360 翻译（带 cookie / 修正路径）---")
    s = requests.Session()
    s.headers.update({**H, "Referer": "https://fanyi.so.com/",
                      "Origin": "https://fanyi.so.com"})
    try:
        home = s.get("https://fanyi.so.com/", timeout=TIMEOUT)
        print(f"  GET / status={home.status_code} cookies={list(s.cookies.keys())}")
    except Exception as exc:
        print(f"  ❌ 首页失败 {type(exc).__name__}: {str(exc)[:80]}")
    for label, url, method, kwargs in [
        ("POST /index/search",
         "https://fanyi.so.com/index/search?prob=1&rel=0", "POST",
         {"data": {"eng": "1", "validate": "", "query": "hello world"}}),
        ("GET /index/search",
         "https://fanyi.so.com/index/search", "GET",
         {"params": {"eng": "1", "validate": "", "query": "hello world"}}),
        ("POST /index/search (json)",
         "https://fanyi.so.com/index/search", "POST",
         {"json": {"eng": "1", "validate": "", "query": "hello world"}}),
    ]:
        t0 = time.perf_counter()
        try:
            r = s.request(method, url, timeout=TIMEOUT, **kwargs)
            show(f"  {label}", r, int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            print(f"  ❌ {label} {type(exc).__name__}: {str(exc)[:100]}")


# --------------------------------------------------------------- 免费 LLM 端点
def free_llm():
    print("\n--- 免费无 key LLM 端点 ---")
    cases = [
        ("Pollinations GET",
         "GET", "https://text.pollinations.ai/Translate%20to%20Chinese%3A%20hello%20world",
         {}),
        ("Pollinations POST /openai",
         "POST", "https://text.pollinations.ai/openai",
         {"json": {"model": "openai",
                   "messages": [{"role": "user",
                                 "content": "Translate into Simplified Chinese, output only the translation: Are you sure you want to delete this file?"}]}}),
        ("Pollinations POST /v1/chat/completions",
         "POST", "https://text.pollinations.ai/v1/chat/completions",
         {"json": {"model": "openai",
                   "messages": [{"role": "user", "content": "translate: hello world"}]}}),
        ("chatanywhere 无key", "POST", "https://api.chatanywhere.tech/v1/chat/completions",
         {"json": {"model": "gpt-4o-mini",
                   "messages": [{"role": "user", "content": "translate: hello"}]}}),
        ("duckduckgo AI chat", "GET",
         "https://duckduckgo.com/duckchat/v1/status", {"headers": {"x-vqd-accept": "1"}}),
    ]
    for name, method, url, kwargs in cases:
        t0 = time.perf_counter()
        try:
            r = requests.request(method, url, headers=H, timeout=TIMEOUT, **kwargs)
            show(f"  {name}", r, int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            print(f"  ❌ {name} {type(exc).__name__}: {str(exc)[:110]}")


# --------------------------------------------------------------- 质量 / 限流
QUALITY_STRINGS = [
    "hello world", "Settings", "Are you sure you want to delete this file?",
    "Copy", "Paste", "Save", "Zoom in", "Trash", "Sign in", "Free up space",
    "Sync now", "Are you sure you want to delete this file? This action cannot be undone.",
]


def q_edge(texts):
    r = requests.post("https://edge.microsoft.com/translate/translatetext",
                      params={"from": "en", "to": "zh-Hans",
                              "isEnterpriseClient": "false"},
                      json=texts, headers={**H, "Content-Type": "application/json"},
                      timeout=TIMEOUT)
    return [i["translations"][0]["text"] for i in r.json()], r.status_code


def q_volc(texts):
    out = []
    for t in texts:
        r = requests.post("https://translate.volcengine.com/crx/translate/v1",
                          json={"source_language": "en", "target_language": "zh",
                                "text": t},
                          headers={**H, "Content-Type": "application/json"},
                          timeout=TIMEOUT)
        out.append(r.json().get("translation", ""))
    return out, 200


def q_transmart(texts):
    r = requests.post("https://transmart.qq.com/api/imt",
                      json={"header": {"fn": "auto_translation",
                                       "client_key": "browser-chrome-131.0.0.0-Windows-abc"},
                            "type": "plain", "model_category": "normal",
                            "source": {"lang": "en", "text_list": texts},
                            "target": {"lang": "zh"}},
                      headers={**H, "Content-Type": "application/json"},
                      timeout=TIMEOUT)
    return r.json().get("auto_translation", []), r.status_code


def q_yandex(texts):
    data = [("text", t) for t in texts] + [("lang", "en-zh")]
    r = requests.post("https://translate.yandex.net/api/v1/tr.json/translate",
                      params={"srv": "android", "id": "0" * 32 + "-0-0"},
                      data=data,
                      headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
                      timeout=TIMEOUT)
    return r.json().get("text", []), r.status_code


def q_youdao(texts):
    out = []
    for t in texts:
        r = requests.post("https://aidemo.youdao.com/trans",
                          data={"q": t, "from": "en", "to": "zh-CHS"},
                          headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
                          timeout=TIMEOUT)
        j = r.json()
        out.append("".join(x.get("dst", "") for x in j.get("translateResults", []))
                   or str(j.get("errorCode", "?")))
    return out, 200


def q_mymemory(texts):
    out = []
    for t in texts:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": t[:480], "langpair": "en|zh-CN"},
                         headers=H, timeout=TIMEOUT)
        out.append((r.json().get("responseData") or {}).get("translatedText", ""))
    return out, 200


PROVIDERS = [
    ("edge(微软)", q_edge), ("volcengine(火山)", q_volc),
    ("transmart(腾讯)", q_transmart), ("yandex", q_yandex),
    ("youdao(有道)", q_youdao), ("mymemory", q_mymemory),
]


def burst(name, fn, n=12):
    lat, ok = [], 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            res, status = fn(["hello world"])
            good = bool(res) and status == 200 and any(res)
        except Exception:
            good = False
        lat.append(int((time.perf_counter() - t0) * 1000))
        ok += 1 if good else 0
    lat.sort()
    return {"name": name, "ok": f"{ok}/{n}", "min": lat[0],
            "p50": lat[len(lat) // 2], "max": lat[-1]}


def main():
    bing_flow("cn.bing.com")
    bing_flow("www.bing.com")
    fanyi360()
    free_llm()

    print("\n" + "=" * 72)
    print("质量对比：同一批英文 -> 中文")
    print("=" * 72)
    table = {}
    for pname, fn in PROVIDERS:
        t0 = time.perf_counter()
        try:
            outs, status = fn(QUALITY_STRINGS)
            ms = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            outs, ms = [], int((time.perf_counter() - t0) * 1000)
            print(f"\n--- {pname} ❌ {type(exc).__name__}: {str(exc)[:100]}")
        table[pname] = outs
        print(f"\n--- {pname} (12 条共 {ms}ms) ---")
        for src, dst in zip(QUALITY_STRINGS, outs):
            print(f"    {src!r:72} -> {dst!r}")
    with open("tools/_probe_quality.json", "w", encoding="utf-8") as fh:
        json.dump({"strings": QUALITY_STRINGS, "table": table}, fh,
                  ensure_ascii=False, indent=1)

    print("\n" + "=" * 72)
    print("连打 12 次看限流")
    print("=" * 72)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for r in pool.map(lambda p: burst(p[0], p[1]), PROVIDERS):
            print(f"  {r['name']:20} 成功 {r['ok']:6} min={r['min']}ms "
                  f"p50={r['p50']}ms max={r['max']}ms")


if __name__ == "__main__":
    main()
