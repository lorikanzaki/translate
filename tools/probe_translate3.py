"""第三轮：真正稳定的免费接口搜索。

重点验证 MyMemory 的额度上限，以及补充候选接口。
用法：.venv\\Scripts\\python.exe tools\\probe_translate3.py
"""
from __future__ import annotations

import concurrent.futures
import json
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
SRC = "Download failed"


def has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ---------------------------------------------------------------- 1. MyMemory 压力测试
def mymemory_stress() -> str:
    print("\n=== MyMemory 压力测试（判断是否真能长期用） ===")
    url = "https://api.mymemory.translated.net/get"

    def once(index: int) -> tuple[int, str]:
        try:
            response = requests.get(
                url, params={"q": f"error message number {index}", "langpair": "en|zh-CN"},
                headers={"User-Agent": UA}, timeout=20,
            )
            data = response.json()
            return response.status_code, data.get("responseData", {}).get("translatedText", "")
        except Exception as exc:
            return -1, f"{type(exc).__name__}"

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(once, range(20)))
    elapsed = (time.perf_counter() - started) * 1000
    ok = sum(1 for code, text in results if code == 200 and text)
    print(f"  20 次并发 8 路：成功 {ok}/20，耗时 {elapsed:.0f}ms")
    print(f"  状态码分布：{sorted({code for code, _ in results})}")

    # 逐条串行 15 次快速请求，看是否 429
    print("  串行 15 次（间隔 0）:")
    codes = []
    for i in range(15):
        try:
            response = requests.get(
                url, params={"q": f"menu {i}", "langpair": "en|zh-CN"},
                headers={"User-Agent": UA}, timeout=20,
            )
            codes.append(response.status_code)
        except Exception as exc:
            codes.append(str(type(exc).__name__))
    print(f"    {codes}")

    # 长文本 / 多行能力
    print("  能力边界:")
    for label, text in [("多行 5 段", "\n".join(f"item {i} failed" for i in range(5))),
                        ("300 字符", "network error " * 25),
                        ("600 字符", "configuration failed " * 30)]:
        response = requests.get(
            url, params={"q": text, "langpair": "en|zh-CN"},
            headers={"User-Agent": UA}, timeout=25,
        )
        data = response.json()
        out = data.get("responseData", {}).get("translatedText", "")
        print(f"    [{label}] status={data.get('responseStatus')} "
              f"详情={str(data.get('responseDetails'))[:70]!r} 译文长={len(out)}")
    return "MyMemory 压力测试完成"


# ---------------------------------------------------------------- 2. 百度带 token 版
def baidu_with_token() -> str:
    print("\n=== 百度 fanyi v2（先取页面 token） ===")
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://fanyi.baidu.com/"})
    page = session.get("https://fanyi.baidu.com/", timeout=15)
    print(f"  首页 HTTP {page.status_code}, 长度 {len(page.text)}")
    import re

    token = re.search(r"token:\s*'([0-9a-f]{32})'", page.text) or \
        re.search(r'"token"\s*:\s*"([0-9a-f]{32})"', page.text)
    print(f"  token = {token.group(1) if token else None}")
    if not token:
        return "拿不到 token"
    response = session.post(
        "https://fanyi.baidu.com/v2transapi",
        params={"from": "en", "to": "zh"},
        data={"from": "en", "to": "zh", "query": SRC, "transtype": "translang",
              "simple_means_flag": "3", "sign": "1", "token": token.group(1),
              "domain": "common"},
        timeout=15,
    )
    print(f"  翻译 HTTP {response.status_code}: {response.text[:200]}")
    return "百度测试完成"


# ---------------------------------------------------------------- 3. 搜狗
def sogou_variants() -> str:
    print("\n=== 搜狗多个端点 ===")
    endpoints = [
        ("transpc", "https://fanyi.sogou.com/api/transpc/text/result",
         {"from": "en", "to": "zh-CHS", "text": SRC, "client": "pc",
          "fr": "browser_pc", "needQc": 1, "s": "fanyi.sogou.com", "uuid": "1"}),
        ("translate", "https://fanyi.sogou.com/api/translate",
         {"from": "en", "to": "zh-CHS", "text": SRC, "client": "pc",
          "fr": "browser_pc"}),
    ]
    for name, url, payload in endpoints:
        try:
            response = requests.post(
                url, json=payload,
                headers={"User-Agent": UA, "Referer": "https://fanyi.sogou.com/text",
                         "Origin": "https://fanyi.sogou.com",
                         "Content-Type": "application/json"},
                timeout=15,
            )
            print(f"  {name}: HTTP {response.status_code} {response.text[:220]}")
        except Exception as exc:
            print(f"  {name}: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 4. 其他候选
def other_candidates() -> str:
    print("\n=== 其他候选 ===")

    # Yandex
    try:
        response = requests.post(
            "https://translate.yandex.net/api/v1/tr.json/translate",
            params={"srv": "android", "id": "1-0-0", "format": "text",
                    "lang": "en-zh", "text": SRC},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        print(f"  Yandex: HTTP {response.status_code} {response.text[:200]}")
    except Exception as exc:
        print(f"  Yandex: {type(exc).__name__}")

    # Google translate_a 通过 translate.google.cn
    try:
        response = requests.post(
            "https://translate.google.cn/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t"},
            data={"q": SRC}, headers={"User-Agent": UA}, timeout=8,
        )
        print(f"  google.cn: HTTP {response.status_code} {response.text[:150]}")
    except Exception as exc:
        print(f"  google.cn: {type(exc).__name__}")

    # 百度翻译开放平台 demo（无需 key 的旧接口）
    for url in ("https://fanyi-api.baidu.com/api/trans/vip/translate",
                "https://api.fanyi.baidu.com/api/trans/vip/translate"):
        try:
            response = requests.get(
                url, params={"q": SRC, "from": "en", "to": "zh", "appid": "demo",
                             "salt": "1", "sign": "1"},
                headers={"User-Agent": UA}, timeout=10,
            )
            print(f"  {url.split('/')[2]}: HTTP {response.status_code} {response.text[:150]}")
        except Exception as exc:
            print(f"  {url.split('/')[2]}: {type(exc).__name__}")

    # DeepL 免费 API（需要 key，仅测连通）
    try:
        response = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={"text": SRC, "target_lang": "ZH"},
            headers={"User-Agent": UA}, timeout=10,
        )
        print(f"  DeepL free: HTTP {response.status_code} {response.text[:150]}")
    except Exception as exc:
        print(f"  DeepL free: {type(exc).__name__}")

    # 火山引擎 / 讯飞 demo
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": SRC, "langpair": "en|zh-CN"}, timeout=10)
        print(f"  MyMemory 健康检查: {response.status_code} "
              f"{json.dumps(response.json().get('responseData'), ensure_ascii=False)[:80]}")
    except Exception as exc:
        print(f"  MyMemory: {type(exc).__name__}")
    return "其他候选测试完成"


if __name__ == "__main__":
    mymemory_stress()
    baidu_with_token()
    sogou_variants()
    other_candidates()
