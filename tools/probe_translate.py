"""翻译接口连通性探测：逐个真实发请求，报告哪个能用、延迟多少。

用法：.venv\\Scripts\\python.exe tools\\probe_translate.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
SAMPLE = ["Settings", "Download failed"]
PROXY = {k: v for k, v in os.environ.items() if k.lower().endswith("_proxy")}
TIMEOUT = 8


def report(name: str, ok: bool, detail: str, ms: float) -> None:
    mark = "[OK]  " if ok else "[FAIL]"
    print(f"{mark} {name:<28} {ms:>6.0f}ms  {detail}")


def timed(fn):
    started = time.perf_counter()
    try:
        detail = fn()
        return True, detail, (time.perf_counter() - started) * 1000
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160], (time.perf_counter() - started) * 1000


# ---------------------------------------------------------------- DNS
def dns_probe() -> None:
    print("--- DNS / 可达性 ---")
    for host in (
        "translate.googleapis.com",
        "translate.google.com",
        "edge.microsoft.com",
        "api-edge.cognitive.microsofttranslator.com",
        "www.bing.com",
        "fanyi.qq.com",
        "api.mymemory.translated.net",
        "api.github.com",
    ):
        started = time.perf_counter()
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            addrs = sorted({item[4][0] for item in infos})
            print(f"  {host:<44} -> {', '.join(addrs[:3])}  {(time.perf_counter()-started)*1000:.0f}ms")
        except Exception as exc:
            print(f"  {host:<44} -> DNS 失败: {exc}")
    print(f"  代理环境变量: {list(PROXY) or '（无）'}")


# ---------------------------------------------------------------- 候选接口
def google_gtx() -> str:
    response = requests.post(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t"},
        data={"q": "\n".join(SAMPLE)},
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return "".join(seg[0] for seg in data[0] if seg and isinstance(seg[0], str))


def google_clients5() -> str:
    response = requests.get(
        "https://clients5.google.com/translate_a/t",
        params={"client": "dict-chrome-ex", "sl": "auto", "tl": "zh-CN", "q": SAMPLE},
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list) and data and isinstance(data[0], list):
        return str(data[0])
    return json.dumps(data, ensure_ascii=False)[:120]


def bing_edge_auth() -> str:
    response = requests.get("https://edge.microsoft.com/translate/auth",
                            headers={"User-Agent": UA}, timeout=TIMEOUT)
    body = response.text.strip()
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} body={body[:120]!r}")
    if len(body) < 20:
        raise RuntimeError(f"token 过短: {body[:80]!r}")
    return f"token {len(body)} 字节"


def bing_ttranslatev3() -> str:
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://www.bing.com/translator",
        "Origin": "https://www.bing.com",
    })
    session.get("https://www.bing.com/translator", timeout=TIMEOUT)  # 取 cookie
    response = session.post(
        "https://www.bing.com/ttranslatev3",
        params={"isVertical": "1", "IG": "1", "IID": "translator.0"},
        data={
            "fromLang": "auto-detect",
            "text": SAMPLE[0],
            "to": "zh-Hans",
            "token": "x",
            "key": "x",
        },
        timeout=TIMEOUT,
    )
    body = response.text[:200]
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {body!r}")
    return body


def mymemory() -> str:
    response = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": SAMPLE[0], "langpair": "en|zh-CN"},
        headers={"User-Agent": UA}, timeout=max(TIMEOUT, 12),
    )
    response.raise_for_status()
    return response.json()["responseData"]["translatedText"]


def tencent_free() -> str:
    response = requests.get(
        "https://fanyi.qq.com/api/translate",
        params={"source": "auto", "target": "zh", "sourceText": SAMPLE[0]},
        headers={"User-Agent": UA, "Referer": "https://fanyi.qq.com/"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("errCode")) not in ("0", "None"):
        raise RuntimeError(f"errCode={data.get('errCode')} msg={data.get('errMsg')}")
    return data.get("translate", {}).get("targetText", "")[:120]


def baidu_free() -> str:
    response = requests.get(
        "https://fanyi.baidu.com/sug", data={"kw": SAMPLE[0]},
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    entry = (data.get("data") or [{}])[0]
    return str(entry.get("v", ""))[:120]


def sougou_free() -> str:
    response = requests.post(
        "https://fanyi.sogou.com/api/transpc/text/result",
        json={"from": "en", "to": "zh-CHS", "text": SAMPLE[0],
              "client": "pc", "fr": "browser_pc"},
        headers={"User-Agent": UA, "Referer": "https://fanyi.sogou.com/text",
                 "Origin": "https://fanyi.sogou.com",
                 "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return str(data.get("data", {}).get("translate", {}).get("dit", ""))[:120]


CANDIDATES = [
    ("Google gtx (translate.googleapis)", google_gtx),
    ("Google clients5", google_clients5),
    ("Bing edge auth token", bing_edge_auth),
    ("Bing ttranslatev3", bing_ttranslatev3),
    ("MyMemory", mymemory),
    ("腾讯 fanyi.qq.com", tencent_free),
    ("百度 fanyi sug", baidu_free),
    ("搜狗 fanyi.sogou.com", sougou_free),
]


def main() -> int:
    print("翻译接口连通性探测（本机网络）\n")
    dns_probe()
    print("\n--- 接口实测 ---")
    working = []
    for name, fn in CANDIDATES:
        ok, detail, ms = timed(fn)
        report(name, ok, detail, ms)
        if ok:
            working.append(name)
    print("\n--- 结论 ---")
    if working:
        print("可用接口：" + "、".join(working))
    else:
        print("没有任何免费接口可用，必须改用带 key 的官方接口或本地离线模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
