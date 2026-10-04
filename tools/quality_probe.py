"""翻译质量体检：把一批真实的软件界面文本送出去，打印译文供人工判读。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.translate import Translator  # noqa: E402

CASES = [
    # 单行、成句
    "Are you sure you want to delete this file?",
    "The installer was unable to write to the selected directory.",
    "Do you want to save your changes before closing?",
    "This action cannot be undone.",
    "Your changes will be lost if you don't restart now.",
    "We couldn't find any matching results. Try a different keyword.",
    "The device you are using does not meet the minimum system requirements.",
    "Please wait while Windows configures the update.",
    "Some settings are managed by your organization.",
    # 界面短句
    "Sign in to continue",
    "Forgot your password?",
    "Update available",
    "Remind me later",
    "Check for updates",
    "Add to cart",
    "Out of stock",
    "Free shipping",
    "Track my order",
    "Read more",
    "Show less",
    # 菜单 / 选项
    "Open file location",
    "Run as administrator",
    "Pin to taskbar",
    "Compatibility mode",
    "Restore default settings",
    "Advanced options",
    # 技术/开发者界面
    "Failed to connect to the server. Retrying in 5 seconds.",
    "Branch not found: origin/main",
    "Commit changes to the local repository",
    "Build succeeded with 3 warnings.",
    "Unhandled exception: index out of range",
    # 视频/媒体界面（用户实际会遇到）
    "Now playing",
    "Add to playlist",
    "Shuffle all",
    "Lyrics not available",
    "Watch later",
    "Continue watching",
    # 游戏界面
    "Press any key to continue",
    "Load last save",
    "Difficulty: Normal",
    "Controller disconnected",
]


def main() -> int:
    translator = Translator(timeout=15)
    print(f"术语表 {len(translator.phrasebook)} 条")
    outcome = translator.translate(CASES, "zh-CN", "en")
    print(f"provider={outcome.provider} elapsed={outcome.elapsed_ms:.0f}ms "
          f"phrasebook={outcome.from_phrasebook} error={outcome.error!r}")
    print("=" * 78)
    for src, dst in zip(CASES, outcome.translations):
        flag = ""
        if not dst:
            flag = "  <<< 空"
        elif dst.strip().lower() == src.strip().lower():
            flag = "  <<< 原样退回"
        print(f"[{src}]\n  -> {dst}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
