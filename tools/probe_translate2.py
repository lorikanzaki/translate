"""第二轮候选接口实测：以「返回里真的有译文」为唯一通过标准。

用法：.venv\\Scripts\\python.exe tools\\probe_translate2.py
"""
from __future__ import annotations

import json
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
SRC = "Download failed"
EXPECT = "下载"


def report(name: str, ok: bool, detail: str, ms: float) -> None:
    print(f"{'[OK]  ' if ok else '[FAIL]'} {name:<34} {ms:>6.0f}ms  {detail}")


def timed(fn):
    started = time.perf_counter()
    try:
        detail = fn()
        elapsed = (time.perf_counter() - started) * 1000
        # 只要调用方返回字符串，就认为函数自身判定过了
        return True, detail, elapsed
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:170], (time.perf_counter() - started) * 1000


def check(text: str) -> str:
    """确认返回文本里确实包含中文，否则抛错。"""
    if not text:
        raise RuntimeError("返回空文本")
    if not any("\u4e00" <= ch <= "\u9fff" for ch in text):
        raise RuntimeError(f"不含中文: {text[:100]!r}")
    return text[:80]


# ---------------------------------------------------------------- 微软 demo 接口
def ms_demo() -> str:
    response = requests.post(
        "https://api.cognitive.microsofttranslator.com/translate",
        params={"api-version": "3.0", "from": "en", "to": "zh-Hans"},
        json=[{"Text": SRC}],
        headers={"User-Agent": UA, "Ocp-Apim-Subscription-Key": "",
                 "Content-Type": "application/json"},
        timeout=55,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:140]!r}")
    return check(response.json()[0]["translations"][0]["text"])


# ---------------------------------------------------------------- 微软 edge 新授权方式
def ms_edge_variants() -> str:
    """试多个可能的 token 端点，找出真的还活着的那个。"""
    endpoints = [
        "https://edge.microsoft.com/translate/auth",
        "https://edge.microsoft.com/translate/auth/",
        "https://api-edge.cognitive.microsofttranslator.com/translate/auth",
        "https://www.bing.com/translator/api/Translate/Translate?",
    ]
    results = []
    for url in endpoints:
        try:
            response = requests.get(url, headers={"User-Agent": UA}, timeout=10)
            body = response.text.strip()
            results.append(f"{url} -> {response.status_code} len={len(body)}")
            if response.status_code == 200 and len(body) > 50:
                return f"{url} 可用，token {len(body)} 字节"
        except Exception as exc:
            results.append(f"{url} -> {type(exc).__name__}")
    raise RuntimeError(" | ".join(results))


# ---------------------------------------------------------------- Bing 页面内嵌 key
def bing_scrape_key() -> str:
    """从 bing translator 页面里抓 params_AbusePreventionHelper 的 key+token。"""
    import re

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://www.bing.com/translator",
                            "Origin": "https://www.bing.com"})
    page = session.get("https://www.bing.com/translator", timeout=12)
    match = re.search(r"params_AbusePreventionHelper\s*=\s*(\[[^\]]*\])", page.text)
    if not match:
        raise RuntimeError("页面里找不到 params_AbusePreventionHelper")
    params = json.loads(match.group(1))
    key, token = str(params[0]), str(params[1])
    ig = re.search(r'IG:"([^"]+)"', page.text)
    iid = re.search(r'data-iid="([^"]+)"', page.text)
    response = session.post(
        "https://www.bing.com/ttranslatev3",
        params={"isVertical": "1", "IG": ig.group(1) if ig else "1",
                "IID": iid.group(1) if iid else "translator.0"},
        data={"fromLang": "auto-detect", "text": SRC, "to": "zh-Hans",
              "token": token, "key": key},
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:140]!r}")
    if not response.text.strip():
        raise RuntimeError("HTTP 200 但响应体为空")
    data = response.json()
    return check(data[0]["translations"][0]["text"])


