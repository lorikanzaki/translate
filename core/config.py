"""配置持久化：region / panel geometry / 热键 / 引擎开关等。"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List

from core.translate import DEFAULT_PROVIDER_ORDER

APP_NAME = "ScreenTranslator"

# 旧版本把 bing/google 放在链首；这两个实测已失效（auth 接口 404、
# bing.com/ttranslatev3 返回空体），见到就整体换成新链路，否则用户会
# 一直用着永远失败的接口。
STALE_PROVIDER_ORDERS: List[List[str]] = [
    ["bing", "google", "mymemory"],
    ["bing", "mymemory", "google"],
    ["google", "bing", "mymemory"],
    # 早期版本（火山/必应中文站接入之前）的默认链。这些接口要么短词质量差
    # （edge 把 Copy 翻成「收到」），要么已被官方废弃（youdao demo 第 5 条起
    # 411），见到就升级到新链，否则用户永远享受不到新接口。
    ["edge", "mymemory", "youdao"],
    ["edge", "mymemory", "youdao", "google"],
    ["edge", "youdao", "mymemory"],
    ["youdao", "edge", "mymemory"],
    # 只活了很短一版：必应一上机就把火山挤到第二位，这个顺序没发出去过，
    # 但万一有人中途跑过开发版，也顺手帮他换掉。
    ["volc", "bing", "edge", "mymemory"],
]

DEFAULTS: Dict[str, Any] = {
    # 抓取区域，物理像素，绝对屏幕坐标；None 表示尚未框选
    "region": None,
    # 面板几何（逻辑像素）
    "panel_geometry": None,
    # 轮询间隔（毫秒）
    "interval_ms": 1500,
    # 屏幕内容变化阈值（0-1，越大越不敏感）
    "change_threshold": 0.002,
    # 强制刷新间隔（毫秒，0 = 只在画面变化时识别）。
    # 只靠 change_threshold 会漏掉「只变了一个数字」（实测 mean_abs_diff 仅 0.00019，
    # 远低于任何安全阈值），面板就再也不刷新，看起来像坏了。所以每隔这么久
    # 无条件重跑一次 OCR。翻译走缓存，重复的屏不会重复请求接口。
    "refresh_ms": 4000,
    # OCR
    "ocr_engine": "rapidocr",
    # 推理设备："cpu" 省内存（默认），"gpu" 用 DirectML 走显卡。
    # 实测同一张 281x412 的界面截图：GPU 中位 109ms / CPU 中位 276ms（快 2.5 倍），
    # 但 GPU 要多占约 300 MB 常驻内存，而且删掉引擎对象也**不还回来**
    # （DirectML 的显存/内存分配在进程退出前一直挂着）。轮询间隔是 1.5 秒，
    # 276ms 完全够用，所以默认选省内存这一边。
    "ocr_device": "cpu",
    "ocr_use_cls": False,
    "ocr_min_score": 0.5,
    "ocr_max_side": 1600,
    # 翻译
    "translate_providers": list(DEFAULT_PROVIDER_ORDER),
    "target_lang": "zh-CN",
    "source_lang": "auto",
    "translate_timeout": 15,
    # 大模型接口（可选，质量最好）。留空 key 时这一家会被自动跳过。
    # DeepSeek 兼容 OpenAI 协议，所以 base_url 直接指到 /v1 即可。
    "llm_api_key": "",
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_model": "deepseek-chat",
    # 翻译质量
    "use_glossary": True,          # 短词/菜单项优先查内置术语表
    "keep_proper_nouns": True,     # GitHub / Python / PDF 这类专名保留原文
    "protect_tokens": True,        # 路径/git 引用/变量名/版本号先挖成占位符再翻译
    # 面板
    "panel_opacity": 0.94,
    "panel_font_size": 12,
    "show_original": True,
    # 窗口外观
    "window_glass": True,          # 磨砂玻璃（Win10 1803+ 亚克力）
    "window_stay_on_top": True,    # 窗口置顶
    # 配色 / 背景图 / 框选边框。这里刻意留空字典：默认值由 ui/theme.py 的
    # DEFAULTS 负责，core 层不该反过来 import ui 层（会绕成循环依赖），
    # ui.theme.theme() 会把空字典跟自己的默认值合并。
    "theme": {},
    # 热键
    "hotkey_toggle": "ctrl+alt+t",
    "hotkey_mark": "ctrl+alt+z",
    # 行为
    "hide_cjk_blocks": True,
    "pause_when_minimized": True,
    # 词典（本地 ECDICT 词库，见 core/dictdb.py）
    "dict_enabled": True,          # 总开关：关掉就不建词典、不喂词
    "dict_hover": True,            # 鼠标停在屏幕上的单词上时弹词条卡片
    "dict_hover_delay_ms": 450,    # 鼠标停多久才弹（太短会一直闪）
    "dict_hover_font_size": 10,
    "dict_hover_width": 280,
}


def device_uses_dml(config: "Config | Dict[str, Any] | None" = None) -> bool:
    """配置里的推理设备是「走显卡」吗？

    接受 Config 对象、配置字典或 None。取值 `"gpu"`（也认 `"dml"` /
    `"directml"`）走 DirectML；其余（`"cpu"`、空、认不出来的词）都走 CPU ——
    **认不出来时偏向省内存**，因为 OcrEngine 自己会在 GPU 不可用时回落 CPU，
    反过来则会白白多占几百 MB。
    """
    if config is None:
        value = DEFAULTS.get("ocr_device", "cpu")
    elif isinstance(config, dict):
        value = config.get("ocr_device", DEFAULTS.get("ocr_device", "cpu"))
    else:
        value = config.get("ocr_device") or DEFAULTS.get("ocr_device", "cpu")
    return str(value).strip().lower() in ("gpu", "dml", "directml")


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


class Config:
    """线程安全的极简配置存储。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or config_path()
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                with self._lock:
                    for key, value in stored.items():
                        self._data[key] = value
                    self._migrate()
        except FileNotFoundError:
            pass
        except Exception as exc:  # 配置损坏不应影响启动
            print(f"[config] 读取失败，使用默认值: {exc}")
        return self._data

    def _migrate(self) -> None:
        """把已失效的旧配置就地升级（调用方需已持有锁）。"""
        order = self._data.get("translate_providers")
        if isinstance(order, list) and order in STALE_PROVIDER_ORDERS:
            self._data["translate_providers"] = list(DEFAULT_PROVIDER_ORDER)
            print("[config] 检测到失效的翻译接口配置（google 直连不通、"
                  "youdao demo 已限流），"
                  f"已重置为 {DEFAULT_PROVIDER_ORDER}")

        # 旧版本默认 8 秒。实测主接口 edge 0.6~1.6s，但降级到 MyMemory 时
        # 要 2s+，8 秒在弱网下容易整批失败，这里把没手动调过的旧值抬到 15。
        if self._data.get("translate_timeout") == 8:
            self._data["translate_timeout"] = DEFAULTS["translate_timeout"]
            print(f"[config] 翻译超时 8 秒偏紧，已调整为 "
                  f"{DEFAULTS['translate_timeout']} 秒")

        # 0.004 是旧版本的默认变化阈值。实测画面只改一个数字时
        # mean_abs_diff 只有 0.00019，旧阈值会把这种变化整个漏掉。
        # 没手动改过的旧值降到 0.002（真正兜底的是 refresh_ms）。
        if self._data.get("change_threshold") == 0.004:
            self._data["change_threshold"] = DEFAULTS["change_threshold"]
            print(f"[config] 变化阈值 0.004 太钝（只改一个数字时测不出来），"
                  f"已调整为 {DEFAULTS['change_threshold']}")

        # 旧配置用布尔值 ocr_use_dml 表示走不走显卡。实测走 DirectML 要多占
        # 约 300 MB 常驻内存（而且删掉引擎也不还给系统），只换来 2.5 倍速度，
        # 而轮询间隔是 1.5 秒 —— 所以统一改成省内存的 CPU，需要快的可以在
        # 设置里改回「更快（显卡 DirectML）」。
        if "ocr_use_dml" in self._data:
            legacy = bool(self._data.pop("ocr_use_dml"))
            if legacy and self._data.get("ocr_device", "cpu") == "cpu":
                self._data["ocr_device"] = "cpu"
                print("[config] OCR 推理设备改为「省内存（CPU）」："
                      "DirectML 要多占约 300 MB 常驻内存，只快 2.5 倍。"
                      "想要速度可在「设置 → 翻译」里改回「更快（显卡 DirectML）」")

    def save(self) -> None:
        with self._lock:
            snapshot = dict(self._data)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as exc:
            print(f"[config] 保存失败: {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any, autosave: bool = True) -> None:
        with self._lock:
            self._data[key] = value
        if autosave:
            self.save()

    def update(self, mapping: Dict[str, Any], autosave: bool = True) -> None:
        with self._lock:
            self._data.update(mapping)
        if autosave:
            self.save()

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
