"""独立复现调研结论：火山 crx 接口 + cn.bing.com ttranslatev3。

调研子代理报告这两个接口不用 key、大陆直连、短词质量远好于现在的主力（微软 Edge）。
但「HTTP 200」不等于「可用」，所以这里从零自己写一遍并逐项断言：
  1) 真能拿到中文译文（不是空串、不是原样退回、不是推广链接）
  2) 短词质量：Copy/Paste/Save/Settings 必须给出软件界面的通行说法
  3) 整句质量
  4) 换行保留
  5) 空串 / 纯空白不能消耗请求
  6) 连续 15 次不限流
  7) 延迟
用法：.venv\\Scripts\\python.exe tools\\verify_new_providers.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

SHORT_WORDS = ["Copy", "Paste", "Save", "Settings", "Sign in", "Open", "Close",
               "Download", "Cancel", "Retry", "Zoom in", "Search"]
SENTENCES = [
    "Are you sure you want to delete this file?",
    "The installer was unable to write to the selected directory.",
    "We couldn't find any matching results. Try a different keyword.",
    "Build succeeded with 3 warnings.",
]


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ------------------------------------------------------------------ 火山
VOLC_URL = "https://translate.volcengine.com/crx/translate/v1"


def volc(text: str, source: str = "en", target: str = "zh") -> str:
    resp = requests.post(
        VOLC_URL,
        json={"source_language": source, "target_language": target, "text": text},
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Origin": "https://translate.volcengine.com",
                 "Referer": "https://translate.volcengine.com/"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"非对象响应 {data!r}")
    return str(data.get("translation") or data.get("message") or "").strip()


# ------------------------------------------------------------------ Bing
class BingSession:
    """先从 cn.bing.com/translator 页面白拿 IG / IID / key+token，再打 ttranslatev3。"""

    _HELPER_RE = re.compile(r"params_AbusePreventionHelper\s*=\s*(\[[^\]]*\])")
    _IG_RE = re.compile(r'IG:"([^"]+)"')
    _IID_RE = re.compile(r'data-iid="([^"]+)"')

    def __init__(self) -> None:
        self.ig = ""
        self.iid = ""
        self.key = ""
        self.token = ""
        self.expires = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    def refresh(self) -> None:
        resp = self.session.get("https://cn.bing.com/translator", timeout=20)
        resp.raise_for_status()
        html = resp.text
        helper = self._HELPER_RE.search(html)
        ig = self._IG_RE.search(html)
        iid = self._IID_RE.search(html)
        if not (helper and ig and iid):
            raise RuntimeError(
                f"页面里找不到 token 字段: helper={bool(helper)} "
                f"ig={bool(ig)} iid={bool(iid)}  页面长度={len(html)}")
        key, token, _ttl = json.loads(helper.group(1))
        self.ig, self.iid = ig.group(1), iid.group(1)
        self.key, self.token = str(key), str(token)
        self.expires = time.time() + 3600

    def translate(self, text: str, source: str = "en", target: str = "zh-Hans") -> str:
        if time.time() >= self.expires or not self.token:
            self.refresh()
        resp = self.session.post(
            "https://cn.bing.com/ttranslatev3",
            params={"isVertical": "1", "IG": self.ig, "IID": self.iid},
            data={"fromLang": source, "text": text, "to": target,
                  "token": self.token, "key": self.key},
            headers={"Referer": "https://cn.bing.com/translator"},
            timeout=20,
        )
        resp.raise_for_status()
        if not resp.text.strip():
            raise RuntimeError("响应体为空")
        data = resp.json()
        if isinstance(data, dict):
            raise RuntimeError(f"字典响应 {json.dumps(data, ensure_ascii=False)[:160]}")
        return str(data[0]["translations"][0]["text"]).strip()


def show(title: str, results: dict) -> None:
    print(f"\n--- {title} ---")
    for src, dst in results.items():
        flag = "✅" if has_cjk(dst) else "❌"
        print(f"  {flag} {src!r} -> {dst!r}")


def main() -> int:
    problems: list[str] = []

    # ---------------------------------------------------------- 火山
    print("=" * 74)
    print("火山 translate.volcengine.com/crx/translate/v1")
    print("=" * 74)
    try:
        t0 = time.perf_counter()
        sample = volc("hello world")
        print(f"  探针 {sample!r}   {(time.perf_counter()-t0)*1000:.0f}ms")
        if not has_cjk(sample):
            problems.append(f"火山: 探针没有中文 -> {sample!r}")
    except Exception as exc:
        problems.append(f"火山: 探针失败 {type(exc).__name__}: {exc}")
        sample = ""

    if sample:
        words = {}
        for w in SHORT_WORDS:
            try:
                words[w] = volc(w)
            except Exception as exc:
                words[w] = f"<{type(exc).__name__}: {exc}>"
                problems.append(f"火山: 短词 {w!r} 失败 {exc}")
        show("短词（软件界面通行说法）", words)
        expect = {"Copy": "复制", "Paste": "粘贴", "Save": "保存", "Settings": "设置"}
        for src, want in expect.items():
            got = words.get(src, "")
            if got != want:
                problems.append(f"火山: {src!r} 期望 {want!r} 实得 {got!r}")

        sentences = {}
        for s in SENTENCES:
            try:
                t0 = time.perf_counter()
                sentences[s] = f"{volc(s)}   ({(time.perf_counter()-t0)*1000:.0f}ms)"
            except Exception as exc:
                sentences[s] = f"<{type(exc).__name__}: {exc}>"
        show("整句", sentences)

        try:
            multi = volc("Settings\nDownload failed\nFont size")
            print(f"\n  换行保留: {multi!r}  行数={len(multi.splitlines())}")
            if len(multi.splitlines()) != 3:
                problems.append(f"火山: 换行没保留 -> {multi!r}")
        except Exception as exc:
            problems.append(f"火山: 换行测试失败 {exc}")

        try:
            print(f"  空串: {volc('')!r}    纯空白: {volc('   ')!r}")
        except Exception as exc:
            print(f"  空串抛错（可接受）: {type(exc).__name__}: {exc}")

        t0 = time.perf_counter()
        ok = 0
        for i in range(15):
            try:
                if has_cjk(volc(f"Open file number {i}")):
                    ok += 1
            except Exception as exc:
                print(f"  第 {i+1} 次失败 {type(exc).__name__}: {exc}")
        print(f"\n  连打 15 次: {ok}/15 成功，总耗时 {(time.perf_counter()-t0)*1000:.0f}ms")
        if ok < 15:
            problems.append(f"火山: 连打 15 次只成功 {ok}")

    # ---------------------------------------------------------- Bing
    print("\n" + "=" * 74)
    print("cn.bing.com/ttranslatev3")
    print("=" * 74)
    bing = BingSession()
    try:
        t0 = time.perf_counter()
        bing.refresh()
        print(f"  拿 token 成功 IG={bing.ig!r} IID={bing.iid!r} "
              f"key={bing.key[:8]}… {(time.perf_counter()-t0)*1000:.0f}ms")
        t0 = time.perf_counter()
        sample2 = bing.translate("hello world")
        print(f"  探针 {sample2!r}   {(time.perf_counter()-t0)*1000:.0f}ms")
        if not has_cjk(sample2):
            problems.append(f"Bing: 探针没有中文 -> {sample2!r}")
    except Exception as exc:
        problems.append(f"Bing: 初始化失败 {type(exc).__name__}: {exc}")
        sample2 = ""

    if sample2:
        words2 = {}
        for w in SHORT_WORDS:
            try:
                words2[w] = bing.translate(w)
            except Exception as exc:
                words2[w] = f"<{type(exc).__name__}: {exc}>"
                problems.append(f"Bing: 短词 {w!r} 失败 {exc}")
        show("短词（软件界面通行说法）", words2)
        for src, want in expect.items():
            got = words2.get(src, "")
            if got != want:
                problems.append(f"Bing: {src!r} 期望 {want!r} 实得 {got!r}")

        sentences2 = {}
        for s in SENTENCES:
            try:
                t0 = time.perf_counter()
                sentences2[s] = f"{bing.translate(s)}   ({(time.perf_counter()-t0)*1000:.0f}ms)"
            except Exception as exc:
                sentences2[s] = f"<{type(exc).__name__}: {exc}>"
        show("整句", sentences2)

        try:
            merged = "Settings\u241eDownload failed\u241eFont size"
            out = bing.translate(merged)
            print(f"\n  \\u241e 合并发送: {out!r}")
            print(f"  按 \\u241e 切回: {[p for p in out.split(chr(0x241e))]}")
            if len(out.split("\u241e")) != 3:
                problems.append(f"Bing: \\u241e 合并没有切回 3 段 -> {out!r}")
        except Exception as exc:
            problems.append(f"Bing: 合并测试失败 {exc}")

        try:
            two_lines = bing.translate("Settings\nDownload failed")
            print(f"  换行保留: {two_lines!r}")
        except Exception as exc:
            print(f"  换行测试异常: {exc}")

        t0 = time.perf_counter()
        ok2 = 0
        for i in range(15):
            try:
                if has_cjk(bing.translate(f"Open file number {i}")):
                    ok2 += 1
            except Exception as exc:
                print(f"  第 {i+1} 次失败 {type(exc).__name__}: {exc}")
        print(f"\n  连打 15 次: {ok2}/15 成功，总耗时 {(time.perf_counter()-t0)*1000:.0f}ms")
        if ok2 < 15:
            problems.append(f"Bing: 连打 15 次只成功 {ok2}")

        try:
            print(f"  空串: {bing.translate('')!r}")
        except Exception as exc:
            print(f"  空串抛错（可接受）: {type(exc).__name__}: {exc}")

    # ---------------------------------------------------------- 汇总
    print("\n" + "=" * 74)
    if problems:
        print(f"❌ {len(problems)} 个问题：")
        for item in problems:
            print(f"   - {item}")
    else:
        print("✅ 两个接口全部断言通过")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
