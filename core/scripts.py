"""文字体系判定：判断一块屏幕文字到底属于哪种语言，用来决定「要不要翻译」。

为什么单独一个模块：`core/ocr.py`（决定哪些识别结果值得送翻译）和
`core/translate.py`（决定译文算不算失败）都要用它，放在任何一边都会让
另一边反向依赖。

背景：程序原本只认「ASCII 字母」，于是日语、俄语、韩语、阿拉伯语、泰语、
印地语、希腊语的整屏文字被静默丢掉，表现出来就是「只能英语转其它语言」。
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional

# 文字体系 -> Unicode 区间。只列界面文字里会碰到的。
_SCRIPT_RANGES = (
    ("han", 0x3400, 0x4DBF),          # 扩展 A
    ("han", 0x4E00, 0x9FFF),          # 基本区
    ("han", 0xF900, 0xFAFF),          # 兼容表意文字
    ("kana", 0x3040, 0x30FF),         # 平假名 + 片假名
    ("hangul", 0x1100, 0x11FF),       # 谚文字母
    ("hangul", 0xAC00, 0xD7AF),       # 谚文音节
    ("cyrillic", 0x0400, 0x04FF),
    ("greek", 0x0370, 0x03FF),
    ("arabic", 0x0600, 0x06FF),
    ("arabic", 0x0750, 0x077F),       # 阿拉伯补充
    ("hebrew", 0x0590, 0x05FF),
    ("thai", 0x0E00, 0x0E7F),
    ("devanagari", 0x0900, 0x097F),
)

# 目标语言 -> 它自己用的文字体系（命中就说明「已经是目标语言，不用翻」）。
# 没收录的语言返回空集合，表示不做这层过滤 —— 宁可不跳，也别误跳。
_TARGET_SCRIPTS: Dict[str, FrozenSet[str]] = {
    "zh": frozenset({"han"}),
    "ja": frozenset({"han", "kana"}),
    "ko": frozenset({"hangul", "han"}),
    "ru": frozenset({"cyrillic"}),
    "uk": frozenset({"cyrillic"}),
    "be": frozenset({"cyrillic"}),
    "bg": frozenset({"cyrillic"}),
    "sr": frozenset({"cyrillic"}),
    "mk": frozenset({"cyrillic"}),
    "ar": frozenset({"arabic"}),
    "fa": frozenset({"arabic"}),
    "ur": frozenset({"arabic"}),
    "he": frozenset({"hebrew"}),
    "el": frozenset({"greek"}),
    "th": frozenset({"thai"}),
    "hi": frozenset({"devanagari"}),
    "ne": frozenset({"devanagari"}),
    "mr": frozenset({"devanagari"}),
}

# 拉丁字母语言一大堆，单独批量登记。
for _latin in (
    "en de fr es pt it nl pl tr vi id ms cs sv da fi no hu ro sk sl hr lt lv "
    "et ca gl eu sq az uz sw tl af cy is ga mt lb"
).split():
    _TARGET_SCRIPTS[_latin] = frozenset({"latin"})


def script_of(char: str) -> str:
    """单个字符属于哪个文字体系。"""
    code = ord(char)
    for name, low, high in _SCRIPT_RANGES:
        if low <= code <= high:
            return name
    if char.isalpha():
        return "latin"
    return "other"


def primary_script(text: str) -> str:
    """文本主要用哪种文字体系（按字母数投票，平手取先遇到的）。"""
    counts: Dict[str, int] = {}
    for char in text or "":
        if not char.isalpha():
            continue
        name = script_of(char)
        if name == "other":
            continue
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "other"
    return max(counts.items(), key=lambda item: item[1])[0]


def base_lang(lang: str) -> str:
    """`zh-CN` / `zh_Hans` / `ZH` 都归成 `zh`。"""
    code = (lang or "").strip().lower().replace("_", "-")
    return code.split("-")[0]


def target_scripts(target: str) -> FrozenSet[str]:
    """目标语言用的文字体系集合；未收录时返回空集合。"""
    return _TARGET_SCRIPTS.get(base_lang(target), frozenset())


def source_is_declared(source: str, target: str) -> bool:
    """用户是不是**明确指定**了源语言，且它和目标语言不是同一种。

    指定了源语言意味着我们比"靠文字体系猜"知道得更多，于是可以绕过
    「这段文字看起来已经是目标语言了」这类判定 —— 日语界面里的
    `設定 / 編集 / 表示 / 終了` 全是汉字，看文字体系和中文一样，
    但对一个选了「源语言=日语」的用户来说，它们恰恰是最需要翻译的行。
    """
    src = base_lang(source or "")
    if not src or src == "auto":
        return False
    return src != base_lang(target or "")


def is_latin_lang(lang: str) -> bool:
    """这个语言是不是用拉丁字母。"""
    return base_lang(lang) in _TARGET_SCRIPTS and \
        _TARGET_SCRIPTS[base_lang(lang)] == frozenset({"latin"})


def has_letters(text: str, count: int = 1) -> bool:
    """是否含至少 `count` 个字母（任何文字体系的字母都算）。"""
    return sum(1 for char in text or "" if char.isalpha()) >= count


def scripts_present(text: str) -> FrozenSet[str]:
    """这段文字里出现了哪些文字体系。

    注意与 `primary_script` 的区别：那个回答「主要是什么语言」，这个回答
    「有没有出现」。判断「译文是不是压根没翻」要用后者 —— 中文译文里夹一个
    没翻的英文专名（`导出项目为 PDF`）是完全正常的，不能因为拉丁字母多就判它
    没翻。
    """
    return frozenset(
        name for name in (script_of(char) for char in text or "")
        if name != "other"
    )


def has_any_script(text: str, wanted: FrozenSet[str]) -> bool:
    """这段文字里是否出现了 `wanted` 里的任何一种文字体系。"""
    if not wanted:
        return True
    return bool(scripts_present(text) & wanted)


def lang_from_script(script: str) -> Optional[str]:
    """把文字体系粗粗映射回语言码（只用于给用户看的猜测）。"""
    return {
        "han": "zh",
        "kana": "ja",
        "hangul": "ko",
        "cyrillic": "ru",
        "arabic": "ar",
        "greek": "el",
        "thai": "th",
        "devanagari": "hi",
        "hebrew": "he",
        "latin": "en",
    }.get(script)
