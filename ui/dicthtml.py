"""把词典条目渲染成 HTML —— 面板的「词典」页、悬停取词浮窗、独立词典窗口共用。

配色跟着 `ui/theme.py` 的主题字典走，所以词典页和别的页签观感一致。
"""
from __future__ import annotations

import html
import re
from typing import Dict, List, Optional

from core.dictdb import DictEntry, Sense, tokens

FALLBACK: Dict[str, str] = {
    "text": "#e8eef5",
    "muted": "#8b939c",
    "accent": "#4da3ff",
    "border": "#3a4450",
}


def _color(theme: Optional[dict], key: str) -> str:
    if theme:
        value = theme.get(key)
        if value:
            return str(value)
    return FALLBACK[key]


def exam_badges(entry: DictEntry, theme: Optional[dict] = None) -> str:
    """[高考] [四级] 这类标签。"""
    accent = _color(theme, "accent")
    out = []
    for label in entry.exam_labels:
        out.append(
            f'<span style="color:{accent};border:1px solid {accent};'
            f'border-radius:3px;padding:0 4px;font-size:85%">{html.escape(label)}</span>'
        )
    if entry.oxford:
        out.append(
            '<span style="color:#7ec96a;border:1px solid #7ec96a;'
            'border-radius:3px;padding:0 4px;font-size:85%">牛津核心</span>'
        )
    return " ".join(out)


def _sense_line(sense: Sense, index: int, theme: Optional[dict]) -> str:
    text_color = _color(theme, "text")
    muted = _color(theme, "muted")
    head = []
    if sense.pos:
        head.append(f'<b style="color:{muted}">{html.escape(sense.pos_label)}</b>')
    elif sense.domain:
        head.append(f'<b style="color:{muted}">{html.escape(sense.domain_label)}</b>')
    prefix = f'<span style="color:{muted}">{index}.</span> '
    mid = (" ".join(head) + " ") if head else ""
    return (f'<p style="margin:0 0 3px 0;color:{text_color}">'
            f'{prefix}{mid}{html.escape(sense.text)}</p>')


def entry_html(
    entry: DictEntry,
    theme: Optional[dict] = None,
    sentence: str = "",
    in_vocab: bool = False,
    compact: bool = False,
    max_senses: int = 0,
) -> str:
    """渲染一个词条。

    compact=True 用于悬停浮窗（少几行、字号小一点）。
    sentence 给了就在顶部标出「它在句子里」的上下文，并把相关义项排前面。
    """
    text_color = _color(theme, "text")
    muted = _color(theme, "muted")
    accent = _color(theme, "accent")
    border = _color(theme, "border")
    size = "88%" if compact else "100%"

    parts: List[str] = []
    head = [f'<span style="font-size:130%;font-weight:bold;color:{text_color}">'
            f'{html.escape(entry.word)}</span>']
    if entry.phonetic:
        head.append(f'<span style="color:{muted}">/{html.escape(entry.phonetic)}/</span>')
    if entry.collins_stars:
        head.append(f'<span style="color:#e0a13a">{entry.collins_stars}</span>')
    parts.append("<p style='margin:0 0 2px 0'>" + " ".join(head) + "</p>")

    if entry.matched_form:
        parts.append(
            f'<p style="margin:0 0 2px 0;color:{muted}">'
            f'（{html.escape(entry.matched_form)} → 原形 {html.escape(entry.word)}）</p>')

    meta: List[str] = []
    badges = exam_badges(entry, theme)
    if badges:
        meta.append(badges)
    if entry.freq_label:
        meta.append(f'<span style="color:{muted}">{entry.freq_label}</span>')
    if meta:
        parts.append('<p style="margin:2px 0">' + "  ".join(meta) + "</p>")

    mix = entry.pos_mix
    if mix and not compact:
        pieces = " · ".join(f"{name} {pct}%" for name, _, pct in mix[:4])
        parts.append(f'<p style="margin:2px 0;color:{muted}">常见词性：{pieces}</p>')

    if sentence:
        parts.append(
            f'<p style="margin:4px 0 2px 0;color:{muted}">出现在：'
            f'<span style="color:{text_color}">{html.escape(sentence)}</span></p>'
        )

    parts.append(f'<hr style="border:none;border-top:1px solid {border};margin:4px 0">')

    senses = entry.senses
    if max_senses and len(senses) > max_senses:
        senses = senses[:max_senses]
        tail = True
    else:
        tail = False
    if senses:
        for index, sense in enumerate(senses, start=1):
            parts.append(_sense_line(sense, index, theme))
        if tail:
            parts.append(f'<p style="margin:0;color:{muted}">…还有 '
                         f'{len(entry.senses) - max_senses} 条义项</p>')
    elif entry.translation:
        parts.append(f'<p style="color:{text_color}">'
                     f'{html.escape(entry.translation)}</p>')
    else:
        parts.append(f'<p style="color:{muted}">（这个词没有中文释义）</p>')

    forms = entry.forms
    if forms and not compact:
        pieces = " · ".join(f"{label} {value}" for label, value in forms)
        parts.append(f'<p style="margin:4px 0 0 0;color:{muted}">变形：{pieces}</p>')

    if not compact:
        star = "★ 已收录" if in_vocab else "☆ 收录到生词本"
        parts.append(
            f'<p style="margin:5px 0 0 0"><a href="vocab:{html.escape(entry.word)}" '
            f'style="color:{accent};text-decoration:none">{star}</a></p>')

    return f'<div style="font-size:{size}">' + "".join(parts) + "</div>"


