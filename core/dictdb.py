"""本地词典库：把 ECDICT 的 `ecdict.csv` 建成一份 SQLite，供词典功能查询。

数据来源 ECDICT（https://github.com/skywind3000/ECDICT ，MIT），字段：

    word, phonetic, definition, translation, pos, collins, oxford,
    tag, bnc, frq, exchange, detail, audio

对学生最有用的两列是：

* ``translation`` —— **按词性分行**的中文释义（``n. 罩；风帽\\nv. 覆盖``），
  这就是「同一个词的不同意思」；
* ``tag`` —— 空格分隔的**考试标签**（zk 中考 / gk 高考 / cet4 四级 /
  cet6 六级 / ky 考研 / toefl / ielts / gre），这就是「考试频率」。

另外 ``frq``（COCA 排名）/ ``bnc``（英国国家语料库排名）/ ``collins``（柯林斯
星级）/ ``oxford``（牛津三千核心词）用来判断「这个词到底常不常用」。

建库时收**所有「纯字母单词 + 有中文释义」的条目**，约 40 万条、SQLite 约 53 MB，
查询瞬时。一开始我用「有考试标签或词频排名」筛，只剩 3.7 万条，`runtime` /
`screenshot` / `tooltip` / `checkbox` / `placeholder` / `mutex` / `async` 这些
软件界面里的常客**全查不到** —— 它们既不上考试也不进词频榜。所以改成宽收。

查不到的词由在线翻译接口兜底。
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

APP_NAME = "ScreenTranslator"
CSV_NAME = "ecdict.csv"
DB_NAME = "dict.sqlite3"

# 建库筛子：COCA / BNC 排名在此之内的收进来
FREQ_LIMIT = 30000

# 考试标签 → 中文。ECDICT 的 tag 列取值就这些。
EXAM_LABELS: Dict[str, str] = {
    "zk": "中考",
    "gk": "高考",
    "cet4": "四级",
    "cet6": "六级",
    "ky": "考研",
    "toefl": "托福",
    "ielts": "雅思",
    "gre": "GRE",
}
# 展示顺序：先国内考试，再出国考试
EXAM_ORDER = ["zk", "gk", "cet4", "cet6", "ky", "toefl", "ielts", "gre"]

# 词性缩写 → 中文
POS_LABELS: Dict[str, str] = {
    "n": "名词", "v": "动词", "vt": "及物动词", "vi": "不及物动词",
    "adj": "形容词", "a": "形容词", "adv": "副词", "prep": "介词",
    "conj": "连词",
    "pron": "代词", "num": "数词", "art": "冠词", "int": "感叹词",
    "aux": "助动词", "abbr": "缩写", "suf": "后缀", "pref": "前缀",
    "phr": "短语", "modal": "情态动词",
}
# 学科/领域标注 → 中文
DOMAIN_LABELS: Dict[str, str] = {
    "网络": "网络", "医": "医学", "经": "经济", "计": "计算机", "化": "化学",
    "法": "法律", "机": "机械", "电": "电子", "建": "建筑", "农": "农业",
    "军": "军事", "生": "生物", "数": "数学", "物": "物理", "地质": "地质",
    "冶": "冶金", "纺": "纺织", "航海": "航海", "航空": "航空", "汽车": "汽车",
    "石油": "石油", "印刷": "印刷", "戏剧": "戏剧", "音乐": "音乐", "体育": "体育",
    "psych": "心理", "医化": "医药化学",
}

# 一行释义的开头：`n.` / `vt.` / `adj.` / `[网络]` / `[医]`
_HEAD_RE = re.compile(r"^\s*(?:\[([^\]]{1,8})\]|([A-Za-z]{1,6})\.)\s*")
# 词形变化字段 `p:ran/d:run/i:running/3:runs`
_EXCHANGE_RE = re.compile(r"([a-z0-9]):([^/]+)")

# 词形变化代号 → 说明
EXCHANGE_LABELS: Dict[str, str] = {
    "p": "过去式", "d": "过去分词", "i": "现在分词", "3": "第三人称单数",
    "r": "比较级", "t": "最高级", "s": "复数", "0": "原形", "1": "变换形式",
}

_ALPHA_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")


def dict_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME, "dict")
    os.makedirs(path, exist_ok=True)
    return path


def csv_path() -> str:
    return os.path.join(dict_dir(), CSV_NAME)


def db_path() -> str:
    return os.path.join(dict_dir(), DB_NAME)


# --------------------------------------------------------------------- 数据结构
@dataclass
class Sense:
    """一条义项：``n. 罩；风帽；面罩``。"""

    pos: str = ""            # "n" / "v" / ""（没有词性前缀）
    domain: str = ""         # "网络" / "医" / ""
    text: str = ""           # "罩；风帽；面罩"
    raw: str = ""            # 原样一行

    @property
    def pos_label(self) -> str:
        return POS_LABELS.get(self.pos, self.pos)

    @property
    def domain_label(self) -> str:
        return DOMAIN_LABELS.get(self.domain, self.domain)


@dataclass
class DictEntry:
    """一个词的全部词典信息。"""

    word: str = ""
    phonetic: str = ""
    translation: str = ""     # 原始中文释义（可能多行）
    definition: str = ""      # 英文释义
    pos: str = ""             # "n:56/v:44"
    collins: int = 0
    oxford: bool = False
    tags: List[str] = field(default_factory=list)
    bnc: int = 0
    frq: int = 0
    exchange: str = ""
    senses: List[Sense] = field(default_factory=list)
    matched_form: str = ""    # 查的是变形时，原词是什么

    # ------------------------------------------------------------ 展示用的派生
    @property
    def exam_labels(self) -> List[str]:
        """[高考, 四级] 这样的中文考试标签。"""
        return [EXAM_LABELS[t] for t in EXAM_ORDER if t in self.tags]

    @property
    def is_exam_word(self) -> bool:
        return bool(self.exam_labels)

    @property
    def freq_label(self) -> str:
        """「COCA 词频 第 1234 位」这类人类可读的频率说明。"""
        ranks = [r for r in (self.frq, self.bnc) if r > 0]
        if not ranks:
            return ""
        best = min(ranks)
        where = "COCA" if best == self.frq else "BNC"
        return f"{where} 词频第 {best} 位"

    @property
    def rarity_label(self) -> str:
        """粗粒度常用度，按 COCA 排名分档。"""
        rank = self.frq or self.bnc
        if not rank:
            return "未收录词频"
        if rank <= 1000:
            return "最常用 1000 词"
        if rank <= 3000:
            return "常用 3000 词"
        if rank <= 10000:
            return "常见 10000 词"
        if rank <= 30000:
            return "一般书面语"
        return "较少见"

    @property
    def collins_stars(self) -> str:
        return "★" * self.collins if self.collins else ""

    @property
    def forms(self) -> List[Tuple[str, str]]:
        """[(过去式, ran), (现在分词, running) …]"""
        out: List[Tuple[str, str]] = []
        for code, value in _EXCHANGE_RE.findall(self.exchange or ""):
            label = EXCHANGE_LABELS.get(code)
            if label and code not in ("0", "1"):
                out.append((label, value.strip()))
        return out

    @property
    def pos_mix(self) -> List[Tuple[str, str, int]]:
        """[(名词, n, 56), (动词, v, 44)]，按占比降序。"""
        out: List[Tuple[str, str, int]] = []
        for piece in (self.pos or "").split("/"):
            if ":" not in piece:
                continue
            code, _, num = piece.partition(":")
            try:
                pct = int(num)
            except ValueError:
                continue
            out.append((POS_LABELS.get(code, code), code, pct))
        out.sort(key=lambda item: -item[2])
        return out

    def ordered_senses(self, sentence: str = "") -> List[Sense]:
        """义项列表；给了句子就按「像不像这句话里的意思」重排。"""
        if not sentence:
            return list(self.senses)
        guess = guess_pos(self.word, sentence)
        if not guess:
            return list(self.senses)
        return sorted(self.senses, key=lambda s: -sense_score(s, guess))


# --------------------------------------------------------------------- 文本解析
def parse_translation(text: str) -> List[Sense]:
    """把 ECDICT 的 translation 拆成一条条义项。

    字段里用**字面量** ``\\n`` 分行（CSV 里就是两个字符），先还原成真换行。
    每行通常以 ``n.`` ``vt.`` ``adj.`` 或 ``[网络]`` ``[医]`` 开头。
    """
    if not text:
        return []
    raw = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n")
    senses: List[Sense] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        pos = ""
        domain = ""
        match = _HEAD_RE.match(line)
        if match:
            domain = (match.group(1) or "").strip()
            code = (match.group(2) or "").strip().lower()
            if code in POS_LABELS:
                pos = code
            elif code:
                # 认不出来的词性缩写（如 `abbr`、`aux`）也留着原样
                pos = code
            body = line[match.end():].strip()
        else:
            body = line
        if not body:
            continue
        senses.append(Sense(pos=pos, domain=domain, text=body, raw=line))
    return senses


def tokens(text: str) -> List[str]:
    """从一行屏幕文字里抠出英文单词（词典功能只查拉丁词）。"""
    return [m.group(0) for m in _ALPHA_RE.finditer(text or "")]


# 同一个词性的不同写法。ECDICT 里动词分 `vi` / `vt`，形容词写 `a.`，
# 而 `guess_pos` 只会猜出笼统的 `v` / `adj`，不认这一层就会被判成「猜错」。
_POS_EQUIV: Dict[str, Tuple[str, ...]] = {
    "v": ("v", "vt", "vi"),
    "adj": ("adj", "a"),
    "adv": ("adv",),
    "n": ("n",),
}


def sense_score(sense: Sense, guess: str) -> int:
    """给义项打个「像不像这句话里的意思」的分，越大越靠前。

    这套判定很土 —— 就是看词在句中的位置和词尾形状猜词性（`guess_pos`），
    再和义项自己标的词性比对。但比「永远按词典原顺序列」有用得多，
    而且不需要联网、不需要模型、毫秒级出结果。
    """
    score = 0
    if guess and sense.pos in _POS_EQUIV.get(guess, (guess,)):
        score += 10
    elif guess:
        score -= 3
    if sense.domain:
        score -= 2          # [网络]/[医]/[经] 这类专业义项靠后
    if not sense.pos:
        score -= 1          # 没标词性的整句释义也靠后
    return score


_POS_HINTS_BEFORE: Dict[str, Tuple[str, ...]] = {
    "n": ("a", "an", "the", "this", "that", "these", "those", "my", "your",
          "his", "her", "its", "our", "their", "some", "any", "no", "every",
          "each", "another", "one", "two", "three", "of", "in", "on", "at",
          "with", "for", "from", "by", "about", "into", "over", "under"),
    "adj": ("is", "are", "was", "were", "be", "been", "am", "very", "so",
            "too", "quite", "really", "more", "most", "less", "least",
            "seems", "looks", "feels", "becomes", "get", "gets", "how"),
    "v": ("to", "will", "would", "can", "could", "should", "must", "may",
          "might", "shall", "do", "does", "did", "don't", "doesn't",
          "didn't", "please", "let", "let's", "i", "you", "we", "they",
          "he", "she", "it"),
    "adv": ("runs", "run", "speaks", "speak", "works", "work"),
}
_SUFFIX_HINTS: Tuple[Tuple[str, str], ...] = (
    ("ing", "v"), ("ed", "v"), ("ize", "v"), ("ise", "v"), ("ate", "v"),
    ("ly", "adv"),
    ("tion", "n"), ("sion", "n"), ("ness", "n"), ("ment", "n"), ("ity", "n"),
    ("ance", "n"), ("ence", "n"), ("ship", "n"), ("hood", "n"),
    ("ous", "adj"), ("ful", "adj"), ("able", "adj"), ("ible", "adj"),
    ("ive", "adj"), ("less", "adj"), ("ish", "adj"), ("ary", "adj"),
)


def guess_pos(word: str, sentence: str) -> str:
    """猜 `word` 在 `sentence` 里当什么词性用。猜不出返回空串。"""
    if not sentence:
        return ""
    lowered = sentence.lower()
    target = word.lower()
    index = lowered.find(target)
    if index < 0:
        return ""
    before = lowered[:index].strip()
    prev = before.split()[-1] if before.split() else ""
    prev = prev.strip(".,;:!?\"'()[]")
    # 「is/are/was/were + 动词-ing」是现在进行时，「be + 动词-ed」是被动，
    # 这两种情况下 be 动词后面跟的是动词，不是形容词（`is beautiful` 才是形容词）。
    # 这条必须放在下面的「冠词/介词后面是名词」判定之前。
    if prev in ("is", "are", "was", "were", "be", "been", "am", "being"):
        for suffix in ("ing", "ed"):
            if target.endswith(suffix) and len(target) > len(suffix) + 1:
                return "v"
    # 冠词/介词后面基本是名词
    for pos, hints in _POS_HINTS_BEFORE.items():
        if prev in hints:
            return pos
    # 词尾形态
    for suffix, pos in _SUFFIX_HINTS:
        if target.endswith(suffix) and len(target) > len(suffix) + 1:
            return pos
    return ""


# --------------------------------------------------------------------- 词典本体
class Dictionary:
    """只读的词典查询接口（SQLite 连接是线程本地的）。"""

    def __init__(self, path: Optional[str] = None, auto_open: bool = True) -> None:
        self.path = path or db_path()
        self._local = threading.local()
        self._available = os.path.exists(self.path)
        if self._available and auto_open:
            try:
                self._conn()
            except sqlite3.Error:
                self._available = False

    # ------------------------------------------------------------------ 连接
    @property
    def available(self) -> bool:
        return self._available

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def reopen(self) -> bool:
        """重新看一眼词库在不在。

        用户在设置里刚下完 / 刚重建词库时，这个 `Dictionary` 对象是启动时建的，
        那会儿库还不存在（`_available` 是 False）。调一下这个方法就能接上，
        不用重启程序。返回现在能不能用。
        """
        self.close()
        self.path = self.path or db_path()
        self._available = os.path.exists(self.path)
        if self._available:
            try:
                self._conn()
                self._available = self.count() > 0
            except sqlite3.Error:
                self._available = False
        return self._available

    def count(self) -> int:
        if not self._available:
            return 0
        try:
            row = self._conn().execute("SELECT COUNT(*) FROM entry").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    # ------------------------------------------------------------------ 查询
    def lookup(self, word: str) -> Optional[DictEntry]:
        """查一个词；查不到时自动试它的原形（running → run）。

        还有一种情况比「查不到」更常见：**变体词自己也是一条记录，但那条记录
        是凑数的**。ECDICT 里 `Settings` 有独立一条，释义只有一句
        「设置（setting的复数）」，而原形 `setting` 那条才有「环境 / 背景 /
        布景 / 镶嵌 / 调整 / 沉落 / 一副餐具」和「设置」两栏。所以直接命中
        之后还要看一眼：如果这条释义只是个「变体占位」，就改用原形那条
        （原形优先从释义括号里读，读不到才查 `form` 表）。
        """
        term = (word or "").strip()
        if not term or not _ALPHA_RE.fullmatch(term):
            return None
        if not self._available:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM entry WHERE word = ? COLLATE NOCASE", (term,)
            ).fetchone()
            if row is not None:
                entry = _row_to_entry(row)
                base = _variant_stub_base(entry)
                if base:
                    base_row = conn.execute(
                        "SELECT * FROM entry WHERE word = ? COLLATE NOCASE", (base,)
                    ).fetchone()
                    if base_row is not None:
                        entry = _row_to_entry(base_row)
                        entry.matched_form = term
                return entry
            link = conn.execute(
                "SELECT base FROM form WHERE form = ? COLLATE NOCASE", (term,)
            ).fetchone()
            if link is not None:
                row = conn.execute(
                    "SELECT * FROM entry WHERE word = ? COLLATE NOCASE",
                    (link["base"],),
                ).fetchone()
                if row is not None:
                    entry = _row_to_entry(row)
                    entry.matched_form = term
                    return entry
            return None
        except sqlite3.Error:
            return None

    def lookup_in_context(self, word: str, sentence: str) -> Optional[DictEntry]:
        """查词，并按 `sentence` 给义项重排。"""
        entry = self.lookup(word)
        if entry is not None and sentence:
            entry.senses = entry.ordered_senses(sentence)
        return entry

    def suggest(self, prefix: str, limit: int = 8) -> List[str]:
        """补全：前缀匹配的常见词（按词频排）。"""
        term = (prefix or "").strip()
        if not term or not self._available:
            return []
        try:
            rows = self._conn().execute(
                "SELECT word FROM entry WHERE word LIKE ? COLLATE NOCASE "
                "ORDER BY CASE WHEN frq > 0 THEN frq ELSE 999999 END LIMIT ?",
                (term.replace("%", "").replace("_", "") + "%", limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [row["word"] for row in rows]

    def lookup_sentence(self, sentence: str, limit: int = 12) -> List[DictEntry]:
        """把一句话里所有查得到的词都查出来（按长度降序，长词优先）。"""
        words = sorted(set(tokens(sentence)), key=len, reverse=True)
        out: List[DictEntry] = []
        for word in words[:limit]:
            entry = self.lookup_in_context(word, sentence)
            if entry is not None:
                out.append(entry)
        return out


def token_at(text: str, ratio: float) -> str:
    """按横向位置比例取词：`ratio` = 鼠标在识别框里从左到右的 0-1 位置。

    OCR 只给一整行的文字和它四个角的坐标，不给每个词的坐标。所以只能拿
    「鼠标这一点的 x 占整行宽度的比例 × 字符数」估算落在第几个字符上，
    再把这个字符所在的英文单词整词取出来（`state-of-the-art` 这类带连字符的
    也算一个词）。落在词间的空格上时就取左边最近的词。
    """
    text = text or ""
    if not text.strip():
        return ""
    index = int(round(max(0.0, min(1.0, ratio)) * max(0, len(text) - 1)))
    best = ""
    best_end = -1
    for match in _ALPHA_RE.finditer(text):
        start, end = match.span()
        if start <= index < end:
            return match.group(0)
        if end <= index and end > best_end:
            best, best_end = match.group(0), end
    if _ALPHA_RE.fullmatch(text.strip()):
        return text.strip()
    return best


_VARIANT_STUB_RE = re.compile(
    r"（\s*([A-Za-z][A-Za-z'\u2019-]{0,30})\s*的(?:复数|过去式|过去分词|现在分词|"
    r"第三人称单数|比较级|最高级|缩写|变形)(?:形式|用法|写法|态)?）\s*$"
)


def _variant_stub_base(entry: "DictEntry") -> Optional[str]:
    """这条记录只是「某某的复数」这种占位释义吗？是的话返回那个某某。

    实测 ECDICT 里有不少这种凑数记录：
    `Settings` → 「设置（setting的复数）」、
    `buttons`  → 「纽扣；按钮（button的复数形式）」、
    `cached`   → 「贮藏起来；隐藏起来（cache 的过去分词形式）」。
    它们命中后会挡住原形那条更完整的释义，所以要认出来并让位。

    原形**直接从释义的括号里读**，不去查 `form` 表 —— 实测 `form` 表把
    `cached` 映射到了 `cach`（某个来源收的错词），而括号里写的是对的
    `cache`。

    判定很保守：整条释义只有一行、不超过 40 字、并以「（…的复数形式）」
    收尾。像 `running`（赛跑 / 流动的，跑着的）这种本身就是正式词条的
    不会被误判，`windows`（微软视窗）也不会。
    """
    text = (entry.translation or "").strip()
    if not text or len(text) > 40:
        return None
    lines = [line for line in text.split("\\n") if line.strip()]
    if len(lines) > 1:
        return None
    match = _VARIANT_STUB_RE.search(lines[0].strip())
    if match is None:
        return None
    base = match.group(1)
    return None if base.lower() == (entry.word or "").lower() else base


def _row_to_entry(row: sqlite3.Row) -> DictEntry:
    tags = [t for t in (row["tag"] or "").split() if t]
    tags.sort(key=lambda t: EXAM_ORDER.index(t) if t in EXAM_ORDER else 99)
    return DictEntry(
        word=row["word"],
        phonetic=row["phonetic"] or "",
        translation=row["translation"] or "",
        definition=row["definition"] or "",
        pos=row["pos"] or "",
        collins=int(row["collins"] or 0),
        oxford=bool(row["oxford"]),
        tags=tags,
        bnc=int(row["bnc"] or 0),
        frq=int(row["frq"] or 0),
        exchange=row["exchange"] or "",
        senses=parse_translation(row["translation"] or ""),
    )


# --------------------------------------------------------------------- 建库
SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
CREATE TABLE entry (
    word        TEXT PRIMARY KEY COLLATE NOCASE,
    phonetic    TEXT,
    translation TEXT,
    definition  TEXT,
    pos         TEXT,
    collins     INTEGER,
    oxford      INTEGER,
    tag         TEXT,
    bnc         INTEGER,
    frq         INTEGER,
    exchange    TEXT
);
CREATE TABLE form (
    form TEXT PRIMARY KEY COLLATE NOCASE,
    base TEXT
);
"""


