"""打印候选翻译接口的原始响应，确认字段结构。

用法：.venv\\Scripts\\python.exe tools\\dump_translate_raw.py
输出同时写入 tools\\_raw\\*.txt，方便直接查看。
"""
from __future__ import annotations

import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_raw")
os.makedirs(OUT, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
TEXT = "Download failed"


def dump(name: str, response: requests.Response) -> None:
    path = os.path.join(OUT, f"{name}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"URL: {response.url}\n")
        fh.write(f"STATUS: {response.status_code}\n")
        fh.write(f"HEADERS: {dict(response.headers)}\n")
        fh.write("-" * 60 + "\n")
        fh.write(response.text[:4000])
    print(f"  -> {path}  HTTP {response.status_code}  {len(response.text)} 字符")
    preview = response.text[:300].replace("\n", " ")
    print(f"     {preview}")


def probe_bing() -> None:
    print("\n=== Bing www.bing.com/ttranslatev3 ===")
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://www.bing.com/translator",
        "Origin": "https://www.bing.com",
    })
    page = session.get("https://www.bing.com/translator", timeout=10)
    print(f"  取首页 HTTP {page.status_code}, cookies={list(session.cookies.keys())}")
    import re

    ig = re.search(r'IG:"([^"]+)"', page.text)
    iid = re.search(r'data-iid="([^"]+)"', page.text)
    print(f"  页面里 IG={ig.group(1) if ig else None} IID={iid.group(1) if iid else None}")

    response = session.post(
        "https://www.bing.com/ttranslatev3",
        params={"isVertical": "1", "IG": ig.group(1) if ig else "1",
                "IID": iid.group(1) if iid else "translator.0"},
        data={"fromLang": "auto-detect", "text": TEXT, "to": "zh-Hans",
              "token": "x", "key": "x"},
        timeout=10,
    )
    dump("bing_ttranslatev3", response)

    # 不带 token/key 试试
    response2 = session.post(
        "https://www.bing.com/ttranslatev3",
        params={"isVertical": "1", "IG": ig.group(1) if ig else "1",
                "IID": iid.group(1) if iid else "translator.0"},
        data={"fromLang": "auto-detect", "text": TEXT, "to": "zh-Hans"},
        timeout=10,
    )
    dump("bing_ttranslatev3_notoken", response2)


def probe_sogou() -> None:
    print("\n=== 搜狗 fanyi.sogou.com ===")
    response = requests.post(
        "https://fanyi.sogou.com/api/transpc/text/result",
        json={"from": "en", "to": "zh-CHS", "text": TEXT,
              "client": "pc", "fr": "browser_pc", "needQc": 1,
              "s": "fanyi.sogou.com", "uuid": "1", "exchange": False},
        headers={"User-Agent": UA, "Referer": "https://fanyi.sogou.com/text",
                 "Origin": "https://fanyi.sogou.com",
                 "Content-Type": "application/json"},
        timeout=10,
    )
    dump("sogou_transpc", response)


def probe_baidu() -> None:
    print("\n=== 百度 fanyi.baidu.com ===")
    response = requests.post(
        "https://fanyi.baidu.com/sug", data={"kw": TEXT},
        headers={"User-Agent": UA, "Referer": "https://fanyi.baidu.com/"}, timeout=10,
    )
    dump("baidu_sug", response)

    response = requests.post(
        "https://fanyi.baidu.com/transapi",
        data={"from": "en", "to": "zh", "query": TEXT, "source": "txt"},
        headers={"User-Agent": UA, "Referer": "https://fanyi.baidu.com/"}, timeout=10,
    )
    dump("baidu_transapi", response)

    response = requests.post(
        "https://fanyi.baidu.com/v2transapi",
        data={"from": "en", "to": "zh", "query": TEXT, "simple_means_flag": "3",
              "sign": "", "token": "", "domain": "common"},
        headers={"User-Agent": UA, "Referer": "https://fanyi.baidu.com/"}, timeout=10,
    )
    dump("baidu_v2transapi", response)


def probe_youdao() -> None:
    print("\n=== 有道 fanyi.youdao.com ===")
    response = requests.post(
        "https://fanyi.youdao.com/translate",
        data={"doctype": "json", "type": "AUTO", "i": TEXT},
        headers={"User-Agent": UA, "Referer": "https://fanyi.youdao.com/",
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    dump("youdao_translate", response)


def probe_mymemory() -> None:
    print("\n=== MyMemory 批量能力 ===")
    import urllib.parse

    payload = urllib.parse.quote(TEXT + "\n" + "Settings")
    response = requests.get(
        f"https://api.mymemory.translated.net/get?q={payload}&langpair=en|zh-CN",
        headers={"User-Agent": UA}, timeout=15,
    )
    try:
        data = response.json()
        print("  translatedText =", json.dumps(
            data.get("responseData", {}).get("translatedText", ""), ensure_ascii=False))
        print("  quotaFinished  =", data.get("quotaFinished"))
        print("  matches(前3)   =", json.dumps(
            [m.get("translation") for m in (data.get("matches") or [])[:3]],
            ensure_ascii=False))
    except Exception as exc:
        print("  解析失败:", exc)
    dump("mymemory", response)


if __name__ == "__main__":
    probe_bing()
    probe_sogou()
    probe_baidu()
    probe_youdao()
    probe_mymemory()
    print("\n原始响应已写入", OUT)
