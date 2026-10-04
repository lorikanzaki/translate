"""验证「合并发送」这条捷径对真实屏幕量级是否可靠。

火山 crx 接口和 cn.bing.com 都**不支持一次发多条**，但都保留换行/特殊字符。
所以打算把一屏文本用 SEP = "\\n\\u241e\\n" 连成一条发过去，回来再按 \\u241e 拆开
（core\\translate.py 的 split_batch 就是干这个的）。

这条捷径只有在下面几件事都成立时才敢用：
  1) 12 条真实界面文本合并后，能**逐条原样拆回**，且每条都真有译文
  2) 拆回的每条，与「单独发那一条」得到的译文**一致**（说明没有串行错位）
  3) 30 条长文本也扛得住（不截断、不错位）
  4) 空行 / 重复行不会把条数搞乱

用法：.venv\\Scripts\\python.exe tools\\verify_merge_batch.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import SEP, has_cjk, split_batch  # noqa: E402
from tools.verify_new_providers import BingSession, volc  # noqa: E402

SCREEN = [
    "File",
    "Edit",
    "View",
    "Export project as PDF",
    "Are you sure you want to delete this file?",
    "The installer was unable to write to the selected directory.",
    "Build succeeded with 3 warnings.",
    "Now playing",
    "Shuffle all",
    "Sign in to continue",
    "Email address",
    "Forgot your password?",
]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {label}  {detail}")
    return ok


def main() -> int:
    problems: list[str] = []

    def merged_roundtrip(name: str, send, lines) -> list[str]:
        merged = SEP.join(lines)
        try:
            out = send(merged)
        except Exception as exc:
            problems.append(f"{name}: 合并请求失败 {type(exc).__name__}: {exc}")
            return []
        parts = split_batch(out, len(lines))
        ok = len(parts) == len(lines) and all(p.strip() for p in parts)
        check(f"{name}: {len(lines)} 条合并后能拆回",
              ok, f"拆出 {len(parts)} 条")
        if not ok:
            problems.append(f"{name}: 拆回条数不对 {len(parts)} != {len(lines)}: {out!r}")
            return parts
        missing = [p for p in parts if not has_cjk(p)]
        if missing:
            problems.append(f"{name}: 有 {len(missing)} 条译文没有中文 {missing}")
            check(f"{name}: 每条都有中文译文", False, str(missing))
        else:
            check(f"{name}: 每条都有中文译文", True)
        for src, dst in zip(lines, parts):
            print(f"       {src!r} -> {dst!r}")
        return parts

    print("=" * 74)
    print("火山 translate.volcengine.com/crx/translate/v1")
    print("=" * 74)
    volc_parts = merged_roundtrip("火山", volc, SCREEN)

    if volc_parts:
        print("\n  与「逐条单独发」对比（应逐条一致）：")
        diff = 0
        for src, got in zip(SCREEN, volc_parts):
            try:
                alone = volc(src)
            except Exception as exc:
                alone = f"<{exc}>"
            same = alone.strip() == got.strip()
            if not same:
                diff += 1
                print(f"     ⚠️ {src!r}\n        单独: {alone!r}\n        合并: {got!r}")
        check("火山: 合并结果与单发一致", diff == 0, f"{diff} 条不同")
        if diff:
            problems.append(f"火山: 合并与单发有 {diff} 条不一致")

    print("\n  30 条长文本压力测试：")
    long_lines = [f"Error {i}: The operation could not be completed because the file is in use."
                  for i in range(30)]
    merged_roundtrip("火山/30条", volc, long_lines)

    print("\n  空行与重复行：")
    tricky = ["Settings", "", "Settings", "   ", "Download failed"]
    merged_roundtrip("火山/含空行", volc, tricky)

    print("\n" + "=" * 74)
    print("cn.bing.com/ttranslatev3")
    print("=" * 74)
    bing = BingSession()
    try:
        bing.refresh()
    except Exception as exc:
        problems.append(f"Bing: 拿 token 失败 {exc}")
        print(f"  ❌ 拿 token 失败: {exc}")
        bing = None

    if bing is not None:
        bing_parts = merged_roundtrip("Bing", bing.translate, SCREEN)

        if bing_parts:
            print("\n  与「逐条单独发」对比（应逐条一致）：")
            diff2 = 0
            for src, got in zip(SCREEN, bing_parts):
                try:
                    alone = bing.translate(src)
                except Exception as exc:
                    alone = f"<{exc}>"
                if alone.strip() != got.strip():
                    diff2 += 1
                    print(f"     ⚠️ {src!r}\n        单独: {alone!r}\n        合并: {got!r}")
            check("Bing: 合并结果与单发一致", diff2 == 0, f"{diff2} 条不同")
            if diff2:
                problems.append(f"Bing: 合并与单发有 {diff2} 条不一致")

        print("\n  30 条长文本压力测试：")
        merged_roundtrip("Bing/30条", bing.translate, long_lines)

        print("\n  空行与重复行：")
        merged_roundtrip("Bing/含空行", bing.translate, tricky)

    print("\n" + "=" * 74)
    if problems:
        print(f"❌ {len(problems)} 个问题：")
        for item in problems:
            print(f"   - {item}")
    else:
        print("✅ 合并发送这条捷径成立")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
