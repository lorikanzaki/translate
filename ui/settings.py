"""设置对话框：翻译行为 + 外观（配色 / 背景图 / 框选边框）。"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.translate import DEFAULT_PROVIDER_ORDER, PROVIDER_CLASSES, has_proxy_configured
from ui import theme as theme_mod
from ui.dictsetup import DictSetupPanel

PROVIDER_LABELS = {
    "llm": "大模型接口（质量最好，需 API key）",
    "volc": "火山翻译接口（短词最准，免费无 key）",
    "bing": "必应翻译中文站（短词全对，免费无 key）",
    "edge": "微软 Edge 接口（可批量，免费无 key）",
    "youdao": "有道 demo 接口（快，但会限流，已基本废弃）",
    "mymemory": "MyMemory（匿名每天 5000 字符，仅作兜底）",
    "google": "Google 接口（中国大陆直连不通，需代理）",
}

# 目标语言：翻成哪种语言。列表顺序按「中文用户最可能用的」排。
LANGUAGES = [
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁体中文"),
    ("en", "英语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("ru", "俄语"),
    ("de", "德语"),
    ("fr", "法语"),
    ("es", "西班牙语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("ar", "阿拉伯语"),
    ("th", "泰语"),
    ("vi", "越南语"),
    ("hi", "印地语"),
    ("id", "印尼语"),
]

# 源语言列表：屏幕上那块文字是什么语言。
# 「自动识别」用接口自己的语种判断（+ 内置 OCR 模型：拉丁字母 / 日语 / 中文）；
# 指定源语言有两个好处：接口不再靠猜（实测 Einstellungen 会被自动识别判成英语），
# 且 OCR 会换成对应语言的识别模型（韩语/俄语/阿拉伯语等，首次用自动下载）。
SOURCE_LANGS = [("auto", "自动识别（推荐）")] + [
    ("en", "英语"),
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁体中文"),
    ("ja", "日语"),
    ("ko", "韩语（首次会下载识别模型）"),
    ("ru", "俄语（首次会下载识别模型）"),
    ("uk", "乌克兰语（首次会下载识别模型）"),
    ("ar", "阿拉伯语（首次会下载识别模型）"),
    ("fa", "波斯语（首次会下载识别模型）"),
    ("th", "泰语（首次会下载识别模型）"),
    ("hi", "印地语（首次会下载识别模型）"),
    ("el", "希腊语（首次会下载识别模型）"),
    ("de", "德语"),
    ("fr", "法语"),
    ("es", "西班牙语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("nl", "荷兰语"),
    ("pl", "波兰语"),
    ("tr", "土耳其语"),
    ("vi", "越南语"),
]


def _normalize_color(value: str) -> str:
    red, green, blue = theme_mod.hex_to_rgb(value)
    return "#{:02x}{:02x}{:02x}".format(red, green, blue)


class ColorButton(QPushButton):
    """一块显示当前颜色的小按钮，点一下弹系统取色器。"""

    changed = Signal()

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = _normalize_color(color)
        self.setFixedSize(76, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._choose)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, value: str) -> None:
        self._color = _normalize_color(value)
        self._refresh()

    def _refresh(self) -> None:
        ink = "#000000" if theme_mod.is_light(self._color) else "#ffffff"
        self.setText(self._color.upper())
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; color: {ink};"
            " border: 1px solid #5a616b; border-radius: 6px;"
            " font-size: 11px; padding: 0px; }"
        )

    def _choose(self) -> None:
        picked = QColorDialog.getColor(theme_mod.qcolor(self._color), self,
                                       "选择颜色")
        if picked.isValid():
            self.set_color(picked.name())
            self.changed.emit()


class AlphaSlider(QWidget):
    """0-255 的透明度滑杆（数值越大越不透明）。"""

    changed = Signal()

    def __init__(self, value: int, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 255)
        self.slider.setValue(max(0, min(255, int(value))))
        self.value_label = QLabel(str(self.slider.value()))
        self.value_label.setFixedWidth(30)
        self.value_label.setStyleSheet("color: #8b929b; font-size: 11px;")
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, value: int) -> None:
        self.value_label.setText(str(value))
        self.changed.emit()

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int) -> None:  # noqa: N802
        self.slider.setValue(max(0, min(255, int(value))))


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, translator=None,
                 preview: Optional[Callable[[dict], None]] = None,
                 dictionary=None) -> None:
        super().__init__(parent)
        self.config = config
        self.translator = translator
        self._preview = preview
        self.dictionary = dictionary
        self._theme = theme_mod.theme(config)
        self.setWindowTitle("屏幕实时翻译 · 设置")
        self.setMinimumWidth(520)
        # 高度绝不能由内容决定：内容一多对话框就会比屏幕还高，
        # 底部的「确定/取消」就点不到了（两个标签页都已套滚动区）。
        self.resize(560, 640)
        self.setStyleSheet(
            "QDialog { background: #14161a; color: #d7dbe0; }"
            "QLabel { color: #b9bfc7; }"
            "QComboBox, QSpinBox, QListWidget, QLineEdit { background: #191c21;"
            " color: #d7dbe0; border: 1px solid #2c3138; border-radius: 6px;"
            " padding: 3px 6px; }"
            "QPushButton { background: #232830; color: #d7dbe0;"
            " border: 1px solid #333a44; border-radius: 7px; padding: 5px 12px; }"
            "QPushButton:hover { background: #2b313a; }"
            "QCheckBox { color: #b9bfc7; }"
            "QTabWidget::pane { border: 1px solid #2c3138; border-radius: 8px;"
            " top: -1px; padding: 10px; }"
            "QTabBar::tab { background: #1b1f24; color: #a9b0b9; padding: 6px 16px;"
            " border: 1px solid #2c3138; border-bottom: none;"
            " border-top-left-radius: 8px; border-top-right-radius: 8px; }"
            "QTabBar::tab:selected { background: #232830; color: #e6eaef; }"
            "QSlider::groove:horizontal { height: 4px; background: #2c3138;"
            " border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #6f7b8a; width: 12px;"
            " margin: -5px 0; border-radius: 6px; }"
            "QSlider::handle:horizontal:hover { background: #8b98a8; }"
        )
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scrollable(self._build_translate_tab()), "翻译")
        self.tabs.addTab(self._scrollable(self._build_dictionary_tab()), "词典")
        self.tabs.addTab(self._scrollable(self._build_appearance_tab()), "外观")
        layout.addWidget(self.tabs, 1)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        # QDialogButtonBox 的标准按钮文字跟着 Qt 的翻译文件走，本程序没有
        # 加载 qtbase_zh_CN.qm，于是按钮显示成英文的 OK / Cancel。这里直接写字。
        box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # -------------------------------------------------------- 翻译标签页
    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """把标签页塞进滚动区。

        大模型接口那一段加进来以后，翻译页在 1366×768 这类屏幕上会超出可用高度，
        QDialog 又会按内容撑高，结果对话框比屏幕还高、底部的按钮点不到。
        所以高度不能靠内容决定，一律可滚动。
        """
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(page)
        return area

    def _build_translate_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.interval = QSpinBox()
        self.interval.setRange(400, 10000)
        self.interval.setSingleStep(100)
        self.interval.setSuffix(" ms")
        self.interval.setValue(int(self.config.get("interval_ms") or 1500))
        self.interval.setToolTip("多久检查一次屏幕有没有变化。")
        form.addRow("刷新间隔", self.interval)

        self.refresh = QSpinBox()
        self.refresh.setRange(0, 60000)
        self.refresh.setSingleStep(500)
        self.refresh.setSuffix(" ms")
        self.refresh.setValue(int(self.config.get("refresh_ms") or 0))
        self.refresh.setToolTip(
            "画面看起来没变也强制重新识别一次的间隔（0 = 只在画面变化时识别）。\n"
            "画面只变了一个数字时，「变化量」小到测不出来，只靠阈值会因为\n"
            "判定为「没变」而一直不刷新 —— 这个兜底就是防这种情况。"
        )
        form.addRow("强制刷新", self.refresh)

        self.threshold = QSpinBox()
        self.threshold.setRange(1, 200)
        self.threshold.setSingleStep(5)
        self.threshold.setSuffix(" ‰")
        self.threshold.setValue(
            int(round(float(self.config.get("change_threshold") or 0.002) * 1000)))
        self.threshold.setToolTip(
            "画面变化多少才算「变了」。调小更灵敏（更耗电），调大更省算力。"
        )
        form.addRow("变化灵敏度", self.threshold)

        self.source = QComboBox()
        for code, label in SOURCE_LANGS:
            self.source.addItem(label, code)
        index = self.source.findData(self.config.get("source_lang") or "auto")
        self.source.setCurrentIndex(max(index, 0))
        self.source.setToolTip(
            "强制按某种语言翻译。自动识别偶尔会判错（比如把德语的\n"
            "Einstellungen 当成英语），明确知道屏幕上是哪国语言时选它更准。"
        )
        form.addRow("源语言", self.source)

        self.target = QComboBox()
        for code, label in LANGUAGES:
            self.target.addItem(label, code)
        current_target = self.config.get("target_lang") or "zh-CN"
        index = self.target.findData(current_target)
        self.target.setCurrentIndex(max(index, 0))
        form.addRow("目标语言", self.target)

        self.use_glossary = QCheckBox(
            "优先使用内置术语表（短词按固定译法，比在线接口准）"
        )
        self.use_glossary.setToolTip(
            "短词、菜单项先查内置的 1297 条术语表，命中就直接用，不消耗网络请求。")
        self.use_glossary.setChecked(bool(self.config.get("use_glossary", True)))
        form.addRow("", self.use_glossary)

        self.keep_proper = QCheckBox(
            "保留专有名词原文（GitHub、Python、PDF 这类词不翻译）"
        )
        self.keep_proper.setChecked(bool(self.config.get("keep_proper_nouns", True)))
        form.addRow("", self.keep_proper)

        # 说明放进 tooltip：勾选框文字太长会把整页撑宽，逼出横向滚动条
        self.protect_tokens = QCheckBox(
            "保护代码片段（路径、分支名、变量名不翻译）"
        )
        self.protect_tokens.setToolTip(
            "文件路径、git 分支名、变量名、版本号先挖成占位符再送接口，"
            "译文里原样放回。\n例：Branch not found: origin/main → 未找到分支：origin/main")
        self.protect_tokens.setChecked(bool(self.config.get("protect_tokens", True)))
        form.addRow("", self.protect_tokens)

        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.setSuffix(" %")
        self.min_score.setValue(int(round(float(self.config.get("ocr_min_score") or 0.5) * 100)))
        form.addRow("OCR 置信度下限", self.min_score)

        self.max_side = QSpinBox()
        self.max_side.setRange(600, 4000)
        self.max_side.setSingleStep(100)
        self.max_side.setSuffix(" px")
        self.max_side.setValue(int(self.config.get("ocr_max_side") or 1600))
        self.max_side.setToolTip(
            "送给 OCR 的图像最长边。调小能明显提速，但太小的字号会识别不出。"
        )
        form.addRow("OCR 最大边长", self.max_side)

        self.device = QComboBox()
        self.device.addItem("省内存（CPU）", "cpu")
        self.device.addItem("更快（显卡 DirectML）", "gpu")
        current = str(self.config.get("ocr_device") or "cpu").lower()
        index = self.device.findData("gpu" if current in ("gpu", "dml", "directml") else "cpu")
        self.device.setCurrentIndex(max(0, index))
        self.device.setToolTip(
            "同一张 281×412 的界面截图实测：\n"
            "· 省内存（CPU）：中位 276 ms，OCR 引擎约多占 48 MB\n"
            "· 更快（显卡 DirectML）：中位 109 ms（快 2.5 倍），"
            "但要多占约 300 MB 常驻内存，\n"
            "  而且 DirectML 占的内存直到程序退出都不会还回来。\n"
            "轮询间隔默认 1.5 秒，CPU 这边完全够用，所以默认选省内存。\n"
            "显卡不可用时会自动回落到 CPU。"
        )
        form.addRow("OCR 推理设备", self.device)

        self.filter_cjk = QCheckBox("跳过已经是目标语言的文本")
        self.filter_cjk.setChecked(bool(self.config.get("hide_cjk_blocks")))
        self.filter_cjk.setToolTip(
            "目标是中文时跳过中文块，目标是英语时跳过英文块。\n"
            "其它文字（日语假名、俄语西里尔、韩语谚文、阿拉伯语…）一律照翻。"
        )
        form.addRow("", self.filter_cjk)

        hotkeys = QLabel(
            f"热键：开关翻译 = {self.config.get('hotkey_toggle')}　·　"
            f"标记区域 = {self.config.get('hotkey_mark')}（可在配置文件中修改）"
        )
        hotkeys.setWordWrap(True)
        form.addRow("", hotkeys)
        layout.addLayout(form)

        layout.addWidget(QLabel("翻译接口优先顺序（取消勾选表示不用，箭头调整顺序）"))
        provider_row = QHBoxLayout()
        self.provider_list = QListWidget()
        self.provider_list.setFixedHeight(96)
        # 列表自己再出横向滚动条的话会把整页也撑宽，长文字改成右侧省略
        self.provider_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.provider_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        configured = [n for n in (self.config.get("translate_providers") or [])
                      if n in PROVIDER_CLASSES]
        for name in configured or list(DEFAULT_PROVIDER_ORDER):
            self._add_provider_item(name)
        # 配置里没有的接口也要能加回来，否则用户一旦删掉就永远选不到了。
        # 默认不勾选（opt-in）：比如大模型接口没填 key 时勾上也没用。
        for name in PROVIDER_CLASSES:
            if name not in configured:
                self._add_provider_item(name, checked=False)
        provider_row.addWidget(self.provider_list, 1)

        buttons_column = QVBoxLayout()
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_probe = QPushButton("测试接口")
        for button in (self.btn_up, self.btn_down, self.btn_probe):
            buttons_column.addWidget(button)
        buttons_column.addStretch(1)
        provider_row.addLayout(buttons_column)
        layout.addLayout(provider_row)

        self.probe_label = QLabel("")
        self.probe_label.setWordWrap(True)
        self.probe_label.setStyleSheet("color: #7f858d; font-size: 11px;")
        layout.addWidget(self.probe_label)

        layout.addWidget(self._section("大模型接口（勾选后才有用）"))
        llm_hint = QLabel(
            "通用机器翻译在软件界面上会直译：Now playing → 「正在演奏」、"
            "Shuffle all → 「全部洗牌」。大模型能按中文软件的习惯翻成"
            "「正在播放」「随机播放全部」。填一个 key 就能用，"
            "DeepSeek 的价位大约是每百万 token 一两块钱。"
        )
        llm_hint.setWordWrap(True)
        llm_hint.setStyleSheet("color: #7f858d; font-size: 11px;")
        layout.addWidget(llm_hint)

        llm_form = QFormLayout()
        llm_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.llm_base_url = QLineEdit(
            str(self.config.get("llm_base_url") or ""))
        self.llm_base_url.setPlaceholderText("https://api.deepseek.com/v1")
        self.llm_base_url.setToolTip(
            "任何 OpenAI 兼容的地址都行，填到 /v1 为止即可。\n"
            "本地 Ollama 用 http://localhost:11434/v1"
        )
        llm_form.addRow("接口地址", self.llm_base_url)

        self.llm_model = QLineEdit(str(self.config.get("llm_model") or ""))
        self.llm_model.setPlaceholderText("deepseek-chat")
        llm_form.addRow("模型", self.llm_model)

        key_row = QHBoxLayout()
        self.llm_api_key = QLineEdit(str(self.config.get("llm_api_key") or ""))
        self.llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_api_key.setPlaceholderText("sk-...")
        self.btn_show_key = QPushButton("显示")
        self.btn_show_key.setCheckable(True)
        self.btn_show_key.setFixedWidth(56)
        self.btn_show_key.toggled.connect(self._toggle_key_visible)
        key_row.addWidget(self.llm_api_key, 1)
        key_row.addWidget(self.btn_show_key)
        llm_form.addRow("API key", key_row)

        layout.addLayout(llm_form)
        llm_note = QLabel(
            "key 只保存在本机 %APPDATA%\\ScreenTranslator\\config.json 里，"
            "不会上传到别处。没填 key 时这一家会自动跳过，"
            "不影响其它的免费接口。"
        )
        llm_note.setWordWrap(True)
        llm_note.setStyleSheet("color: #7f858d; font-size: 11px;")
        layout.addWidget(llm_note)

        self.btn_clear_cache = QPushButton("清空翻译缓存")
        layout.addWidget(self.btn_clear_cache)
        layout.addStretch(1)

        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.btn_probe.clicked.connect(self._probe)
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        return page

    # -------------------------------------------------------- 词典标签页
    def _build_dictionary_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.dict_setup = DictSetupPanel(self.config, self.dictionary, page)
        self.dict_setup.statusMessage.connect(
            lambda text: self._preview_status(text))
        layout.addWidget(self.dict_setup)
        self._dict_changed = False
        self.dict_setup.libraryChanged.connect(self._on_library_changed)
        return page

    def _preview_status(self, text: str) -> None:
        # 设置对话框没有自己的状态栏，把消息丢给主窗口（preview 回调是主题用的，
        # 这里另走一条：直接写窗口标题不方便，就改成一个屏幕提示）
        self.setWindowTitle(f"屏幕实时翻译 · 设置 — {text}")

    def _on_library_changed(self) -> None:
        self._dict_changed = True

    # -------------------------------------------------------- 外观标签页
    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.preset = QComboBox()
        for name in theme_mod.PRESETS:
            self.preset.addItem(name, name)
        self.preset.addItem("自定义", "")
        form.addRow("配色预设", self.preset)

        self.panel_color = ColorButton(str(self._theme["panel_color"]))
        self.panel_alpha = AlphaSlider(int(self._theme["panel_alpha"]))
        form.addRow("面板底色", self._pair(self.panel_color, self.panel_alpha,
                                          "面板/标题栏/状态栏的底色与不透明度"))
        self.content_color = ColorButton(str(self._theme["content_color"]))
        self.content_alpha = AlphaSlider(int(self._theme["content_alpha"]))
        form.addRow("正文底色", self._pair(self.content_color, self.content_alpha,
                                          "原文/译文两块文本区的底色与不透明度"))
        self.accent_color = ColorButton(str(self._theme["accent"]))
        form.addRow("强调色", self.accent_color)
        self.text_color = ColorButton(str(self._theme["text"]))
        form.addRow("文字颜色", self.text_color)
        self.muted_color = ColorButton(str(self._theme["muted"]))
        form.addRow("次要文字", self.muted_color)
        self.border_color = ColorButton(str(self._theme["border"]))
        self.border_alpha = AlphaSlider(int(self._theme["border_alpha"]))
        form.addRow("窗口边框", self._pair(self.border_color, self.border_alpha,
                                          "窗口四周那条细边"))
        layout.addLayout(form)

        layout.addWidget(self._section("背景图片"))

        image_row = QHBoxLayout()
        self.bg_label = QLabel()
        self.bg_label.setWordWrap(True)
        self.bg_label.setStyleSheet("color: #8b929b; font-size: 11px;")
        image_row.addWidget(self.bg_label, 1)
        self.btn_bg_pick = QPushButton("选择图片…")
        self.btn_bg_clear = QPushButton("清除")
        image_row.addWidget(self.btn_bg_pick)
        image_row.addWidget(self.btn_bg_clear)
        layout.addLayout(image_row)

        bg_form = QFormLayout()
        bg_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.bg_fit = QComboBox()
        for code, label in theme_mod.BG_FITS:
            self.bg_fit.addItem(label, code)
        index = self.bg_fit.findData(str(self._theme["bg_fit"]))
        self.bg_fit.setCurrentIndex(max(index, 0))
        bg_form.addRow("填充方式", self.bg_fit)
        self.bg_alpha = AlphaSlider(int(self._theme["bg_alpha"]))
        bg_form.addRow("显示强度", self.bg_alpha)
        self.glass = QCheckBox(
            "保留磨砂玻璃（Real 磨砂会虚化底下的窗口，和背景图叠加时可能显脏）"
        )
        self.glass.setChecked(bool(self.config.get("window_glass", True)))
        bg_form.addRow("", self.glass)
        self.on_top = QCheckBox("窗口始终置顶（关掉后会被其他窗口盖住）")
        self.on_top.setChecked(bool(self.config.get("window_stay_on_top", True)))
        bg_form.addRow("", self.on_top)
        layout.addLayout(bg_form)

        layout.addWidget(self._section("框选翻译范围时的边框"))

        region_form = QFormLayout()
        region_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.region_enabled = QCheckBox(
            "框选后在该区域四周显示一圈边框，方便看清翻译的是哪一块"
        )
        self.region_enabled.setChecked(bool(self._theme["region_enabled"]))
        region_form.addRow("", self.region_enabled)
        self.region_color = ColorButton(str(self._theme["region_color"]))
        region_form.addRow("边框颜色", self.region_color)
        self.region_width = QSpinBox()
        self.region_width.setRange(1, 10)
        self.region_width.setSuffix(" px")
        self.region_width.setValue(int(self._theme["region_width"]))
        region_form.addRow("边框粗细", self.region_width)
        self.region_label = QCheckBox("在边框上显示区域大小（如 826 × 41）")
        self.region_label.setChecked(bool(self._theme["region_label"]))
        region_form.addRow("", self.region_label)
        layout.addLayout(region_form)

        hint = QLabel(
            "边框画在区域外面、鼠标可以穿透，所以不会挡住底下的软件，"
            "也不会被抓进 OCR。改完立刻生效，不用重启。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7f858d; font-size: 11px;")
        layout.addWidget(hint)

        self.btn_reset_theme = QPushButton("恢复默认外观")
        layout.addWidget(self.btn_reset_theme)
        self.btn_reset_theme.clicked.connect(self._reset_theme)

        # 接信号：任何一项改动都刷新预览
        for widget in (self.region_color, self.text_color, self.muted_color,
                       self.accent_color, self.panel_color, self.content_color,
                       self.border_color):
            widget.changed.connect(self._on_theme_changed)
        for widget in (self.panel_alpha, self.content_alpha, self.border_alpha,
                       self.bg_alpha):
            widget.changed.connect(self._on_theme_changed)
        for widget in (self.region_width,):
            widget.valueChanged.connect(self._on_theme_changed)
        for widget in (self.region_enabled, self.region_label):
            widget.toggled.connect(self._on_theme_changed)
        self.bg_fit.currentIndexChanged.connect(self._on_theme_changed)
        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        self.btn_bg_pick.clicked.connect(self._pick_background)
        self.btn_bg_clear.clicked.connect(self._clear_background)

        self._refresh_bg_label()
        return page

    @staticmethod
    def _section(title: str) -> QLabel:
        label = QLabel(title)
        label.setStyleSheet(
            "color: #e6eaef; font-size: 12px; font-weight: 600;"
            " margin-top: 8px; border-top: 1px solid #262b32; padding-top: 8px;"
        )
        return label

    @staticmethod
    def _pair(color_button: ColorButton, alpha: AlphaSlider,
              tip: str = "") -> QWidget:
        """颜色按钮 + 透明度滑杆拼成一行。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(color_button)
        row.addWidget(alpha, 1)
        if tip:
            holder.setToolTip(tip)
        return holder

    # -------------------------------------------------------- 主题交互
    def _collect_theme(self) -> dict:
        """把界面上的控件读成一份主题字典。"""
        return {
            "panel_color": self.panel_color.color(),
            "panel_alpha": self.panel_alpha.value(),
            "content_color": self.content_color.color(),
            "content_alpha": self.content_alpha.value(),
            "accent": self.accent_color.color(),
            "text": self.text_color.color(),
            "muted": self.muted_color.color(),
            "border": self.border_color.color(),
            "border_alpha": self.border_alpha.value(),
            "bg_image": str(self._theme.get("bg_image") or ""),
            "bg_fit": self.bg_fit.currentData(),
            "bg_alpha": self.bg_alpha.value(),
            "region_enabled": self.region_enabled.isChecked(),
            "region_color": self.region_color.color(),
            "region_width": self.region_width.value(),
            "region_label": self.region_label.isChecked(),
        }

    def _apply_theme_to_widgets(self, values: dict) -> None:
        self.panel_color.set_color(str(values["panel_color"]))
        self.panel_alpha.setValue(int(values["panel_alpha"]))
        self.content_color.set_color(str(values["content_color"]))
        self.content_alpha.setValue(int(values["content_alpha"]))
        self.accent_color.set_color(str(values["accent"]))
        self.text_color.set_color(str(values["text"]))
        self.muted_color.set_color(str(values["muted"]))
        self.border_color.set_color(str(values["border"]))
        self.border_alpha.setValue(int(values["border_alpha"]))
        index = self.bg_fit.findData(str(values["bg_fit"]))
        self.bg_fit.setCurrentIndex(max(index, 0))
        self.bg_alpha.setValue(int(values["bg_alpha"]))
        self.region_enabled.setChecked(bool(values["region_enabled"]))
        self.region_color.set_color(str(values["region_color"]))
        self.region_width.setValue(int(values["region_width"]))
        self.region_label.setChecked(bool(values["region_label"]))

    def _on_preset_changed(self) -> None:
        name = self.preset.currentData()
        if not name:
            return
        merged = theme_mod.apply_preset(self._theme, name)
        for key in theme_mod.DEFAULTS:
            merged.setdefault(key, theme_mod.DEFAULTS[key])
        # 预设只管颜色，不该顺手把用户选的背景图/边框开关清掉
        for key in ("bg_image", "bg_fit", "bg_alpha",
                    "region_enabled", "region_label", "region_width"):
            merged[key] = self._theme.get(key, theme_mod.DEFAULTS[key])
        self._apply_theme_to_widgets(merged)
        self._on_theme_changed()

    def _on_theme_changed(self) -> None:
        self._theme = self._collect_theme()
        self._refresh_bg_label()
        if self._preview is not None:
            self._preview(dict(self._theme))

    def _refresh_bg_label(self) -> None:
        path = str(self._theme.get("bg_image") or "")
        self.bg_label.setText(f"当前：{path}" if path else "当前：未设置（面板用纯色）")

    def _pick_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)",
        )
        if path:
            self._theme["bg_image"] = path
            self._on_theme_changed()

    def _clear_background(self) -> None:
        self._theme["bg_image"] = ""
        self._on_theme_changed()

    def _reset_theme(self) -> None:
        self._theme = theme_mod.theme(None)
        self._apply_theme_to_widgets(self._theme)
        # 挡住信号：否则 setCurrentIndex 会再触发一次 _on_preset_changed，
        # 用它手里那份旧主题把刚恢复的默认值又盖回去
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)
        self._on_theme_changed()

    # -------------------------------------------------------- 接口列表
    def _toggle_key_visible(self, shown: bool) -> None:
        self.llm_api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self.btn_show_key.setText("隐藏" if shown else "显示")

    def _add_provider_item(self, name: str, checked: bool = True) -> None:
        item = QListWidgetItem(PROVIDER_LABELS.get(name, name))
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked
                           else Qt.CheckState.Unchecked)
        self.provider_list.addItem(item)

    def _move(self, delta: int) -> None:
        row = self.provider_list.currentRow()
        if row < 0:
            return
        target = row + delta
        if not 0 <= target < self.provider_list.count():
            return
        item = self.provider_list.takeItem(row)
        self.provider_list.insertItem(target, item)
        self.provider_list.setCurrentRow(target)

    def _provider_order(self) -> List[str]:
        return [
            self.provider_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.provider_list.count())
            if self.provider_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _probe(self) -> None:
        if self.translator is None:
            return
        order = self._provider_order()
        if not order:
            self.probe_label.setText("请至少勾选一个翻译接口。")
            return
        self.translator.set_provider_order(order)
        if has_proxy_configured():
            self.probe_label.setText("检测到代理，正在测试（含 Google）…")
        else:
            self.probe_label.setText("正在测试…（未检测到代理，Google 接口会被跳过）")
        QApplication_process()
        report = self.translator.probe(
            target=str(self.target.currentData() or "zh-CN"),
            source=str(self.source.currentData() or ""),
        )
        lines = []
        for entry in report:
            mark = "✓" if entry["ok"] else "✗"
            if entry["ok"]:
                lines.append(f'{mark} {entry["label"]}  {entry["ms"]}ms  「{entry["sample"]}」')
            else:
                lines.append(f'{mark} {entry["label"]}  {entry["error"]}')
        self.probe_label.setText("\n".join(lines) or "没有可测试的接口。")

    def _clear_cache(self) -> None:
        if self.translator is not None:
            self.translator.clear_cache()
        self.probe_label.setText("翻译缓存已清空")

    # -------------------------------------------------------- 保存
    def _save(self) -> None:
        order = self._provider_order()
        if not order:
            QMessageBox.warning(self, "设置", "至少要保留一个翻译接口。")
            return
        self.config.update({
            "interval_ms": self.interval.value(),
            "refresh_ms": self.refresh.value(),
            "change_threshold": self.threshold.value() / 1000.0,
            "source_lang": self.source.currentData(),
            "target_lang": self.target.currentData(),
            "use_glossary": self.use_glossary.isChecked(),
            "keep_proper_nouns": self.keep_proper.isChecked(),
            "protect_tokens": self.protect_tokens.isChecked(),
            "ocr_min_score": self.min_score.value() / 100.0,
            "ocr_max_side": self.max_side.value(),
            "ocr_device": self.device.currentData(),
            "hide_cjk_blocks": self.filter_cjk.isChecked(),
            "translate_providers": order,
            "llm_base_url": self.llm_base_url.text().strip(),
            "llm_model": self.llm_model.text().strip(),
            "llm_api_key": self.llm_api_key.text().strip(),
            "window_glass": self.glass.isChecked(),
            "window_stay_on_top": self.on_top.isChecked(),
            "theme": self._collect_theme(),
        })
        if getattr(self, "dict_setup", None) is not None:
            self.config.update(self.dict_setup.values())
        if self.translator is not None:
            self.translator.set_provider_order(order)
            setter = getattr(self.translator, "set_llm_settings", None)
            if callable(setter):
                setter(self.llm_base_url.text().strip(),
                       self.llm_api_key.text().strip(),
                       self.llm_model.text().strip())
            setter = getattr(self.translator, "set_glossary_enabled", None)
            if callable(setter):
                setter(self.use_glossary.isChecked())
            self.translator.keep_proper_nouns = self.keep_proper.isChecked()
            self.translator.protect_tokens = self.protect_tokens.isChecked()
        self.accept()


def QApplication_process() -> None:
    """处理一次待办事件，让“正在测试…”能立刻显示出来。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.processEvents()
