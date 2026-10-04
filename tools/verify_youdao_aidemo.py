"""详细测试有道 aidemo 接口（https://aidemo.youdao.com/trans）的能力边界。

这是有道官方 demo 接口，无需 key。
用法：.venv\\Scripts\\python.exe tools\\verify_youdao_aidemo.py
"""
from __future__ import annotations

import json
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
URL = "https://aidemo.youdao.com/trans"


def translate(text: str, frm: str = "en", to: str = "zh-CHS") -> dict:
    response = requests.get(
        URL, params={"q": text, "from": frm, "to": to},
        headers={"User-Agent": UA}, timeout=20,
    )
    if response.status_code != 200:
        return {"_http": response.status_code, "_body": response.text[:200]}
    return response.json()


def join(dict_or_list) -> str:
    """translation 字段有时是 list[str]，有时是 list[dict]。"""
    if isinstance(dict_or_list, str):
        return dict_or_list
    parts = []
    for item in dict_or_list or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(item.get("t") or "")
    return "".join(parts)


def main() -> None:
    print("--- 1) 基本信息 ---")
    data = translate("Download failed")
    print("  errorCode =", data.get("errorCode"))
    print("  译文 =", join(data.get("translation")))
    print("  顶层字段 =", list(data.keys()))
    print("  AIGC 字段 =", json.dumps(data.get("AIGC"), ensure_ascii=False)[:200])

    print("\n--- 2) 多行文本（关键：能否一次翻多段并保留换行） ---")
    data = translate("Settings\nDownload failed\nFont size")
    print("  translation 类型 =", type(data.get("translation")).__name__)
    print("  原始 translation =", json.dumps(data.get("translation"), ensure_ascii=False)[:300])
    print("  拼接译文 =", repr(join(data.get("translation"))))

    print("\n--- 3) 自动识别语种（from=AUTO） ---")
    for text in ["Download failed", "下载失败", "Configuration"]:
        data = translate(text, frm="AUTO")
        print(f"  {text!r:<22} -> {join(data.get('translation'))!r}  "
              f"l={data.get('l')} type={data.get('type')}")

    print("\n--- 4) 长度上限（逐步加长，找拐点） ---")
    for length in (100, 200, 300, 500, 800, 1200, 2000, 5000):
        text = ("error " * (length // 6 + 1))[:length]
        try:
            data = translate(text)
            code = data.get("errorCode")
            out = join(data.get("translation"))
            print(f"  {length:>5} 字符 -> errorCode={code} 译文长={len(out)} "
                  f"{'✅' if code == '0' and out else '❌'}")
        except Exception as exc:
            print(f"  {length:>5} 字符 -> 异常 {type(exc).__name__}: {exc}")

    print("\n--- 5) 连续 15 次请求测限流 ---")
    codes = []
    started = time.perf_counter()
    for i in range(15):
        data = translate(f"setting number {i}")
        codes.append(data.get("errorCode"))
        time.sleep(0.1)
    elapsed = time.perf_counter() - started
    ok = sum(1 for code in codes if code == "0")
    print(f"  成功 {ok}/15，用时 {elapsed:.1f}s，errorCode 序列 = {codes}")

    print("\n--- 6) 批量并发 6 路（模拟 UI 多段同时翻） ---")
    import concurrent.futures

    texts = [f"menu item {i} could not be loaded" for i in range(6)]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda t: translate(t), texts))
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  6 段并发耗时 {elapsed:.0f} ms")
    for text, data in zip(texts, results):
        print(f"   {text!r} -> {join(data.get('translation'))!r}")

    print("\n--- 7) 特殊字符 / 代码片段（OCR 常见干扰） ---")
    for text in ["File: %s", "C:\\Users\\test", "1,234.56", "Hello, world!",
                 "Are you sure?", "\u2026\u2026", "OK"]:
        data = translate(text)
        print(f"  {text!r:<24} -> {join(data.get('translation'))!r} "
              f"code={data.get('errorCode')}")

    print("\n--- 8) 目标语言支持 ---")
    for to in ["zh-CHS", "zh-CHT", "en", "ja"]:
        data = translate("Download failed", to=to)
        print(f"  to={to:<8} -> {join(data.get('translation'))!r} "
              f"code={data.get('errorCode')}")


if __name__ == "__main__":
    main()
