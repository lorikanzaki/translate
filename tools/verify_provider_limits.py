"""两个新接口的边界探测：源语言参数、合并文本的长度上限。

上一轮（tools\\verify_merge_batch.py）已证明：
  · 火山 12/30 条合并发送能逐条拆回，且与单发一致
  · cn.bing.com 12/30 条合并发送能逐条拆回（译文与单发略有出入但无错位/串行）
这一轮要回答接入前必须知道的两件事：
  1) 源语言怎么写：火山 "auto" 行不行？Bing 的 fromLang=auto-detect / 空串行不行？
     （调研说 Bing 的 from=auto / 空串会返回 statusCode 400）
  2) 合并后的文本能有多长：调研怀疑 Bing 对约 1023 字符以上的源文本会截断，
     如果属实，就必须限制一次合并的长度，否则一屏长文本会整批错乱。
     做法：用 N 条等长英文拼成不同总长度，看能否拆回 N 段、段数对不对。

用法：.venv\\Scripts\\python.exe tools\\verify_provider_limits.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import SEP, has_cjk, split_batch  # noqa: E402
from tools.verify_new_providers import BingSession, volc  # noqa: E402


def probe_sources() -> None:
    print("=" * 74)
    print("1) 源语言参数")
    print("=" * 74)

    print("\n  火山 source_language:")
    for src in ["auto", "en", ""]:
        try:
            got = volc("Einstellungen", source=src, target="zh")
            print(f"    {src!r:8} -> {got!r}")
        except Exception as exc:
            print(f"    {src!r:8} -> ❌ {type(exc).__name__}: {exc}")

    print("\n  Bing fromLang（用英文与德文各试一次）:")
    bing = BingSession()
    try:
        bing.refresh()
        for src in ["auto-detect", "en", ""]:
            for text in ["Download failed", "Einstellungen"]:
                try:
                    got = bing.translate(text, source=src)
                    print(f"    {src!r:14} {text!r:18} -> {got!r}")
                except Exception as exc:
                    print(f"    {src!r:14} {text!r:18} -> ❌ {type(exc).__name__}: {exc}")
    except Exception as exc:
        print(f"    ❌ 拿 token 失败: {exc}")


def probe_lengths(send, name: str) -> list[str]:
    """用不同总长度的合并文本，看能不能原样拆回。"""
    print(f"\n  {name} 长度上限:")
    problems: list[str] = []
    line = "The operation could not be completed because the file is in use."
    for count in [3, 6, 10, 16, 24, 32, 48]:
        lines = [f"{i:02d} {line}" for i in range(count)]
        merged = SEP.join(lines)
        try:
            t0 = time.perf_counter()
            out = send(merged)
            elapsed = (time.perf_counter() - t0) * 1000
        except Exception as exc:
            print(f"    {count:3} 条 / {len(merged):5} 字符 -> ❌ {type(exc).__name__}: {exc}")
            problems.append(f"{name}: {count} 条时请求失败 {exc}")
            continue
        parts = split_batch(out, count)
        ok = len(parts) == count and all(has_cjk(p) for p in parts)
        mark = "✅" if ok else "❌"
        print(f"    {mark} {count:3} 条 / {len(merged):5} 字符 -> 拆回 {len(parts):3} 条  "
              f"响应 {len(out):5} 字符  {elapsed:5.0f}ms")
        if not ok:
            problems.append(
                f"{name}: 合并 {count} 条（{len(merged)} 字符）只拆回 {len(parts)} 条")
            if len(parts) < count:
                print(f"        前两条: {parts[0][:60]!r} / {parts[1][:60]!r}")
                print(f"        后两条: {parts[-2][:60]!r} / {parts[-1][:60]!r}")
    return problems


def main() -> int:
    probe_sources()

    problems: list[str] = []
    print("\n" + "=" * 74)
    print("2) 合并文本长度上限")
    print("=" * 74)
    problems += probe_lengths(volc, "火山")

    bing = BingSession()
    try:
        bing.refresh()
        problems += probe_lengths(bing.translate, "Bing")
    except Exception as exc:
        problems.append(f"Bing: 拿 token 失败 {exc}")
        print(f"  ❌ 拿 token 失败: {exc}")

    print("\n" + "=" * 74)
    if problems:
        print(f"❌ {len(problems)} 个问题：")
        for item in problems:
            print(f"   - {item}")
    else:
        print("✅ 没有发现长度上限问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
