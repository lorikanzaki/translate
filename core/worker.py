"""后台工作线程：OCR + 翻译流水线，绝不阻塞 UI。

线程边界只用两样东西跨过去：
  * `request` 信号：主线程 → 工作线程（显式 QueuedConnection，见 main.py）
  * `results` 队列：工作线程 → 主线程，由主线程的定时器轮询取出

结果**刻意不用 Qt 信号回传**。实测 PySide6 对「连接建立时 worker 还在主线程」
这类情形的连接类型判定很容易退化成 DirectConnection，槽函数会跑在 OCR 线程里，
于是 `QTextBrowser.setHtml()` 就在后台线程动到了主线程的 QTextDocument，Qt 报
`Cannot create children for a parent that is in a different thread` 并可能随机崩溃。
用 `queue.Queue` + 主线程轮询把这条路径彻底变成普通 Python 数据传递，与 Qt 的
连接类型语义无关。
"""
from __future__ import annotations

import queue
import time
import traceback
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.ocr import OcrEngine, looks_like_translatable, merge_close_blocks
from core.translate import DEFAULT_PROVIDER_ORDER, PROVIDER_CLASSES, Translator


@dataclass
class PipelineSettings:
    use_dml: bool = True
    use_cls: bool = False
    max_side: Optional[int] = 1600
    min_score: float = 0.5
    target_lang: str = "zh-CN"
    # 源语言：空串 / "auto" 表示让接口自己识别。指定后接口不再靠猜，
    # 短词和混排界面的准确率明显更好（实测自动识别对 Einstellungen 这种
    # 单词都会判错，detectedLanguage 满分也照样错）。
    source_lang: str = ""
    filter_cjk: bool = True
    provider_order: Sequence[str] = field(
        default_factory=lambda: list(DEFAULT_PROVIDER_ORDER))


@dataclass
class PipelineResult:
    lines: List[str] = field(default_factory=list)
    translations: List[str] = field(default_factory=list)
    blocks: List[dict] = field(default_factory=list)
    ocr_ms: float = 0.0
    translate_ms: float = 0.0
    total_ms: float = 0.0
    backend: str = ""
    # 当前用的 OCR 识别模型（内置模型 / 韩语识别模型 …）。显示在状态栏，
    # 让用户知道「为什么这屏日语认不出来」是模型问题还是别的问题。
    ocr_model: str = ""
    provider: str = ""
    error: str = ""
    from_phrasebook: int = 0
    # 识别到、但被判为「不需要翻译」（已是中文 / 没有可翻译的字母）的段数。
    # 只用于面板提示，让用户能区分「程序没在识别」和「识别了但不用翻」。
    skipped: int = 0


class TranslateWorker(QObject):
    """常驻后台线程对象；每次收到 request 跑一轮流水线。

    跨线程约定：
      * 主线程调用 `push(frame, settings)` —— 内部发 `request` 信号（已显式
        排队到工作线程），主线程不碰 OCR。
      * 工作线程把 `PipelineResult` / 错误字符串放进 `self.results` 队列；
        主线程用 `drain()` 取。（Qt 信号连接必须显式给 receiver，
        绝不要用 lambda/普通函数当槽，否则会退化成 DirectConnection。）
    """

    request = Signal(object, object)      # image(np.ndarray), PipelineSettings
    engineReady = Signal(str, float)      # backend, 加载耗时（诊断用）

    def __init__(self, translator: Translator) -> None:
        super().__init__()
        self.translator = translator
        self.results: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self._engine: Optional[OcrEngine] = None
        self._engine_key: Tuple[bool, bool, Optional[int]] = (True, False, 1600)
        self.request.connect(self._handle, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------ 主线程侧
    def push(self, frame: np.ndarray, settings: PipelineSettings) -> None:
        """主线程调用：提交一帧。"""
        self.request.emit(frame, settings)

    def drain(self) -> List[Tuple[str, object]]:
        """主线程调用：取出所有已完成的结果（非阻塞）。"""
        items: List[Tuple[str, object]] = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items

    # ------------------------------------------------------------ 引擎
    def _ensure_engine(self, settings: PipelineSettings) -> OcrEngine:
        key = (settings.use_dml, settings.use_cls, settings.max_side,
               settings.source_lang)
        if self._engine is None or key != self._engine_key:
            self._engine = OcrEngine(
                use_dml=settings.use_dml,
                use_cls=settings.use_cls,
                max_side=settings.max_side,
                source_lang=settings.source_lang,
            )
            self._engine_key = key
            started = time.perf_counter()
            self._engine.ensure_loaded()
            self.engineReady.emit(self._engine.backend, time.perf_counter() - started)
        return self._engine

    def _ensure_providers(self, settings: PipelineSettings) -> None:
        """接口顺序变化时才重建降级链，避免每轮都重复构造。"""
        wanted = [name for name in settings.provider_order
                  if name in PROVIDER_CLASSES]
        if not wanted:
            wanted = list(DEFAULT_PROVIDER_ORDER)
        if wanted != self.translator.order:
            self.translator.set_provider_order(wanted)

    @Slot(object, object)
    def _handle(self, image: np.ndarray, settings: PipelineSettings) -> None:
        started = time.perf_counter()
        try:
            self._ensure_providers(settings)
            engine = self._ensure_engine(settings)
            ocr = engine.run(image)
            # 过阈值的全部识别结果：即使一行都不需要翻译，也要回传，
            # 否则面板只能显示「没检测到外语」，用户无从判断程序到底有没有
            # 在识别（区域里全是中文时这是最容易被误判成「坏了」的情形）。
            seen = [b for b in ocr.blocks if b.score >= settings.min_score]
            # 「要不要翻译」现在按**目标语言**判定：目标是中文就跳过中文块，
            # 目标是英语就跳过英文块，中日韩/西里尔/阿拉伯/泰文/天城文都算字母。
            target = settings.target_lang or "zh-CN"
            source = settings.source_lang or "auto"
            blocks = [b for b in seen
                      if looks_like_translatable(b.text, target=target, source=source)] \
                if settings.filter_cjk else list(seen)
            lines = merge_close_blocks(blocks)
            lines = [line for line in lines
                     if looks_like_translatable(line, target=target, source=source)]

            if not lines:
                self.results.put(("result", PipelineResult(
                    blocks=[b.as_dict() for b in seen],
                    skipped=len(seen),
                    backend=engine.backend,
                    ocr_model=engine.pack_note,
                    ocr_ms=ocr.elapsed_ms,
                    total_ms=(time.perf_counter() - started) * 1000,
                )))
                return

            outcome = self.translator.translate(
                lines, settings.target_lang, settings.source_lang)
            self.results.put(("result", PipelineResult(
                lines=lines,
                translations=outcome.translations,
                blocks=[b.as_dict() for b in blocks],
                ocr_ms=ocr.elapsed_ms,
                translate_ms=outcome.elapsed_ms,
                total_ms=(time.perf_counter() - started) * 1000,
                backend=engine.backend,
                ocr_model=engine.pack_note,
                provider=outcome.provider,
                error=outcome.error,
                from_phrasebook=outcome.from_phrasebook,
            )))
        except Exception as exc:  # 后台线程不能把异常抛给 Qt
            traceback.print_exc()
            self.results.put(("error", f"{type(exc).__name__}: {exc}"))