# ---------------------------------------------------------------- 有道网页版
def youdao_web() -> str:
    """有道新版接口需要 sign，试最简的 m.youdao 与旧 fanyi 接口。"""
    attempts = []
    try:
        response = requests.post(
            "https://fanyi.youdao.com/translate_o?smartresult=dict&smartresult=rule",
            data={"i": SRC, "from": "AUTO", "to": "AUTO", "doctype": "json",
                  "client": "fanyideskweb", "salt": "1", "sign": "1", "lts": "1",
                  "bv": "1", "version": "2.1", "keyfrom": "fanyi.web"},
            headers={"User-Agent": UA, "Referer": "https://fanyi.youdao.com/"},
            timeout=12,
        )
        attempts.append(f"translate_o -> {response.status_code}")
        if response.status_code == 200 and response.text.strip().startswith("{"):
            data = response.json()
            text = "".join(seg[0] for seg in data.get("translateResult", [[]])[0])
            return check(text)
    except Exception as exc:
        attempts.append(f"translate_o -> {type(exc).__name__}")

    try:
        response = requests.get(
            "https://aidemo.youdao.com/trans",
            params={"q": SRC, "from": "en", "to": "zh-CHS"},
            headers={"User-Agent": UA}, timeout=12,
        )
        attempts.append(f"aidemo -> {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            text = "".join(t.get("t") or "" for t in (data.get("translation") or []))
            return check(text)
    except Exception as exc:
        attempts.append(f"aidemo -> {type(exc).__name__}")

    raise RuntimeError(" | ".join(attempts))


# ---------------------------------------------------------------- 腾讯交互翻译
def tencent_webtran() -> str:
    response = requests.post(
        "https://transmart.qq.com/api/imt",
        json={"header": {"fn": "auto_translation", "client_key": "browser-chrome-111111",
                         "user": "browser-chrome-111111"},
              "type": "plain", "model_category": "normal", "text_domain": "general",
              "source": {"lang": "en", "text_list": [SRC]},
              "target": {"lang": "zh"}},
        headers={"User-Agent": UA, "Referer": "https://transmart.qq.com/",
                 "Content-Type": "application/json"},
        timeout=12,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:140]!r}")
    data = response.json()
    texts = data.get("auto_translation") or []
    return check(texts[0] if texts else "")


# ---------------------------------------------------------------- 火山/彩云等无需 key 的
def caiyun() -> str:
    response = requests.post(
        "https://api.interpreter.caiyunai.com/v1/translator",
        json={"source": [SRC], "trans_type": "en2zh", "request_id": "demo",
              "detect": True},
        headers={"User-Agent": UA, "content-type": "application/json",
                 "x-authorization": "token"},
        timeout=12,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:140]!r}")
    data = response.json()
    return check(data.get("target") or "")


# ---------------------------------------------------------------- 谷歌镜像
def google_mirror() -> str:
    response = requests.post(
        "https://translate.google.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t"},
        data={"q": SRC}, headers={"User-Agent": UA}, timeout=10,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    data = response.json()
    return check("".join(seg[0] for seg in data[0] if seg and seg[0]))


def libretranslate() -> str:
    response = requests.post(
        "https://libretranslate.com/translate",
        json={"q": SRC, "source": "en", "target": "zh", "format": "text"},
        headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:140]!r}")
    return check(response.json().get("translatedText", ""))


CANDIDATES = [
    ("微软 demo (cognitive)", ms_demo),
    ("微软 edge 授权变体", ms_edge_variants),
    ("Bing 页面内嵌 key", bing_scrape_key),
    ("有道 web", youdao_web),
    ("腾讯 transmart", tencent_webtran),
    ("彩云小译", caiyun),
    ("Google 直连镜像", google_mirror),
    ("LibreTranslate", libretranslate),
]


def main() -> None:
    print("第二轮翻译接口实测（标准：响应里真的含中文译文）\n")
    working = []
    for name, fn in CANDIDATES:
        ok, detail, ms = timed(fn)
        report(name, ok, detail, ms)
        if ok:
            working.append(name)
    print("\n--- 结论 ---")
    print("可用：" + ("、".join(working) if working else "（无）"))


if __name__ == "__main__":
    main()
