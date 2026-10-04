"""第二轮：新增候选连通性 + 已通接口的质量/批量/限流对比。"""
from __future__ import annotations

import json
import os
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
# 从环境变量读，绝不把 key 写进源码（写进去会被 GitHub 机密扫描抓到，
# 而且这个文件会公开）。当年这一行硬编码过一把网上流传的 Google key。
GOOGLE_KEY = os.environ.get("GOOGLE_KEY", "")

# ------------------------------------------------------------------ 新增候选
NEW = [
    ("G1_google_translateHtml(公开key)", "POST",
     "https://translate-pa.googleapis.com/v1/translateHtml",
     {"Content-Type": "application/json+protobuf",
      "X-Goog-API-Key": GOOGLE_KEY},
     {"body_raw": [[["hello world"], "en", "zh"], "wt_lib"]}),
    ("G2_google_translateHtml_key_in_url", "POST",
     "https://translate-pa.googleapis.com/v1/translateHtml?key=" + GOOGLE_KEY,
     {"Content-Type": "application/json+protobuf"},
     {"body_raw": [[["hello world"], "en", "zh"], "wt_lib"]}),
    ("G3_deeplx_org_nokey", "POST", "https://api.deeplx.org/translate",
     {"Content-Type": "application/json"},
     {"json": {"text": "hello world", "source_lang": "EN", "target_lang": "ZH"}}),
    ("G4_deeplx_vercel", "POST", "https://deeplx.vercel.app/translate",
     {"Content-Type": "application/json"},
     {"json": {"text": "hello world", "source_lang": "EN", "target_lang": "ZH"}}),
    ("G5_deeplx_multi", "POST", "https://api.deeplx.org/helloworld/translate",
     {"Content-Type": "application/json"},
     {"json": {"text": "hello world", "source_lang": "EN", "target_lang": "ZH"}}),
    ("G6_360_fanyi", "POST", "https://fanyi.so.com/index/search",
     {"Content-Type": "application/x-www-form-urlencoded",
      "Referer": "https://fanyi.so.com/"},
     {"data": {"eng": "1", "validate": "", "ignore_trans": "0",
               "query": "hello world"}}),
    ("G7_360_fanyi_json", "GET", "https://fanyi.so.com/index/search",
     {"Referer": "https://fanyi.so.com/"},
     {"params": {"eng": "1", "validate": "", "query": "hello world"}}),
    ("G8_volc_crx_autolang", "POST",
     "https://translate.volcengine.com/crx/translate/v1",
     {"Content-Type": "application/json"},
     {"json": {"source_language": "auto", "target_language": "zh",
               "text": "hello world"}}),
    ("G9_volc_crx_textlist", "POST",
     "https://translate.volcengine.com/crx/translate/v1",
     {"Content-Type": "application/json"},
     {"json": {"source_language": "en", "target_language": "zh",
               "text_list": ["hello world", "Settings"]}}),
    ("G10_volc_crx_array", "POST",
     "https://translate.volcengine.com/crx/translate/v1",
     {"Content-Type": "application/json"},
     {"json": {"source_language": "en", "target_language": "zh",
               "text": ["hello world", "Settings"]}}),
    ("G11_transmart_text_translate", "POST", "https://transmart.qq.com/api/imt",
     {"Content-Type": "application/json", "Referer": "https://transmart.qq.com/"},
     {"json": {"header": {"fn": "text_translate", "client_key": "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f0a9ba4"},
               "source": {"lang": "en", "text_list": ["hello world", "Settings"]},
               "target": {"lang": "zh"}}}),
    ("G12_transmart_random_clientkey", "POST", "https://transmart.qq.com/api/imt",
     {"Content-Type": "application/json", "Referer": "https://transmart.qq.com/"},
     {"json": {"header": {"fn": "auto_translation", "client_key": "browser-chrome-131.0.0.0-Windows-testkey-0000"},
               "type": "plain", "model_category": "normal",
               "source": {"lang": "en", "text_list": ["hello world"]},
               "target": {"lang": "zh"}}}),
    ("G13_transmart_noreferer", "POST", "https://transmart.qq.com/api/imt",
     {"Content-Type": "application/json"},
     {"json": {"header": {"fn": "auto_translation", "client_key": "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f0a9ba4"},
               "type": "plain", "model_category": "normal",
               "source": {"lang": "en", "text_list": ["hello world"]},
               "target": {"lang": "zh"}}}),
    ("G14_yandex_multitext", "POST",
     "https://translate.yandex.net/api/v1/tr.json/translate",
     {"Content-Type": "application/x-www-form-urlencoded"},
     {"params": {"srv": "android", "id": "0" * 32 + "-0-0"},
      "data": [("text", "hello world"), ("text", "Settings"), ("lang", "en-zh")]}),
    ("G15_baidu_home(cookie)", "GET", "https://fanyi.baidu.com/"),
    ("G16_ali_alt", "POST", "https://translate.alibaba.com/api/translate/text",
     {"Content-Type": "application/json", "Referer": "https://translate.alibaba.com/"},
     {"json": {"srcLang": "en", "tgtLang": "zh", "query": "hello world",
               "domain": "general"}}),
    ("G17_niutrans_web2", "POST", "https://niutrans.com/api/text-translate",
     {"Content-Type": "application/json"},
     {"json": {"from": "en", "to": "zh", "text": "hello world"}}),
    ("G18_qq_fanyi_api_get", "GET", "https://fanyi.qq.com/api/translate",
     {"Referer": "https://fanyi.qq.com/"},
     {"params": {"source": "en", "target": "zh", "sourceText": "hello world"}}),
]