def sentence_html(sentence: str, theme: Optional[dict] = None,
                  highlight: str = "") -> str:
    """把一句话渲染出来，其中的 `highlight` 加粗高亮。"""
    text_color = _color(theme, "text")
    accent = _color(theme, "accent")
    if not highlight:
        return f'<span style="color:{text_color}">{html.escape(sentence)}</span>'
    pattern = html.escape(highlight)
    body = html.escape(sentence)
    lower = body.lower()
    needle = pattern.lower()
    index = lower.find(needle)
    if index < 0:
        return f'<span style="color:{text_color}">{body}</span>'
    before = body[:index]
    hit = body[index:index + len(pattern)]
    after = body[index + len(pattern):]
    return (f'<span style="color:{text_color}">{before}'
            f'<b style="color:{accent}">{hit}</b>'
            f'{after}</span>')


def sentence_of(text: str, word: str) -> str:
    """从一屏文字里找出包含 `word` 的那一句（按中英文句末标点切）。"""
    if not word:
        return ""
    target = word.lower()
    for chunk in _split_sentences(text or ""):
        if target in chunk.lower():
            return chunk.strip()
    return ""


_SENTENCE_ENDS = ".!?。！？；;\n"


def _split_sentences(text: str) -> List[str]:
    out: List[str] = []
    current = []
    for char in text or "":
        current.append(char)
        if char in _SENTENCE_ENDS:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def vocab_summary_html(entry: DictEntry, sentence: str = "",
                       theme: Optional[dict] = None) -> str:
    """收录到生词本时用的释义摘要（只取一条最相关的义项）。"""
    senses = entry.ordered_senses(sentence) if sentence else entry.senses
    if not senses:
        return entry.translation.strip()
    first = senses[0]
    label = first.pos_label or first.domain_label
    return f"{label} {first.text}".strip() if label else first.text


def vocab_meaning(entry: DictEntry, sentence: str = "") -> str:
    """收录到生词本时存进 `meaning` 字段的**纯文本**释义。

    和 `vocab_summary_html` 的区别：这个保证是纯文本（生词本要导出 CSV，
    里面夹 HTML 标签会很难看），并且会截断到 200 字。
    """
    text = re.sub(r"<[^>]+>", "", vocab_summary_html(entry, sentence))
    text = html.unescape(text).strip()
    return text[:200]


def word_list_html(words: List[str], theme: Optional[dict] = None) -> str:
    """一屏里查得到的词，列成一排可点的链接。"""
    accent = _color(theme, "accent")
    muted = _color(theme, "muted")
    links = " ".join(
        f'<a href="word:{html.escape(word)}" '
        f'style="color:{accent};text-decoration:none">{html.escape(word)}</a>'
        for word in words
    )
    return f'<p style="color:{muted};margin:0 0 6px 0">这一屏的词：{links}</p>'


def pick_words(text: str, limit: int = 40) -> List[str]:
    """把一屏文字切成候选词（去重、长词优先、过滤太短的）。"""
    seen: List[str] = []
    for token in tokens(text):
        lowered = token.lower()
        if len(lowered) < 2:
            continue
        if lowered not in seen:
            seen.append(lowered)
    seen.sort(key=len, reverse=True)
    return seen[:limit]