def _keep(row: Dict[str, str], strict: bool = False) -> bool:
    """建库筛子：值不值得收进本地库。

    默认是**宽收**：只要是个纯字母单词、有中文释义就收。实测 ECDICT 共
    770611 行，其中 400847 行满足这个条件，包含 runtime / screenshot /
    tooltip / checkbox / mutex / async / localhost 这类**软件界面上很常见、
    但既没有考试标签也没有词频排名**的技术词 —— 早期版本按「有考试标签 /
    柯林斯星级 / 牛津核心 / 词频前 30000」过滤，只剩 37672 条，这些词全丢了。

    宽收的代价是库大一些（SQLite 约 130 MB）和二义性更多，但词典页里
    义项是按「最长词优先」排的，长词恰恰是更有信息量的那些。

    `strict=True` 时退回旧口径（只收有考试标签/星级/词频的），留给需要
    小库的场合。
    """
    word = (row.get("word") or "").strip()
    if not word or not _ALPHA_RE.fullmatch(word):
        return False
    if not (row.get("translation") or "").strip():
        return False
    if not strict:
        return True
    if (row.get("tag") or "").strip():
        return True
    if int(row.get("collins") or 0) > 0:
        return True
    if int(row.get("oxford") or 0) > 0:
        return True
    for key in ("frq", "bnc"):
        try:
            rank = int(row.get(key) or 0)
        except ValueError:
            rank = 0
        if 0 < rank <= FREQ_LIMIT:
            return True
    # 两个字母以内的短词（of / to / be / up …）几乎都会被引用到
    return len(word) <= 2


