"""质检：edge translatetext 在「真实软件界面短句」上的表现。

重点回答三个问题：
1. HTML 转义 + 反转义的往返是否安全（含 < > & 的文本）。
2. 译文有没有多出原文没有的内容（拼接判断）。
3. 按 1.5 秒刷新节奏批量发送，延迟能否接受。

用法：.venv\\Scripts\\python.exe tools/quality_edge.py
"""
from __future__ import annotations

import html
import json
import time

import requests

URL = "https://edge.microsoft.com/translate/translatetext"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

# 典型软件界面文本（英文/德文/法文混排，模拟 OCR 抓到的窗口内容）
UI_SAMPLES = [
    "Settings", "Preferences", "Save As", "Open File", "Download failed",
    "Are you sure you want to delete this file?",
    "Network connection is not available",
    "The installer was unable to write to the selected directory.",
    "Please choose another location or run the setup as administrator.",
    "Font size", "Line spacing", "Recent files", "Untitled document",
    "Copy", "Paste", "Undo", "Redo", "Zoom in", "Zoom out",
    "Check for updates", "Restart required", "License agreement",
    "I accept the terms in the License Agreement",
    "File already exists. Do you want to replace it?",
    "Not enough disk space", "Access denied", "Invalid file format",
    "Einstellungen", "Datei speichern", "Konfiguration",
    "Paramètres", "Enregistrer sous", "Fichier introuvable",
]

# 含 HTML 敏感字符的真实场景
HTML_TRAPS = [
    "Cost < $10", "A & B", "R&D department", "Value > 100",
    "Use the <TAB> key", "x < y && y > z", "AT&T Inc.",
    "Search: \"foo\" & \"bar\"", "5 < 3 is false",
]


def escape_for_api(text: str) -> str:
    """转义 HTML 敏感字符，防止接口的标签对齐器吃掉文本。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape_from_api(text: str) -> str:
    """反转义，并把接口可能塞回来的 <b> 等标签去掉。"""
    import re

    text = re.sub(r"</?[a-zA-Z][^>]{0,40}>", "", text)
    return html.unescape(text)


def tr(texts: list[str], src: str = "", tgt: str = "zh-Hans") -> list[str]:
    payload = [escape_for_api(t) for t in texts]
    response = requests.post(
        URL, params={"from": src, "to": tgt, "isEnterpriseClient": "false"},
        json=payload,
        headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=30,
    )
    response.raise_for_status()
    return [unescape_from_api(item["translations"][0]["text"])
            for item in response.json()]


def main() -> None:
    print("=== A) UI 词表整体翻译（一次批量发送） ===")
    started = time.perf_counter()
    out = tr(UI_SAMPLES)
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  {len(UI_SAMPLES)} 条 / 1 次请求，耗时 {elapsed:.0f} ms")
    print()
    for src, dst in zip(UI_SAMPLES, out):
        flag = ""
        if len(dst) > len(src) * 3:
            flag = "  ⚠ 译文异常长"
        if not dst.strip():
            flag = "  ⚠ 空译文"
        print(f"  {src!r:<52} -> {dst!r}{flag}")

    print("\n=== B) HTML 敏感字符往返 ===")
    out = tr(HTML_TRAPS)
    for src, dst in zip(HTML_TRAPS, out):
        ok = "✅" if dst.strip() else "❌空"
        has_lt = "<" in dst or ">" in dst
        print(f"  {ok} {src!r:<26} -> {dst!r}")

    print("\n=== C) 空串与空白（接口对空串返回空，需本地兜底） ===")
    out = tr(["", "   ", "\t", "a", "ab"])
    for src, dst in zip(["", "   ", "\\t", "a", "ab"], out):
        print(f"  {src!r:<8} -> {dst!r}")

    print("\n=== D) 模拟实时轮询：每次 8 段，连做 10 轮 ===")
    frames = [
        ["Settings", "Download failed", "Are you sure?", "Cancel", "Retry",
         "Network error", "Save As", "Preferences"],
        ["Line 1 of the log file", "Line 2: connection reset by peer",
         "Line 3: retrying in 5 seconds", "Warning: disk usage at 91%",
         "Error code 0x80070005", "Access is denied", "OK", "Help"],
    ]
    latencies = []
    for round_index in range(10):
        batch = frames[round_index % len(frames)]
        started = time.perf_counter()
        tr(batch)
        latencies.append((time.perf_counter() - started) * 1000)
        time.sleep(1.5)
    latencies.sort()
    print(f"  10 轮延迟: 最小 {latencies[0]:.0f}ms / "
          f"中位 {latencies[len(latencies)//2]:.0f}ms / 最大 {latencies[-1]:.0f}ms")

    print("\n=== E) 换行分隔的多段（实际用法） ===")
    block = "\n".join(["Settings", "Download failed", "Font size"])
    out = tr([block])
    print(f"  原文 = {json.dumps(block, ensure_ascii=False)}")
    print(f"  译文 = {json.dumps(out[0], ensure_ascii=False)}")
    print(f"  行数一致 = {len(block.splitlines()) == len(out[0].splitlines())}")

    print("\n=== F) 判断接口返回的语种标记 ===")
    response = requests.post(
        URL, params={"from": "", "to": "zh-Hans", "isEnterpriseClient": "false"},
        json=["Einstellungen", "Paramètres", "Settings"],
        headers={"User-Agent": UA, "Content-Type": "application/json"}, timeout=20,
    )
    for item in response.json():
        print(f"  {item['translations'][0]['text']!r}  "
              f"检测={item.get('detectedLanguage')}")
    print("\n  sentLen 示例:", json.dumps(
        response.json()[0].get("translations", [{}])[0].get("sentLen"), ensure_ascii=False))


if __name__ == "__main__":
    main()
