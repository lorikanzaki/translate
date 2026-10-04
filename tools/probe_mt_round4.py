"""第四轮：补齐第二轮漏跑的候选；测试「分隔符批量」能否替代不支持的批量接口；探测单请求上限。"""
from __future__ import annotations

import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = (4, 8)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
H = {"User-Agent": UA}
SEP = "\n\u241e\n"
TESTS = ["hello world", "Settings", "Are you sure you want to delete this file?"]


def show(name, r, ms):
    ok = r is not None and r.status_code == 200
    print(f"{'✅' if ok else '❌'} {name} status={r.status_code if r is not None else None} {ms}ms")
    if r is not None:
        print(f"     body[:300]={(r.text or '')[:300]!r}")


def simple(name, method, url, **kwargs):
    t0 = time.perf_counter()
    try:
        r = requests.request(method, url, headers={**H, **kwargs.pop("headers", {})},
                             timeout=TIMEOUT, **kwargs)
        show(name, r, int((time.perf_counter() - t0) * 1000))
        return r
    except Exception as exc:
        print(f"❌ {name} {type(exc).__name__}: {str(exc)[:120]}")
        return None


print("=" * 72)
print("A. 补齐第二轮漏跑的候选")
print("=" * 72)
s = requests.Session()
s.headers.update(H)
try:
    home = s.get("https://fanyi.baidu.com/", timeout=TIMEOUT)
    print(f"GET fanyi.baidu.com/ -> {home.status_code} len={len(home.text)} "
          f"cookies={list(s.cookies.keys())}")
    tok = None
    for pat in (r"token['\"]?\s*[:=]\s*['\"]([0-9a-f]{32})['\"]", r"'token'\s*:\s*'([^']+)'"):
        import re
        m = re.search(pat, home.text)
        if m:
            tok = m.group(1)
            break
    print(f"  page token = {tok}")
    body = {"from": "en", "to": "zh", "query": "hello world", "transtype": "realtime",
            "simple_means_flag": "3", "sign": "123456.789012", "domain": "common"}
    if tok:
        body["token"] = tok
    r = s.post("https://fanyi.baidu.com/v2transapi", params={"from": "en", "to": "zh"},
               data=body, headers={"Referer": "https://fanyi.baidu.com/",
                                   "Origin": "https://fanyi.baidu.com"},
               timeout=TIMEOUT)
    show("  baidu v2transapi(带 cookie+token)", r, 0)
except Exception as exc:
    print(f"  ❌ baidu 流程失败 {type(exc).__name__}: {str(exc)[:120]}")

simple("niutrans /api/translate/web (POST)",
       "POST", "https://niutrans.com/api/translate/web",
       json={"from": "en", "to": "zh", "src_text": "hello world"})
simple("fanyi.qq.com /api/translate (POST)",
       "POST", "https://fanyi.qq.com/api/translate",
       data={"source": "en", "target": "zh", "sourceText": "hello world"},
       headers={"Referer": "https://fanyi.qq.com/",
                "Origin": "https://fanyi.qq.com"})
simple("aiskillstore/market 阿里变体",
       "POST", "https://translate.alibaba.com/api/translate/text",
       json={"srcLang": "en", "tgtLang": "zh", "domain": "general",
             "query": "hello world"})
# 搜狗：先拿 cookie
try:
    s2 = requests.Session()
    s2.headers.update({**H, "Referer": "https://fanyi.sogou.com/",
                       "Origin": "https://fanyi.sogou.com"})
    home2 = s2.get("https://fanyi.sogou.com/", timeout=TIMEOUT)
    print(f"\nGET fanyi.sogou.com/ -> {home2.status_code} cookies={list(s2.cookies.keys())}")
    r = s2.post("https://fanyi.sogou.com/api/transpc/text/result",
                json={"from": "en", "to": "zh-CHS", "text": "hello world",
                      "client": "pc", "fr": "browser_pc", "needQc": 1,
                      "s": "f0e0d0c0b0a0", "uuid": "a" * 32,
                      "exchangeFlag": 0},
                timeout=TIMEOUT)
    show("  sogou transpc(带 cookie+uuid)", r, 0)
except Exception as exc:
    print(f"  ❌ sogou 流程失败 {type(exc).__name__}: {str(exc)[:120]}")

print()
print("=" * 72)
print("B. 分隔符批量（SEP='\\n\\u241e\\n'）能否替代不支持批量的接口")
print("=" * 72)
joined = SEP.join(TESTS)


