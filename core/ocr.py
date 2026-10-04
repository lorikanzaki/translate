"""OCR 引擎封装：RapidOCR(ONNX) + 线程安全 + 自动降级 + 多语种识别模型。

实测经验（RapidOCR 官方 benchmark）：OCR 输入尺寸是动态的，DirectML/CUDA
在执行器层反而可能比 CPU 慢（0.999s vs 0.505s）。所以这里把推理后端做成
可配置项，DirectML 初始化失败自动回落 CPU，并提供 benchmarks/bench_ocr.py
让你在本机自己量一遍再决定。

多语种说明：内置的 `multi_PP-OCRv6_rec_small` 识别字符集里**只有**拉丁字母、
日语假名和中文汉字（18708 个字符，其中汉字 15565、平假名 86、片假名 94、
希腊 76，一个西里尔/谚文/阿拉伯/泰文/天城文字符都没有）。实测俄语、韩语、
阿拉伯语在默认模型下**整行识别为空**，检测框数 > 识别行数。所以源语言不是
拉丁/日语/中文时，要换成对应的识别模型（首次使用会自动下载，单个 8~14 MB，
下载地址 modelscope.cn，实测可达）。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from core.scripts import (
    has_letters, primary_script, source_is_declared, target_scripts,
)


# --------------------------------------------------------------- 语言与文字体系
#
# RapidOCR 识别模型的命名是 `<语言>_PP-OCRv<版本>_rec_<规模>`，实测可下载的
# 组合（见 site-packages/rapidocr/default_models.yaml）里，PP-OCRv5 + mobile
# 覆盖最全。这里只列**内置模型认不了**的语言；内置模型认得的（拉丁字母、
# 日语、中文）一律不填，免得白下载几十 MB。
LANGUAGE_PACKS: dict = {
    # 谚文
    "ko": ("KOREAN", "PPOCRV5"),
    # 西里尔字母（eslav = 东斯拉夫，比老的 cyrillic 准）
    "ru": ("ESLAV", "PPOCRV5"),
    "uk": ("ESLAV", "PPOCRV5"),
    "be": ("ESLAV", "PPOCRV5"),
    "bg": ("ESLAV", "PPOCRV5"),
    "sr": ("ESLAV", "PPOCRV5"),
    "mk": ("ESLAV", "PPOCRV5"),
    # 阿拉伯字母
    "ar": ("ARABIC", "PPOCRV5"),
    "fa": ("ARABIC", "PPOCRV5"),
    "ur": ("ARABIC", "PPOCRV5"),
    # 泰文
    "th": ("TH", "PPOCRV5"),
    # 天城文
    "hi": ("DEVANAGARI", "PPOCRV5"),
    "ne": ("DEVANAGARI", "PPOCRV5"),
    "mr": ("DEVANAGARI", "PPOCRV5"),
    # 希腊文
    "el": ("EL", "PPOCRV5"),
}

# 语言包的中文名，用于日志与界面提示
PACK_LABELS: dict = {
    "KOREAN": "韩语",
    "ESLAV": "俄语/西里尔字母",
    "ARABIC": "阿拉伯语",
    "TH": "泰语",
    "DEVANAGARI": "印地语/天城文",
    "EL": "希腊语",
}


def language_pack(source_lang: str) -> Optional[tuple]:
    """源语言 -> (RapidOCR 语言名, OCR 版本)；None 表示用内置模型。

    只认两位主语言码：`ru-RU` / `ru_RU` / `RU` 都当成 `ru`。
    """
    code = (source_lang or "").strip().lower().replace("_", "-")
    if not code or code in ("auto", "自动", "自动识别"):
        return None
    return LANGUAGE_PACKS.get(code.split("-")[0])


def pack_file_name(pack: tuple) -> str:
    """语言包在 rapidocr/models 下的文件名。"""
    return f"{pack[0].lower()}_{pack[1].replace('PPOCRV', 'PP-OCRv')}_rec_mobile.onnx"


def pack_is_downloaded(pack: tuple) -> bool:
    """这个语言包是否已经在磁盘上（RapidOCR 用的是同一个目录）。"""
    try:
        import rapidocr

        models = os.path.join(os.path.dirname(rapidocr.__file__), "models")
        return os.path.exists(os.path.join(models, pack_file_name(pack)))
    except Exception:
        return False


# 文字体系判定（script_of / primary_script / target_scripts）统一放在
# core/scripts.py：core/translate.py 也要用同一套判定，放在任一边都会让
# 另一边反向依赖。


def looks_like_translatable(
    text: str, min_letters: int = 2, target: str = "zh-CN", source: str = "auto"
) -> bool:
    """粗筛：这块文字值不值得送去翻译。

    判定只有两条：
      1. 至少要有 `min_letters` 个字母（`str.isalpha()`，所以汉字、假名、
         谚文、西里尔、阿拉伯、泰文、天城文都算字母），纯数字符号直接跳过；
      2. 主要文字体系不能就是**目标语言**的文字体系 —— 目标是中文时跳过
         中文块，目标是英语时跳过英文块。

    这条判定以前要求「至少 2 个 ASCII 字母」，导致日语、俄语、韩语、
    阿拉伯语的整屏文字被静默丢掉（这是「只能英语转其它语言」的直接原因）。

    `source` 只在**用户明确指定了源语言**时才起作用：日语里的「設定 / 編集 /
    表示 / 終了」这些纯汉字行，文字体系和中文完全一样，靠第 2 条会被当成
    「已经是中文」跳过；但用户既然选了日语源，就该把它们翻成「设置 / 编辑 /
    显示 / 结束」。所以源语言明确且与目标语言不同时，不信文字体系这一条。
    """
    if not text:
        return False
    if not has_letters(text, min_letters):
        return False
    if source_is_declared(source, target):
        return True
    return primary_script(text) not in target_scripts(target)



@dataclass
class TextBlock:
    """一个 OCR 文本框。坐标是相对抓取区域的物理像素。"""

    text: str
    score: float
    box: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def left(self) -> float:
        return min(p[0] for p in self.box) if self.box else 0.0

    @property
    def top(self) -> float:
        return min(p[1] for p in self.box) if self.box else 0.0

    @property
    def right(self) -> float:
        return max(p[0] for p in self.box) if self.box else 0.0

    @property
    def bottom(self) -> float:
        return max(p[1] for p in self.box) if self.box else 0.0

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "score": round(self.score, 4),
            "box": [[round(x, 1), round(y, 1)] for x, y in self.box],
        }


@dataclass
class OcrResult:
    blocks: List[TextBlock]
    elapsed_ms: float
    backend: str


class OcrEngine:
    """懒加载的 OCR 单例包装。"""

    def __init__(
        self,
        use_dml: bool = True,
        use_cls: bool = False,
        max_side: Optional[int] = None,
        source_lang: str = "auto",
        allow_download: bool = True,
    ) -> None:
        self.use_dml = use_dml
        self.use_cls = use_cls
        self.max_side = max_side
        self.source_lang = source_lang
        self.allow_download = allow_download
        self._engine = None
        self._backend = "未初始化"
        self._lock = threading.RLock()
        self._load_seconds: float = 0.0
        self._pack_note: str = ""

    # ---------------------------------------------------------------- 加载
    @property
    def backend(self) -> str:
        return self._backend

    @property
    def load_seconds(self) -> float:
        return self._load_seconds

    @property
    def pack(self) -> Optional[tuple]:
        """当前用的语言识别包（RapidOCR 语言名, 版本）；内置模型时为 None。

        `allow_download=False` 时，只认已经在磁盘上的语言包 —— 怕在没网的
        机器上开一次设置就卡在下载上。
        """
        pack = language_pack(self.source_lang)
        if pack is None:
            return None
        if not self.allow_download and not pack_is_downloaded(pack):
            return None
        return pack

    @property
    def pack_note(self) -> str:
        """给状态栏用的一句话：用的是内置模型还是哪个语言包。"""
        if self._pack_note:
            return self._pack_note
        pack = self.pack
        if not pack:
            return "内置识别模型"
        return f"{PACK_LABELS.get(pack[0], pack[0])}识别模型"

    def _build_params(self, dml: bool) -> dict:
        params: dict = {}
        if dml:
            params["EngineConfig.onnxruntime.use_dml"] = True
        if not self.use_cls:
            params["Global.use_cls"] = False
        if self.max_side:
            params["Global.max_side_len"] = int(self.max_side)
        pack = self.pack
        if pack:
            # 注意：这三个值必须是枚举对象，传字符串会抛
            # TypeError: The value of Rec.ocr_version must be Enum Type.
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

            params["Rec.lang_type"] = getattr(LangRec, pack[0])
            params["Rec.ocr_version"] = getattr(OCRVersion, pack[1])
            params["Rec.model_type"] = ModelType.MOBILE
        return params

    def _create(self, dml: bool):
        from rapidocr import RapidOCR  # 延迟导入，缺失时给出可读报错

        return RapidOCR(params=self._build_params(dml))

    def ensure_loaded(self) -> None:
        """首次调用时加载模型。DirectML 不可用时静默回落 CPU。"""
        if self._engine is not None:
            return
        with self._lock:
            if self._engine is not None:
                return
            started = time.perf_counter()
            if self.use_dml:
                try:
                    self._engine = self._create(True)
                    self._backend = "DirectML(GPU)"
                except Exception as exc:
                    print(f"[ocr] DirectML 初始化失败，回落 CPU: {type(exc).__name__}: {exc}")
                    self._engine = None
            if self._engine is None:
                self._engine = self._create_fallback()
                providers = []
                try:
                    import onnxruntime as ort

                    providers = list(ort.get_available_providers())
                except Exception:
                    pass
                hint = "DmlExecutionProvider" in providers
                if hint:
                    self._backend = "CPU(onnxruntime)"
                else:
                    self._backend = "CPU(纯 CPU 版 onnxruntime)"
            self._load_seconds = time.perf_counter() - started
            note = self.pack_note
            print(f"[ocr] 引擎就绪 backend={self._backend} 识别模型={note} "
                  f"加载耗时 {self._load_seconds:.2f}s")

    def _create_fallback(self):
        """拿不到语言包时退回内置模型 —— 宁可识别差一点，也别开不了工。"""
        pack = self.pack
        if not pack:
            return self._create(False)
        try:
            return self._create(False)
        except Exception as exc:
            self._pack_note = "内置识别模型（语言包加载失败）"
            print(f"[ocr] {PACK_LABELS.get(pack[0], pack[0])}识别模型加载失败，"
                  f"改用内置模型: {type(exc).__name__}: {exc}")
            self.source_lang = "auto"
            return self._create(False)

    # ---------------------------------------------------------------- 推理
    def run(self, image: np.ndarray) -> OcrResult:
        """对 RGB numpy 图像做 OCR。"""
        self.ensure_loaded()
        assert self._engine is not None
        started = time.perf_counter()
        with self._lock:  # DirectML 的 session 不允许并发 Run
            output = self._engine(image, use_det=True, use_cls=False, use_rec=True)
        elapsed = (time.perf_counter() - started) * 1000.0
        return OcrResult(
            blocks=self._to_blocks(output),
            elapsed_ms=elapsed,
            backend=self._backend,
        )

    @staticmethod
    def _to_blocks(output) -> List[TextBlock]:
        if output is None:
            return []
        boxes = getattr(output, "boxes", None)
        txts = getattr(output, "txts", None) or ()
        scores = getattr(output, "scores", None)
        if boxes is None or txts is None:
            return []
        blocks: List[TextBlock] = []
        for index, text in enumerate(txts):
            text = (text or "").strip()
            if not text:
                continue
            try:
                box = [
                    (float(point[0]), float(point[1]))
                    for point in np.asarray(boxes[index]).reshape(-1, 2)
                ]
            except Exception:
                box = []
            try:
                score = float(scores[index]) if scores is not None else 0.0
            except Exception:
                score = 0.0
            blocks.append(TextBlock(text=text, score=score, box=box))
        blocks.sort(key=lambda b: (round(b.top / 8.0), b.left))
        return blocks


def merge_close_blocks(
    blocks: Sequence[TextBlock], line_gap_ratio: float = 0.7
) -> List[str]:
    """按行合并碎片，减少翻译请求条数。"""
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b.top, b.left))
    lines: List[List[TextBlock]] = []
    for block in ordered:
        placed = False
        for line in lines:
            reference = line[0]
            tolerance = max(reference.height, block.height) * line_gap_ratio
            if abs(block.top - reference.top) <= tolerance:
                line.append(block)
                placed = True
                break
        if not placed:
            lines.append([block])
    merged: List[str] = []
    for line in lines:
        line.sort(key=lambda b: b.left)
        merged.append(" ".join(b.text for b in line).strip())
    return [text for text in merged if text]
