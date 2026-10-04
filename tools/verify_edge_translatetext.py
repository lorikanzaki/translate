"""验证 edge.microsoft.com/translate/translatetext —— 当前唯一稳定的免费接口。

要点：body 是裸 JSON 字符串数组（不是 [{"Text":...}]），且 HTML 标签对齐器常开，
发送前必须转义 < > &。
用法：.venv\\Scripts\\python.exe tools/verify_edge_translatetext.py
"""
from __future__ import annotations

import concurrent.futures
import json
import time

import requests

URL = "https://edge.microsoft.com/translate/translatetext"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)


def tr(texts: list[str], src: str = "en", tgt: str = "zh-Hans") -> list[str]:
    response = requests.post(
        URL,
        params={"from": src, "to": tgt, "isEnterpriseClient": "false"},
        json=texts,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:200]!r}")
    data = response.json()
    return [item["translations"][0]["text"] for item in data]


def main() -> None:
    print("=== 1) 基本翻译 + 批量 ===")
    sample = ["Settings", "Download failed", "Save As", "Preferences",
              "Are you sure you want to delete this file?"]
    started = time.perf_counter()
    out = tr(sample)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  批量 {len(sample)} 条耗时 {elapsed:.0f} ms")
    for src, dst in zip(sample, out):
        print(f"   {src!r:<46} -> {dst!r}")

    print("\n=== 2) 自动识别语种 (from='') ===")
    response = requests.post(
        URL, params={"from": "", "to": "zh-Hans", "isEnterpriseClient": "false"},
        json=["Configuration", "Konfiguration"],
        headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=20,
    )
    data = response.json()
    for item in data:
        print(f"   {item['translations'][0]['text']!r} 检测={item.get('detectedLanguage')}")

    print("\n=== 3) 换行是否原样保留（关键，决定能否整段发送） ===")
    multi = "Settings\nDownload failed\nFont size"
    out = tr([multi])
    print(f"  原文 = {multi!r}")
    print(f"  译文 = {out[0]!r}")
    print(f"  行数一致: {len(multi.splitlines()) == len(out[0].splitlines())}")
    sent = json.dumps([multi])
    print(f"  实际发送体 = {sent}")

    print("\n=== 4) HTML 标签对齐器陷阱 ===")
    for text in ["Compare a < b and c > d & more", "Value: 5 < 10",
                 "Use <div> tag", "A & B"]:
        try:
            out = tr([text])
            print(f"   {text!r:<36} -> {out[0]!r}")
        except Exception as exc:
            print(f"   {text!r:<36} -> 异常 {exc}")
    escaped = ["Compare a &lt; b and c &gt; d &amp; more"]
    print(f"  转义后 {escaped[0]!r} -> {tr(escaped)[0]!r}")

    print("\n=== 5) 空串 / 纯空白 / 特殊内容 ===")
    out = tr(["", "   ", "1,234.56", "C:\\Users\\test", "……", "OK", "%s"])
    for src, dst in zip(["", "   ", "1,234.56", "C:\\Users\\test", "……", "OK", "%s"], out):
        print(f"   {src!r:<18} -> {dst!r}")

    print("\n=== 6) 长度与条数上限 ===")
    for label, texts in [
        ("单条 5000 字符", ["error occurred " * 360]),
        ("单条 20000 字符", ["x" * 20000]),
        ("单条 50000 字符", ["y" * 50000]),
        ("100 条", [f"item {i}" for i in range(100)]),
        ("300 条", [f"entry number {i}" for i in range(300)]),
    ]:
        try:
            started = time.perf_counter()
            out = tr(texts)
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  {label:<18} -> OK 返回 {len(out)} 条 {elapsed:.0f}ms")
        except Exception as exc:
            print(f"  {label:<18} -> 失败 {str(exc)[:120]}")

    print("\n=== 7) 限流压测：15 次背靠背 ===")
    codes = []
    started = time.perf_counter()
    for i in range(15):
        try:
            tr([f"message number {i} failed to load"])
            codes.append(200)
        except Exception as exc:
            codes.append(str(exc)[:40])
    print(f"  耗时 {(time.perf_counter()-started)*1000:.0f}ms  结果 = {codes}")

    print("\n=== 8) 并发 8 路 ===")
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: tr([f"concurrent {i}"]), range(8)))
    print(f"  耗时 {(time.perf_counter()-started)*1000:.0f}ms 成功 {len(results)}/8")

    print("\n=== 9) 目标语言 ===")
    for tgt in ["zh-Hans", "zh-Hant", "en", "ja"]:
        try:
            out = tr(["Download failed"], src="en", tgt=tgt)
            print(f"  to={tgt:<9} -> {out[0]!r}")
        except Exception as exc:
            print(f"  to={tgt:<9} -> 失败 {str(exc)[:100]}")

    print("\n=== 10) 无需 UA / 无 headers 是否也行 ===")
    response = requests.post(
        URL, params={"from": "en", "to": "zh-Hans", "isEnterpriseClient": "false"},
        json=["Settings"], timeout=20,
    )
    print(f"  裸请求 HTTP {response.status_code} {response.text[:160]}")


if __name__ == "__main__":
    main()
