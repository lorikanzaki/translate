"""词典功能测试：释义解析、考试标签、上下文排序、变体原形、生词本、导出。

分两部分：
  不需要词库的（解析 / 排序 / 生词本 / 导出）  -> 一定跑
  需要真实词库的（查词 / 覆盖率）              -> 词库没建好就跳过并提示

用法：.venv\\Scripts\\python.exe tools/test_dict.py
      .venv\\Scripts\\python.exe tools/test_dict.py offline   # 只跑不依赖词库的
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dictdb  # noqa: E402
from core.dictdb import (  # noqa: E402
    Dictionary, DictEntry, Sense, _variant_stub_base, guess_pos, library_status,
    parse_translation, sense_score, token_at, tokens,
)
from core.vocab import VocabBook  # noqa: E402
from ui import dicthtml  # noqa: E402

PASS, FAIL = [], []


def check(label: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label)
    mark = "✅" if condition else "❌"
    print(f"  {mark} {label}" + (f"  [{detail}]" if detail else ""))


def skip(label: str, why: str) -> None:
    print(f"  ⏭️  {label}  [{why}]")


# ==================================================================== 离线
def test_parse_translation() -> None:
    print("\n=== 1) 释义解析（ECDICT 的 translation 列） ===")
    senses = parse_translation("n. 罩；风帽；（布质）面罩\\nv. 覆盖；用头巾包")
    check("按字面量 \\n 分行", len(senses) == 2, f"得到 {len(senses)} 行")
    check("第一行剥出词性 n", senses[0].pos == "n", f"得到 {senses[0].pos!r}")
    check("第一行正文正确", senses[0].text == "罩；风帽；（布质）面罩",
          f"得到 {senses[0].text!r}")
    check("第二行词性是 v", senses[1].pos == "v", f"得到 {senses[1].pos!r}")
    check("词性转中文", senses[0].pos_label == "名词", f"得到 {senses[0].pos_label!r}")

    network = parse_translation("[网络] 胡德；兜帽")
    check("方括号领域被剥出来", network[0].domain == "网络",
          f"得到 {network[0].domain!r}")
    check("领域转中文", network[0].domain_label == "网络",
          f"得到 {network[0].domain_label!r}")
    check("领域行正文", network[0].text == "胡德；兜帽",
          f"得到 {network[0].text!r}")

    check("真换行也认",
          len(parse_translation("n. 甲\nv. 乙")) == 2)
    check("空串得到空列表", parse_translation("") == [])
    check("没有词性前缀时整行当正文",
          parse_translation("直接是一句话")[0].pos == "")


def test_tokens() -> None:
    print("\n=== 2) 分词 ===")
    check("普通句子",
          tokens("Please select the Export project as PDF option")
          == ["Please", "select", "the", "Export", "project", "as", "PDF", "option"])
    check("连字符算一个词",
          tokens("a state-of-the-art tool") == ["a", "state-of-the-art", "tool"])
    check("撇号算一个词", tokens("don't stop") == ["don't", "stop"])
    check("数字不算词", tokens("version 2 of it") == ["version", "of", "it"])
    check("中英混排只取英文词", tokens("导出项目为 PDF") == ["PDF"])


def test_guess_pos() -> None:
    print("\n=== 3) 上下文猜词性 ===")
    cases = [
        ("run", "I run a small company.", "v"),
        ("run", "The run was long.", "n"),
        ("beautiful", "She is beautiful.", "adj"),
        ("quickly", "He ran quickly.", "adv"),
        ("running", "He is running fast.", "v"),
        ("settings", "Open the settings menu.", "n"),
    ]
    for word, sentence, want in cases:
        got = guess_pos(word, sentence)
        check(f"{word!r} 在 {sentence!r} 里是 {want}", got == want, f"得到 {got!r}")
    check("完全不认识时返回空串", guess_pos("xyzzy", "???") == "")


def test_sense_score() -> None:
    print("\n=== 4) 义项打分（上下文重排用的） ===")
    noun = Sense("n", "", "跑, 赛跑", "")
    verb = Sense("v", "", "跑, 运行", "")
    check("猜中词性加分", sense_score(verb, "v") > sense_score(noun, "v"))
    check("猜错词性扣分", sense_score(noun, "v") < 0)
    check("没有词性时轻微扣分", sense_score(Sense("", "", "x", ""), "v") == -4,
          f"得到 {sense_score(Sense('', '', 'x', ''), 'v')}")
    check("专业领域再扣一点",
          sense_score(Sense("n", "网络", "x", ""), "n")
          < sense_score(Sense("n", "", "x", ""), "n"))
    # ECDICT 里动词分 vi / vt、形容词写 a.，猜出来的是笼统的 v / adj
    check("vi 也算动词猜中",
          sense_score(Sense("vi", "", "x", ""), "v") == 10,
          f"得到 {sense_score(Sense('vi', '', 'x', ''), 'v')}")
    check("vt 也算动词猜中",
          sense_score(Sense("vt", "", "x", ""), "v") == 10)
    check("a. 也算形容词猜中",
          sense_score(Sense("a", "", "x", ""), "adj") == 10)


def test_variant_stub() -> None:
    print("\n=== 5) 变体占位（Settings → setting） ===")
    stub = DictEntry(word="Settings", translation="设置（setting的复数）")
    check("认出占位释义", _variant_stub_base(stub) == "setting",
          f"得到 {_variant_stub_base(stub)!r}")
    stub2 = DictEntry(word="buttons", translation="纽扣；按钮（button的复数形式）")
    check("认出具「形式」二字的占位", _variant_stub_base(stub2) == "button",
          f"得到 {_variant_stub_base(stub2)!r}")
    stub3 = DictEntry(word="cached", translation="贮藏起来；隐藏起来（cache 的过去分词形式）")
    check("认出一段话结尾的占位", _variant_stub_base(stub3) == "cache",
          f"得到 {_variant_stub_base(stub3)!r}")
    real = DictEntry(word="running", translation="赛跑, 流出, 运转\\n流动的, 跑着的")
    check("本身就是正式词条的不误判", _variant_stub_base(real) is None)
    long_one = DictEntry(word="x", translation="a" * 50 + "（y的复数）")
    check("超过 40 字的不当占位", _variant_stub_base(long_one) is None)
    same = DictEntry(word="foo", translation="（foo的复数）")
    check("括号里就是自己时不算", _variant_stub_base(same) is None)


def test_token_at() -> None:
    print("\n=== 6) 按鼠标位置取词 ===")
    line = "Please select the Export option"
    check("最左边取到 Please", token_at(line, 0.02) == "Please",
          f"得到 {token_at(line, 0.02)!r}")
    check("中间取到 Export", token_at(line, 0.62) == "Export",
          f"得到 {token_at(line, 0.62)!r}")
    check("最右边取到 option", token_at(line, 0.98) == "option",
          f"得到 {token_at(line, 0.98)!r}")
    check("落在空格上取左边最近的词", token_at("Please  select", 0.30) == "Please",
          f"得到 {token_at('Please  select', 0.30)!r}")
    check("空行返回空串", token_at("", 0.5) == "")
    check("纯中文返回空串", token_at("设置 编辑 视图", 0.5) == "")
    check("越界不崩", isinstance(token_at(line, 5.0), str))


def test_entry_properties() -> None:
    print("\n=== 7) 词条属性（考试标签 / 频率 / 变形） ===")
    entry = DictEntry(
        word="abandon", phonetic="ə'bændən", translation="n. 放任\\nv. 放弃",
        definition="", pos="", collins=3, oxford=1, tags="gk cet4 cet6 ky toefl",
        bnc=2000, frq=1500, exchange="p:abandoned/d:abandoned/i:abandoning/3:abandons",
    )
    check("考试标签按国内→出国排序",
          entry.exam_labels == ["高考", "四级", "六级", "考研", "托福"],
          f"得到 {entry.exam_labels}")
    check("是考试词", entry.is_exam_word)
    check("柯林斯星级", entry.collins_stars == "★★★", f"得到 {entry.collins_stars!r}")
    check("词频说明", entry.freq_label == "COCA 词频第 1500 位", f"得到 {entry.freq_label!r}")
    check("常用度", entry.rarity_label == "常用 3000 词", f"得到 {entry.rarity_label!r}")
    check("变形解析出 4 条", len(entry.forms) == 4, f"得到 {entry.forms}")
    check("变形用中文标签",
          ("过去式", "abandoned") in entry.forms, f"得到 {entry.forms}")
    check("没有标签时不是考试词",
          not DictEntry(word="x", translation="y").is_exam_word)


def test_ordered_senses() -> None:
    print("\n=== 8) 义项按上下文重排 ===")
    entry = DictEntry(
        word="run", translation="n. 跑, 奔跑的路程\\nv. 跑, 经营, 运行",
    )
    entry.senses = parse_translation(entry.translation)
    noun_first = [sense.pos for sense in entry.ordered_senses("The run was long.")]
    verb_first = [sense.pos for sense in entry.ordered_senses("I run a company.")]
    check("名词语境下名词义项在前", noun_first[0] == "n", f"得到 {noun_first}")
    check("动词语境下动词义项在前", verb_first[0] == "v", f"得到 {verb_first}")
    check("没给句子时顺序不变",
          [sense.pos for sense in entry.ordered_senses()] == ["n", "v"])


def test_vocab_book() -> None:
    print("\n=== 9) 生词本 ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "vocab.jsonl")
        book = VocabBook(path)
        check("新建时是空的", book.count() == 0)
        check("第一次收录返回 True",
              book.add("abandon", meaning="v. 放弃", pos="动词", exam="四级",
                       sentence="Never abandon hope.", sentence_translation="永不放弃希望。"))
        check("收录后有 1 条", book.count() == 1)
        check("重复收录返回 False",
              not book.add("abandon", meaning="v. 放弃"))
        check("重复收录不增加条数", book.count() == 1)
        check("出现次数累加", book.get("abandon").seen == 2,
              f"得到 {book.get('abandon').seen}")
        check("has 认词", book.has("abandon") and not book.has("missing"))

        book.bump_lookup("abandon")
        book.bump_lookup("abandon")
        check("查询次数累加", book.get("abandon").lookups == 2,
              f"得到 {book.get('abandon').lookups}")

        book.update("abandon", note="例句来自阅读", mastered=True)
        check("改笔记", book.get("abandon").note == "例句来自阅读")
        check("标记掌握", book.get("abandon").mastered)

        book.add("run", meaning="v. 跑")
        check("最新的在最前面", book.items()[0].word == "run",
              f"得到 {book.items()[0].word}")
        check("按旧到新也能取", book.items(newest_first=False)[0].word == "abandon")

        reloaded = VocabBook(path)
        reloaded.load()
        check("落盘后能读回来", reloaded.count() == 2, f"得到 {reloaded.count()}")
        check("字段完整", reloaded.get("abandon").meaning == "v. 放弃"
              and reloaded.get("abandon").mastered)

        csv_path = os.path.join(tmp, "out.csv")
        count = book.export_csv(csv_path)
        check("导出 CSV 返回条数", count == 2, f"得到 {count}")
        with open(csv_path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
        check("CSV 表头是十二列",
              text.splitlines()[0].count(",") == 11,
              f"得到 {text.splitlines()[0]!r}")
        check("CSV 含单词", "abandon" in text and "run" in text)
        check("CSV 带 BOM（Excel 才不乱码）",
              open(csv_path, "rb").read(3) == b"\xef\xbb\xbf")

        anki_path = os.path.join(tmp, "anki.txt")
        book.export_anki(anki_path)
        with open(anki_path, "r", encoding="utf-8") as handle:
            anki = handle.read()
        check("Anki 头有 separator", "#separator:Comma" in anki)
        check("Anki 头有 html", "#html:true" in anki)
        check("Anki 列名是 Word,Back", "#columns:Word,Back" in anki)
        check("Anki 正文含单词", "abandon" in anki)

        book.remove("run")
        check("删除生效", book.count() == 1 and not book.has("run"))
        book.clear()
        check("清空生效", book.count() == 0)
        check("坏行不影响加载", _tolerate_bad_lines(tmp))


def _tolerate_bad_lines(tmp: str) -> bool:
    path = os.path.join(tmp, "bad.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"word": "ok", "meaning": "好"}\n')
        handle.write("这不是 JSON\n")
        handle.write("\n")
        handle.write('{"word": "ok2", "meaning": "也好"}\n')
    book = VocabBook(path)
    book.load()
    return book.count() == 2


def test_dicthtml() -> None:
    print("\n=== 10) 词条卡片 HTML ===")
    theme = {"text": "#ffffff", "muted": "#888888", "accent": "#4da3ff",
             "border": "#333333"}
    entry = DictEntry(
        word="abandon", phonetic="ə'bændən", translation="n. 放任\\nv. 放弃",
        definition="to give up", pos="", collins=3, oxford=1, tags="gk cet4",
        bnc=2000, frq=1500, exchange="p:abandoned",
    )
    entry.senses = parse_translation(entry.translation)
    html = dicthtml.entry_html(entry, theme, sentence="Never abandon hope.")
    check("卡片里有单词", "abandon" in html)
    # HTML 里撇号会被转义成 &#x27;，所以只比对后半段
    check("卡片里有音标", "bændən" in html)
    check("卡片里有考试标签", "高考" in html and "四级" in html)
    check("卡片里有释义", "放弃" in html)
    check("卡片里有例句", "Never abandon hope." in html)
    check("卡片里有收录链接", 'href="vocab:abandon"' in html)

    compact = dicthtml.entry_html(entry, theme, compact=True)
    check("紧凑模式不带收录链接", 'href="vocab:' not in compact)
    check("紧凑模式仍有单词", "abandon" in compact)

    check("切句找得到含目标词的句子",
          dicthtml.sentence_of("Never abandon hope. Try again.", "abandon")
          == "Never abandon hope.")
    check("切句找不到时返回空串",
          dicthtml.sentence_of("Nothing here.", "abandon") == "")

    words = dicthtml.pick_words("Please select the Export option and the PDF file.")
    check("挑词去重且去掉单字母", len(words) == len(set(words)) and "a" not in words,
          f"得到 {words}")
    check("挑词一律小写", words == [word.lower() for word in words], f"得到 {words}")
    check("挑词按长度降序",
          [len(word) for word in words] == sorted((len(w) for w in words), reverse=True),
          f"得到 {words}")
    check("挑词把长词放前面（export 在 the 之前）",
          words.index("export") < words.index("the"), f"得到 {words}")

    plain = dicthtml.vocab_meaning(entry, "Never abandon hope.")
    check("生词本释义是纯文本（没有标签）", "<" not in plain, f"得到 {plain!r}")
    check("生词本释义带词性", "动词" in plain or "名词" in plain, f"得到 {plain!r}")


# ==================================================================== 依赖词库
def test_library() -> None:
    print("\n=== 11) 真实词库（需要先建库） ===")
    info = library_status()
    if not info["ready"]:
        skip("查词", f"还没建库（{info['db_path']}）；设置 → 词典 → 下载并建立词典库")
        return
    print(f"  词库：{info['entries']:,} 条，{info['db_bytes'] / 1048576:.1f} MB")
    book = Dictionary()
    check("词库可用", book.available)
    check("条数与磁盘一致", book.count() == info["entries"],
          f"{book.count()} vs {info['entries']}")

    entry = book.lookup("abandon")
    check("查到 abandon", entry is not None and entry.word == "abandon")
    if entry is not None:
        check("abandon 是考试词", entry.is_exam_word, f"标签 {entry.exam_labels}")
        check("abandon 有多个义项", len(entry.senses) >= 2, f"{len(entry.senses)} 条")

    check("大写也能查", book.lookup("ABANDON") is not None)
    check("前后空格被忽略", book.lookup("  run  ") is not None)
    check("含数字的不当词查（返回 None）", book.lookup("abc123") is None)
    check("空串返回 None", book.lookup("") is None)

    settings = book.lookup("Settings")
    check("Settings 走原形 setting（不当成占位释义）",
          settings is not None and settings.word == "setting",
          f"得到 {settings.word if settings else None!r}")
    check("Settings 记下原形", settings is not None and settings.matched_form == "Settings",
          f"得到 {settings.matched_form if settings else None!r}")
    cached = book.lookup("cached")
    check("cached 走原形 cache（不是 cach）",
          cached is not None and cached.word == "cache",
          f"得到 {cached.word if cached else None!r}")

    for word in ("runtime", "screenshot", "tooltip", "checkbox", "placeholder",
                 "mutex", "async"):
        check(f"技术词 {word} 查得到", book.lookup(word) is not None)

    in_context = book.lookup_in_context("run", "I run a small company.")
    check("上下文查词把动词义项排前",
          in_context is not None and in_context.senses
          and in_context.senses[0].pos in ("v", "vt", "vi"),
          f"得到 {in_context.senses[0].pos if in_context else None!r}")

    suggestions = book.suggest("set", limit=8)
    check("补全返回结果", len(suggestions) > 0, f"得到 {suggestions[:4]}")
    check("补全都是 set 开头",
          all(word.lower().startswith("set") for word in suggestions))

    entries = book.lookup_sentence(
        "Please select the Export project as PDF option and check the runtime settings.")
    check("整句查词返回多个词条", len(entries) >= 5, f"得到 {len(entries)} 条")
    check("整句查词里没有重复词",
          len({entry.word.lower() for entry in entries}) == len(entries))
    check("整句查词有 settings",
          any(entry.word.lower() == "setting" for entry in entries))


# ==================================================================== 汇总
def main() -> int:
    only_offline = "offline" in sys.argv
    test_parse_translation()
    test_tokens()
    test_guess_pos()
    test_sense_score()
    test_variant_stub()
    test_token_at()
    test_entry_properties()
    test_ordered_senses()
    test_vocab_book()
    test_dicthtml()
    if not only_offline:
        test_library()
    print(f"\n{'=' * 60}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败清单：")
        for label in FAIL:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