def build_db(
    source: Optional[str] = None,
    target: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
    force: bool = False,
    strict: bool = False,
) -> Tuple[int, int]:
    """把 CSV 建成 SQLite。返回 (收录条数, 词形条数)。"""
    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    source = source or csv_path()
    target = target or db_path()
    if not os.path.exists(source):
        raise FileNotFoundError(f"找不到词典数据：{source}")
    if os.path.exists(target) and not force:
        say(f"已有词典库，跳过建库：{target}")
        return (-1, -1)

    tmp = target + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        kept = 0
        forms: Dict[str, str] = {}
        total = 0
        with open(source, "r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if not header:
                raise ValueError("空的 CSV")
            index = {name.strip(): i for i, name in enumerate(header)}

            def field(row: Sequence[str], name: str) -> str:
                pos = index.get(name)
                return row[pos] if pos is not None and pos < len(row) else ""

            batch: List[Tuple] = []
            for row in reader:
                total += 1
                if total % 200000 == 0:
                    say(f"  读到 {total} 行，已收录 {kept} 条")
                if len(row) < 4:
                    continue
                item = {name: field(row, name) for name in
                        ("word", "phonetic", "translation", "definition", "pos",
                         "collins", "oxford", "tag", "bnc", "frq", "exchange")}
                if not _keep(item, strict=strict):
                    continue
                word = item["word"].strip()
                batch.append((
                    word, item["phonetic"], item["translation"],
                    item["definition"], item["pos"],
                    _int(item["collins"]), _int(item["oxford"]),
                    item["tag"].strip(), _int(item["bnc"]), _int(item["frq"]),
                    item["exchange"],
                ))
                kept += 1
                for code, value in _EXCHANGE_RE.findall(item["exchange"] or ""):
                    if code in ("0", "1"):
                        continue
                    for one in value.split(","):
                        one = one.strip()
                        if one and one.lower() != word.lower():
                            forms.setdefault(one, word)
                if len(batch) >= 5000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO entry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        batch)
                    batch.clear()
            if batch:
                conn.executemany(
                    "INSERT OR IGNORE INTO entry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    batch)
            say(f"  共 {total} 行，收录 {kept} 条，词形 {len(forms)} 条")
            conn.executemany("INSERT OR IGNORE INTO form VALUES (?,?)",
                             list(forms.items()))
            conn.commit()
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    if os.path.exists(target):
        os.remove(target)
    os.replace(tmp, target)
    say(f"词典库已生成：{target}")
    return (kept, len(forms))


def _int(value: str) -> int:
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


# --------------------------------------------------------------------- 下载
# ECDICT 的 ecdict.csv，约 66 MB。raw.githubusercontent.com 在国内时通时不通，
# 所以列了几个镜像，逐个试。
CSV_URLS = [
    "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
    "https://cdn.jsdelivr.net/gh/skywind3000/ECDICT@master/ecdict.csv",
    "https://ghproxy.net/https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
]
CSV_SIZE = 65933428


def download_csv(
    path: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
    force: bool = False,
    timeout: int = 60,
) -> str:
    """下载 `ecdict.csv`，**支持断点续传**，返回文件路径。

    实测这个下载很不稳：速度只有 0.2-1 MB/s，而且会连着几分钟一个字节都不动。
    所以（1）续传是必须的，中断后重跑就从已有字节数继续；（2）判「下完了没」
    只认**最终字节数**，不信 `Content-Length` —— 续传时那个值是剩余量，
    拿它算百分比会算出 286% 这种数。
    """
    import requests  # 放函数里，免得没装 requests 时连查词都用不了

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    target = path or csv_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target) and not force:
        size = os.path.getsize(target)
        if size >= CSV_SIZE:
            say(f"词典数据已存在（{size / 1e6:.1f} MB），跳过下载")
            return target
    done = os.path.getsize(target) if os.path.exists(target) else 0
    if force and done:
        os.remove(target)
        done = 0
    if done:
        say(f"从 {done / 1e6:.1f} MB 处继续下载")

    last_error = ""
    for url in CSV_URLS:
        headers = {"User-Agent": f"{APP_NAME}/1.0"}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            started = _time.perf_counter()
            with requests.get(url, headers=headers, stream=True,
                              timeout=timeout) as response:
                if response.status_code not in (200, 206):
                    last_error = f"HTTP {response.status_code}"
                    say(f"{url.split('/')[2]} 返回 {last_error}，换下一个镜像")
                    continue
                mode = "ab" if (done and response.status_code == 206) else "wb"
                if mode == "wb":
                    done = 0
                got = done
                last = 0.0
                with open(target, mode) as handle:
                    for chunk in response.iter_content(1 << 20):
                        handle.write(chunk)
                        got += len(chunk)
                        now = _time.perf_counter()
                        if now - last > 1.0:
                            last = now
                            speed = (got - done) / max(now - started, 1e-6) / 1e6
                            say(f"  已下载 {got / 1e6:.1f} MB / "
                                f"{CSV_SIZE / 1e6:.1f} MB  {speed:.2f} MB/s")
            size = os.path.getsize(target)
            if size >= CSV_SIZE:
                say(f"下载完成：{size / 1e6:.1f} MB")
                return target
            done = size
            last_error = f"只下到 {size / 1e6:.1f} MB（应为 {CSV_SIZE / 1e6:.1f} MB）"
            say(f"没下完（{last_error}），换下一个镜像续传")
        except Exception as exc:  # noqa: BLE001 - 网络什么都可能抛
            last_error = f"{type(exc).__name__}: {exc}"
            say(f"{url.split('/')[2]} 出错（{last_error}），换下一个镜像续传")
            done = os.path.getsize(target) if os.path.exists(target) else 0
    raise RuntimeError(f"词典数据下载失败：{last_error}")


def ensure_db(
    progress: Optional[Callable[[str], None]] = None,
    force: bool = False,
) -> Tuple[int, int]:
    """一条龙：没有 CSV 就下，没有库就建。返回 (收录条数, 词形条数)。"""
    download_csv(progress=progress)
    return build_db(progress=progress, force=force)


def library_status() -> Dict[str, object]:
    """给界面看的词库状态：有没有 CSV / 有没有库 / 多大 / 多少条。"""
    csv = csv_path()
    db = db_path()
    info: Dict[str, object] = {
        "csv_path": csv,
        "db_path": db,
        "csv_bytes": os.path.getsize(csv) if os.path.exists(csv) else 0,
        "db_bytes": os.path.getsize(db) if os.path.exists(db) else 0,
        "entries": 0,
        "ready": False,
    }
    if info["db_bytes"]:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                info["entries"] = int(
                    conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0])
                info["ready"] = info["entries"] > 0
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    return info
