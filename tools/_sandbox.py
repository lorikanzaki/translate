"""给「只是看看 / 只是截图 / 只是测内存」的脚本用的沙箱配置。

**为什么需要它**：`ui.frameless.FramelessWindow` 会在移动/缩放时把窗口几何
写回 `Config`，而 `Config` 直接落在 `%APPDATA%\\ScreenTranslator\\config.json`。
于是任何一句 `panel.show()` 都会悄悄改掉用户的配置 —— 更糟的是
`tools\\_shot_dict.py` 早期版本还写过 `config.set("region", None)`，
**把用户框好的识别区域清掉了**。

这里的做法：把用户真实配置**复制**一份到临时目录，脚本在副本上随便折腾，
真配置一个字节都不动。副本保留了用户的配色/背景图，所以截图观感不变。
"""
from __future__ import annotations

import os
import shutil
import tempfile


def sandbox_config():
    """返回一个指向临时副本的 `Config`（带用户现有设置）。"""
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from core.config import Config, config_path

    path = os.path.join(tempfile.mkdtemp(prefix="translate-cfg-"), "config.json")
    try:
        shutil.copy(config_path(), path)
    except OSError:
        pass  # 还没有配置文件就用默认值
    return Config(path)


def install():
    """把 `get_config()` 重定向到沙箱副本，并返回那个 `Config`。

    脚本只要在建立窗口**之前**调用一次 `install()`，之后无论是自己写的
    `get_config()`、还是 `main.TranslatorApp()` 内部去要配置，拿到的都是
    临时副本，真配置不会被写。
    """
    sandbox = sandbox_config()
    try:
        from core import config as config_mod

        config_mod.get_config = lambda: sandbox
    except Exception:
        pass
    try:
        import main as app_main

        app_main.get_config = lambda: sandbox
    except Exception:
        pass
    return sandbox

