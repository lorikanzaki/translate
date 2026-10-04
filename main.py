"""屏幕实时翻译 · 程序入口

用法：run.bat  （或 .venv\\Scripts\\pythonw.exe main.py）
    Ctrl+Alt+Z  框选 / 重新框选要翻译的区域
    Ctrl+Alt+T  开始 / 暂停实时翻译
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

# 高 DPI：必须在 QApplication 之前声明，保证 Qt 与 Win32 坐标同一套换算
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, QThread
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.capture import grab_region, mean_abs_diff, get_window_rect, window_title
from core.config import device_uses_dml, get_config
from core.dictdb import Dictionary, token_at
from core.hotkeys import HotkeyManager
from core.translate import DEFAULT_PROVIDER_ORDER, Translator
from core.vocab import VocabBook
from core.worker import PipelineResult, PipelineSettings, TranslateWorker
from ui import dicthtml, theme as theme_mod
from ui.dictwin import DictWindow
from ui.hover import HoverCard
from ui.panel import MainPanel
from ui.regionbox import RegionFrame
from ui.selector import RegionSelector, WindowPicker

APP_TITLE = "屏幕实时翻译"
APP_USER_MODEL_ID = "ScreenTranslator.Translator.1"


def set_app_user_model_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


class TranslatorApp:
    def __init__(self) -> None:
        self.config = get_config()
        self.translator = Translator(
            self.config.get("translate_providers") or None,
            timeout=int(self.config.get("translate_timeout") or 15),
            use_glossary=bool(self.config.get("use_glossary", True)),
            keep_proper_nouns=bool(self.config.get("keep_proper_nouns", True)),
        )
        self.translator.protect_tokens = bool(self.config.get("protect_tokens", True))
        self.translator.set_llm_settings(
            base_url=self.config.get("llm_base_url") or "",
            api_key=self.config.get("llm_api_key") or "",
            model=self.config.get("llm_model") or "",
        )

        # 词典（本地 ECDICT 词库 + 生词本）。词库没建好时 available 为 False，
        # 词典页会提示去设置里下载，其余功能照常。
        self.dictionary = Dictionary()
        self.vocab = VocabBook()
        self.dict_window = None
        # 悬停取词用的「这一屏的文字 + 它们在屏幕上的位置」
        self._screen_blocks: list = []
        self._screen_text = ""
        self._hover_word = ""
        self._hover_anchor = None
        self._hover_at = 0.0

        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_TITLE)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setStyleSheet(
            "QToolTip { background: #232830; color: #d7dbe0; border: 1px solid #333a44; }"
        )

        self.panel = MainPanel(self.config, self.translator,
                               dictionary=self.dictionary, vocab=self.vocab)
        self.selector = RegionSelector()
        self.window_picker = WindowPicker()
        # 框选区域的常驻边框：区域本身是别人的界面、看不见，靠这圈边框
        # 让用户知道正在翻译哪一块（鼠标穿透 + 画在区域外面，见 ui/regionbox.py）
        self.region_frame = RegionFrame()
        self.region_frame.apply_theme(theme_mod.theme(self.config))

        # 鼠标悬停取词：一个不接受焦点、鼠标穿透的小浮窗（见 ui/hover.py）
        self.hover = HoverCard(self.config, self.dictionary, self.vocab)
        self.hover.vocabToggled.connect(self._on_vocab_toggled)
        self.hover_timer = QTimer()
        self.hover_timer.setInterval(120)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.hover_timer.start()

        # 后台 OCR/翻译线程
        self.thread = QThread()
        self.worker = TranslateWorker(self.translator)
        self.worker.moveToThread(self.thread)
        self.thread.start()
        self._busy_since = 0.0
        # 结果不经 Qt 信号回传（连接类型判定不可靠，曾导致 setHtml 跑在 OCR
        # 线程里）。主线程用这个定时器轮询结果队列，界面更新永远发生在主线程。
        self.result_timer = QTimer()
        self.result_timer.setInterval(80)
        self.result_timer.timeout.connect(self._drain_results)
        self.result_timer.start()
        self.worker.engineReady.connect(
            self._on_engine_ready, Qt.ConnectionType.QueuedConnection)

        # 热键
        self.hotkeys = HotkeyManager()
        self.app.installNativeEventFilter(self.hotkeys)
        toggle_key = str(self.config.get("hotkey_toggle") or "ctrl+alt+t")
        mark_key = str(self.config.get("hotkey_mark") or "ctrl+alt+z")
        self.hotkeys.register(toggle_key, self._hotkey_toggle)
        self.hotkeys.register(mark_key, self.mark_region)

        # 轮询
        self.timer = QTimer()
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.timer.timeout.connect(self._tick)
        self._prev_frame = None
        self._busy = False
        self._force_next = True
        self._running = False
        self._frame_count = 0
        self._last_ocr_at = 0.0
        self._refresh_ms = max(0, int(self.config.get("refresh_ms") or 0))

        self.panel.markRequested.connect(self.mark_region)
        self.panel.minimizeRequested.connect(self._save_window_state)
        # 现在的面板是一个真正的应用程序窗口，关闭按钮就应当**退出程序**
        # （以前是隐藏到后台，热键再唤起；那种行为对「软件窗口」来说很反直觉）。
        # 想临时收起窗口请用最小化，程序继续跑、热键照常可用。
        self.panel.btn_close.clicked.disconnect()
        self.panel.btn_close.clicked.connect(self.quit)
        self.panel.settingsChanged.connect(self._apply_settings)
        self.panel.settingsChanged.connect(self.panel.apply_appearance)
        self.panel.dictWindowRequested.connect(self.open_dictionary)
        # 设置里调颜色时实时刷新框选边框（还没点确定也能看到）
        self.panel.themePreviewed.connect(self._on_theme_previewed)

        self._apply_settings()
        self.panel.show()
        self._report_region()
        self.status_hint()

    # ------------------------------------------------------------ 生命周期
    def _save_window_state(self) -> None:
        """最小化/退出前把窗口位置落盘。

        只记普通状态下的位置：最大化和最小化时的 geometry 要么铺满屏幕、
        要么是系统给的怪值，存下来下次启动就会开在不该开的地方。
        """
        self.panel._save_geometry()
        self.config.save()

    def quit(self) -> None:
        """关闭窗口的收尾：保存窗口位置，然后退出事件循环。

        真正的线程收尾在 shutdown() 里做（run() 返回后调用）。
        """
        self.panel._save_geometry()
        self.config.save()
        self.app.quit()

    def status_hint(self) -> None:
        toggle_key = self.config.get("hotkey_toggle")
        mark_key = self.config.get("hotkey_mark")
        self.panel.status_label.setText(
            f"就绪 · 热键 开始/暂停 {toggle_key} · 框选 {mark_key}"
        )
        if not self.config.get("region"):
            self.panel.source_view.setHtml(
                "<p style='color:#8a9199'>按 <b>Ctrl+Alt+Z</b> 框选要翻译的窗口区域，"
                "然后点「开始」（或按 <b>Ctrl+Alt+T</b>）。</p>"
                "<p style='color:#6d747d'>提示：圈住需要翻译的界面部分即可，区域越小越快。</p>"
            )
            self.panel.target_view.setHtml(
                "<p style='color:#6d747d'>译文会显示在这里。</p>"
            )

    def _report_region(self) -> None:
        region = self.config.get("region")
        if not region:
            self.panel.set_region_text("未框选区域")
            self.region_frame.clear()
            return
        self.panel.set_region_text(
            f"区域 {region[2]}×{region[3]} @ ({region[0]}, {region[1]})"
        )
        self.region_frame.apply_theme(theme_mod.theme(self.config))
        self.region_frame.set_region(region)

    def _on_theme_previewed(self, values: dict) -> None:
        """设置对话框里改配色时，框选边框跟着一起变。"""
        self.region_frame.apply_theme(values)
        self.hover.set_theme(values)
        if self.dict_window is not None:
            self.dict_window._theme = values

    # ------------------------------------------------------------ 词典
    def open_dictionary(self, word: str = "") -> None:
        """打开（或唤到前面）独立的词典窗口。"""
        if self.dict_window is None:
            self.dict_window = DictWindow(self.config, self.dictionary, self.vocab)
            self.dict_window.vocabChanged.connect(self._refresh_vocab_pages)
        if word:
            self.dict_window.show_word(word)
        else:
            self.dict_window.show()
        self.dict_window.raise_()
        self.dict_window.activateWindow()

    def _on_vocab_toggled(self, word: str) -> None:
        self._refresh_vocab_pages()

    def _refresh_vocab_pages(self) -> None:
        if self.dict_window is not None:
            self.dict_window.page_vocab.refresh()
        if getattr(self.panel, "page_dict", None) is not None:
            self.panel.page_dict.render()

    def _poll_hover(self) -> None:
        """鼠标停在一个英文单词上超过 delay 就弹出词条卡片。

        这个定时器一直跑（120ms 一次，只是读一下鼠标位置和几个矩形判断），
        比装全局鼠标钩子干净得多，也不会被别的程序拦截。
        """
        if not (self.config.get("dict_enabled", True)
                and self.config.get("dict_hover", True)):
            return
        if not self.dictionary.available or not self._screen_blocks:
            self._hide_hover()
            return
        if self.selector.isVisible() or self.panel.isActiveWindow():
            self._hide_hover()
            return
        pos = QCursor.pos()
        word, rect, sentence = self._word_at(pos)
        if not word:
            self._hide_hover()
            return
        if word != self._hover_word or rect != self._hover_anchor:
            # 换了一个词：先记时间，停留够久才真的弹出来
            self._hover_word = word
            self._hover_anchor = rect
            self._hover_at = time.perf_counter()
            self.hover.hide_card()
            return
        if self.hover.isVisible():
            return
        delay = int(self.config.get("dict_hover_delay_ms") or 450)
        if (time.perf_counter() - self._hover_at) * 1000.0 < delay:
            return
        entry = self.dictionary.lookup(word)
        if entry is None:
            return
        self.hover.show_entry(entry, sentence, rect)

    def _hide_hover(self) -> None:
        self._hover_word = ""
        self._hover_anchor = None
        if self.hover.isVisible():
            self.hover.hide_card()

    def _word_at(self, pos) -> tuple[str, object, str]:
        """鼠标位置 → (单词, 它在屏幕上的矩形, 它所在的那行原文)。"""
        for text, rect in self._screen_blocks:
            if not rect.contains(pos):
                continue
            width = max(1, rect.width())
            ratio = (pos.x() - rect.left()) / width
            word = token_at(text, ratio)
            if word:
                return word, rect, text
        return "", None, ""

    def _update_screen_blocks(self, result: PipelineResult) -> None:
        """记下这一屏每段文字和它在**屏幕**上的位置（悬停取词要用）。

        OCR 给的是区域内的相对坐标，要加上区域左上角才是屏幕坐标。
        """
        region = self.config.get("region")
        if not region:
            self._screen_blocks = []
            return
        left, top = int(region[0]), int(region[1])
        blocks = []
        for block in (result.blocks or []):
            text = str((block or {}).get("text") or "").strip()
            box = (block or {}).get("box") or []
            if not text or len(box) < 2:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            rect = QRect(int(min(xs)) + left, int(min(ys)) + top,
                         max(1, int(max(xs) - min(xs))),
                         max(1, int(max(ys) - min(ys))))
            blocks.append((text, rect))
        self._screen_blocks = blocks

    def _apply_settings(self) -> None:
        interval = int(self.config.get("interval_ms") or 1500)
        self.timer.setInterval(interval)
        self._refresh_ms = max(0, int(self.config.get("refresh_ms") or 0))
        self.translator.set_provider_order(
            self.config.get("translate_providers") or list(DEFAULT_PROVIDER_ORDER)
        )
        setter = getattr(self.translator, "set_glossary_enabled", None)
        if callable(setter):
            setter(bool(self.config.get("use_glossary", True)))
        self.translator.keep_proper_nouns = bool(
            self.config.get("keep_proper_nouns", True))
        self.translator.protect_tokens = bool(self.config.get("protect_tokens", True))
        setter = getattr(self.translator, "set_llm_settings", None)
        if callable(setter):
            setter(self.config.get("llm_base_url") or "",
                   self.config.get("llm_api_key") or "",
                   self.config.get("llm_model") or "")
        self.region_frame.apply_theme(theme_mod.theme(self.config))
        if self.config.get("region"):
            self.region_frame.set_region(self.config.get("region"))
        # 词典：设置里刚下完词库的话，这里把已经打开的连接重新接上
        if not self.hover.isVisible():
            self._hide_hover()
        if bool(self.config.get("dict_enabled", True)):
            if not self.dictionary.available:
                self.dictionary.reopen()
            if getattr(self.panel, "page_dict", None) is not None:
                self.panel.page_dict.render()

    def _pipeline_settings(self) -> PipelineSettings:
        return PipelineSettings(
            use_dml=device_uses_dml(self.config),
            use_cls=bool(self.config.get("ocr_use_cls")),
            max_side=self.config.get("ocr_max_side"),
            min_score=float(self.config.get("ocr_min_score") or 0.5),
            target_lang=str(self.config.get("target_lang") or "zh-CN"),
            source_lang=str(self.config.get("source_lang") or ""),
            filter_cjk=bool(self.config.get("hide_cjk_blocks")),
            provider_order=self.config.get("translate_providers")
            or list(DEFAULT_PROVIDER_ORDER),
        )

    # ------------------------------------------------------------ 热键
    def _hotkey_toggle(self) -> None:
        print("[hotkey] 开关翻译")
        if not self.config.get("region"):
            self.panel.show()
            self.mark_region()
            return
        self.toggle()

    def toggle(self, force: bool | None = None) -> None:
        target = (not self._running) if force is None else force
        if target and not self.config.get("region"):
            self.mark_region()
            return
        self._running = target
        if self._running:
            self._force_next = True
            self._frame_count = 0
            self.timer.start()
        else:
            self.timer.stop()
            self.panel.status_label.setText("已暂停")
        self.panel.set_running(self._running)
        self.panel.show()

    # ------------------------------------------------------------ 框选
    def mark_region(self) -> None:
        was_running = self._running
        self.timer.stop()
        print("[selector] 进入框选模式")
        self.selector.start()
        try:
            self.selector.selected.disconnect()
        except Exception:
            pass
        self.selector.selected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._on_region_cancelled)
        self._was_running = was_running

    def _on_region_selected(self, left: int, top: int, width: int, height: int) -> None:
        self.config.set("region", [left, top, width, height])
        self._prev_frame = None
        self._force_next = True
        self._report_region()
        self.panel.status_label.setText(
            f"已框选 {width}×{height}，正在预热 OCR 模型…"
        )
        self.panel.show()
        self.toggle(True)

    def _on_region_cancelled(self) -> None:
        self.panel.status_label.setText("已取消框选")
        if getattr(self, "_was_running", False):
            self.toggle(True)

    # ------------------------------------------------------------ 主循环
    def _tick(self) -> None:
        region = self.config.get("region")
        if not region:
            self.timer.stop()
            self.panel.set_running(False)
            self._running = False
            return
        if self._busy:
            return
        frame = grab_region(tuple(region))
        if frame is None:
            self.panel.set_error("抓屏失败")
            return
        if not self._force_next:
            diff = mean_abs_diff(frame, self._prev_frame)
            threshold = float(self.config.get("change_threshold") or 0.002)
            # 只靠阈值会漏掉「只变了一个数字」：实测把 Round 1 改成 Round 2，
            # mean_abs_diff 只有 0.00019，远低于任何安全阈值。那样面板会永远
            # 停在旧内容上，看起来就像「不能实时翻译」。所以再加一道定时兜底：
            # 距上次识别超过 refresh_ms 就无条件重跑一次。重复的画面会命中翻译
            # 缓存，不会重复请求接口。
            stale = self._refresh_ms > 0 and (
                (time.perf_counter() - self._last_ocr_at) * 1000.0 >= self._refresh_ms
            )
            if diff < threshold and not stale:
                return  # 画面没变且没到兜底时间，跳过 OCR，省算力
        self._prev_frame = frame
        self._force_next = False
        self._last_ocr_at = time.perf_counter()
        self._busy = True
        self._busy_since = time.perf_counter()
        self._frame_count += 1
        self.panel.set_busy(True)
        self.worker.push(frame, self._pipeline_settings())

    def _drain_results(self) -> None:
        """主线程定时器：取出后台线程算好的结果并刷新面板。"""
        for kind, payload in self.worker.drain():
            if kind == "result":
                self._on_result(payload)
            else:
                self._on_failed(str(payload))
        # 自我保护：接口卡死时不能让「忙碌」状态永久锁住轮询
        if self._busy and self._busy_since:
            if time.perf_counter() - self._busy_since > 60.0:
                self._busy = False
                self._busy_since = 0.0
                self.panel.set_error("上一轮处理超时（已跳过）")

    def _on_result(self, result: PipelineResult) -> None:
        self._busy = False
        self._busy_since = 0.0
        self.panel.update_result(result)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self._busy_since = 0.0
        self.panel.set_error(f"处理失败：{message}")

    def _on_engine_ready(self, backend: str, seconds: float) -> None:
        self.panel.status_label.setText(f"OCR 引擎就绪：{backend}（{seconds:.1f}s）")

    # ------------------------------------------------------------ 退出
    def shutdown(self) -> None:
        """停止一切回调并等后台线程真正结束。

        必须等线程退干净再让 QApplication 析构：否则 QThread 对象在 Python 侧
        被回收时它还在跑，Qt 会打印
        `QThread: Destroyed while thread is still running` 并以非 0 码崩溃
        （0xC0000409）。这里先停定时器、再 quit + wait，最后兜底 terminate。
        """
        for timer in (getattr(self, "timer", None),
                      getattr(self, "result_timer", None)):
            if timer is not None:
                timer.stop()
        self.hotkeys.unregister_all()
        frame = getattr(self, "region_frame", None)
        if frame is not None:
            frame.clear()
        self.thread.quit()
        if not self.thread.wait(3000):
            print("[exit] 后台线程未在 3 秒内结束，强制终止")
            self.thread.terminate()
            self.thread.wait(1000)

    def run(self) -> int:
        code = self.app.exec()
        self.shutdown()
        return code


def main() -> int:
    set_app_user_model_id()
    app = TranslatorApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
