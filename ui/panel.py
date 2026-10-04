"""主窗口：左侧原文、右侧译文，实时刷新。

外观是一个**真正的应用程序窗口**：
  * 无系统标题栏，改用自绘标题栏（状态胶囊 + 操作按钮 + 最小化/最大化/关闭）
  * 四边四角可拖动缩放（走 Windows 原生缩放，见 ui\\frameless.py）
  * 磨砂玻璃背景 + 细边框（见 ui\\win11.py）
  * 配色、背景图、框选边框全部可调（见 ui\\theme.py，样式表是模板生成的）

内容区刻意**不透明**：磨砂玻璃只作用在标题栏、状态栏和边框上。
如果连正文底色也半透明，桌面壁纸会透上来，小号中文笔画会糊掉，
实测非常难读 —— 用户自定义背景图时同理，正文底色默认 alpha 235。
"""
from __future__ import annotations

import html
from typing import List, Optional

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.translate import Translator
from core.worker import PipelineResult
from ui import dictpage as dictpage_mod
from ui import theme as theme_mod
from ui.dictpage import DictPage
from ui.frameless import FramelessWindow
from ui.settings import SettingsDialog


class MainPanel(FramelessWindow):
    """主窗口。所有耗时工作都在 TranslateWorker 里做。"""

    markRequested = Signal()
    settingsChanged = Signal()
    quitRequested = Signal()
    minimizeRequested = Signal()
    # 「词典」页里点「独立窗口」
    dictWindowRequested = Signal()
    # 设置对话框里调配色时实时广播，让 main.py 把框选边框也一起刷新
    themePreviewed = Signal(dict)

    def __init__(self, config, translator: Translator, dictionary=None,
                 vocab=None) -> None:
        super().__init__(None)
        self.config = config
        self.translator = translator
        self.dictionary = dictionary
        self.vocab = vocab
        self._running = False
        self._busy = False
        self._pairs: List[tuple[str, str]] = []
        # 上一次画进两个正文栏的内容指纹；一样就不重渲染（见 update_result）
        self._render_key: object = None
        self._last_result: Optional[PipelineResult] = None
        self._dict_key: object = None
        self._glass_pending = bool(self.config.get("window_glass", True))
        # 主题要在 _build() 之前读好，_make_text_view / _style_splitter_handles
        # 建控件时就要用它的颜色。
        self._theme = theme_mod.theme(config)

        self.setMinimumSize(460, 320)
        self.resize(780, 540)
        self.setStyleSheet(theme_mod.panel_style(self._theme))
        self._build()
        self._restore_geometry()
        self._apply_stay_on_top(bool(self.config.get("window_stay_on_top", True)))

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = QFrame(self)
        root.setObjectName("panelRoot")
        self.root_frame = root
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(9)

        layout.addWidget(self._build_title_bar())
        layout.addWidget(self._build_content(), 1)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusBar")
        layout.addWidget(self.status_label)
        if self.page_dict is not None:
            self.page_dict.statusMessage.connect(self.status_label.setText)

    def _build_title_bar(self) -> QWidget:
        self.title_bar = QWidget()
        self._use_glass_background(self.title_bar)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(32)
        bar = QHBoxLayout(self.title_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)

        self.title_label = QLabel("屏幕实时翻译")
        self.title_label.setObjectName("titleLabel")
        self.state_pill = QLabel("已停止")
        self.state_pill.setObjectName("pill")
        self.hint_label = QLabel("未框选区域")
        self.hint_label.setObjectName("hintLabel")

        bar.addWidget(self.title_label)
        bar.addWidget(self.state_pill)
        bar.addWidget(self.hint_label, 1)

        self.btn_pause = QPushButton("开始")
        self.btn_pause.setObjectName("primary")
        self.btn_mark = QPushButton("标记区域")
        self.btn_settings = QPushButton("设置")

        # --- 窗口按钮：最小化 / 最大化-还原 / 关闭 ---
        self.btn_min = self._win_button("—", "最小化", "winBtn")
        self.btn_max = self._win_button("□", "最大化 / 还原", "winBtn")
        self.btn_close = self._win_button("✕", "关闭窗口", "winClose")
        self.btn_min.clicked.connect(self._minimize)
        self.btn_max.clicked.connect(self._toggle_maximize)
        self.btn_close.clicked.connect(self.close)

        for button in (self.btn_pause, self.btn_mark, self.btn_settings):
            bar.addWidget(button)
        bar.addSpacing(6)
        for button in (self.btn_min, self.btn_max, self.btn_close):
            bar.addWidget(button)
        return self.title_bar

    @staticmethod
    def _win_button(text: str, tip: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setToolTip(tip)
        button.setFixedSize(32, 24)
        button.setFlat(True)
        return button

    def _toggle_maximize(self) -> None:
        self.toggle_maximize()

    def _minimize(self) -> None:
        """先让 main.py 保存位置，再最小化（最小化后 geometry 仍可读，
        但把保存动作放在前面更稳妥，也方便外部接管）。"""
        self._save_geometry()
        self.minimizeRequested.emit()
        self.showMinimized()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            maximized = self.is_maximized()
            self.btn_max.setText("❐" if maximized else "□")
            # 属性名用 winMaximized，不能用 maximized：后者是 QWidget 的内建
            # 只读属性，写入会被静默丢弃（见 ui/theme.py 里 _QSS 的注释）。
            self.root_frame.setProperty("winMaximized", maximized)
            self.root_frame.style().unpolish(self.root_frame)
            self.root_frame.style().polish(self.root_frame)
            self.update()

    # ------------------------------------------------------------ 背景图
    def _corner_radius(self) -> int:
        """跟样式表里 #panelRoot 的 border-radius 保持一致。

        最大化时样式表会把圆角去掉（`[winMaximized="true"]`），背景图的
        裁剪半径也要跟着变，否则四角会露出一块背景图。
        """
        return 0 if self.is_maximized() else 10

    def paintEvent(self, event) -> None:  # noqa: N802
        """把用户选的背景图画在面板底下（标题栏/正文/状态栏都盖在它上面）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = self._corner_radius()
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(path)
        theme_mod.draw_background(painter, self.rect(), self._theme)
        painter.end()
        super().paintEvent(event)

    # ------------------------------------------------------------ 内容区
    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        return label

    def _make_text_view(self) -> QTextBrowser:
        """建一个正文视图（底色/文字颜色由主题决定）。

        QTextBrowser 继承 QAbstractScrollArea，内容区是独立的 viewport 控件，
        Qt 样式表里的 QTextBrowser 规则只会染到外框；viewport 继续用调色板的
        默认底色（实测是 #FFFFFF，于是深色面板里嵌了一块刺眼的白底，
        浅色中文几乎不可读）。Qt 样式表对 QAbstractScrollArea 的 viewport
        换肤支持有限，这里直接改调色板 + 自动填充，最可靠。
        """
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setFrameShape(QFrame.Shape.NoFrame)
        self._style_text_view(view)
        return view

    def _style_text_view(self, view: QTextBrowser) -> None:
        """按主题刷新正文视图的底色与文字色。"""
        background = theme_mod.qcolor(str(self._theme["content_color"]),
                                      int(self._theme["content_alpha"]))
        palette = view.palette()
        palette.setColor(QPalette.ColorRole.Base, background)
        palette.setColor(QPalette.ColorRole.Window, background)
        palette.setColor(QPalette.ColorRole.Text,
                         theme_mod.qcolor(str(self._theme["text"])))
        view.setPalette(palette)
        view.viewport().setAutoFillBackground(True)
        view.viewport().setPalette(palette)
        view.viewport().update()

    @staticmethod
    def _use_glass_background(widget: QWidget) -> None:
        """让普通容器控件透出父级的底色/背景图。

        Qt 的普通 QWidget 默认会用调色板的 Window 色给自己填底（本机实测是
        不透明白色 RGB(243,243,243)），QSS 没显式指定 background 的容器就会
        露出这块白底。实测面板正中一条 5px 竖缝（分割条位置）整条白，
        就是 QSplitter 里两个普通 QWidget 容器画的。
        """
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        widget.setPalette(palette)
        widget.setAutoFillBackground(False)

    def _style_splitter_handles(self, splitter: QSplitter) -> None:
        """给分割条单独设置样式。

        面板级样式表里的 `QSplitter::handle` 规则实测会被静默丢弃（handle 会
        退回调色板的白底，面板正中留一条 4px 白缝），但直接设在 handle 控件
        上的同一条规则立刻生效。所以这里逐个 handle 设。
        """
        rule = theme_mod.splitter_handle_style(self._theme)
        for index in range(1, splitter.count()):
            handle = splitter.handle(index)
            if handle is not None:
                handle.setStyleSheet(rule)

    def _build_content(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_translate_tab(), "翻译")
        self.page_dict = None
        if self.dictionary is not None and self.vocab is not None:
            self.page_dict = DictPage(self.config, self.dictionary, self.vocab,
                                      theme=self._theme)
            self.page_dict.openWindowRequested.connect(self.dictWindowRequested.emit)
            self.tabs.addTab(self.page_dict, "词典")
        return self.tabs

    def _build_translate_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        self.splitter = splitter

        source_box = QWidget()
        self._use_glass_background(source_box)
        source_layout = QVBoxLayout(source_box)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(4)
        source_layout.addWidget(self._section_label("原文（屏幕识别结果）"))
        self.source_view = self._make_text_view()
        source_layout.addWidget(self.source_view)

        target_box = QWidget()
        self._use_glass_background(target_box)
        target_layout = QVBoxLayout(target_box)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(4)
        target_layout.addWidget(self._section_label("译文（实时翻译）"))
        self.target_view = self._make_text_view()
        target_layout.addWidget(self.target_view)

        splitter.addWidget(source_box)
        splitter.addWidget(target_box)
        splitter.setSizes([390, 390])
        self._style_splitter_handles(splitter)

        # --- 事件 ---
        self.btn_pause.clicked.connect(self.toggle_running)
        self.btn_mark.clicked.connect(self.markRequested.emit)
        self.btn_settings.clicked.connect(self.open_settings)

        self._apply_font()
        return splitter

    def _apply_font(self) -> None:
        size = int(self.config.get("panel_font_size") or 12)
        font = QFont("Microsoft YaHei UI", size)
        for view in (self.source_view, self.target_view):
            view.setFont(font)
            view.document().setDefaultStyleSheet(
                "p { margin: 0 0 6px 0; line-height: 150%; }"
            )

    # ------------------------------------------------------------ 外观设置
    def _apply_stay_on_top(self, on_top: bool) -> None:
        self.set_stay_on_top(on_top)
        self.btn_close.setToolTip("关闭窗口（程序继续在后台等热键）")

    def _glass_kwargs(self) -> dict:
        return {
            "tint": theme_mod.acrylic_tint(self._theme),
            "dark": theme_mod.dark_title_bar(self._theme),
        }

    def apply_appearance(self) -> None:
        """把配置里的外观项应用到窗口（设置对话框保存后调用）。"""
        self._theme = theme_mod.theme(self.config)
        self.setStyleSheet(theme_mod.panel_style(self._theme))
        self._apply_font()
        for view in (self.source_view, self.target_view):
            self._style_text_view(view)
        self._style_splitter_handles(self.splitter)
        if self.page_dict is not None:
            self.page_dict.set_theme(self._theme)
        self._apply_stay_on_top(bool(self.config.get("window_stay_on_top", True)))
        self._glass_pending = bool(self.config.get("window_glass", True))
        self.enable_glass(self._glass_pending, **self._glass_kwargs())
        self.update()

    def preview_theme(self, values: dict) -> None:
        """设置对话框里调颜色的实时预览（还没保存就能看到效果）。"""
        self._theme = theme_mod.theme(values)
        self.setStyleSheet(theme_mod.panel_style(self._theme))
        for view in (self.source_view, self.target_view):
            self._style_text_view(view)
        self._style_splitter_handles(self.splitter)
        if self.page_dict is not None:
            self.page_dict.set_theme(self._theme)
        self.enable_glass(self._glass_pending, **self._glass_kwargs())
        self.update()
        self.themePreviewed.emit(dict(self._theme))

    @property
    def theme_settings(self) -> dict:
        """当前生效的主题（main.py 拿去刷框选边框）。"""
        return dict(self._theme)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 窗口句柄要 show() 之后才存在，玻璃只能在这里首次应用。
        # 但 showEvent 触发时窗口往往还没被 DWM 真正映射，此时
        # DwmSetWindowAttribute 对「暗色标题栏 / 圆角」会直接返回失败
        # （实测真实启动时拿到 dark_title=False, rounded=False，
        #  而 show() + processEvents() 之后再调用三项全 True）。
        # 所以推到事件循环下一轮再上玻璃，失败就换时机重试几次。
        QTimer.singleShot(0, self._apply_glass_now)

    def _apply_glass_now(self, attempt: int = 0) -> None:
        """上磨砂玻璃；对「窗口还没映射」导致的失败自动重试。"""
        if not self._glass_pending:
            return
        result = self.enable_glass(True, **self._glass_kwargs())
        # offscreen 平台没有真 HWND，apply_glass 会直接报 supported=False，
        # 这种是环境限制而不是时序问题，重试没意义。
        if not result.get("supported"):
            return
        if QGuiApplication.platformName().startswith("offscreen"):
            return
        if all(result.get(k) for k in ("acrylic", "dark_title", "rounded")):
            return
        if attempt >= 4:
            print("[glass] 多次重试仍有项目未生效，保留当前效果（不影响功能）")
            return
        QTimer.singleShot(90 * (attempt + 1),
                          lambda: self._apply_glass_now(attempt + 1))

    # ------------------------------------------------------------ 位置
    def _restore_geometry(self) -> None:
        stored = self.config.get("panel_geometry")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        if isinstance(stored, list) and len(stored) == 4:
            rect = QRect(*[int(v) for v in stored])
            if rect.intersects(available):
                self.setGeometry(rect)
                return
        self.move(available.right() - self.width() - 40, available.top() + 60)

    def _save_geometry(self) -> None:
        if self.is_maximized():
            return
        geo = self.geometry()
        self.config.set("panel_geometry", [geo.x(), geo.y(), geo.width(), geo.height()])

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        super().closeEvent(event)

    # ------------------------------------------------------------ 状态
    def set_region_text(self, text: str) -> None:
        self.hint_label.setText(text)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.btn_pause.setText("暂停" if running else "开始")
        self._set_pill("运行中" if running else "已停止", "pillOn" if running else "pill")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._set_pill("识别中…", "pillBusy")
        else:
            self._set_pill("运行中" if self._running else "已停止",
                           "pillOn" if self._running else "pill")

    def set_error(self, message: str) -> None:
        self._set_pill("出错", "pillErr")
        self.status_label.setText(message)

    def _set_pill(self, text: str, object_name: str) -> None:
        self.state_pill.setText(text)
        self.state_pill.setObjectName(object_name)
        self.state_pill.style().unpolish(self.state_pill)
        self.state_pill.style().polish(self.state_pill)

    def toggle_running(self) -> None:
        if not self.config.get("region"):
            self.markRequested.emit()
            return
        self.set_running(not self._running)
        if not self._running:
            self.status_label.setText("已暂停")

    # ------------------------------------------------------------ 刷新结果
    def update_result(self, result: PipelineResult) -> None:
        self._last_result = result
        self.set_busy(False)
        self._update_dictionary(result)
        # provider 为空说明整批都没翻成；否则只是部分条目有问题（如超长被跳过），
        # 这时不该在状态栏喊「翻译失败」。
        if result.error and not result.provider:
            self.status_label.setText(f"翻译失败：{result.error}")
        elif result.error:
            self.status_label.setText(f"部分未翻译：{result.error}")
        muted = f"color:{self._theme.get('muted') or '#8b939c'}"
        if not result.lines:
            # 识别到了、只是不用翻译：把原文灰字列出来。区域里全是中文时，
            # 只显示「没有检测到外语」会让用户以为程序根本没在识别。
            texts = [str((b or {}).get("text") or "").strip()
                     for b in (result.blocks or [])]
            texts = [t for t in texts if t]
            if texts:
                self.source_view.setHtml("".join(
                    f'<p style="{muted}">{html.escape(t)}</p>' for t in texts))
                self.target_view.setHtml(
                    f'<p style="{muted}">（识别到 {len(texts)} 段文字，'
                    f"但都不需要翻译：已经是中文，或没有可翻译的字母）</p>"
                )
                self._pairs = []
                self._render_key = ("skipped", tuple(texts))
            else:
                self.source_view.setHtml(
                    f"<p style='{muted}'>（这一屏没有识别到文字）</p>")
                self.target_view.setHtml(f"<p style='{muted}'>—</p>")
                self._pairs = []
                self._render_key = ("empty",)
            self._update_status_line(result, 0)
            return

        pairs = list(zip(result.lines, result.translations))
        # 强制刷新（refresh_ms）会周期性重跑 OCR，内容没变的话结果完全一样。
        # 这时不重设 HTML：否则每几秒闪一次，用户选中的文字也会被清掉。
        key = ("pairs", tuple(pairs))
        if key == self._render_key:
            self._pairs = pairs
            self._update_status_line(result, len(pairs))
            return
        self._render_key = key
        self._pairs = pairs
        source_html = "".join(
            f"<p>{html.escape(src)}</p>" for src, _ in pairs
        )
        missing_mark = f'<span style="{muted}">（未翻译）</span>'
        target_html = "".join(
            f"<p>{html.escape(dst) if dst else missing_mark}</p>"
            for _, dst in pairs
        )
        self.source_view.setHtml(source_html)
        self.target_view.setHtml(target_html)
        self._update_status_line(result, len(pairs))

    def _update_dictionary(self, result: PipelineResult) -> None:
        """把这一屏识别到的文字喂给「词典」页。

        用 `result.lines`（要翻译的行）加上 `result.blocks` 里被跳过的原文，
        因为跳过中文的规则会顺手把英文单词也筛掉一部分，词典不该跟着漏。
        """
        if self.page_dict is None or self.dictionary is None:
            return
        if not self.dictionary.available:
            return
        texts = list(result.lines or [])
        for block in (result.blocks or []):
            text = str((block or {}).get("text") or "").strip()
            if text and text not in texts:
                texts.append(text)
        joined = "\n".join(texts)
        if not joined:
            return
        try:
            entries = self.dictionary.lookup_sentence(joined)
        except Exception as exc:                      # 词典坏了不能拖垮翻译
            print(f"[dict] 查词失败：{exc}")
            return
        self._dict_key = joined
        self.page_dict.set_screen(joined, entries)

    def _update_status_line(self, result: PipelineResult, count: int) -> None:
        parts = [
            f"{count} 段",
            f"OCR {result.ocr_ms:.0f}ms",
        ]
        if result.translate_ms:
            parts.append(f"翻译 {result.translate_ms:.0f}ms")
        parts.append(f"合计 {result.total_ms:.0f}ms")
        if result.backend:
            parts.append(result.backend)
        if result.ocr_model and "内置" not in result.ocr_model:
            # 只有换了语言包才显示，免得状态栏天天挂着「内置识别模型」
            parts.append(result.ocr_model)
        if result.provider:
            parts.append(f"via {result.provider}")
        if result.from_phrasebook:
            parts.append(f"术语表 {result.from_phrasebook}")
        parts.append(f"缓存 {self.translator.cache_size()}")
        self.status_label.setText(" · ".join(parts))

    # ------------------------------------------------------------ 设置
    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self, translator=self.translator,
                                preview=self.preview_theme,
                                dictionary=self.dictionary)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_appearance()
            self.settingsChanged.emit()
            self.status_label.setText("设置已保存")
        else:
            # 用户取消：把实时预览过的临时配色退回配置里的值
            self.apply_appearance()
            self.themePreviewed.emit(dict(self._theme))
