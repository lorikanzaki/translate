"""临时校验：大模型接口的解析、端点拼装、无 key 时是否被跳过。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import LLMProvider, Translator, ProviderError  # noqa: E402


def main() -> int:
    p = LLMProvider(5, {})
    print("ready(no key) =", p.ready)
    print("endpoint =", p._endpoint())
    print("endpoint(custom) =", LLMProvider(
        5, {"base_url": "http://localhost:11434/v1/"})._endpoint())
    print("endpoint(openai) =", LLMProvider(
        5, {"base_url": "https://x/v1/chat/completions"})._endpoint())

    fence = "`" * 3
    print("parse(fenced) =", p._parse_array(f"{fence}json\n[\"a\",\"b\"]\n{fence}"))
    print("parse(chatty) =", p._parse_array('好的：[ "x", "y" ] 完毕'))
    for bad in ("没有数组", "[\"a\","):
        try:
            p._parse_array(bad)
            print("parse(bad) -> 没抛异常（不对）")
        except ProviderError as exc:
            print("parse(bad) ->", str(exc)[:60])

    try:
        p.batch_translate(["Settings"], "zh-CN", "en")
        print("no-key batch_translate -> 没抛异常（不对）")
    except ProviderError as exc:
        print("no-key batch_translate ->", exc)

    t = Translator(["llm", "edge"], timeout=10)
    print("order =", t.order, "| llm_ready =", t.llm_ready())
    out = t.translate(["Settings"], "zh-CN", "en")
    print("no-key 降级 -> provider =", out.provider, "| attempted =", out.attempted)

    t2 = Translator(["edge"], timeout=10)
    t2.set_llm_settings(base_url="https://api.deepseek.com/v1",
                        api_key="sk-test", model="deepseek-chat")
    t2.set_provider_order(["llm", "edge"])
    print("after set_llm_settings -> llm_ready =", t2.llm_ready())
    return 0


if __name__ == "__main__":
    sys.exit(main())
