"""全局热键：直接用 Win32 RegisterHotKey，不依赖第三方库。

热键消息进入 Qt 事件循环，通过 QAbstractNativeEventFilter 派发。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Dict

from PySide6.QtCore import QAbstractNativeEventFilter

user32 = ctypes.windll.user32

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

_VK_NAMES = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """把 "ctrl+alt+t" 解析为 (modifiers, vk)。解析失败抛 ValueError。"""
    mods = 0
    vk = None
    for raw in str(spec).split("+"):
        token = raw.strip().lower()
        if not token:
            continue
        if token in _MODS:
            mods |= _MODS[token]
        elif token in _VK_NAMES:
            vk = _VK_NAMES[token]
        elif len(token) == 1 and token.isalpha():
            vk = ord(token.upper())
        elif len(token) == 1 and token.isdigit():
            vk = ord(token)
        else:
            raise ValueError(f"无法识别的按键: {token!r}")
    if vk is None:
        raise ValueError(f"热键缺少主键: {spec!r}")
    return mods, vk


class HotkeyManager(QAbstractNativeEventFilter):
    """注册若干全局热键并在按下时回调。"""

    def __init__(self) -> None:
        super().__init__()
        self._next_id = 1
        self._actions: Dict[int, Callable[[], None]] = {}
        self._specs: Dict[str, int] = {}

    def register(self, spec: str, callback: Callable[[], None]) -> bool:
        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as exc:
            print(f"[hotkey] {exc}")
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        ok = bool(user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk))
        if not ok:
            print(f"[hotkey] 注册失败（可能已被别的程序占用）: {spec}")
            return False
        self._actions[hotkey_id] = callback
        self._specs[spec] = hotkey_id
        print(f"[hotkey] 已注册 {spec}")
        return True

    def unregister_all(self) -> None:
        for hotkey_id in list(self._actions):
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        self._actions.clear()
        self._specs.clear()

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt 命名)
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            return False, 0
        if msg.message == WM_HOTKEY:
            callback = self._actions.get(int(msg.wParam))
            if callback is not None:
                try:
                    callback()
                except Exception as exc:
                    print(f"[hotkey] 回调异常: {exc}")
                return True, 0
        return False, 0
