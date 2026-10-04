"""看有道 / 腾讯 transmart 的原始响应结构，判断是否可救。

用法：.venv\\Scripts\\python.exe tools\\dump_youdao.py
"""
from __future__ import annotations

import json
import os

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
SRC = "Download failed"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_raw")
os.makedirs(OUT, exist_ok=True)


def show(label: str, response: requests.Response) -> None:
    print(f"\n--- {label} | HTTP {response.status_code} | {len(response.content)} 字节")
    print("  最终 URL:", response.url)
    text = response.text
    print("  前 600 字符:")
    print("   ", text[:600].replace("\n", " ")[:600])


def main() -> None:
    # 1) 有道 translate_o
    response = requests.post(
        "https://fanyi.youdao.com/translate_o?smartresult=dict&smartresult=rule",
        data={"i": SRC, "from": "AUTO", "to": "AUTO", "doctype": "json",
              "client": "fanyideskweb", "salt": "1", "sign": "1", "lts": "1",
              "bv": "1", "version": "2.1", "keyfrom": "fanyi.web"},
        headers={"User-Agent": UA, "Referer": "https://fanyi.youdao.com/"},
        timeout=15,
    )
    show("有道 translate_o", response)

    # 2) 有道 aidemo
    response = requests.get(
        "https://aidemo.youdao.com/trans",
        params={"q": SRC, "from": "en", "to": "zh-CHS"},
        headers={"User-Agent": UA}, timeout=15,
    )
    show("有道 aidemo", response)

    # 3) 腾讯 transmart
    response = requests.post(
        "https://transmart.qq.com/api/imt",
        json={"header": {"fn": "auto_translation",
                         "client_key": "browser-chrome-111111",
                         "user": "browser-chrome-111111"},
              "type": "plain", "model_category": "normal",
              "text_domain": "general",
              "source": {"lang": "en", "text_list": [SRC]},
              "target": {"lang": "zh"}},
        headers={"User-Agent": UA, "Referer": "https://transmart.qq.com/",
                 "Content-Type": "application/json"},
        timeout=15,
    )
    show("腾讯 transmart", response)

    # 4) MyMemory 的日额度与长度
    print("\n--- MyMemory 详细能力 ---")
    for label, text in [("单句", SRC), ("多行", "Settings\nDownload failed"),
                        ("长文本 200 字符", "A" * 100 + " error occurred " + "B" * 80)]:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|zh-CN"},
            headers={"User-Agent": UA}, timeout=20,
        )
        data = response.json()
        print(f"  [{label}] HTTP {response.status_code} "
              f"status={data.get('responseStatus')} "
              f"quotaFinished={data.get('quotaFinished')} "
              f"details={str(data.get('responseDetails'))[:60]!r}")
        out = data.get("responseData", {}).get("translatedText", "")
        print(f"    译文 = {json.dumps(out[:90], ensure_ascii=False)}")

    # 5) MyMemory 有没有 key 限制 / 语言对格式
    print("\n--- MyMemory 语言对格式 ---")
    for pair in ["en|zh-CN", "en|zh", "en-GB|zh-CN"]:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": SRC, "langpair": pair},
            headers={"User-Agent": UA}, timeout=20,
        )
        data = response.json()
        print(f"  {pair:<14} status={data.get('responseStatus')} "
              f"text={json.dumps(data.get('responseData', {}).get('translatedText', '')[:40], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
