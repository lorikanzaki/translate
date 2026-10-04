"""生词本：收录查过的词，带来源句子，可导出 CSV / Anki 卡片。

存储是 JSON Lines（``%APPDATA%\\ScreenTranslator\\vocab.jsonl``），一行一个词，
追加写、原子替换，坏行跳过 —— 半行写坏不至于把整个生词本赔进去。
"""
from __future__ import annotations

import csv
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional

APP_NAME = "ScreenTranslator"
VOCAB_NAME = "vocab.jsonl"


def vocab_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_vocab_path() -> str:
    return os.path.join(vocab_dir(), VOCAB_NAME)


@dataclass
class VocabItem:
    """生词本里的一条。"""

    word: str = ""
    phonetic: str = ""
    meaning: str = ""              # 收录时的中文释义（挑最相关的一条义项）
    pos: str = ""                  # 这条义项的词典标注，如 "n"
    exam: List[str] = field(default_factory=list)   # [高考, 四级]
    sentence: str = ""             # 屏幕上它出现的那句话
    sentence_translation: str = ""  # 那句话的译文
    added_at: str = ""             # ISO 时间
    lookups: int = 0               # 查词次数（每看一次词典 +1）
    seen: int = 1                  # 在屏幕上出现过几次
    note: str = ""
    mastered: bool = False

    @property
    def exam_text(self) -> str:
        return " ".join(self.exam)

    @property
    def added_text(self) -> str:
        return (self.added_at or "").replace("T", " ")[:16]


class VocabBook:
    """生词本；线程安全，写盘用临时文件替换。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_vocab_path()
        self._lock = threading.RLock()
        self._items: Dict[str, VocabItem] = {}
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        with self._lock:
            self._items.clear()
            if not os.path.exists(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    word = (data.get("word") or "").strip()
                    if not word:
                        continue
                    known = set(VocabItem.__dataclass_fields__)
                    clean = {k: v for k, v in data.items() if k in known}
                    clean["word"] = word
                    if not isinstance(clean.get("exam"), list):
                        clean["exam"] = []
                    self._items[word.lower()] = VocabItem(**clean)

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                for item in self._items.values():
                    handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------ 增删改
    def add(
        self,
        word: str,
        meaning: str = "",
        pos: str = "",
        phonetic: str = "",
        exam: Optional[Iterable[str]] = None,
        sentence: str = "",
        sentence_translation: str = "",
        note: str = "",
    ) -> bool:
        """收录一个词。已收录则只累加出现次数并补空字段，返回是否是新词。"""
        term = (word or "").strip()
        if not term:
            return False
        key = term.lower()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                item = VocabItem(
                    word=term,
                    phonetic=phonetic,
                    meaning=meaning,
                    pos=pos,
                    exam=list(exam or []),
                    sentence=sentence,
                    sentence_translation=sentence_translation,
                    added_at=datetime.now().isoformat(timespec="seconds"),
                    note=note,
                )
                self._items[key] = item
                self.save()
                return True
            item.seen += 1
            if not item.meaning and meaning:
                item.meaning = meaning
            if not item.pos and pos:
                item.pos = pos
            if not item.phonetic and phonetic:
                item.phonetic = phonetic
            if not item.exam and exam:
                item.exam = list(exam)
            if not item.sentence and sentence:
                item.sentence = sentence
            if not item.sentence_translation and sentence_translation:
                item.sentence_translation = sentence_translation
            self.save()
            return False

    def remove(self, word: str) -> bool:
        with self._lock:
            if self._items.pop((word or "").strip().lower(), None) is None:
                return False
            self.save()
            return True

    def bump_lookup(self, word: str) -> None:
        with self._lock:
            item = self._items.get((word or "").strip().lower())
            if item is None:
                return
            item.lookups += 1
            self.save()

    def update(self, word: str, **changes) -> bool:
        with self._lock:
            item = self._items.get((word or "").strip().lower())
            if item is None:
                return False
            for key, value in changes.items():
                if hasattr(item, key):
                    setattr(item, key, value)
            self.save()
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.save()

    # ------------------------------------------------------------------ 查询
    def has(self, word: str) -> bool:
        with self._lock:
            return (word or "").strip().lower() in self._items

    def get(self, word: str) -> Optional[VocabItem]:
        with self._lock:
            return self._items.get((word or "").strip().lower())

    def items(self, newest_first: bool = True) -> List[VocabItem]:
        with self._lock:
            values = list(self._items.values())
        # `added_at` 只精确到秒，同一秒里收进来的几个词会打平。dict 保留的是
        # 插入顺序（也就是 jsonl 的追加顺序），所以拿它当第二排序键，
        # 保证「后收的排在前面」而不是随 dict 顺序碰运气。
        order = {id(item): index for index, item in enumerate(values)}
        values.sort(key=lambda item: (item.added_at, order[id(item)]),
                    reverse=newest_first)
        return values

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    # ------------------------------------------------------------------ 导出
    CSV_HEADER = ["单词", "音标", "词性", "释义", "考试", "来源句子", "句子译文",
                  "收录时间", "查询次数", "出现次数", "掌握", "笔记"]

    def export_csv(self, path: str) -> int:
        """导出成通用 CSV（Excel 直接打开）。返回写了多少行。"""
        rows = self.items(newest_first=False)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.CSV_HEADER)
            for item in rows:
                writer.writerow([
                    item.word, item.phonetic, item.pos, item.meaning,
                    item.exam_text, item.sentence, item.sentence_translation,
                    item.added_text, item.lookups, item.seen,
                    "是" if item.mastered else "", item.note,
                ])
        return len(rows)

    def export_anki(self, path: str) -> int:
        """导出成 Anki 可导入的 CSV：正面=单词，背面=音标+释义+例句。

        头部按 Anki 的文件导入约定写（`#separator` / `#html` / `#columns`），
        导入时字段会自动对上。
        """
        rows = self.items(newest_first=False)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("#separator:Comma\n")
            handle.write("#html:true\n")
            handle.write("#columns:Word,Back\n")
            writer = csv.writer(handle)
            for item in rows:
                back = [f"<b>{item.meaning or '（无释义）'}</b>"]
                if item.pos:
                    back.append(f"<i>{item.pos}</i>")
                if item.phonetic:
                    back.append(f"/{item.phonetic}/")
                if item.exam_text:
                    back.append(f"考试：{item.exam_text}")
                if item.sentence:
                    back.append(f"<br>例：{item.sentence}")
                if item.sentence_translation:
                    back.append(f"<br>{item.sentence_translation}")
                writer.writerow([item.word, "<br>".join(back)])
        return len(rows)

    def export_json(self, path: str) -> int:
        rows = self.items(newest_first=False)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([asdict(item) for item in rows], handle,
                      ensure_ascii=False, indent=2)
        return len(rows)
