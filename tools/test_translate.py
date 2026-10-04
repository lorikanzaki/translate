"""翻译层测试：术语表、HTML 转义、缓存、降级链、真实接口质量。

分两部分：
  纯离线单元测试（不需要网络）  -> 术语表、转义、批切分、缓存、降级
  在线集成测试                  -> 真实调用微软接口，看译文中是否真有汉字

用法：.venv\\Scripts\\python.exe tools/test_translate.py
      .venv\\Scripts\\python.exe tools/test_translate.py offline   # 只跑离线部分
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import (  # noqa: E402
    MAX_ITEMS_PER_REQUEST, MicrosoftEdgeProvider, ProviderError,
    Translator, escape_for_api, load_phrasebook, looks_like_proper_noun,
    normalize_lang, normalize_menu_arrows, phrasebook_lookup, sentence_lookup,
    split_batch, split_decorations, unescape_from_api,
)
from core.ocr import looks_like_translatable  # noqa: E402
from core.scripts import (  # noqa: E402
    has_any_script, has_letters, primary_script, script_of, scripts_present,
    target_scripts,
)

PASS, FAIL = [], []


def check(label: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label)
    mark = "✅" if condition else "❌"
    print(f"  {mark} {label}" + (f"  [{detail}]" if detail else ""))


def has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ==================================================================== 离线
def test_escape() -> None:
    print("\n=== 1) HTML 转义往返 ===")
    for original in ["A & B", "Cost < $10", "Value > 100", "Use <TAB> key",
                     "x < y && y > z", "AT&T Inc.", "plain text",
                     "Settings\nDownload failed"]:
        restored = unescape_from_api(escape_for_api(original))
        check(f"往返 {original!r}", restored == original, f"得到 {restored!r}")

    check("接口插入的 ASCII 标签被清掉",
          unescape_from_api("<b>加粗</b>") == "加粗",
          f"得到 {unescape_from_api('<b>加粗</b>')!r}")
    check("含中文的伪标签按原文保留",
          unescape_from_api("比较<b和c>d") == "比较<b和c>d",
          f"得到 {unescape_from_api('比较<b和c>d')!r}")
    check("正常的角括号文本不被误删",
          unescape_from_api("成本<10美元及研发预算") == "成本<10美元及研发预算",
          f"得到 {unescape_from_api('成本<10美元及研发预算')!r}")
    check("纯 ASCII 比较式不被误删",
          unescape_from_api("a < b and c > d") == "a < b and c > d",
          f"得到 {unescape_from_api('a < b and c > d')!r}")
    check("转义后不含裸尖括号", "<" not in escape_for_api("a < b"))


def test_phrasebook() -> None:
    print("\n=== 2) 术语表 ===")
    book = load_phrasebook()
    check("术语表已加载", len(book) > 100, f"{len(book)} 条")

    for text, expected in [("Copy", "复制"), ("Paste", "粘贴"), ("Cut", "剪切"),
                           ("Redo", "重做"), ("Access denied", "拒绝访问"),
                           ("Settings", "设置"), ("COPY", "复制"),
                           ("  Paste  ", "粘贴"), ("Save As", "另存为"),
                           ("File not found", "找不到文件")]:
        hit = phrasebook_lookup(text, book)
        check(f"{text!r} -> {expected!r}", hit == expected, f"得到 {hit!r}")

    print("\n  以下应当**不**走术语表（整句交给在线接口）：")
    for text in ["Are you sure you want to delete this file?",
                 "The installer was unable to write to the selected directory.",
                 "Download failed. Please try again later.",
                 "This is a fairly long sentence with many words in it",
                 ""]:
        hit = phrasebook_lookup(text, book)
        check(f"{text[:44]!r} 不查表", hit is None, f"得到 {hit!r}")


def test_normalize_lang() -> None:
    print("\n=== 3) 语言代码映射 ===")
    for source, expected in [("zh-CN", "zh-Hans"), ("zh-cn", "zh-Hans"),
                             ("zh", "zh-Hans"), ("zh-TW", "zh-Hant"),
                             ("en", "en"), ("ja", "ja"), ("", "zh-Hans"),
                             ("de", "de")]:
        got = normalize_lang(source)
        check(f"{source!r} -> {expected!r}", got == expected, f"得到 {got!r}")


def test_split_batch() -> None:
    print("\n=== 4) 批次拆分（供整段发送的接口使用） ===")
    SEP_CHAR = "\u241e"
    joined = SEP_CHAR.join(["甲", "乙", "丙"])
    check("分隔符切分", split_batch(joined, 3) == ["甲", "乙", "丙"],
          f"{split_batch(joined, 3)}")
    newline_joined = "\n".join(["甲", "乙", "丙"])
    check("换行退化切分", split_batch(newline_joined, 3) == ["甲", "乙", "丙"],
          f"{split_batch(newline_joined, 3)}")
    check("条数不足时补空", split_batch("甲", 3) == ["甲", "", ""],
          f"{split_batch('甲', 3)}")
    check("单条直接返回", split_batch("甲", 1) == ["甲"])


class _FakeProvider:
    """可控的假接口，用于验证降级链和缓存。"""

    def __init__(self, name, label, behavior):
        self.name = name
        self.label = label
        self.behavior = behavior
        self.calls = []

    def batch_translate(self, texts, target, source=""):
        self.calls.append(list(texts))
        return self.behavior(list(texts))


def test_multilingual() -> None:
    print("\n=== 4b) 多语言互译（文字体系判定 / 过滤 / 目标语言）===")

    # --- 文字体系判定
    check("script_of 汉字 = han", script_of("设") == "han", script_of("设"))
    check("script_of 平假名 = kana", script_of("こ") == "kana", script_of("こ"))
    check("script_of 片假名 = kana", script_of("カ") == "kana", script_of("カ"))
    check("script_of 谚文 = hangul", script_of("설") == "hangul", script_of("설"))
    check("script_of 西里尔 = cyrillic", script_of("Н") == "cyrillic", script_of("Н"))
    check("script_of 阿拉伯 = arabic", script_of("ا") == "arabic", script_of("ا"))
    check("script_of 泰文 = thai", script_of("ก") == "thai", script_of("ก"))
    check("script_of 天城文 = devanagari", script_of("स") == "devanagari", script_of("स"))
    check("script_of 希腊 = greek", script_of("Ρ") == "greek", script_of("Ρ"))
    check("script_of 拉丁 = latin", script_of("A") == "latin", script_of("A"))
    check("script_of 数字 = other", script_of("7") == "other", script_of("7"))

    check("主要文字：纯日语假名行", primary_script("こんにちは世界") == "kana",
          primary_script("こんにちは世界"))
    check("主要文字：混排里拉丁占多数",
          primary_script("Spring-thief的2025年度歌单") == "latin",
          primary_script("Spring-thief的2025年度歌单"))
    check("主要文字：纯中文", primary_script("设置") == "han")
    check("主要文字：没字母", primary_script("50%") == "other")

    check("目标 zh 的文字体系", target_scripts("zh-CN") == frozenset({"han"}),
          f"{target_scripts('zh-CN')}")
    check("目标 zh-TW 也算汉字", target_scripts("zh-TW") == frozenset({"han"}))
    check("目标 ja 含汉字与假名",
          target_scripts("ja") == frozenset({"han", "kana"}),
          f"{target_scripts('ja')}")
    check("目标 en 是拉丁", target_scripts("en") == frozenset({"latin"}))
    check("目标 ru 是西里尔", target_scripts("ru") == frozenset({"cyrillic"}))
    check("未收录语言不做过滤", target_scripts("xx") == frozenset(),
          f"{target_scripts('xx')}")

    check("scripts_present 能看出混排",
          scripts_present("导出项目为 PDF") == frozenset({"han", "latin"}),
          f"{scripts_present('导出项目为 PDF')}")
    check("has_any_script 中文译文里有汉字就通过",
          has_any_script("译<Settings>", frozenset({"han"})))
    check("has_any_script 纯英文译文没有汉字",
          not has_any_script("Settings", frozenset({"han"})))

    # --- 值不值得翻译（这是「只能英译中」的直接病根）
    check("日语假名要翻（旧判定会丢掉）",
          looks_like_translatable("こんにちは世界", target="zh-CN"))
    check("俄语要翻", looks_like_translatable("Настройки", target="zh-CN"))
    check("韩语要翻", looks_like_translatable("설정", target="zh-CN"))
    check("阿拉伯语要翻", looks_like_translatable("الإعدادات", target="zh-CN"))
    check("泰语要翻", looks_like_translatable("การตั้งค่า", target="zh-CN"))
    check("印地语要翻", looks_like_translatable("सेटिंग्स", target="zh-CN"))
    check("英语要翻", looks_like_translatable("Settings", target="zh-CN"))
    check("中文不翻（目标是中文）",
          not looks_like_translatable("设置", target="zh-CN"))
    check("中文也不翻（目标是中文、长句）",
          not looks_like_translatable("我喜欢的音乐", target="zh-CN"))
    check("日语不翻（目标是日语）",
          not looks_like_translatable("こんにちは世界", target="ja"))
    check("英语不翻（目标是英语）",
          not looks_like_translatable("Settings", target="en"))
    check("中文要翻（目标是英语）",
          looks_like_translatable("设置", target="en"))
    check("纯数字不翻", not looks_like_translatable("50%", target="zh-CN"))
    check("项目符号不翻", not looks_like_translatable("\u2460\u2461\u2462"))
    check("单个字母不翻（默认至少两个字母）",
          not looks_like_translatable("v", target="zh-CN"))
    check("路径要翻", looks_like_translatable(r"C:\Users\asus\a.txt", target="zh-CN"))

    # --- 语言码归一化
    check("normalize_lang zh-Hans", normalize_lang("zh-CN") == "zh-Hans")
    check("normalize_lang 新增日语码原样", normalize_lang("ja") == "ja")
    check("normalize_lang 新增泰语码原样", normalize_lang("th") == "th")
    check("normalize_lang 大写也能认", normalize_lang("ZH-CN") == "zh-Hans",
          normalize_lang("ZH-CN"))

    # --- 端到端：非拉丁原文真的被送到接口，不再被本地吞掉
    translator = Translator(use_glossary=False, keep_proper_nouns=False)
    sent: list = []

    def _wrap(text: str, target: str) -> str:
        # 假接口也要「翻成目标语言」，否则会被 _looks_untranslated 判成没翻
        code = (target or "").lower()
        if code.startswith("zh"):
            mark = "译"
        elif code.startswith("ja"):
            mark = "設"
        else:
            mark = "T"
        return f"{mark}[{text}]"

    class _Echo:
        name = "fake"
        label = "假接口"

        def batch_translate(self, texts, target, source=""):
            sent.append((list(texts), target, source))
            return [_wrap(text, target) for text in texts]

    translator.providers = {"fake": _Echo()}
    translator.order = ["fake"]

    texts = ["こんにちは世界", "Настройки", "설정", "50%", "   ", "Settings"]
    outcome = translator.translate(texts, "zh-CN", "auto")
    check("非拉丁文字确实发给了接口（不再本地吞掉）",
          len(sent) == 1 and sent[0][0] == ["こんにちは世界", "Настройки", "설정",
                                            "Settings"],
          f"{sent[0][0] if sent else None}")
    check("纯数字与空白没有发给接口",
          "50%" not in (sent[0][0] if sent else []) and "   " not in (sent[0][0] if sent else []))
    check("日语得到译文", outcome.translations[0] == "译[こんにちは世界]",
          outcome.translations[0])
    check("俄语得到译文", outcome.translations[1] == "译[Настройки]",
          outcome.translations[1])
    check("韩语得到译文", outcome.translations[2] == "译[설정]",
          outcome.translations[2])
    check("纯数字原样返回", outcome.translations[3] == "50%",
          outcome.translations[3])
    check("空白原样返回", outcome.translations[4] == "   ",
          repr(outcome.translations[4]))
    check("接口收到的目标语言正确", sent[0][1] == "zh-CN" if sent else False)

    # --- 目标是英语时，中文原文要送出去，英文原文要跳过
    translator2 = Translator(use_glossary=False, keep_proper_nouns=False)
    sent2: list = []

    class _Echo2:
        name = "fake2"
        label = "假接口2"

        def batch_translate(self, texts, target, source=""):
            sent2.append(list(texts))
            return [_wrap(text, target) for text in texts]

    translator2.providers = {"fake2": _Echo2()}
    translator2.order = ["fake2"]
    outcome2 = translator2.translate(["设置", "Settings"], "en")
    check("目标英语：中文被送出去", sent2 and sent2[0] == ["设置"],
          f"{sent2}")
    check("目标英语：英文原文没被送出去",
          "Settings" not in (sent2[0] if sent2 else []))
    check("目标英语：英文原文原样返回",
          outcome2.translations[1] == "Settings", outcome2.translations[1])
    check("目标英语：中文拿到了英文译文",
          outcome2.translations[0] == "T[设置]", outcome2.translations[0])

    # --- 译文校验推广到非中文目标
    translator3 = Translator(use_glossary=False, keep_proper_nouns=False)
    attempts: list = []

    class _Lazy:
        name = "lazy"
        label = "没翻的接口"

        def batch_translate(self, texts, target, source=""):
            attempts.append(target)
            # 假装「翻了」，其实原样退回（大小写变一下）
            return [text.upper() for text in texts]

    translator3.providers = {"lazy": _Lazy()}
    translator3.order = ["lazy"]
    bad = translator3.translate(["设置"], "en", "auto")
    check("目标是英语时，原样退回的译文被判失败",
          bad.translations == [""] and bad.error != "",
          f"{bad.translations} error={bad.error!r}")


    # --- 术语表是英译中的，目标不是中文时绝不能用它
    translator4 = Translator(use_glossary=True, keep_proper_nouns=False)
    sent4: list = []

    class _Echo4:
        name = "fake4"
        label = "假接口4"

        def batch_translate(self, texts, target, source=""):
            sent4.append(list(texts))
            return [_wrap(text, target) for text in texts]

    translator4.providers = {"fake4": _Echo4()}
    translator4.order = ["fake4"]
    out_ja = translator4.translate(["Settings"], "ja")
    check("目标是日语时术语表不生效（否则会返回中文「设置」）",
          sent4 and sent4[0] == ["Settings"], f"{sent4}")
    check("目标是日语时拿到的是接口译文，不是术语表的中文",
          out_ja.translations == ["設[Settings]"], f"{out_ja.translations}")
    check("目标是日语时不计入术语表命中", out_ja.from_phrasebook == 0,
          f"{out_ja.from_phrasebook}")

    sent4.clear()
    translator4.clear_cache()
    out_zh = translator4.translate(["Settings"], "zh-CN")
    check("目标是中文时术语表照常生效", out_zh.translations == ["设置"],
          f"{out_zh.translations}")
    check("目标是中文时根本没请求接口", sent4 == [], f"{sent4}")

    # --- OCR 识别语言包的映射（不真的加载模型，只看挑包对不对）
    from core.ocr import (  # noqa: PLC0415
        OcrEngine, PACK_LABELS, language_pack, pack_file_name, pack_is_downloaded,
    )

    check("源语言 ko 选韩语包", language_pack("ko") == ("KOREAN", "PPOCRV5"),
          f"{language_pack('ko')}")
    check("源语言 ko-KR 也认", language_pack("ko-KR") == ("KOREAN", "PPOCRV5"))
    check("源语言 ru 选西里尔包", language_pack("ru") == ("ESLAV", "PPOCRV5"))
    check("源语言 ar 选阿拉伯包", language_pack("ar") == ("ARABIC", "PPOCRV5"))
    check("源语言 th 选泰语包", language_pack("th") == ("TH", "PPOCRV5"))
    check("源语言 hi 选天城文包", language_pack("hi") == ("DEVANAGARI", "PPOCRV5"))
    check("源语言 el 选希腊包", language_pack("el") == ("EL", "PPOCRV5"))
    check("自动识别不选包（用内置模型）", language_pack("auto") is None)
    check("空串不选包", language_pack("") is None)
    check("拉丁语言不选包（内置模型认拉丁）", language_pack("de") is None,
          f"{language_pack('de')}")
    check("日语不选包（内置模型认假名）", language_pack("ja") is None)
    check("中文不选包", language_pack("zh-CN") is None)
    check("包文件名拼写正确",
          pack_file_name(("KOREAN", "PPOCRV5")) == "korean_PP-OCRv5_rec_mobile.onnx",
          pack_file_name(("KOREAN", "PPOCRV5")))
    check("每个包都有中文名", all(name in PACK_LABELS for name, _ in
                                  __import__("core.ocr", fromlist=["LANGUAGE_PACKS"])
                                  .LANGUAGE_PACKS.values()), "")

    ko_pack = language_pack("ko")
    assert ko_pack is not None
    if pack_is_downloaded(ko_pack):
        check("已下载的韩语包在没有网络时也能用",
              OcrEngine(source_lang="ko", allow_download=False).pack == ko_pack)
    else:
        check("没下载的韩语包在禁止下载时会被拒绝",
              OcrEngine(source_lang="ko", allow_download=False).pack is None)
    check("状态栏文案：韩语", "韩语" in OcrEngine(source_lang="ko").pack_note,
          OcrEngine(source_lang="ko").pack_note)
    check("状态栏文案：自动识别用内置模型",
          "内置" in OcrEngine(source_lang="auto").pack_note,
          OcrEngine(source_lang="auto").pack_note)

    # --- 日语源语言：纯汉字行必须能过筛（否则「設定 / 編集 / 表示」全被当中文丢掉）
    from core.translate import _already_target_language  # noqa: PLC0415

    for word in ("設定", "編集", "表示", "終了", "保存"):
        check(f"源语言=日语时「{word}」要翻",
              looks_like_translatable(word, target="zh-CN", source="ja"), word)
        check(f"源语言=日语时「{word}」不算已是中文",
              not _already_target_language(word, "zh-CN", "ja"), word)
    check("源语言=自动时「設定」仍当已经是中文跳过（不误翻中文界面）",
          not looks_like_translatable("設定", target="zh-CN", source="auto"), "")
    check("源语言=自动时中文句子仍然跳过",
          not looks_like_translatable("我喜欢的音乐", target="zh-CN", source="auto"), "")
    check("源语言=日语、目标是日语时「設定」算已经是日语，不重复翻",
          _already_target_language("設定", "ja", "ja"), "")
    check("源语言与目标语言相同时不启用「指定源语言」豁免",
          not looks_like_translatable("設定", target="ja", source="ja"), "")
    check("源语言=日语时纯数字仍不翻",
          not looks_like_translatable("50%", target="zh-CN", source="ja"), "")


def test_fallback_and_cache() -> None:
    print("\n=== 5) 降级链与缓存 ===")
    translator = Translator(use_glossary=False)

    def broken(texts):
        raise ProviderError("模拟接口故障")

    def working(texts):
        return [f"译<{text}>" for text in texts]

    translator.providers = {
        "edge": _FakeProvider("edge", "假主接口", broken),
        "mymemory": _FakeProvider("mymemory", "假备用接口", working),
    }
    translator.order = ["edge", "mymemory"]

    outcome = translator.translate(["Settings", "Download failed"], "zh-CN")
    check("主接口失败后切到备用", outcome.provider == "假备用接口",
          outcome.provider)
    check("结果正确", outcome.translations == ["译<Settings>", "译<Download failed>"],
          f"{outcome.translations}")
    check("记录了尝试顺序", outcome.attempted == ["edge", "mymemory"],
          f"{outcome.attempted}")

    edge_calls_before = len(translator.providers["edge"].calls)
    outcome2 = translator.translate(["Settings"], "zh-CN")
    check("第二次命中缓存", outcome2.cached and outcome2.provider == "cache",
          f"cached={outcome2.cached} provider={outcome2.provider}")
    check("缓存命中不再请求接口",
          len(translator.providers["edge"].calls) == edge_calls_before)

    translator.clear_cache()
    check("清空缓存后重新请求", translator.cache_size() == 0)


def test_empty_and_blank() -> None:
    print("\n=== 6) 空串与空白不浪费配额 ===")
    translator = Translator(use_glossary=False)
    calls = []

    def record(texts):
        calls.append(list(texts))
        return [f"译<{text}>" for text in texts]

    translator.providers = {"edge": _FakeProvider("edge", "假", record)}
    translator.order = ["edge"]
    outcome = translator.translate(["", "   ", "Settings", ""], "zh-CN")
    check("空串原样返回",
          outcome.translations == ["", "   ", "译<Settings>", ""],
          f"{outcome.translations}")
    check("接口只收到非空文本", calls == [["Settings"]], f"{calls}")


def test_phrasebook_skips_api() -> None:
    print("\n=== 7) 术语表命中时不应调用接口 ===")
    translator = Translator(use_glossary=True)
    calls = []

    def record(texts):
        calls.append(list(texts))
        return [f"在线<{text}>" for text in texts]

    translator.providers = {"edge": _FakeProvider("edge", "假", record)}
    translator.order = ["edge"]
    # 这条句子必须**不**在整句对照表里，否则打不到接口。
    online_only = "The selected printer could not be reached from this network."
    outcome = translator.translate(["Copy", "Paste", online_only], "zh-CN")
    check("Copy/Paste 走术语表",
          outcome.translations[0] == "复制" and outcome.translations[1] == "粘贴",
          f"{outcome.translations}")
    check("整句走在线", outcome.translations[2] == f"在线<{online_only}>",
          f"{outcome.translations[2]!r}")
    check("接口只收到未命中术语表的那条",
          calls == [[online_only]], f"{calls}")
    check("统计了术语表命中数", outcome.from_phrasebook == 2,
          f"from_phrasebook={outcome.from_phrasebook}")


def test_oversized_guard() -> None:
    print("\n=== 8) 超长文本不会被截断后错位缓存 ===")
    translator = Translator(use_glossary=False)
    received = []

    def record(texts):
        received.extend(texts)
        return [f"译<{len(text)}>" for text in texts]

    translator.providers = {"edge": _FakeProvider("edge", "假", record)}
    translator.order = ["edge"]
    long_text = "x" * 50000
    normal = "Bluetooth"  # 不在术语表里，确保一定会走到接口
    outcome = translator.translate([long_text, normal], "zh-CN")
    check("超长文本被跳过而不是截断", long_text not in received,
          f"接口收到 {len(received)} 条，最长 {max((len(t) for t in received), default=0)}")
    check("正常文本仍被翻译", outcome.translations[1] == f"译<{len(normal)}>",
          f"{outcome.translations[1]!r}")
    check("超长文本有明确提示", "未能翻译" in outcome.error or "长度上限" in outcome.error,
          f"error={outcome.error!r}")


def test_chunking() -> None:
    print("\n=== 9) 大批量自动分批 ===")
    translator = Translator(use_glossary=False)
    batch_sizes = []

    def record(texts):
        batch_sizes.append(len(texts))
        return [f"译{i}" for i in range(len(texts))]

    translator.providers = {"edge": _FakeProvider("edge", "假", record)}
    translator.order = ["edge"]
    many = [f"item number {i}" for i in range(150)]
    outcome = translator.translate(many, "zh-CN")
    check("全部有结果", all(outcome.translations), f"{len(outcome.translations)} 条")
    check(f"每批不超过 {MAX_ITEMS_PER_REQUEST} 条",
          all(size <= MAX_ITEMS_PER_REQUEST for size in batch_sizes),
          f"批大小 {batch_sizes}")
    check("总条数正确", sum(batch_sizes) == 150, f"共 {sum(batch_sizes)}")


# ==================================================================== 在线
def test_llm_provider() -> None:
    """大模型接口：没 key 跳过、端点拼装、JSON 解析、条数校验、降级。"""
    from core.translate import LLMProvider, ProviderError

    provider = LLMProvider(5, {})
    check("没填 key 时 ready 为 False", provider.ready is False)
    check("默认端点指向 DeepSeek",
          provider._endpoint() == "https://api.deepseek.com/v1/chat/completions",
          provider._endpoint())
    check("自定义端点去掉尾部斜杠",
          LLMProvider(5, {"base_url": "http://localhost:11434/v1/"})._endpoint()
          == "http://localhost:11434/v1/chat/completions")
    check("已经写到 chat/completions 的端点不重复拼",
          LLMProvider(5, {"base_url": "https://x/v1/chat/completions"}
                      )._endpoint() == "https://x/v1/chat/completions")

    fence = "`" * 3
    check("能剥掉 markdown 代码块围栏",
          provider._parse_array(f"{fence}json\n[\"a\",\"b\"]\n{fence}") == ["a", "b"])
    check("能在前后废话里抠出数组",
          provider._parse_array('好的：[ "x", "y" ] 完毕') == ["x", "y"])
    for bad in ("没有数组", "[\"a\","):
        try:
            provider._parse_array(bad)
            check(f"非数组回复应抛错（{bad!r}）", False)
        except ProviderError:
            check(f"非数组回复应抛错（{bad!r}）", True)

    try:
        provider.batch_translate(["Settings"], "zh-CN", "en")
        check("没 key 时调用应抛 ProviderError", False)
    except ProviderError as exc:
        check("没 key 时调用应抛 ProviderError", "未配置" in str(exc), str(exc))

    # 没有 key 的大模型接口必须被自动跳过，不能占失败名额
    translator = Translator(["llm", "edge"], timeout=5)
    calls = []
    translator.providers = {
        "llm": LLMProvider(5, {}),
        "edge": _FakeProvider("edge", "假接口", lambda ts: [f"译<{t}>" for t in ts]),
    }
    translator.order = ["llm", "edge"]
    outcome = translator.translate(["Zebra"], "zh-CN", "en")
    check("没 key 的大模型接口被跳过",
          outcome.attempted == ["edge"] and outcome.provider == "假接口",
          f"attempted={outcome.attempted} provider={outcome.provider}")
    check("跳过时仍拿到译文", outcome.translations == ["译<Zebra>"],
          str(outcome.translations))
    del calls

    # 填了 key 之后必须排在最前面被使用
    translator2 = Translator(["edge"], timeout=5)
    captured = {}

    class _FakeLLM(LLMProvider):
        def batch_translate(self, texts, target, source=""):
            captured["texts"] = list(texts)
            captured["target"] = target
            return [f"大模型<{t}>" for t in texts]

    translator2.providers = {
        "edge": _FakeProvider("edge", "假接口", lambda ts: [f"译<{t}>" for t in ts]),
        "llm": _FakeLLM(5, {"api_key": "sk-test", "model": "deepseek-chat"}),
    }
    translator2.order = ["llm", "edge"]
    outcome2 = translator2.translate(["Zebra"], "zh-CN", "en")
    check("填了 key 后大模型接口被优先使用",
          outcome2.provider == "大模型接口（质量最好，需 API key）",
          outcome2.provider)
    check("大模型接口收到的是原始文本", captured.get("texts") == ["Zebra"],
          str(captured.get("texts")))
    check("大模型接口拿到目标语言", captured.get("target") == "zh-CN",
          str(captured.get("target")))
    check("llm_ready() 反映 key 状态",
          Translator(["llm"], timeout=5).llm_ready() is False, "")


def test_new_providers() -> None:
    """火山 / 必应中文站：合并拆分守卫、源语言写法、token 过期重试、降级。"""
    print("\n=== 10b) 火山与必应中文站 ===")
    from core.translate import (
        DEFAULT_PROVIDER_ORDER, SEP, BingCNProvider, VolcengineProvider,
        _base_lang, _split_merged_strict,
    )

    class _FakeResponse:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload
            if text:
                self.text = text
            elif payload is not None:
                import json
                self.text = json.dumps(payload, ensure_ascii=False)
            else:
                self.text = ""

        def json(self):
            if self._payload is None:
                raise ValueError("不是 JSON")
            return self._payload

    class _FakeSession:
        """只记账、不联网的 requests.Session 替身。"""

        def __init__(self, handler=None):
            self.handler = handler
            self.calls = []
            self.headers = {}

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            return self.handler("GET", url, kwargs)

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return self.handler("POST", url, kwargs)

    def _raises(fn) -> str:
        try:
            fn()
            return ""
        except ProviderError as exc:
            return str(exc)

    check("默认降级链：必应打头、火山替补、不再含有道",
          DEFAULT_PROVIDER_ORDER == ["bing", "volc", "edge", "mymemory"],
          str(DEFAULT_PROVIDER_ORDER))
    check("_base_lang 取主语言",
          _base_lang("zh-CN") == "zh" and _base_lang("ja") == "ja")
    check("火山目标语言：简体 zh、繁体 zh-Hant、日语 ja",
          VolcengineProvider._target("zh-CN") == "zh"
          and VolcengineProvider._target("zh-TW") == "zh-Hant"
          and VolcengineProvider._target("ja") == "ja",
          f"{VolcengineProvider._target('zh-CN')}/"
          f"{VolcengineProvider._target('zh-TW')}")

    # --- 合并拆分守卫：静默丢一行会让整屏串行错位，必须拆不出来就报错
    check("控制符在：按 \\u241e 拆回",
          _split_merged_strict("复制\u241e粘贴\u241e设置", 3, "x")
          == ["复制", "粘贴", "设置"])
    check("控制符被吃掉：改按行拆回（火山实况）",
          _split_merged_strict("复制\n\n粘贴\n\n设置", 3, "x")
          == ["复制", "粘贴", "设置"])
    check("条数对不上必须抛错，不能猜",
          "拆出" in _raises(lambda: _split_merged_strict("复制\n粘贴", 3, "火山"))
          or "对不上" in _raises(lambda: _split_merged_strict("复制\n粘贴", 3, "火山")),
          _raises(lambda: _split_merged_strict("复制\n粘贴", 3, "火山")))
    check("单条直接返回", _split_merged_strict(" 复制 ", 1, "x") == ["复制"])

    # --- 火山：一屏合并成一条，source_language 必须发空串
    volc = VolcengineProvider(5)
    captured: dict = {}

    def _volc_handler(method, url, kwargs):
        captured.update(kwargs.get("json") or {})
        # 模拟火山把 \u241e 吃掉、只留换行
        return _FakeResponse(200, {"translation": "复制\n\n粘贴\n\n设置"})

    volc._session = _FakeSession(_volc_handler)
    out = volc.batch_translate(["Copy", "Paste", "Settings"], "zh-CN", "")
    check("火山：一屏合并成一条请求并拆回", out == ["复制", "粘贴", "设置"], str(out))
    check("火山：自动识别发空串（写 auto 会音译成埃因斯特伦根）",
          captured.get("source_language") == "",
          repr(captured.get("source_language")))
    check("火山：目标语言简体是 zh", captured.get("target_language") == "zh",
          repr(captured.get("target_language")))
    check("火山：三条用 SEP 合并成一条 text",
          captured.get("text", "").count("\u241e") == 2,
          repr(captured.get("text")))

    volc2 = VolcengineProvider(5)
    got2: dict = {}
    volc2._session = _FakeSession(
        lambda m, u, k: (got2.update(k.get("json") or {}),
                         _FakeResponse(200, {"translation": "设置"}))[1])
    volc2.batch_translate(["Settings"], "zh-CN", "en")
    check("火山：指定源语言时照发", got2.get("source_language") == "en",
          repr(got2.get("source_language")))

    volc3 = VolcengineProvider(5)
    volc3._session = _FakeSession(
        lambda m, u, k: (_ for _ in ()).throw(AssertionError("不该发请求")))
    check("火山：全是空白时本地直接返回，不白跑一次请求",
          volc3.batch_translate(["", "   "], "zh-CN", "") == ["", ""])

    # --- 必应：token 抓取 / 结构变化报错 / 过期重试
    bing = BingCNProvider(5)
    bing._session = _FakeSession(
        lambda m, u, k: _FakeResponse(200, None, "<html>没有 token</html>"))
    check("必应：页面结构变了要明确报错",
          "挖不到" in _raises(bing._ensure_token), _raises(bing._ensure_token))

    page = ('<html>IG:"ABC123" data-iid="translator.5023" '
            'params_AbusePreventionHelper = [12345,"TOKEN-abc",3600000];</html>')
    bing2 = BingCNProvider(5)
    bing2._session = _FakeSession(lambda m, u, k: _FakeResponse(200, None, page))
    bing2._ensure_token()
    check("必应：从翻译页挖到 IG / IID / key / token",
          (bing2._ig, bing2._iid, bing2._key, bing2._token)
          == ("ABC123", "translator.5023", "12345", "TOKEN-abc"),
          f"{bing2._ig}/{bing2._iid}/{bing2._key}/{bing2._token}")
    check("必应：token 有效期按 ttl 算（1 小时，留 60 秒余量）",
          bing2._expires_at > time.time() + 3000)

    bing3 = BingCNProvider(5)
    sent: list = []
    bing3._post = lambda text, src, target: (
        sent.append((text, src, target)),
        _FakeResponse(200, [{"translations": [{"text": "复制\u241e粘贴"}]}]))[1]
    out3 = bing3.batch_translate(["Copy", "Paste"], "zh-CN", "")
    check("必应：合并成一条发、按分隔符拆回", out3 == ["复制", "粘贴"], str(out3))
    check("必应：自动识别写 auto-detect（空串会 400）",
          sent and sent[0][1] == "auto-detect", str(sent[0][1] if sent else None))
    check("必应：目标语言走 normalize_lang（zh-Hans）",
          sent and sent[0][2] == "zh-Hans", str(sent[0][2] if sent else None))

    bing4 = BingCNProvider(5)
    tries = {"n": 0, "force": []}

    def _post4(text, src, target):
        tries["n"] += 1
        if tries["n"] == 1:
            return _FakeResponse(200, {"statusCode": 205, "errorMessage": ""})
        return _FakeResponse(200, [{"translations": [{"text": "设置"}]}])

    bing4._post = _post4
    bing4._ensure_token = lambda force=False: tries["force"].append(force)
    out4 = bing4.batch_translate(["Settings"], "zh-CN", "en")
    check("必应：token 失效（205）时强制重拿再试一次",
          out4 == ["设置"] and tries["n"] == 2 and tries["force"] == [True],
          f"n={tries['n']} force={tries['force']}")

    bing5 = BingCNProvider(5)
    bing5._session = _FakeSession(
        lambda m, u, k: (_ for _ in ()).throw(AssertionError("不该发请求")))
    check("必应：全是空白时不发请求（空串会 400）",
          bing5.batch_translate(["", "  "], "zh-CN", "") == ["", ""])

    # --- 降级链：火山挂了自动换必应
    translator = Translator(["volc", "bing"], timeout=5)
    translator.use_glossary = False
    translator.keep_proper_nouns = False
    translator.protect_tokens = False

    def _boom(texts):
        raise ProviderError("模拟火山挂了")

    translator.providers = {
        "volc": _FakeProvider("volc", "火山翻译接口", _boom),
        "bing": _FakeProvider("bing", "必应翻译（中文站）",
                              lambda ts: [f"必应<{x}>" for x in ts]),
    }
    translator.order = ["volc", "bing"]
    outcome = translator.translate(["Zebra"], "zh-CN", "en")
    check("火山挂掉时自动换必应", outcome.translations == ["必应<Zebra>"],
          str(outcome.translations))
    check("换家时记录了尝试过的接口", outcome.attempted == ["volc", "bing"],
          str(outcome.attempted))


def test_calibration() -> None:
    """翻译校准：装饰拆分、菜单箭头、专名保留、术语表剥壳。"""
    print("\n=== 10) 翻译校准（装饰拆分 / 专名 / 剥壳）===")
    cases = [
        ("Open Recent...", ("", "Open Recent", "...")),
        ("Copy (Ctrl+C)", ("", "Copy", " (Ctrl+C)")),
        ("Zoom In\tCtrl++", ("", "Zoom In", "\tCtrl++")),
        ("\u2022 Paste", ("\u2022 ", "Paste", "")),
        ("Save As...", ("", "Save As", "...")),
        ("Settings", ("", "Settings", "")),
        # 整句不该被拆 —— 普通括号说明留在核心里去翻译
        ("Something (optional)", ("", "Something (optional)", "")),
        ("Note:", ("", "Note:", "")),
    ]
    for text, expected in cases:
        got = split_decorations(text)
        check(f"拆分 {text!r}", got == expected, f"{got}")

    check("菜单箭头补空格",
          normalize_menu_arrows("File > Save", "文件>保存") == "文件 > 保存")
    check("菜单箭头不重复补",
          normalize_menu_arrows("File > Save", "文件 > 保存") == "文件 > 保存")
    check("源文没有箭头就不动",
          normalize_menu_arrows("Level 3", "x>y") == "x>y")

    for text in ("GitHub", "PDF", "C++", "onnxruntime", "DirectML",
                 "v1.2.3", "setup.exe", "win11"):
        check(f"专名保留 {text!r}", looks_like_proper_noun(text), text)
    for text in ("Open in GitHub", "Are you sure?", "Download failed",
                 "The installer was unable to write to the selected directory."):
        check(f"不是专名 {text!r}", not looks_like_proper_noun(text), text)

    book = load_phrasebook()
    check("术语表足够大", len(book) >= 900, f"{len(book)} 条")
    for text, expected in (
        ("Save As...", "另存为"),
        ("Copy (Ctrl+C)", "复制"),
        ("Settings\tCtrl+,", "设置"),
        ("Undo [Ctrl+Z]", "撤销"),
        ("\u2022 Paste", "粘贴"),
        ("Access denied", "拒绝访问"),
        ("Open Recent...", "最近打开"),
    ):
        check(f"剥壳查表 {text!r}", phrasebook_lookup(text, book) == expected,
              f"{phrasebook_lookup(text, book)!r}")

    print("\n  高频整句对照表（接口会把 before closing 翻成「交房」）：")
    for text, expected in (
        ("Do you want to save your changes before closing?", "关闭前要保存你的更改吗？"),
        ("Are you sure you want to delete this file?", "你确定要删除这个文件吗？"),
        ("are you sure you want to delete this file?", "你确定要删除这个文件吗？"),
        ("Are you sure you want to delete this file?  ", "你确定要删除这个文件吗？"),
        ("Access is denied.", "拒绝访问。"),
        ("Not responding", "未响应"),
    ):
        got = sentence_lookup(text)
        check(f"整句对照 {text[:34]!r}", got == expected, f"{got!r}")
    for text in ("Are you sure you want to delete these two files?",
                 "Do you want to save your changes before quitting?",
                 "Download failed",
                 "This is a sentence that is not in the table."):
        got = sentence_lookup(text)
        check(f"不误伤 {text[:34]!r}", got is None, f"{got!r}")

    translator = Translator(use_glossary=True, keep_proper_nouns=True)
    translator.providers = {
        "edge": _FakeProvider("edge", "假接口",
                              lambda texts: [f"译<{t}>" for t in texts]),
    }
    translator.order = ["edge"]
    outcome = translator.translate(
        ["Do you want to save your changes before closing?"], "zh-CN")
    check("整句走本地对照表、不打接口",
          outcome.translations == ["关闭前要保存你的更改吗？"]
          and translator.providers["edge"].calls == [],
          f"{outcome.translations} calls={translator.providers['edge'].calls}")
    check("整句命中计入 phrasebook 统计", outcome.from_phrasebook == 1,
          str(outcome.from_phrasebook))


def test_placeholder_protection() -> None:
    """路径 / git 引用 / 变量名 / 版本号必须先挖成占位符，翻完再原样放回。"""
    print("\n=== 12) 占位符保护（路径、git 引用不被翻译）===")
    from core.translate import (mask_protected, restore_protected,  # noqa: E402
                                strip_placeholders)

    masked, tokens = mask_protected(r"Cannot open C:\Users\asus\a.txt")
    check("Windows 路径被挖出",
          tokens == [r"C:\Users\asus\a.txt"] and masked == "Cannot open {{0}}",
          f"{masked!r} {tokens}")
    check("占位符能换回原文",
          restore_protected("无法打开 {{0}}", tokens) == r"无法打开 C:\Users\asus\a.txt",
          f"{restore_protected('无法打开 {{0}}', tokens)!r}")
    check("占位符丢失时返回 None",
          restore_protected("无法打开", tokens) is None,
          f"{restore_protected('无法打开', tokens)!r}")

    check("git 引用被挖出",
          mask_protected("Branch not found: origin/main")[1] == ["origin/main"],
          f"{mask_protected('Branch not found: origin/main')}")
    check("版本号被挖出",
          mask_protected("Update to v1.2.3 failed")[1] == ["v1.2.3"],
          f"{mask_protected('Update to v1.2.3 failed')}")
    check("变量名被挖出",
          mask_protected("Field user_id is required")[1] == ["user_id"],
          f"{mask_protected('Field user_id is required')}")
    url_tokens = mask_protected("Visit https://example.com/help for details")[1]
    check("URL 被挖出",
          url_tokens and url_tokens[0].startswith("https://example.com"),
          f"{url_tokens}")
    check("普通句子不动",
          mask_protected("File not found")[1] == [],
          f"{mask_protected('File not found')}")
    check("残留占位符能被清掉",
          strip_placeholders("未找到分支 {{0}}") == "未找到分支 ",
          f"{strip_placeholders('未找到分支 {{0}}')!r}")

    translator = Translator(use_glossary=False, keep_proper_nouns=False)
    translator.providers = {
        "edge": _FakeProvider(
            "edge", "假接口",
            lambda texts: [t.replace("Branch not found", "未找到分支") for t in texts]),
    }
    translator.order = ["edge"]
    outcome = translator.translate(["Branch not found: origin/main"], "zh-CN")
    check("接口收到的是占位符版本",
          translator.providers["edge"].calls == [["Branch not found: {{0}}"]],
          f"{translator.providers['edge'].calls}")
    check("译文里的占位符被换回 git 引用",
          outcome.translations == ["未找到分支: origin/main"],
          f"{outcome.translations}")

    print("\n  接口把占位符吃掉了必须退回重发，且不能把 {{0}} 泄漏到界面：")
    translator2 = Translator(use_glossary=False, keep_proper_nouns=False)
    translator2.providers = {
        "edge": _FakeProvider("edge", "吃占位符的假接口",
                              lambda texts: ["译" + strip_placeholders(t)
                                             for t in texts]),
    }
    translator2.order = ["edge"]
    outcome2 = translator2.translate(["Branch not found: origin/main"], "zh-CN")
    check("占位符丢失时用不带占位符的版本重发",
          len(translator2.providers["edge"].calls) == 2,
          f"{translator2.providers['edge'].calls}")
    check("界面里不会出现占位符残留",
          all("{{" not in t for t in outcome2.translations),
          f"{outcome2.translations}")

    translator3 = Translator(use_glossary=False, keep_proper_nouns=False)
    translator3.protect_tokens = False
    translator3.providers = {
        "edge": _FakeProvider("edge", "假接口", lambda texts: [f"译<{t}>" for t in texts]),
    }
    translator3.order = ["edge"]
    translator3.translate(["Branch not found: origin/main"], "zh-CN")
    check("protect_tokens=False 时不做保护",
          translator3.providers["edge"].calls == [["Branch not found: origin/main"]],
          f"{translator3.providers['edge'].calls}")


def test_decorations_via_fake_provider() -> None:
    """装饰必须本地拼回：接口只该收到核心文本，译文要带原装饰。"""
    print("\n=== 11) 装饰拆分后接口只收到核心 ===")
    translator = Translator(use_glossary=False, keep_proper_nouns=False)
    translator.providers = {
        "edge": _FakeProvider("edge", "假接口",
                              lambda texts: [f"译<{t}>" for t in texts]),
    }
    translator.order = ["edge"]
    texts = ["Zoom In\tCtrl++", "Open Recent...", "Zoom In\tCtrl++"]
    outcome = translator.translate(texts, "zh-CN")
    check("装饰被拼回原文",
          outcome.translations[0] == "译<Zoom In>\tCtrl++",
          f"{outcome.translations[0]!r}")
    check("省略号被拼回",
          outcome.translations[1] == "译<Open Recent>...",
          f"{outcome.translations[1]!r}")
    check("重复行共享一次请求",
          outcome.translations[2] == outcome.translations[0],
          f"{outcome.translations[2]!r}")
    sent = translator.providers["edge"].calls
    check("接口只收到核心且去重", sent == [["Zoom In", "Open Recent"]], f"{sent}")

    print("\n  原样退回 / 空译文必须触发降级，不能算成功：")
    translator2 = Translator(use_glossary=False, keep_proper_nouns=False)
    translator2.providers = {
        "edge": _FakeProvider("edge", "原样退回的假接口",
                              lambda texts: list(texts)),
        "mymemory": _FakeProvider("mymemory", "正常假接口",
                                  lambda texts: [f"译<{t}>" for t in texts]),
    }
    translator2.order = ["edge", "mymemory"]
    outcome2 = translator2.translate(["Open Recent", "Loading"], "zh-CN")
    check("原样退回被判失败并换下一家",
          outcome2.provider == "正常假接口" and
          outcome2.translations == ["译<Open Recent>", "译<Loading>"],
          f"provider={outcome2.provider} {outcome2.translations}")


def test_live_quality() -> None:
    print("\n=== 10) 在线质量：真实调用微软接口 ===")
    provider = MicrosoftEdgeProvider(timeout=20)
    cases = [
        ("Are you sure you want to delete this file?", "删除"),
        ("The installer was unable to write to the selected directory.", "目录"),
        ("Network connection is not available", "网络"),
        ("File already exists. Do you want to replace it?", "文件"),
        ("I accept the terms in the License Agreement", "协议"),
        ("Not enough disk space", "磁盘"),
    ]
    try:
        results = provider.batch_translate([c[0] for c in cases], "zh-CN")
    except Exception as exc:
        check(f"在线调用成功", False, f"{type(exc).__name__}: {exc}")
        return
    check("在线调用成功", True)
    for (source, keyword), translated in zip(cases, results):
        ok = has_chinese(translated) and keyword in translated
        check(f"{source[:38]!r} 含关键词 {keyword!r}", ok, f"得到 {translated!r}")


def test_live_multiline() -> None:
    print("\n=== 11) 在线：多行文本按行保留 ===")
    provider = MicrosoftEdgeProvider(timeout=20)
    block = "Settings\nDownload failed\nFont size"
    try:
        results = provider.batch_translate([block], "zh-CN")
    except Exception as exc:
        check("多行调用成功", False, str(exc))
        return
    lines_in = block.splitlines()
    lines_out = results[0].splitlines()
    check("行数一致", len(lines_in) == len(lines_out),
          f"{len(lines_in)} vs {len(lines_out)}")
    check("每行都含中文", all(has_chinese(line) for line in lines_out),
          f"{results[0]!r}")
    print(f"     原文 = {block!r}")
    print(f"     译文 = {results[0]!r}")


def test_live_translator_end_to_end() -> None:
    print("\n=== 12) 端到端：Translator 真实翻译一屏文本 ===")
    translator = Translator(timeout=20, use_glossary=True)
    blocks = [
        "Settings",                                     # 术语表
        "Download failed",                              # 术语表
        "Copy",                                         # 术语表
        "Are you sure you want to delete this file?",   # 在线
        "The installer was unable to write to the selected directory.",  # 在线
        "Cost < $10 and R&D budget",                    # HTML 陷阱 + 在线
    ]
    started = time.perf_counter()
    outcome = translator.translate(blocks, "zh-CN")
    elapsed = (time.perf_counter() - started) * 1000
    check("没有报错", not outcome.error, outcome.error)
    for source, translated in zip(blocks, outcome.translations):
        print(f"     {source!r:<56} -> {translated!r}")
    check("全部有译文", all(outcome.translations), f"{outcome.translations}")
    check("HTML 陷阱未丢字符",
          len(outcome.translations[5]) >= 5,
          f"{outcome.translations[5]!r}")
    print(f"     接口={outcome.provider} 耗时={elapsed:.0f}ms "
          f"术语表命中={outcome.from_phrasebook}")

    print("\n  第二次相同请求应全部命中缓存：")
    started = time.perf_counter()
    outcome2 = translator.translate(blocks, "zh-CN")
    elapsed2 = (time.perf_counter() - started) * 1000
    check("第二次走缓存", outcome2.cached, f"provider={outcome2.provider}")
    check("缓存极快", elapsed2 < 50, f"{elapsed2:.1f}ms")


def test_live_probe() -> None:
    print("\n=== 13) 接口探测报告 ===")
    translator = Translator(timeout=20)
    for row in translator.probe():
        mark = "✅" if row["ok"] else "❌"
        print(f"  {mark} {row['label']:<22} {row['ms']:>5}ms  "
              f"{row['sample'] or row['error']}")


if __name__ == "__main__":
    offline_only = "offline" in sys.argv
    test_escape()
    test_phrasebook()
    test_normalize_lang()
    test_multilingual()
    test_split_batch()
    test_fallback_and_cache()
    test_empty_and_blank()
    test_phrasebook_skips_api()
    test_oversized_guard()
    test_chunking()
    test_llm_provider()
    test_new_providers()
    test_calibration()
    test_placeholder_protection()
    test_decorations_via_fake_provider()

    if not offline_only:
        test_live_quality()
        test_live_multiline()
        test_live_translator_end_to_end()
        test_live_probe()

    print(f"\n{'=' * 60}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for label in FAIL:
            print(f"  - {label}")
    sys.exit(1 if FAIL else 0)