def split_back(text: str):
    parts = [p.strip() for p in text.split("\u241e")]
    return parts


# volcengine
t0 = time.perf_counter()
try:
    r = requests.post("https://translate.volcengine.com/crx/translate/v1",
                      json={"source_language": "en", "target_language": "zh", "text": joined},
                      headers={**H, "Content-Type": "application/json"}, timeout=TIMEOUT)
    print(f"volcengine 拼接: {r.status_code} {int((time.perf_counter()-t0)*1000)}ms "
          f"-> {split_back(r.json().get('translation',''))}")
except Exception as exc:
    print(f"volcengine 拼接 ❌ {type(exc).__name__}: {str(exc)[:100]}")

# transmart
t0 = time.perf_counter()
try:
    r = requests.post("https://transmart.qq.com/api/imt",
                      json={"header": {"fn": "auto_translation",
                                       "client_key": "browser-chrome-131.0.0.0-Windows-abc"},
                            "type": "plain", "model_category": "normal",
                            "source": {"lang": "en", "text_list": [joined]},
                            "target": {"lang": "zh"}},
                      headers={**H, "Content-Type": "application/json"}, timeout=TIMEOUT)
    print(f"transmart 拼接: {r.status_code} {int((time.perf_counter()-t0)*1000)}ms "
          f"-> {split_back((r.json().get('auto_translation') or [''])[0])}")
except Exception as exc:
    print(f"transmart 拼接 ❌ {type(exc).__name__}: {str(exc)[:100]}")

# yandex
t0 = time.perf_counter()
try:
    r = requests.post("https://translate.yandex.net/api/v1/tr.json/translate",
                      params={"srv": "android", "id": "0" * 32 + "-0-0"},
                      data=[("text", joined), ("lang", "en-zh")],
                      headers={**H,
                               "Content-Type": "application/x-www-form-urlencoded"},
                      timeout=TIMEOUT)
    print(f"yandex 拼接: {r.status_code} {int((time.perf_counter()-t0)*1000)}ms "
          f"-> {split_back((r.json().get('text') or [''])[0])}")
except Exception as exc:
    print(f"yandex 拼接 ❌ {type(exc).__name__}: {str(exc)[:100]}")

print()
print("=" * 72)
print("C. Edge 接口规模/上限探测")
print("=" * 72)
EDGE = "https://edge.microsoft.com/translate/translatetext"


def edge(payload, label, params=None):
    t0 = time.perf_counter()
    try:
        r = requests.post(EDGE,
                          params=params or {"from": "en", "to": "zh-Hans",
                                            "isEnterpriseClient": "false"},
                          json=payload, headers={**H, "Content-Type": "application/json"},
                          timeout=(4, 20))
        ms = int((time.perf_counter() - t0) * 1000)
        tail = ""
        if r.status_code == 200:
            try:
                j = r.json()
                tail = f" entries={len(j)} first={(j[0]['translations'][0]['text'] if j else None)!r} " \
                       f"last={(j[-1]['translations'][0]['text'][:30] if j else None)!r}"
            except Exception as exc:
                tail = f" parse-error {exc}"
        print(f"  {label}: {r.status_code} {ms}ms{tail} raw[:120]={r.text[:120]!r}")
    except Exception as exc:
        print(f"  {label} ❌ {type(exc).__name__}: {str(exc)[:110]}")


edge(["hello world"], "1 条")
edge(["hello world"] * 50, "50 条重复")
edge(["hello world"] * 200, "200 条重复")
edge(["This is sentence number %d about software settings." % i for i in range(100)],
     "100 条不同")
edge(["A" * 400], "单条 400 字符")
edge(["A word " * 1000], "单条 6000 字符")
edge(["hello world"], "from=auto 空 from", params={"from": "", "to": "zh-Hans",
                                                  "isEnterpriseClient": "false"})
t0 = time.perf_counter()
try:
    r = requests.post("https://edge.microsoft.com/translate/translatetext",
                      params={"from": "en", "to": "zh-Hans", "isEnterpriseClient": "false"},
                      data="[\"hello world\"]",
                      headers={**H, "Content-Type": "text/plain"}, timeout=TIMEOUT)
    print(f"  Content-Type=text/plain: {r.status_code} {int((time.perf_counter()-t0)*1000)}ms "
          f"{r.text[:120]!r}")
except Exception as exc:
    print(f"  text/plain ❌ {type(exc).__name__}: {str(exc)[:100]}")
