"""免费机器翻译接口实测探针（英->简中）。

用法:
    python tools/probe_mt_2026.py            # 跑全部候选
    python tools/probe_mt_2026.py edge lingva  # 只跑名字含这些子串的候选
输出: JSON 行 + 人读汇总，写入 tools/_probe_mt_out.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = (4, 8)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")
H = {"User-Agent": UA}

TESTS = ["hello world", "Settings", "Are you sure you want to delete this file?"]

CJK = re.compile(r"[\u4e00-\u9fff]")


def has_cjk(s: str) -> bool:
    return bool(CJK.search(s or ""))


# --------------------------------------------------------------------- 候选定义
# 每项: (name, kind, spec)
#   kind = "get"  -> spec = (url, params)
#   kind = "postjson" -> (url, params, json_body)
#   kind = "postform" -> (url, params, form_data)
#   kind = "raw"  -> (method, url, params, headers, body_kind, body)
CASES = []


def add(name, method, url, params=None, headers=None, json_body=None, data=None):
    CASES.append({
        "name": name, "method": method, "url": url, "params": params,
        "headers": {**H, **(headers or {})}, "json": json_body, "data": data,
    })


# ---- A. 微软系 ----------------------------------------------------------
add("A1_edge_translatetext(基线)", "POST",
    "https://edge.microsoft.com/translate/translatetext",
    params={"from": "en", "to": "zh-Hans", "isEnterpriseClient": "false"},
    headers={"Content-Type": "application/json"}, json_body=["hello world"])
add("A2_edge_translatetext_enterprise_true", "POST",
    "https://edge.microsoft.com/translate/translatetext",
    params={"from": "en", "to": "zh-Hans", "isEnterpriseClient": "true"},
    headers={"Content-Type": "application/json"}, json_body=["hello world"])
add("A3_edge_translatehtml", "POST",
    "https://edge.microsoft.com/translate/translatehtml",
    params={"from": "en", "to": "zh-Hans", "isEnterpriseClient": "false"},
    headers={"Content-Type": "application/json"}, json_body=["<p>hello world</p>"])
add("A4_edge_auth(GET)", "GET", "https://edge.microsoft.com/translate/auth")
add("A5_edge_auth_alt1", "GET", "https://edge.microsoft.com/translate/auth?ref=translator")
add("A6_edge_auth_alt2", "GET", "https://edge.microsoft.com/translate/token")
add("A7_edge_authtoken", "GET", "https://edge.microsoft.com/translate/authtoken")
add("A8_api-edge_translate_nokey", "POST",
    "https://api-edge.cognitive.microsofttranslator.com/translate",
    params={"api-version": "3.0", "from": "en", "to": "zh-Hans"},
    headers={"Content-Type": "application/json"}, json_body=[{"Text": "hello world"}])
add("A9_api-cognitive_nokey", "POST",
    "https://api.cognitive.microsofttranslator.com/translate",
    params={"api-version": "3.0", "from": "en", "to": "zh-Hans"},
    headers={"Content-Type": "application/json"}, json_body=[{"Text": "hello world"}])
add("A10_bing_ttranslatev3", "POST", "https://www.bing.com/ttranslatev3",
    params={"isVertical": "1", "IG": "0", "IID": "0"},
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={"fromLang": "en", "text": "hello world", "to": "zh-Hans"})
add("A11_bing_page", "GET", "https://www.bing.com/translator")
add("A12_bing_ttranslate", "POST", "https://www.bing.com/ttranslate",
    params={"IG": "0", "IID": "0"},
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={"fromLang": "en", "text": "hello world", "to": "zh-Hans"})
add("A13_bing_api_translate", "POST",
    "https://www.bing.com/translator/api/Translate/Translate",
    headers={"Content-Type": "application/json"},
    json_body={"fromLang": "en", "text": "hello world", "to": "zh-Hans"})
add("A14_edge_cv_translate", "POST",
    "https://edge.microsoft.com/translate/cv/translate",
    params={"from": "en", "to": "zh-Hans"},
    headers={"Content-Type": "application/json"}, json_body=["hello world"])

# ---- B. Google 第三方代理前端 --------------------------------------------
LINGVA = [
    "https://lingva.ml", "https://lingva.thedaviddelta.com",
    "https://translate.plausibility.cloud", "https://lingva.garudalinux.org",
    "https://lingva.lunar.icu", "https://translate.igna.wtf",
    "https://lingva.ducks.party", "https://lingva.pussthecat.org",
    "https://lingva.hostux.net", "https://lingva.vern.cc",
    "https://lingva.esmailelbob.xyz", "https://lingva.lol",
]
for base in LINGVA:
    add("B_lingva:" + base.split("//")[1], "GET", base + "/api/v1/en/zh/hello%20world")

SIMPLY = [
    "https://simplytranslate.org", "https://nyc1.simplytranslate.org",
    "https://translate.josias.dev", "https://simplytranslate.ducks.party",
    "https://simplytranslate.pussthecat.org", "https://st.tokhmi.xyz",
]
for base in SIMPLY:
    add("B_simple:" + base.split("//")[1], "GET", base + "/api/translate",
        params={"engine": "google", "from": "en", "to": "zh", "text": "hello world"})

MOZHI = [
    "https://mozhi.aryak.me", "https://mozhi.frontendfriendly.xyz",
    "https://mozhi.ducks.party", "https://mozhi.vern.cc",
    "https://translate.projectsegfau.lt",
]
for base in MOZHI:
    add("B_mozhi:" + base.split("//")[1], "GET", base + "/api/translate",
        params={"engine": "google", "from": "en", "to": "zh-Hans", "text": "hello world"})

# 其它 Google 代理 / 公共实例
add("B_gtx_gtranslate", "GET", "https://translate.googleapis.com/translate_a/single",
    params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": "hello world"})
add("B_clients5", "GET", "https://clients5.google.com/translate_a/t",
    params={"client": "dict-chrome-ex", "sl": "en", "tl": "zh-CN", "q": "hello world"})
add("B_libretranslate_org", "POST", "https://libretranslate.com/translate",
    headers={"Content-Type": "application/json"},
    json_body={"q": "hello world", "source": "en", "target": "zh", "format": "text"})
add("B_libretranslate_de", "POST", "https://libretranslate.de/translate",
    headers={"Content-Type": "application/json"},
    json_body={"q": "hello world", "source": "en", "target": "zh", "format": "text"})
add("B_translate_disroot", "POST", "https://translate.disroot.org/translate",
    headers={"Content-Type": "application/json"},
    json_body={"q": "hello world", "source": "en", "target": "zh", "format": "text"})
add("B_cxserver_wikimedia", "POST", "https://cxserver.wikimedia.org/v1/mt/en/zh",
    headers={"Content-Type": "application/json"},
    json_body={"html": "hello world"})

# ---- C. 火山引擎 --------------------------------------------------------
add("C1_volc_crx", "POST", "https://translate.volcengine.com/crx/translate/v1",
    headers={"Content-Type": "application/json"},
    json_body={"source_language": "en", "target_language": "zh",
               "text": "hello world"})
add("C2_volc_api_v1", "POST", "https://translate.volcengine.com/api/v1/translate",
    headers={"Content-Type": "application/json"},
    json_body={"source_language": "en", "target_language": "zh", "text": "hello world"})
add("C3_volc_web", "POST", "https://translate.volcengine.com/web/translate/v1/",
    headers={"Content-Type": "application/json"},
    json_body={"source_language": "en", "target_language": "zh", "text": "hello world"})
add("C4_volc_home", "GET", "https://translate.volcengine.com/")

# ---- D. 腾讯 / 阿里 / 百度 / 有道 / 搜狗 ----------------------------------
add("D1_qq_fanyi_api", "POST", "https://fanyi.qq.com/api/translate",
    headers={"Content-Type": "application/x-www-form-urlencoded",
             "Referer": "https://fanyi.qq.com/",
             "Origin": "https://fanyi.qq.com"},
    data={"source": "en", "target": "zh", "sourceText": "hello world",
          "sessionUuid": "translate_uuid" + str(int(time.time() * 1000))})
add("D2_qq_transmart_imt", "POST", "https://transmart.qq.com/api/imt",
    headers={"Content-Type": "application/json",
             "Referer": "https://transmart.qq.com/"},
    json_body={"header": {"fn": "auto_translation", "client_key":
                          "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f0a9ba4"},
               "type": "plain", "model_category": "normal",
               "source": {"lang": "en", "text_list": ["hello world"]},
               "target": {"lang": "zh"}})
add("D3_ali_translate", "POST", "https://translate.alibaba.com/api/translate/text",
    headers={"Content-Type": "application/x-www-form-urlencoded",
             "Referer": "https://translate.alibaba.com/"},
    data={"srcLang": "en", "tgtLang": "zh", "domain": "general",
          "query": "hello world"})
add("D4_ali_translate2", "POST", "https://translate.alibaba.com/api/translate/text",
    headers={"Content-Type": "application/x-www-form-urlencoded",
             "Referer": "https://translate.alibaba.com/"},
    data={"srcLang": "en", "tgtLang": "zh-CN", "domain": "general",
          "query": "hello world"})
add("D5_baidu_v2transapi", "POST", "https://fanyi.baidu.com/v2transapi",
    params={"from": "en", "to": "zh"},
    headers={"Content-Type": "application/x-www-form-urlencoded",
             "Referer": "https://fanyi.baidu.com/"},
    data={"from": "en", "to": "zh", "query": "hello world", "transtype": "realtime",
          "simple_means_flag": "3", "sign": "1", "token": "1", "domain": "common"})
add("D6_baidu_transapi", "GET", "https://fanyi.baidu.com/transapi",
    params={"from": "en", "to": "zh", "query": "hello world",
            "source": "txt", "transtype": "realtime"})
add("D7_youdao_dict_translate", "GET", "https://dict.youdao.com/translate",
    params={"&doctype": "json", "type": "AUTO", "i": "hello world"})
add("D8_youdao_aidemo", "POST", "https://aidemo.youdao.com/trans",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={"q": "hello world", "from": "en", "to": "zh-CHS"})
add("D9_sogou_api", "POST", "https://fanyi.sogou.com/api/transpc/text/result",
    headers={"Content-Type": "application/json",
             "Referer": "https://fanyi.sogou.com/"},
    json_body={"from": "en", "to": "zh-CHS", "text": "hello world",
               "client": "pc", "exchangeFlag": False})
add("D10_xiaoniu", "POST", "https://www.xiaoniu168.com/api/translate",
    headers={"Content-Type": "application/json"},
    json_body={"from": "en", "to": "zh", "text": "hello world"})
add("D11_niutrans_web", "POST", "https://niutrans.com/api/translate/web",
    headers={"Content-Type": "application/json"},
    json_body={"from": "en", "to": "zh", "src_text": "hello world"})
add("D12_caiyun", "POST", "https://api.interpreter.caiyunai.com/v1/translator",
    headers={"Content-Type": "application/json"},
    json_body={"source": ["hello world"], "trans_type": "en2zh",
               "request_id": "demo", "detect": True})

# ---- E. MyMemory / 其它通用 ----------------------------------------------
add("E1_mymemory", "GET", "https://api.mymemory.translated.net/get",
    params={"q": "hello world", "langpair": "en|zh-CN"})
add("E2_reverso", "POST", "https://api.reverso.net/translate/v1/translation",
    headers={"Content-Type": "application/json",
             "Origin": "https://www.reverso.net",
             "Referer": "https://www.reverso.net/"},
    json_body={"input": "hello world", "from": "eng", "to": "chi",
               "format": "text", "options": {"sentenceSplitter": True}})
add("E3_yandex_tr", "POST", "https://translate.yandex.net/api/v1/tr.json/translate",
    params={"srv": "android", "id": "0" * 32 + "-0-0"},
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={"text": "hello world", "lang": "en-zh"})
add("E4_deepl_free_nokey", "POST", "https://api-free.deepl.com/v2/translate",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={"text": "hello world", "target_lang": "ZH"})
add("E5_papago", "POST", "https://papago.naver.com/apis/n2mt/translate",
    headers={"Content-Type": "application/x-www-form-urlencoded",
             "Referer": "https://papago.naver.com/"},
    data={"source": "en", "target": "zh-CN", "text": "hello world"})
add("E6_baidu_fanyi_api_nokey", "GET", "https://fanyi-api.baidu.com/api/trans/vip/translate",
    params={"q": "hello world", "from": "en", "to": "zh", "appid": "", "salt": "1", "sign": ""})

# ---- F. LLM / 推理端点 ---------------------------------------------------
add("F1_hf_opus_mt", "POST",
    "https://api-inference.huggingface.co/models/Helsinki-NLP/opus-mt-en-zh",
    headers={"Content-Type": "application/json"},
    json_body={"inputs": "hello world"})
add("F2_hf_nllb", "POST",
    "https://api-inference.huggingface.co/models/facebook/nllb-200-distilled-600M",
    headers={"Content-Type": "application/json"}, json_body={"inputs": "hello world"})
add("F3_hf_router_nllb", "POST",
    "https://router.huggingface.co/hf-inference/models/Helsinki-NLP/opus-mt-en-zh",
    headers={"Content-Type": "application/json"}, json_body={"inputs": "hello world"})
add("F4_deepseek_nokey", "POST", "https://api.deepseek.com/chat/completions",
    headers={"Content-Type": "application/json"},
    json_body={"model": "deepseek-chat",
               "messages": [{"role": "user", "content": "translate to Chinese: hello world"}]})
add("F5_ollama_local", "POST", "http://127.0.0.1:11434/api/generate",
    headers={"Content-Type": "application/json"},
    json_body={"model": "qwen2.5:3b", "prompt": "translate to Chinese: hello world",
               "stream": False})


def extract(name, status, body):
    """尽力从响应里抠出译文，用于判断是否真的返回中文。"""
    if status != 200 or not body:
        return ""
    try:
        data = json.loads(body)
    except Exception:
        # 非 JSON：找第一段连续中文
        m = CJK.findall(body)
        return body[:200] if m else ""
    def walk(node, depth=0):
        if depth > 8:
            return None
        if isinstance(node, str):
            return node if has_cjk(node) else None
        if isinstance(node, list):
            for item in node:
                got = walk(item, depth + 1)
                if got:
                    return got
        if isinstance(node, dict):
            # 优先常见字段
            for key in ("translatedText", "translation", "text", "tgt_text",
                        "target", "result", "translations", "trans_result",
                        "dst", "translationText", "data", "sentences"):
                if key in node:
                    got = walk(node[key], depth + 1)
                    if got:
                        return got
            for value in node.values():
                got = walk(value, depth + 1)
                if got:
                    return got
        return None
    got = walk(data)
    return got or ""


def run(case):
    started = time.perf_counter()
    res = {"name": case["name"], "method": case["method"], "url": case["url"],
           "params": case["params"], "sent_body": case.get("json") or case.get("data")}
    try:
        r = requests.request(
            case["method"], case["url"], params=case["params"],
            headers=case["headers"], json=case.get("json"), data=case.get("data"),
            timeout=TIMEOUT, allow_redirects=True,
        )
        ms = int((time.perf_counter() - started) * 1000)
        text = r.text or ""
        res.update({"status": r.status_code, "ms": ms, "len": len(text),
                    "head": text[:200].replace("\n", " "),
                    "extracted": extract(case["name"], r.status_code, text),
                    "error": None})
    except Exception as exc:
        ms = int((time.perf_counter() - started) * 1000)
        res.update({"status": None, "ms": ms, "len": 0, "head": "",
                    "extracted": "", "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return res


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    filters = sys.argv[1:]
    cases = CASES
    if filters:
        cases = [c for c in CASES if any(f.lower() in c["name"].lower() for f in filters)]
    print(f"共 {len(cases)} 个候选，开始实测（并发 10，超时 8s）...\n", flush=True)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(run, cases))
    results.sort(key=lambda r: r["name"])
    with open("tools/_probe_mt_out.json", "w", encoding="utf-8") as fh:
        json.dump({"tests": TESTS, "results": results}, fh, ensure_ascii=False, indent=1)
    for r in results:
        ok = "✅" if (r["status"] == 200 and r["extracted"]) else (
            "⚠️ " if r["status"] == 200 else "❌")
        print(f"{ok} {r['name']}")
        print(f"    {r['method']} {r['url']}  params={r['params']}")
        print(f"    status={r['status']} {r['ms']}ms len={r['len']}")
        if r["error"]:
            print(f"    ERROR {r['error']}")
        if r["head"]:
            print(f"    body[:200]={r['head']!r}")
        if r["extracted"]:
            print(f"    译文={r['extracted']!r}")
        print()
    with open("tools/_probe_mt_out.json", "w", encoding="utf-8") as fh:
        json.dump({"tests": TESTS, "results": results}, fh, ensure_ascii=False, indent=1)
    alive = [r for r in results if r["status"] == 200 and r["extracted"]]
    print("=" * 70)
    print(f"可用（HTTP 200 且有中文译文）: {len(alive)}/{len(results)}")
    for r in alive:
        print(f"  - {r['name']}  {r['ms']}ms  -> {r['extracted'][:60]!r}")


if __name__ == "__main__":
    main()