def run_new(item):
    name, method, url, headers, spec = item
    started = time.perf_counter()
    out = {"name": name, "method": method, "url": url}
    try:
        r = requests.request(
            method, url, params=spec.get("params"), headers={**H, **headers},
            json=spec.get("json"), data=spec.get("data"),
            data_raw=spec.get("body_raw") if False else None,
            timeout=TIMEOUT,
        )
        ms = int((time.perf_counter() - started) * 1000)
        out.update({"status": r.status_code, "ms": ms,
                    "head": (r.text or "")[:200].replace("\n", " "), "error": None})
    except Exception as exc:
        out.update({"status": None, "ms": int((time.perf_counter() - started) * 1000),
                    "head": "", "error": f"{type(exc).__name__}: {str(exc)[:140]}"})
    return out


def run_new_raw(item):
    """支持 raw body（protobuf 风格 JSON）的版本。"""
    name, method, url, headers, spec = item
    started = time.perf_counter()
    out = {"name": name, "method": method, "url": url}
    try:
        kwargs = {"params": spec.get("params"), "headers": {**H, **headers},
                  "timeout": TIMEOUT}
        if "body_raw" in spec:
            kwargs["data"] = json.dumps(spec["body_raw"])
        else:
            kwargs["json"] = spec.get("json")
            kwargs["data"] = spec.get("data")
        r = requests.request(method, url, **kwargs)
        ms = int((time.perf_counter() - started) * 1000)
        out.update({"status": r.status_code, "ms": ms,
                    "head": (r.text or "")[:200].replace("\n", " "), "error": None})
    except Exception as exc:
        out.update({"status": None, "ms": int((time.perf_counter() - started) * 1000),
                    "head": "", "error": f"{type(exc).__name__}: {str(exc)[:140]}"})
    return out


# ------------------------------------------------------------------ 质量对比
QUALITY_STRINGS = [
    "hello world",
    "Settings",
    "Are you sure you want to delete this file?",
    "Copy",
    "Paste",
    "Save",
    "Zoom in",
    "Trash",
    "Sign in",
    "Free up space",
    "Sync now",
    "Are you sure you want to delete this file? This action cannot be undone.",
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
                                       "client_key": "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f0a9ba4"},
                            "type": "plain", "model_category": "normal",
                            "source": {"lang": "en", "text_list": texts},
                            "target": {"lang": "zh"}},
                      headers={**H, "Content-Type": "application/json",
                               "Referer": "https://transmart.qq.com/"},
                      timeout=TIMEOUT)
    return r.json().get("auto_translation", []), r.status_code


def q_yandex(texts):
    data = [("text", t) for t in texts] + [("lang", "en-zh")]
    r = requests.post("https://translate.yandex.net/api/v1/tr.json/translate",
                      params={"srv": "android", "id": "0" * 32 + "-0-0"},
                      data=data, headers={**H, "Content-Type": "application/x-www-form-urlencoded"},
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
        out.append("".join(x.get("dst", "") for x in j.get("translateResults", [])) or j.get("errorCode", "?"))
    return out, 200


def q_mymemory(texts):
    out = []
    for t in texts:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": t[:480], "langpair": "en|zh-CN"},
                         headers=H, timeout=TIMEOUT)
        out.append((r.json().get("responseData") or {}).get("translatedText", ""))
    return out, 200


QUALITY_PROVIDERS = [
    ("edge(微软)", q_edge),
    ("volcengine(火山)", q_volc),
    ("transmart(腾讯)", q_transmart),
    ("yandex", q_yandex),
    ("youdao(有道)", q_youdao),
    ("mymemory", q_mymemory),
]


def burst(name, fn, n=12):
    """连打 n 次，统计成功率与 p50/p95 延迟，用来看限流。"""
    lat, ok = [], 0
    for i in range(n):
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
    print("=" * 72)
    print("第一部分：新增候选连通性")
    print("=" * 72)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(run_new_raw, NEW):
            flag = "✅" if r["status"] == 200 else "❌"
            print(f"{flag} {r['name']}  status={r['status']} {r['ms']}ms")
            if r["error"]:
                print(f"     ERROR {r['error']}")
            elif r["head"]:
                print(f"     body[:200]={r['head']!r}")
    print()
    print("=" * 72)
    print("第二部分：质量对比（同一批英文 -> 中文）")
    print("=" * 72)
    table = {}
    for pname, fn in QUALITY_PROVIDERS:
        t0 = time.perf_counter()
        try:
            outs, status = fn(QUALITY_STRINGS)
            ms = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            outs, status, ms = [], f"ERR {type(exc).__name__}: {str(exc)[:80]}", \
                int((time.perf_counter() - t0) * 1000)
        table[pname] = outs
        print(f"\n--- {pname}  (总的 {ms}ms, 12 条) ---")
        for src, dst in zip(QUALITY_STRINGS, outs):
            print(f"    {src!r:70} -> {dst!r}")
    with open("tools/_probe_quality.json", "w", encoding="utf-8") as fh:
        json.dump({"strings": QUALITY_STRINGS, "table": table}, fh,
                  ensure_ascii=False, indent=1)
    print()
    print("=" * 72)
    print("第三部分：连打 12 次看限流")
    print("=" * 72)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for r in pool.map(lambda p: burst(p[0], p[1]), QUALITY_PROVIDERS):
            print(f"  {r['name']:20} 成功 {r['ok']:6} min={r['min']}ms "
                  f"p50={r['p50']}ms max={r['max']}ms")


if __name__ == "__main__":
    main()
