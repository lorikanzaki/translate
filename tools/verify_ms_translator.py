"""验证微软 translator 免费接口的批量能力、长度上限、限流情况。

这是 Edge 浏览器内置翻译所用的接口，实测可用。
用法：.venv\\Scripts\\python.exe tools\\verify_ms_translator.py
"""
from __future__ import annotations

import json
import time

import requests

AUTH = "https://edge.microsoft.com/translate/auth"
API = "https://api-edge.cognitive.microsofttranslator.com/translate"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)


def get_token(session: requests.Session) -> str:
    response = session.get(AUTH, headers={"User-Agent": UA}, timeout=10)
    response.raise_for_status()
    token = response.text.strip()
    print(f"token 长度 = {len(token)}")
    print(f"token 前缀 = {token[:40]}...")
    return token


def translate(session: requests.Session, token: str, texts: list[str],
              to: str = "zh-Hans", frm: str = "") -> dict:
    body = [{"Text": t} for t in texts]
    params = {"api-version": "3.0", "to": to}
    if frm:
        params["from"] = frm
    response = session.post(
        API, params=params, json=body,
        headers={"User-Agent": UA, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        timeout=15,
    )
    print(f"  HTTP {response.status_code}  {len(response.content)} 字节")
    if response.status_code != 200:
        print("  body:", response.text[:300])
        return {}
    return response.json()


def main() -> None:
    session = requests.Session()
    token = get_token(session)

    print("\n--- 1) 单条 + 自动识别语种 ---")
    data = translate(session, token, ["Download failed"])
    print("  ", json.dumps(data, ensure_ascii=False))

    print("\n--- 2) 批量 5 条（数组一次请求） ---")
    batch = ["Settings", "Download failed", "Are you sure you want to delete this file?",
             "Network connection is not available", "Font size"]
    started = time.perf_counter()
    data = translate(session, token, batch)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  耗时 {elapsed:.0f} ms")
    for item, src in zip(data, batch):
        print(f"   {src!r} -> {item['translations'][0]['text']!r}")

    print("\n--- 3) 长文本 / 多行（模拟 OCR 段落合并） ---")
    long_text = ("The installer was unable to write to the selected directory. "
                 "Please choose another location or run the setup as administrator.")
    data = translate(session, token, [long_text])
    print("  ", json.dumps(data, ensure_ascii=False))

    print("\n--- 4) 换行分隔的多段（关键：接口是否保留换行） ---")
    data = translate(session, token, ["Settings\nDownload failed\nFont size"])
    print("  ", json.dumps(data, ensure_ascii=False))

    print("\n--- 5) 连续 10 次请求，测限流 ---")
    ok = 0
    codes: list[int] = []
    session2 = requests.Session()
    token2 = get_token(session2)
    for i in range(10):
        body = [{"Text": f"line number {i} of the test file"}]
        response = session2.post(
            API, params={"api-version": "3.0", "to": "zh-Hans"}, json=body,
            headers={"User-Agent": UA, "Authorization": f"Bearer {token2}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        codes.append(response.status_code)
        if response.status_code == 200:
            ok += 1
        time.sleep(0.2)
    print(f"  成功 {ok}/10，状态码序列 = {codes}")

    print("\n--- 6) 无 token / 假 token ---")
    for label, header in [("空 token", ""), ("假 token", "Bearer faketoken123")]:
        response = requests.post(
            API, params={"api-version": "3.0", "to": "zh-Hans"},
            json=[{"Text": "hello"}],
            headers={"User-Agent": UA, "Authorization": header}, timeout=15,
        )
        print(f"  {label}: HTTP {response.status_code}  {response.text[:150]}")

    print("\n--- 7) token 有效期的粗略判断 ---")
    print("  当前 token 直接复用第 3 次请求仍成功，说明可在进程内缓存。")

    print("\n--- 8) 单次请求条数上限（一次塞 50 条） ---")
    many = [f"menu item number {i}" for i in range(50)]
    data = translate(session, token, many)
    if data:
        print(f"  返回 {len(data)} 条")
        print("  末条:", json.dumps(data[-1], ensure_ascii=False))
    else:
        print("  50 条失败，可能超上限")


if __name__ == "__main__":
    main()
