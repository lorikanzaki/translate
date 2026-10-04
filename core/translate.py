"""翻译后端：微软 Edge 免费接口为主，多接口降级 + 术语表 + 缓存。

为什么是这套结构
----------------
本机网络（中国大陆）实测结论：
- Google 的 gtx / clients5 接口**完全不通**（连接挂起 12~100 秒后超时），不能放进默认链。
- Bing 老接口 `edge.microsoft.com/translate/auth` 已 404，`www.bing.com/ttranslatev3`
  返回 HTTP 200 但响应体为空（假阳性，早期探测误判为"可用"）。
- 有道 `aidemo.youdao.com/trans` 只在前几次请求可用，随后一直返回 errorCode=411（限流）。
- MyMemory 稳定但匿名额度只有 5000 字符/天，只能做兜底。
- **`edge.microsoft.com/translate/translatetext` 是唯一真正稳定的免费接口**：
  无 key、无需 cookie、支持一次 1000 条 / 单条 5 万字符、换行原样保留、
  1.5 秒间隔连续请求不触发限流，实测延迟中位数约 1 秒。

它的两个坑（本模块已处理）
--------------------------
1. HTML 标签对齐器常开：`A < B & C` 里的 `<` 会被当成标签，导致译文错乱甚至丢字。
   发送前要把 `& < >` 转义，收到后再反转义。
2. 孤立的短词翻译质量差（Copy→"收到"、Paste→"粘土"、Settings→"背景设定"）。
   因此对"短且不含句末标点"的文本优先查 `glossary.json` 术语表。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from core.scripts import (
    has_any_script, has_letters, scripts_present, source_is_declared, target_scripts,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

# 批次分隔符：控制字符，避免与正文冲突
SEP = "\n\u241e\n"

# 含这些标点说明是"整句"，不该套用术语表
SENTENCE_MARKS = ".?!,;:。？！，；：…\u201c\u201d\"'（）()[]【】/\\|"

# 术语表仅用于"短文本"：字符数与词数都要够小，避免把整句当成词条
PHRASEBOOK_MAX_CHARS = 40
PHRASEBOOK_MAX_WORDS = 6

# --------------------------------------------------------------------- 专名
# 这些词保留原文比硬翻准确得多。接口对**孤立短词**的翻译质量极差
# （实测 Copy→"收到"、Paste→"粘土"、Settings→"背景设定"），专名更糟：
# GitHub 可能被译成"代码托管平台"，Python 可能被译成"蟒蛇"。
# 所以短句里如果**每个词都是专名/代号**，就直接原样保留，连接口都不发。
PROPER_NOUNS = {
    # 品牌 / 产品 / 语言
    "github", "gitlab", "gitee", "git", "python", "pypi", "anaconda", "conda",
    "numpy", "pandas", "opencv", "pytorch", "tensorflow", "keras", "django",
    "flask", "fastapi", "node", "nodejs", "npm", "pnpm", "yarn", "webpack",
    "vite", "react", "vue", "angular", "svelte", "jquery", "bootstrap",
    "java", "javascript", "typescript", "kotlin", "swift", "golang", "rust",
    "php", "ruby", "perl", "scala", "matlab", "latex", "markdown", "yaml",
    "toml", "ini", "docker", "kubernetes", "k8s", "nginx", "apache", "redis",
    "mysql", "postgresql", "postgres", "sqlite", "mongodb", "oracle", "excel",
    "word", "powerpoint", "outlook", "onenote", "teams", "edge", "chrome",
    "firefox", "safari", "opera", "brave", "windows", "linux", "ubuntu",
    "debian", "centos", "fedora", "arch", "macos", "ios", "ipados", "android",
    "harmonyos", "visual", "studio", "vscode", "pycharm", "intellij", "eclipse",
    "android", "xcode", "photoshop", "illustrator", "premiere", "blender",
    "autocad", "solidworks", "revit", "sketchup", "figma", "notion", "slack",
    "discord", "telegram", "whatsapp", "wechat", "zoom", "obs", "steam",
    "nvidia", "geforce", "radeon", "intel", "amd", "qualcomm", "snapdragon",
    "python3", "c++", "c#", "c", "r", "go", "rust", "lua", "dart", "julia",
    "bash", "powershell", "cmd", "zsh", "fish", "wsl", "ssh", "curl", "wget",
    "ffmpeg", "imagemagick", "opencv-python", "directml", "directx", "vulkan",
    "opengl", "cuda", "onnx", "onnxruntime", "tesseract", "opencv",
}

# 全大写缩写：长度不固定，单独列出来（不能只靠"全大写"判断，
# 否则 "OK" / "NO" 这类普通词也会被当成专名保留）
ACRONYMS = {
    "pdf", "gpu", "cpu", "ram", "rom", "ssd", "hdd", "usb", "api", "sdk",
    "ide", "url", "uri", "html", "css", "json", "xml", "sql", "csv", "zip",
    "rar", "exe", "dll", "png", "jpg", "jpeg", "gif", "svg", "mp3", "mp4",
    "avi", "mkv", "utf", "ascii", "unicode", "ip", "tcp", "udp", "http",
    "https", "ftp", "dns", "vpn", "lan", "wan", "bios", "uefi", "os", "vm",
    "cli", "gui", "ux", "ui", "ai", "ml", "llm", "gpt", "ocr", "tts", "asr",
    "dpi", "ppi", "rgb", "hdr", "fps", "vram", "tdp", "bios",
}

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
# 出现这些字符说明是"整句"（引号/中文标点），不该当专名
_PROPER_REJECT_CHARS = "。？！，；：…\u201c\u201d\u2018\u2019\"'"
# 以这些字符结尾说明是整句
_PROPER_END_MARKS = ".!?,;:"


def has_cjk(text: str) -> bool:
    """文本里是否已经含中日韩文字。"""
    return bool(_CJK_RE.search(text or ""))


def wants_cjk(target: str) -> bool:
    """目标语言是不是中文（用来判断译文该不该出现汉字）。"""
    return normalize_lang(target).lower().startswith("zh")


_FILE_EXT_RE = re.compile(
    r"[\w\-]+\.(?:exe|dll|zip|rar|7z|tar|gz|png|jpe?g|gif|bmp|svg|webp|"
    r"txt|log|json|ya?ml|toml|ini|cfg|py|js|ts|tsx|jsx|java|c|cpp|h|cs|go|"
    r"rs|rb|php|sh|bat|ps1|md|rst|pdf|docx?|xlsx?|pptx?|csv|db|sqlite|"
    r"mp[34]|wav|flac|avi|mkv|mov|wmv|torrent)"
)
_VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+[a-z0-9\-]*")
_CAMEL_RE = re.compile(r"[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+")
_UPPER_CODE_RE = re.compile(r"[A-Za-z0-9]{2,8}")
_DOMAIN_RE = re.compile(r"[\w\-]+(?:\.[\w\-]+)+")
_URL_MAIL_RE = re.compile(r"[A-Za-z]+://\S+|[\w.\-]+@[\w.\-]+\.\w+")


def _is_proper_token(token: str) -> bool:
    """单个词是否是"应当保留原文"的专名 / 代号 / 文件名 / 版本号。"""
    core = token.strip("()[]{}<>.,:;!?\"'`*")
    if not core:
        return False
    lower = core.lower()
    if lower in PROPER_NOUNS or lower in ACRONYMS:
        return True
    if _URL_MAIL_RE.fullmatch(core):
        return True
    if _FILE_EXT_RE.fullmatch(lower):
        return True
    # 域名 / 包名 / 版本号：含点且各段是标识符
    if _DOMAIN_RE.fullmatch(core) and len(core) <= 40:
        return True
    if _VERSION_RE.fullmatch(lower):
        return True
    if _CAMEL_RE.fullmatch(core):
        return True
    # 含数字的短代号（RTX4060、win11、x64、SHA256…）。
    # 正则要大小写都收：原来只写 [A-Z0-9]，导致 win11 / x64 这类小写代号
    # 被当成普通单词送接口翻译。
    if _UPPER_CODE_RE.fullmatch(core) and any(ch.isdigit() for ch in core):
        return True
    # C++ / C# / F#
    if len(core) <= 6 and core[0].isalpha() and core.endswith(("++", "#")):
        return True
    return False


def looks_like_proper_noun(text: str) -> bool:
    """整行是否只是专名/代号（是的话保留原文，不送接口）。

    只认**很短**且**每个词都是专名**的行，避免误伤正常句子：
    `Open in GitHub` 里的 Open/in 不是专名，所以照常翻译；
    `GitHub` 本身就是专名，保留。
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 48:
        return False
    if has_cjk(stripped):
        return False
    if any(ch in stripped for ch in _PROPER_REJECT_CHARS):
        return False
    if stripped[-1] in _PROPER_END_MARKS:
        return False
    tokens = [t for t in re.split(r"[\s/\\|]+", stripped) if t]
    if not tokens or len(tokens) > 4:
        return False
    return all(_is_proper_token(token) for token in tokens)

# 自动识别语种的可信度下限，低于它就带 from= 重试
DETECT_MIN_SCORE = 0.6

# 单条文本长度上限（实测 50000 字符仍可，留出余量）
MAX_CHARS_PER_ITEM = 45000
# 一次请求最多条数（实测 1000 条上限）
MAX_ITEMS_PER_REQUEST = 60
# 单次请求的总字符上限，超过就拆成多批
MAX_CHARS_PER_REQUEST = 9000

DEFAULT_TIMEOUT = 15


# --------------------------------------------------------------------- 工具
def escape_for_api(text: str) -> str:
    """转义 HTML 敏感字符，避免接口的标签对齐器破坏文本。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# 形如标签的片段：`<b>`、`</b>`、`<a href="x">`。真正的 HTML 标签名和属性
# 全是 ASCII，所以只有整体为 ASCII 的匹配才是接口插进来的标签；
# 混杂中文的（如 `A < b 和 c > d` 被接口扭成 `<b和c>`）只是原文的角括号，
# 必须原样保留，否则会吃掉正文（实测被吃成 `比较d` 之外的 `比较和c>d`）。
_TAG_PATTERN = re.compile(r"</?[A-Za-z][A-Za-z0-9]{0,10}(?:\s[^<>]{0,60})?/?>")


def _strip_tags(text: str) -> str:
    """只删掉整体为 ASCII 的标签，保留含中文的「伪标签」。"""
    return _TAG_PATTERN.sub(lambda m: "" if m.group(0).isascii() else m.group(0), text)


def unescape_from_api(text: str) -> str:
    """反转义，并清掉接口标签对齐器插入的标签。"""
    return html.unescape(_strip_tags(text))


def normalize_lang(target: str) -> str:
    """把界面上的语言代码映射成接口认识的形式。

    没收录的代码原样返回（接口多数能透传两位码），空值退回简体中文。
    """
    table = {
        "zh-cn": "zh-Hans", "zh": "zh-Hans", "zh-hans": "zh-Hans",
        "zh-sg": "zh-Hans",
        "zh-tw": "zh-Hant", "zh-hant": "zh-Hant", "zh-hk": "zh-Hant",
        "zh-mo": "zh-Hant",
        "en": "en", "en-us": "en", "en-gb": "en",
        "ja": "ja", "ko": "ko",
        "fr": "fr", "de": "de", "es": "es", "ru": "ru",
        "pt": "pt", "it": "it", "nl": "nl", "pl": "pl", "tr": "tr",
        "vi": "vi", "th": "th", "id": "id", "ms": "ms", "ar": "ar",
        "hi": "hi", "uk": "uk", "cs": "cs", "sv": "sv", "da": "da",
        "fi": "fi", "no": "no", "hu": "hu", "ro": "ro", "el": "el",
        "he": "he", "fa": "fa", "bn": "bn", "ta": "ta", "te": "te",
        "ur": "ur", "bg": "bg", "hr": "hr", "sk": "sk", "sl": "sl",
    }
    return table.get((target or "").strip().lower(), target or "zh-Hans")


def load_phrasebook(path: Optional[str] = None) -> Dict[str, str]:
    """读取术语表，键统一转小写。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}
    return {str(k).strip().lower(): str(v)
            for k, v in raw.items() if not str(k).startswith("_")}


# 界面上的短标签常常带"尾巴"：快捷键、省略号、括号说明、菜单箭头、
# 项目符号。这些尾巴会让整条键查不到表，所以要一层层剥掉再查。
# 顺序有讲究：先去项目符号，再去尾巴，直到没有变化为止。
_LEADING_PATTERNS = (
    re.compile(r"^\s*[\-\*\u2022\u25cf\u25cb\u25a0\u25a1\u2192\u00b7\u2713\u2714]\s+"),
    re.compile(r"^\s*\d+[.)]\s+"),
)
_TRAILING_PATTERNS = (
    # 制表符后面的快捷键，如 "Settings\tCtrl+,"
    re.compile(r"\s*[\t\u0009\u2003|]\s*[A-Za-z0-9+\u2318,\u2190-\u21ff]{1,16}\s*$"),
    # 括号里的快捷键，如 "Copy (Ctrl+C)" / "Undo [Ctrl+Z]"
    re.compile(r"\s*[(\[]\s*[^()\[\]]{0,28}"
               r"(?:Ctrl|Alt|Shift|Cmd|\u2318|F\d{1,2}|Esc|Del|Ins|Tab|Enter|"
               r"Space|Home|End|PgUp|PgDn|Backspace|\u2190|\u2191|\u2192|\u2193)"
               r"[^()\[\]]{0,28}[)\]]\s*$", re.IGNORECASE),
    # 裸快捷键尾巴，如 "Copy Ctrl+C"
    re.compile(r"\s*(?:Ctrl|Alt|Shift|Cmd|\u2318)\s*\+\s*[A-Za-z0-9\u2190-\u21ff]"
               r"(?:\s*\+\s*[A-Za-z0-9\u2190-\u21ff]+)*\s*$", re.IGNORECASE),
    # 省略号
    re.compile(r"\s*[.\u2026]{2,}\s*$"),
    # 普通括号 / 方括号补充说明
    re.compile(r"\s*\(\s*[^()]{0,32}\)\s*$"),
    re.compile(r"\s*\[\s*[^\[\]]{0,32}\]\s*$"),
    # 菜单层级箭头
    re.compile(r"\s*[>\u203a\u2192]\s*$"),
    # 冒号、尾随逗号
    re.compile(r"\s*[:：,，]\s*$"),
)


def _phrasebook_candidates(text: str) -> List[str]:
    """把一条界面文本规整成若干可能的术语表键（由具体到宽松）。"""
    current = text.strip()
    seen: List[str] = []
    for _ in range(6):
        cleaned = current
        for pattern in _LEADING_PATTERNS:
            cleaned = pattern.sub("", cleaned, count=1).strip()
        for pattern in _TRAILING_PATTERNS:
            stripped = pattern.sub("", cleaned, count=1).strip()
            if stripped and stripped != cleaned:
                cleaned = stripped
                break
        key = cleaned.strip().lower()
        if key and key not in seen:
            seen.append(key)
        if cleaned == current:
            break
        current = cleaned
    return seen


def phrasebook_lookup(text: str, book: Dict[str, str]) -> Optional[str]:
    """短文本查术语表；整句一律不查，否则会破坏句子结构。"""
    if not book:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    for key in _phrasebook_candidates(stripped):
        if len(key) > PHRASEBOOK_MAX_CHARS or len(key.split()) > PHRASEBOOK_MAX_WORDS:
            continue
        # 标点检查放在**剥掉尾巴之后**：否则 "Save As..." / "Copy (Ctrl+C)"
        # 这类最常见的菜单项永远查不到表
        if any(mark in key for mark in SENTENCE_MARKS):
            continue
        hit = book.get(key)
        if hit is not None:
            return hit
    return None


# 常见整句：接口翻整句的总体质量不错，但个别高频句会翻得离谱 ——
# 实测 "Do you want to save your changes before closing?" 被翻成
# 「你想在交房前保存你的更改吗？」（before closing 当成"交房"）。
# 这些句子在软件界面上反复出现，所以给一份**精确匹配**的对照表。
# 只有整句（忽略大小写、把连续空白折成一个空格）完全一致才命中，
# 不做任何模糊匹配，因此不会误伤别的句子。
COMMON_SENTENCES: Dict[str, str] = {
    "are you sure you want to delete this file?":
        "你确定要删除这个文件吗？",
    "are you sure you want to permanently delete this file?":
        "你确定要永久删除此文件吗？",
    "are you sure you want to delete the selected items?":
        "你确定要删除选中的项目吗？",
    "are you sure you want to continue?":
        "你确定要继续吗？",
    "are you sure you want to exit?":
        "你确定要退出吗？",
    "are you sure you want to uninstall this application?":
        "你确定要卸载此应用吗？",
    "do you want to save your changes before closing?":
        "关闭前要保存你的更改吗？",
    "do you want to save the changes?":
        "要保存更改吗？",
    "would you like to save your work?":
        "要保存你的工作吗？",
    "your changes will be lost.":
        "你的更改将丢失。",
    "unsaved changes will be lost.":
        "未保存的更改将丢失。",
    "this action cannot be undone.":
        "此操作无法撤销。",
    "the installer was unable to write to the selected directory.":
        "安装程序无法写入所选目录。",
    "the file already exists. do you want to replace it?":
        "文件已存在。要替换它吗？",
    "this file does not have an app associated with it for performing this action.":
        "此文件没有与之关联的应用来执行该操作。",
    "do you want to allow this app to make changes to your device?":
        "是否允许此应用对你的设备进行更改？",
    "this app has been blocked by your system administrator.":
        "此应用已被系统管理员阻止。",
    "the following errors occurred during installation.":
        "安装过程中发生了以下错误。",
    "please wait while the setup completes.":
        "请稍候，安装正在完成。",
    "click finish to close this wizard.":
        "单击「完成」关闭此向导。",
    "check your internet connection and try again.":
        "请检查网络连接后重试。",
    "an error occurred while saving the file.":
        "保存文件时发生错误。",
    "please select a file to continue.":
        "请选择一个文件以继续。",
    "all files will be deleted and cannot be recovered.":
        "所有文件都将被删除且无法恢复。",
    "the system cannot find the file specified.":
        "系统找不到指定的文件。",
    "the system cannot find the path specified.":
        "系统找不到指定的路径。",
    "unhandled exception has occurred in your application.":
        "你的应用程序中发生了未经处理的异常。",
    "access is denied.": "拒绝访问。",
    "the operation completed successfully.": "操作成功完成。",
    "the operation was cancelled by the user.": "操作已被用户取消。",
    "not enough space on the disk.": "磁盘空间不足。",
    "there is not enough free disk space.": "可用磁盘空间不足。",
    "insufficient permissions.": "权限不足。",
    "invalid file name.": "文件名无效。",
    "this folder is empty.": "此文件夹为空。",
    "no items match your search.": "没有与搜索条件匹配的项。",
    "windows protected your pc": "Windows 已保护你的电脑",
    "not responding": "未响应",
    "operation failed": "操作失败",
    "installation complete": "安装完成",
    "no internet connection": "无 Internet 连接",
    "downloading updates...": "正在下载更新…",
    "connecting to the network...": "正在连接到网络…",
    "an error occurred. please try again.": "发生错误，请重试。",
}


def sentence_lookup(text: str) -> Optional[str]:
    """整句精确匹配。只认完全相同（忽略大小写与空白差异）的句子。"""
    stripped = text.strip()
    if len(stripped) < 8:
        return None
    return COMMON_SENTENCES.get(" ".join(stripped.lower().split()))


# 菜单层级写法：源文本里有 " > " 时，译文也应保留 " > "（接口常给成 "文件>保存"）
_MENU_ARROW_RE = re.compile(r"\s*>\s*")

# 拆装饰时只用**确定是纯 ASCII 装饰**的那几条（前面 4 条：制表符快捷键、
# 括号快捷键、裸快捷键、省略号）。后面几条（普通括号说明、方括号说明、
# 尾随冒号逗号）在查术语表时很有用，但拿来拆整句会误伤：
# "Something (optional)" 会被拆成核心 + 不翻译的尾巴。
_DECOR_TRAILING_PATTERNS = _TRAILING_PATTERNS[:4]


def split_decorations(text: str) -> Tuple[str, str, str]:
    """把带装饰的界面文本拆成 (前缀, 核心, 后缀)。

    菜单项常带项目符号、省略号、快捷键尾巴。这些装饰**要拆下来本地拼回去**：
    接口会糟蹋它们 —— 实测 `Open Recent...` 被翻成「最近开启......」（省略号翻倍），
    而快捷键一旦被卷进翻译，`Ctrl+S` 可能被译成中文，热键就失效了。
    拆出核心后只翻核心，再把装饰按原样贴回，两边都对得上。
    """
    prefix = ""
    suffix = ""
    current = text
    for _ in range(6):
        before = current
        stripped = current.lstrip()
        leading_ws = current[:len(current) - len(stripped)]
        for pattern in _LEADING_PATTERNS:
            match = pattern.match(stripped)
            if match:
                prefix += leading_ws + stripped[:match.end()]
                current = stripped[match.end():]
                break
        for pattern in _DECOR_TRAILING_PATTERNS:
            match = pattern.search(current)
            if match and match.end() == len(current) and match.start() < len(current):
                suffix = current[match.start():] + suffix
                current = current[:match.start()]
                break
        if current == before:
            break
    return prefix, current, suffix


def normalize_menu_arrows(source_text: str, value: str) -> str:
    """源文本用 " > " 表示菜单层级时，把译文里被挤掉的空格补回来。"""
    if ">" not in value or " > " not in source_text:
        return value
    return _MENU_ARROW_RE.sub(" > ", value)


# ---------------------------------------------------------------- 占位保护
# 实测 "Branch not found: origin/main" 被翻成「未找到分支：起源/主线」——
# 接口把 git 引用当成两个普通单词直译了。文件路径、变量名、版本号同理。
# 所以发送前把这些片段换成 {{0}} {{1}} 占位符，收到后再原样换回来。
# 占位符形式是实测挑出来的：接口会原样保留 {{0}}、##0##、❶❷；
# 但 $0$ 会丢掉末尾的 $，私有区字符（U+E000）会被替换成乱码，都不能用。
_PROTECT_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"https?://[^\s<>\"'\uff08\uff09]+"),              # URL
    re.compile(r"[A-Za-z]:\\[^\s<>\"'|]+"),                        # Windows 路径
    re.compile(r"(?<![\w.])(?:\.{1,2}/|~/)[\w./\-]+"),             # unix / 相对路径
    re.compile(r"(?<![\w.])\b[\w.\-]+/[\w.\-/]+"),                 # git 引用 a/b
    re.compile(r"\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b"),        # snake_case
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)*\b"),                       # 版本号
    re.compile(r"\b0[xX][0-9A-Fa-f]+\b"),                          # 十六进制
    re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b"),                     # 邮箱
)
_PLACEHOLDER_RE = re.compile(r"\{\{(\d+)\}\}")


def mask_protected(text: str) -> Tuple[str, List[str]]:
    """把不该被翻译的片段换成占位符，返回 (替换后的文本, 原片段列表)。"""
    tokens: List[str] = []

    def replace(match: "re.Match[str]") -> str:
        tokens.append(match.group(0))
        return "{{%d}}" % (len(tokens) - 1)

    masked = text
    for pattern in _PROTECT_PATTERNS:
        masked = pattern.sub(replace, masked)
    return masked, tokens


def restore_protected(text: str, tokens: Sequence[str]) -> Optional[str]:
    """把占位符换回原文；有任何占位符丢失就返回 None（调用方要退回去重试）。"""
    restored = text
    for index, token in enumerate(tokens):
        placeholder = "{{%d}}" % index
        if placeholder not in restored:
            return None
        restored = restored.replace(placeholder, token)
    return restored


def strip_placeholders(text: str) -> str:
    """清掉没换回来的占位符残留（最后一道保险，避免界面出现 {{0}}）。"""
    return _PLACEHOLDER_RE.sub("", text)


@dataclass
class TranslationOutcome:
    translations: List[str]
    provider: str
    elapsed_ms: float
    cached: bool = False
    error: str = ""
    attempted: List[str] = field(default_factory=list)
    from_phrasebook: int = 0


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    name = "base"
    label = "基础"

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        """source 为空 / "auto" 表示让接口自己识别语种。"""
        raise NotImplementedError


def _is_auto(source: str) -> bool:
    return (source or "").strip().lower() in ("", "auto", "自动", "自动识别")


def _normalize_source(source: str) -> str:
    """把界面上的源语言代码整成接口认识的写法；自动识别返回空串。"""
    if _is_auto(source):
        return ""
    return normalize_lang(source)


def _mymemory_code(code: str, default: str) -> str:
    """MyMemory 用的是 RFC3066（en / zh-CN / zh-TW），没有 auto。"""
    normalized = normalize_lang(code).lower()
    if normalized.startswith("zh"):
        if "hant" in normalized or normalized.endswith("tw") or normalized.endswith("hk"):
            return "zh-TW"
        return "zh-CN"
    base = normalized.split("-")[0]
    return base if len(base) == 2 else default


def _youdao_to(target: str) -> str:
    return "zh-CHT" if "hant" in normalize_lang(target).lower() else "zh-CHS"


def _base_lang(target: str) -> str:
    """zh-CN → zh；en-US → en。给只认两字母代码的接口用。"""
    code = normalize_lang(target)
    return (code.split("-")[0] or "zh").lower()


def _split_merged_strict(merged: str, expected: int, label: str) -> List[str]:
    """把合并发送的译文拆回原条数。

    接口要是悄悄吞掉一行，后面所有行都会串行错位——整屏译文全错，
    比直接失败还糟。所以这里两种拆法都试，拆不出正好 `expected` 条就抛错
    换下一家：

      * 先按控制符 `\\u241e` 拆（必应会原样保留它）
      * 再按行拆（火山会把 `\\u241e` 吃掉、只留下换行：实测合并 4 条送进去，
        回来是 `'复制\\n\\n粘贴\\n\\n设置\\n\\n签到'`）

    两种都对不上就说明这一屏没法安全对齐，宁可换一家重来。
    """
    if expected <= 1:
        return [merged.strip()]
    by_sep = [part.strip() for part in merged.split("\u241e") if part.strip()]
    if len(by_sep) == expected:
        return by_sep
    by_line = [line.strip() for line in merged.splitlines() if line.strip()]
    if len(by_line) == expected:
        return by_line
    raise ProviderError(
        f"{label}: 合并 {expected} 条，响应只能拆出 {len(by_sep)} 段 / "
        f"{len(by_line)} 行（分隔符和换行都对不上，怕串行错位）")


# ------------------------------------------------------------------ 火山翻译
class VolcengineProvider(BaseProvider):
    """火山翻译的浏览器扩展接口。不需要 key、cookie、登录。

    实测（tools\\verify_new_providers.py / verify_provider_limits.py）：
      * **短词质量是免费接口里最好的**：Copy→复制、Paste→粘贴、Settings→设置
        （Edge 同样输入会给"收到""粘土""背景设定"）
      * 整句也对：`Build succeeded with 3 warnings.` → 构建成功，有3个警告。
      * **一次只收一条 text**（`text_list` 会 400），所以把一屏文本用 SEP
        合并成一条发出去，回来再按分隔符拆开
      * `source_language` **不能写 "auto"**：实测 Einstellungen 被按英语音译成
        「埃因斯特伦根」；写空串才走自动识别（同样输入得到「设置」）
      * 延迟与长度无关：48 条 3357 字符仍是 831ms
    """

    name = "volc"
    label = "火山翻译接口"
    endpoint = "https://translate.volcengine.com/crx/translate/v1"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://translate.volcengine.com",
            "Referer": "https://translate.volcengine.com/",
        })

    @staticmethod
    def _target(target: str) -> str:
        code = normalize_lang(target)
        return code if code.lower() in ("zh-hant",) else _base_lang(target)

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        items = list(texts)
        if not items:
            return []
        merged = SEP.join(items)
        # 注意：全是空串时 `merged.strip()` 仍是 "\u241e"（分隔符不是空白字符），
        # 得逐条判断，否则会拿一条纯分隔符去白跑一次请求。
        if not any(item.strip() for item in items):
            return ["" for _ in items]
        payload = {
            # 空串 = 自动识别；这里绝不能写 "auto"
            "source_language": _normalize_source(source),
            "target_language": self._target(target),
            "text": escape_for_api(merged),
        }
        response = self._session.post(self.endpoint, json=payload,
                                      timeout=self.timeout)
        if response.status_code == 429:
            raise ProviderError("触发限流 HTTP 429")
        if response.status_code != 200:
            raise ProviderError(
                f"HTTP {response.status_code} {response.text[:120]!r}")
        try:
            data = response.json()
        except Exception as exc:
            raise ProviderError(f"响应不是 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"响应结构异常: {type(data).__name__}")
        raw = data.get("translation")
        if raw is None:
            nested = data.get("data")
            if isinstance(nested, dict):
                raw = nested.get("translation")
        if raw is None:
            raise ProviderError(
                f"接口未给出译文: {json.dumps(data, ensure_ascii=False)[:160]}")
        text = unescape_from_api(str(raw))
        return _split_merged_strict(text, len(items), self.label)


# ------------------------------------------------------------ 必应翻译（中文站）
class BingCNProvider(BaseProvider):
    """cn.bing.com/ttranslatev3 —— 不用 key，但要先从翻译页里拿 token。

    流程：GET https://cn.bing.com/translator，从 HTML 里挖出
    `IG`、`data-iid` 和 `params_AbusePreventionHelper=[key, token, ttl]`，
    再 POST 到 /ttranslatev3。token 实测有效期 1 小时，所以缓存在实例上。

    实测（tools\\verify_new_providers.py / verify_provider_limits.py）：
      * UI 短词 12/12 全对，连 Edge 翻错的 `Sign in`→登录、`Now playing`→正在播放 也对
      * 整句好：`Build succeeded with 3 warnings.` → 构建成功，但有 3 个警告。
      * **一次只收一条 text**（多条只回第 1 条），所以同样要 SEP 合并再拆回
      * `fromLang` 不能为空（返回 statusCode 400），自动识别要写 "auto-detect"
      * 延迟随长度线性上升：3 条 1994ms、24 条 3388ms、48 条 6072ms
    """

    name = "bing"
    label = "必应翻译（中文站）"
    page = "https://cn.bing.com/translator"
    endpoint = "https://cn.bing.com/ttranslatev3"
    _HELPER_RE = re.compile(r"params_AbusePreventionHelper\s*=\s*(\[[^\]]*\])")
    _IG_RE = re.compile(r'IG:"([^"]+)"')
    _IID_RE = re.compile(r'data-iid="([^"]+)"')

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})
        self._ig = ""
        self._iid = ""
        self._key = ""
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _ensure_token(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._token and time.time() < self._expires_at:
                return
            response = self._session.get(self.page, timeout=self.timeout)
            if response.status_code != 200:
                raise ProviderError(
                    f"取 token 失败 HTTP {response.status_code}")
            body = response.text
            helper = self._HELPER_RE.search(body)
            ig = self._IG_RE.search(body)
            iid = self._IID_RE.search(body)
            if not (helper and ig and iid):
                raise ProviderError("必应翻译页结构变了，挖不到 token")
            try:
                parsed = json.loads(helper.group(1))
                key, token = parsed[0], parsed[1]
                ttl = float(parsed[2]) / 1000.0
            except Exception as exc:
                raise ProviderError(f"token 字段解析失败: {exc}") from exc
            self._ig = ig.group(1)
            self._iid = iid.group(1)
            self._key = str(key)
            self._token = str(token)
            # 留 60 秒余量，别让刚好过期的那一次白跑
            self._expires_at = time.time() + max(60.0, ttl - 60.0)

    def _post(self, text: str, src: str, target: str):
        self._ensure_token()
        return self._session.post(
            self.endpoint,
            params={"isVertical": "1", "IG": self._ig, "IID": self._iid},
            data={"fromLang": src, "text": text, "to": target,
                  "token": self._token, "key": self._key},
            headers={"Referer": self.page},
            timeout=self.timeout,
        )

    def _translate_once(self, text: str, src: str, target: str) -> str:
        response = self._post(text, src, target)
        if response.status_code != 200:
            raise ProviderError(
                f"HTTP {response.status_code} {response.text[:120]!r}")
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderError(f"响应不是 JSON: {exc}") from exc
        if isinstance(payload, dict):
            code = payload.get("statusCode")
            if code in (205, 401, 403):
                raise _TokenExpired(f"statusCode={code}")
            raise ProviderError(
                f"接口报错 statusCode={code} "
                f"{payload.get('errorMessage') or ''}".strip())
        if not isinstance(payload, list) or not payload:
            raise ProviderError(f"响应结构异常: {type(payload).__name__}")
        try:
            return str(payload[0]["translations"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"响应缺少译文: {exc}") from exc

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        items = list(texts)
        if not items:
            return []
        merged = SEP.join(items)
        # 必应收到空串直接 statusCode 400，本地先拦下来（同样不能只看
        # merged.strip()：全空时它剩下的是分隔符 "\u241e"）
        if not any(item.strip() for item in items):
            return ["" for _ in items]
        target_code = normalize_lang(target)
        src = _normalize_source(source) or "auto-detect"
        text = escape_for_api(merged)
        try:
            raw = self._translate_once(text, src, target_code)
        except _TokenExpired:
            # token 被判失效：强制重拿一次再试
            self._ensure_token(force=True)
            raw = self._translate_once(text, src, target_code)
        return _split_merged_strict(unescape_from_api(raw), len(items), self.label)


class _TokenExpired(ProviderError):
    """内部信号：必应 token 过期，重拿一次即可，不算接口故障。"""


# --------------------------------------------------------------------- 微软 Edge
class MicrosoftEdgeProvider(BaseProvider):
    """edge.microsoft.com/translate/translatetext —— 当前唯一稳定的免费接口。

    请求体是**裸 JSON 字符串数组**（老的 [{"Text": ...}] 形式会返回 400），
    响应是对应长度的数组，每项形如 {"translations": [{"text": "..."}]}。
    """

    name = "edge"
    label = "微软 Edge 接口"
    endpoint = "https://edge.microsoft.com/translate/translatetext"
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _post(self, payload: List[str], src: str, target: str) -> List[dict]:
        params = {"from": src, "to": target, "isEnterpriseClient": "false"}
        response = self._session.post(
            self.endpoint, params=params, json=payload, timeout=self.timeout,
        )
        if response.status_code == 429:
            raise ProviderError("触发限流 HTTP 429")
        if response.status_code != 200:
            raise ProviderError(
                f"HTTP {response.status_code} {response.text[:120]!r}")
        try:
            data = response.json()
        except Exception as exc:
            raise ProviderError(f"响应不是 JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ProviderError(f"响应结构异常: {type(data).__name__}")
        if len(data) != len(payload):
            raise ProviderError(f"返回条数不符: {len(data)} != {len(payload)}")
        return data

    @staticmethod
    def _extract(data: List[dict]) -> List[str]:
        out: List[str] = []
        for item in data:
            try:
                out.append(item["translations"][0]["text"])
            except (KeyError, IndexError, TypeError):
                out.append("")
        return out

    @staticmethod
    def _weak_languages(data: List[dict]) -> List[str]:
        """收集置信度低的语种检测结果，用于第二次带 from= 重试。"""
        weak: List[str] = []
        for item in data:
            detected = item.get("detectedLanguage") or {}
            language = detected.get("language")
            score = detected.get("score", 1.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 1.0
            if language and score < DETECT_MIN_SCORE and language not in weak:
                weak.append(language)
        return weak

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        if not texts:
            return []
        payload = [escape_for_api(text) for text in texts]
        target = normalize_lang(target)
        src = _normalize_source(source)

        data = self._post(payload, src, target)
        results = [unescape_from_api(text) for text in self._extract(data)]

        # 语种识别不可信时，用它给出的猜测再试一次，往往能救回整批。
        # 用户已经指定源语言时不需要这一趟。
        if not src:
            weak = self._weak_languages(data)
            for language in weak[:2]:
                try:
                    retry_data = self._post(payload, language, target)
                except ProviderError:
                    break
                retry_results = [unescape_from_api(text)
                                 for text in self._extract(retry_data)]
                # 只替换那些原本为空或明显是"原样返回"的位置
                for index, (before, after) in enumerate(zip(results, retry_results)):
                    if after and (not before or before == texts[index]):
                        results[index] = after
        return results


# --------------------------------------------------------------------- MyMemory
class MyMemoryProvider(BaseProvider):
    """MyMemory：匿名 5000 字符/天，只适合小批量兜底，逐条请求。"""

    name = "mymemory"
    label = "MyMemory"
    endpoint = "https://api.mymemory.translated.net/get"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        # MyMemory 不支持自动识别，源语言留空时只能按英语试
        src = "en" if _is_auto(source) else _mymemory_code(source, "en")
        langpair = f"{src}|{_mymemory_code(target, 'zh-CN')}"
        results: List[str] = []
        for text in texts:
            response = self._session.get(
                self.endpoint,
                params={"q": text[:480], "langpair": langpair},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ProviderError(f"HTTP {response.status_code}")
            data = response.json()
            translated = (data.get("responseData") or {}).get("translatedText") or ""
            if not translated:
                details = str(data.get("responseDetails") or "")[:80]
                raise ProviderError(f"返回为空 {details}")
            results.append(html.unescape(translated))
        return results


# --------------------------------------------------------------------- 有道
class YoudaoDemoProvider(BaseProvider):
    """有道官方 demo 接口。

    实测限流极严：连续请求很快全部返回 errorCode=411，所以只在
    Translator 判定"主接口全挂"时才被调用，且自身一次只发很少的条数。
    """

    name = "youdao"
    label = "有道 demo 接口"
    endpoint = "https://aidemo.youdao.com/trans"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    @staticmethod
    def _join(value) -> str:
        if isinstance(value, str):
            return value
        parts: List[str] = []
        for item in value or []:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("t") or "")
        return "".join(parts)

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        from_code = "AUTO" if _is_auto(source) else _normalize_source(source).upper()
        to_code = _youdao_to(target)
        results: List[str] = []
        for text in texts:
            response = self._session.get(
                self.endpoint,
                params={"q": text[:400], "from": from_code, "to": to_code},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ProviderError(f"HTTP {response.status_code}")
            data = response.json()
            if str(data.get("errorCode")) != "0":
                raise ProviderError(f"errorCode={data.get('errorCode')}（限流）")
            text_out = self._join(data.get("translation"))
            if not text_out:
                raise ProviderError("返回为空")
            results.append(text_out)
        return results


# --------------------------------------------------------------------- Google（需代理）
class GoogleWebProvider(BaseProvider):
    """translate.googleapis.com 的 gtx 接口。本机直连不通，仅供配置代理后使用。

    注意：该接口会把换行当作可折叠空白，多行文本不保证按行返回，
    因此本实现按「一次一条」发送，靠条数对齐而不是靠分隔符。
    """

    name = "google"
    label = "Google 接口（需代理）"
    endpoint = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, timeout: int = 8) -> None:
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        sl = _normalize_source(source) or "auto"
        tl = normalize_lang(target)
        results: List[str] = []
        for text in texts:
            params = {"client": "gtx", "sl": sl, "tl": tl, "dt": "t"}
            response = self._session.post(
                self.endpoint, params=params, data={"q": text[:4000]},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ProviderError(f"HTTP {response.status_code}")
            data = response.json()
            if not isinstance(data, list) or not data or not isinstance(data[0], list):
                raise ProviderError("响应结构异常")
            pieces = [seg[0] for seg in data[0]
                      if isinstance(seg, list) and seg and isinstance(seg[0], str)]
            results.append("".join(pieces))
        return results


class LLMProvider(BaseProvider):
    """OpenAI 兼容的大模型接口（DeepSeek / 通义 / Kimi / 本地 Ollama 都行）。

    为什么需要它：通用机器翻译（微软/有道/Google）在**软件界面**这种短文本上
    会给出很生硬的直译，实测差异很明显 ——

        Now playing              -> 正在演奏      （中文软件是「正在播放」）
        Shuffle all              -> 全部洗牌      （「随机播放全部」）
        Show less                -> 显示更少      （「收起」）
        Lyrics not available     -> 歌词未公开    （「暂无歌词」）
        Load last save           -> 加载最后存档  （「读取上次存档」）
        Controller disconnected  -> 控制器断开连接（「手柄已断开」）
        Build succeeded with 3 warnings. -> Build成功了，但收到了3次警告。
        Branch not found: origin/main    -> 未找到分支：起源/主线

    大模型只要在提示词里说清「这是软件界面、要按中文软件的习惯说法」，
    上面这些基本都能翻对。代价是需要一个 API key（DeepSeek 的分价大概是
    每百万 token 一两块钱，屏幕翻译一天也就几万 token）。
    """

    name = "llm"
    label = "大模型接口（质量最好，需 API key）"

    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"

    SYSTEM_PROMPT = """你是软件界面本地化专家。用户给你一个 JSON 数组，\
里面是要翻译的界面文本（菜单、按钮、对话框、提示、网页或歌词）。

把它们逐条翻译成简体中文，**只输出一个 JSON 数组**，元素个数必须与输入完全一致。

规则：
1. 用中文软件里的通行说法，不要逐字直译。例如
   "Now playing"→"正在播放"，"Shuffle all"→"随机播放全部"，
   "Show less"→"收起"，"Lyrics not available"→"暂无歌词"，
   "Load last save"→"读取上次存档"，"Controller disconnected"→"手柄已断开"，
   "Out of stock"→"缺货"，"Read more"→"展开阅读"。
2. 专有名词、产品名、代码标识符、分支名、变量名保持原文，
   例如 GitHub、Python、origin/main、user_id、JSON。
3. 快捷键原样保留，例如 Ctrl+S、F5、（Ctrl+N）、&File。
4. 占位符原样保留，例如 %s、{0}、%d、$1、{name}。
5. 原文里的换行必须原样保留。
6. 已经是中文、或本来就不需要翻译的条目，原样返回。
7. 不要输出解释、注释、markdown 代码块或任何额外文字。"""

    def __init__(self, timeout: int = 20, options: Optional[dict] = None) -> None:
        self.timeout = timeout
        self.options: Dict[str, str] = dict(options or {})
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    # ------------------------------------------------------------ 配置
    def configure(self, base_url: str = "", api_key: str = "",
                  model: str = "", timeout: Optional[int] = None) -> None:
        self.options = {
            "base_url": (base_url or "").strip(),
            "api_key": (api_key or "").strip(),
            "model": (model or "").strip(),
        }
        if timeout:
            self.timeout = int(timeout)

    @property
    def ready(self) -> bool:
        """没填 API key 就当作未就绪，调度器会直接跳过这一家。"""
        return bool(self.options.get("api_key"))

    def _endpoint(self) -> str:
        base = (self.options.get("base_url")
                or self.DEFAULT_BASE_URL).strip().rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @staticmethod
    def _parse_array(raw: str) -> List[str]:
        """从模型回复里抠出 JSON 数组；容忍 ```json 代码块和前后废话。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ProviderError(f"模型没有返回 JSON 数组：{raw[:120]!r}")
        try:
            data = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise ProviderError(f"模型返回的 JSON 解析失败：{exc}") from exc
        if not isinstance(data, list):
            raise ProviderError("模型返回的不是数组")
        return [item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                for item in data]

    def batch_translate(self, texts: Sequence[str], target: str,
                        source: str = "") -> List[str]:
        if not texts:
            return []
        if not self.ready:
            raise ProviderError("未配置 API key")
        language = "简体中文" if wants_cjk(target) else target
        payload = {
            "model": self.options.get("model") or self.DEFAULT_MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system",
                 "content": self.SYSTEM_PROMPT.replace("简体中文", language, 1)},
                {"role": "user",
                 "content": json.dumps(list(texts), ensure_ascii=False)},
            ],
        }
        try:
            response = self._session.post(
                self._endpoint(),
                headers={"Authorization": f"Bearer {self.options['api_key']}",
                         "Content-Type": "application/json"},
                json=payload, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"请求失败 {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"HTTP {response.status_code} {response.text[:160]}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"响应结构异常：{response.text[:160]}") from exc
        results = self._parse_array(content)
        if len(results) != len(texts):
            raise ProviderError(f"返回条数不符 {len(results)} != {len(texts)}")
        return results


PROVIDER_CLASSES = {
    "volc": VolcengineProvider,
    "bing": BingCNProvider,
    "edge": MicrosoftEdgeProvider,
    "mymemory": MyMemoryProvider,
    "youdao": YoudaoDemoProvider,
    "google": GoogleWebProvider,
    "llm": LLMProvider,
}

# 默认优先级：实测「短词 + 整句」质量从高到低，同质量再比速度。
#   1. bing     质量最好：开启术语表后 12/12 全对（含 Sign in→登录、Now playing→
#               正在播放），德语 Einstellungen 也能自动识别成「设置」，响应带
#               usedLLM=true。代价是慢，且耗时随条数线性上升（12 条 1.7s、48 条 6s）。
#   2. volc     短词在术语表兜底下同样 12/12，且**快得多**（12 条 0.5s、48 条仍 0.8s）；
#               唯一短板是自动识别不可靠（德语 Einstellungen→埃因斯特伦根）。
#               所以让它当必应挂掉时的高速替补，而不是首选。
#   3. edge     唯一**真批量**接口，最稳；短词差，但短词基本都被术语表挡在前面了
#   4. mymemory 逐条送、额度小，只作最后兜底
# youdao demo 已被官方废弃（前 4 条能用，第 5 条起 411），不再进默认链。
# google 刻意**不在**默认链里：本机直连会挂起 40 秒才超时，放进降级链会把
# 每次刷新拖到无法忍受；只有在系统配置了 HTTP 代理时才由 ensure_google() 追加。
DEFAULT_PROVIDER_ORDER = ["bing", "volc", "edge", "mymemory"]

PROXY_ENV_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                   "ALL_PROXY", "all_proxy")


def has_proxy_configured() -> bool:
    """判断系统是否配置了 HTTP 代理（Google 接口只有在有代理时才值得尝试）。"""
    return any(os.environ.get(name, "").strip() for name in PROXY_ENV_NAMES)


def split_batch(merged: str, expected: int) -> List[str]:
    """把合并后的译文按分隔符拆回（供只支持整段发送的接口使用）。"""
    if expected <= 1:
        return [merged.strip()]
    parts = [p.strip() for p in merged.split("\u241e")]
    parts = [p for p in parts if p]
    if len(parts) == expected:
        return parts
    lines = [line.strip() for line in merged.splitlines() if line.strip()]
    if len(lines) == expected:
        return lines
    if len(parts) > expected:
        return parts[: expected - 1] + [" ".join(parts[expected - 1:])]
    parts += [""] * (expected - len(parts))
    return parts


def _already_target_language(text: str, target: str, source: str = "auto") -> bool:
    """这段文字是不是已经用目标语言写了。

    实现在 core.scripts：目标是中文时认汉字，目标是日语时认汉字+假名，
    目标是英语时认拉丁字母，西里尔/阿拉伯/泰文/天城文各有对应判定。
    目标语言没收录时一律返回 False（宁可不跳，也别误跳）。

    用户明确指定了源语言且与目标语言不同时（例如日语 → 中文），直接返回 False：
    日语的「設定 / 保存 / 終了」全是汉字，按文字体系会误判成「已经是中文」。
    """
    if source_is_declared(source, target):
        return False
    wanted = target_scripts(target)
    if not wanted or not has_letters(text):
        return False
    return bool(scripts_present(text) <= wanted)


def _looks_untranslated(source_text: str, value: str, target: str) -> bool:
    """判断接口给的"译文"是不是等于没翻。

    三种情况都算失败（交给下一家接口再试）：
      * 返回空
      * 原样退回（大小写不敏感）—— 旧代码的注释说这么做，但实际没实现，
        于是 "Bluetooth" 这种会被当成翻译成功的原文一直挂在界面上
      * 译文里**完全没有**目标语言的文字，而原文里却确实有字母可翻
        （目标是中文却一个汉字都没有；目标是英语却一个拉丁字母都没有……）
    """
    src = (source_text or "").strip()
    val = (value or "").strip()
    if not val:
        return True
    if val.lower() == src.lower():
        return True
    wanted = target_scripts(target)
    if wanted and has_letters(src) and not has_any_script(val, wanted):
        return True
    return False


class Translator:
    """带缓存、术语表和降级链的翻译调度器（线程安全）。"""

    def __init__(self, provider_order: Optional[Sequence[str]] = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 glossary_path: Optional[str] = None,
                 use_glossary: bool = True,
                 keep_proper_nouns: bool = True) -> None:
        self.timeout = timeout
        self.use_glossary = use_glossary
        self.keep_proper_nouns = keep_proper_nouns
        self._glossary_path = glossary_path
        # 术语表总是读进来（900 多条，开销可忽略），是否启用由 use_glossary
        # 决定 —— 这样设置里勾/取消勾选不用重新读盘。
        self.phrasebook = load_phrasebook(glossary_path)
        order = list(provider_order) if provider_order else list(DEFAULT_PROVIDER_ORDER)
        # 有代理时把 google 补到链尾：它是质量最好的免费接口，但直连不通
        if "google" not in order and has_proxy_configured():
            order.append("google")
        self.llm_options: Dict[str, str] = {}
        # 把路径/git 引用/变量名/版本号挖成占位符再翻译（见 mask_protected）
        self.protect_tokens: bool = True
        self.providers: Dict[str, BaseProvider] = {}
        for name in order:
            cls = PROVIDER_CLASSES.get(name)
            if cls is not None and name not in self.providers:
                self.providers[name] = self._make(name, cls)
        if not self.providers:
            self.providers["edge"] = MicrosoftEdgeProvider(timeout)
        self.order = list(self.providers.keys())
        self._cache: Dict[str, str] = {}
        self._cache_lock = threading.RLock()
        self.last_provider = ""
        self.last_error = ""
        self.stats = {"requests": 0, "cache_hits": 0, "failures": 0,
                      "phrasebook_hits": 0, "proper_noun_kept": 0}

    # ------------------------------------------------------------ 开关
    def _make(self, name: str, cls: type) -> BaseProvider:
        """建 provider。只有大模型接口需要额外配置（key/base_url/model）。"""
        if name == "llm":
            return cls(self.timeout, self.llm_options)
        return cls(self.timeout)

    def set_llm_settings(self, base_url: str = "", api_key: str = "",
                         model: str = "", timeout: Optional[int] = None) -> None:
        """设置里填的 API key / 地址 / 模型；已经建好的实例就地更新。"""
        self.llm_options = {
            "base_url": (base_url or "").strip(),
            "api_key": (api_key or "").strip(),
            "model": (model or "").strip(),
        }
        provider = self.providers.get("llm")
        if isinstance(provider, LLMProvider):
            provider.configure(**self.llm_options, timeout=timeout)

    def llm_ready(self) -> bool:
        provider = self.providers.get("llm")
        return isinstance(provider, LLMProvider) and provider.ready

    def set_glossary_enabled(self, enabled: bool) -> None:
        """设置里勾/取消"术语表优先"。"""
        self.use_glossary = bool(enabled)
        if self.use_glossary and not self.phrasebook:
            self.phrasebook = load_phrasebook(self._glossary_path)

    # ------------------------------------------------------------ 缓存
    def set_provider_order(self, provider_order: Sequence[str]) -> None:
        """按新的顺序重建降级链（保留已建的 provider 实例与缓存）。"""
        order = [name for name in provider_order if name in PROVIDER_CLASSES]
        if not order:
            return
        if "google" not in order and has_proxy_configured():
            order.append("google")
        with self._cache_lock:
            for name in order:
                if name not in self.providers:
                    self.providers[name] = self._make(name, PROVIDER_CLASSES[name])
            self.order = list(dict.fromkeys(order))

    @staticmethod
    def _key(text: str, target: str, source: str = "") -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        return f"{target}:{(source or 'auto').lower()}:{digest}"

    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def invalidate(self, texts: Sequence[str], target: str = "zh-CN",
                   source: str = "") -> None:
        with self._cache_lock:
            for text in texts:
                self._cache.pop(self._key(text, target, source), None)

    # ------------------------------------------------------------ 分批
    @staticmethod
    def _chunk(indices: List[int], texts: Sequence[str]) -> List[List[int]]:
        """按条数/总字符数把待翻译下标切成若干批。"""
        batches: List[List[int]] = []
        current: List[int] = []
        chars = 0
        for index in indices:
            length = min(len(texts[index]), MAX_CHARS_PER_ITEM)
            if current and (len(current) >= MAX_ITEMS_PER_REQUEST
                            or chars + length > MAX_CHARS_PER_REQUEST):
                batches.append(current)
                current = []
                chars = 0
            current.append(index)
            chars += length
        if current:
            batches.append(current)
        return batches

    # ------------------------------------------------------------ 主流程
    def translate(self, texts: Sequence[str], target: str = "zh-CN",
                  source: str = "") -> TranslationOutcome:
        started = time.perf_counter()
        items = list(texts)
        if not items:
            return TranslationOutcome([], "", 0.0, cached=True)

        results: List[Optional[str]] = [None] * len(items)
        pending: List[int] = []
        phrasebook_hits = 0
        proper_kept = 0
        source = (source or "").strip()

        with self._cache_lock:
            for index, text in enumerate(items):
                if not text or not text.strip():
                    # 空串不消耗配额，原样返回
                    results[index] = text
                    continue
                if not any(ch.isalpha() for ch in text):
                    # 纯数字 / 符号 / 项目符号：一个字母都没有，没有可翻译的内容。
                    # 送出去只会被原样退回，再被判成"翻译失败"，白白消耗配额。
                    #
                    # 注意这里以前写的是 `ch.isascii() and ch.isalpha()`，于是
                    # 俄语、韩语、阿拉伯语、泰语、印地语这些**非拉丁文字**也被
                    # 当成"没有字母"直接原样返回 —— 这是「只能英语转其它语言」
                    # 的第二个原因。
                    results[index] = text.strip()
                    continue
                if _already_target_language(text, target, source):
                    # 已经是目标语言了（目标是中文时的中文块、目标是英语时的
                    # 英文块），送出去只会被原样退回，白耗配额。
                    results[index] = text.strip()
                    continue
                cached = self._cache.get(self._key(text, target, source))
                if cached is not None:
                    results[index] = cached
                    continue
                if self.use_glossary and wants_cjk(target):
                    # 术语表和整句对照表都是**英译中**的，所以只在目标是中文时
                    # 才能用。以前这里不判目标语言，于是「英语 → 日语」会把
                    # Settings 直接返回成"设置"（实测就是这么发现的）。
                    hit = phrasebook_lookup(text, self.phrasebook)
                    if hit is None:
                        # 高频整句有本地对照表（接口会把
                        # "before closing" 翻成"交房"），精确匹配才用。
                        hit = sentence_lookup(text)
                    if hit is not None:
                        results[index] = hit
                        self._cache[self._key(text, target, source)] = hit
                        phrasebook_hits += 1
                        continue
                if (self.keep_proper_nouns and wants_cjk(target)
                        and looks_like_proper_noun(text)):
                    # 专名保留原文。接口对孤立短词极不靠谱（实测 Copy→"收到"），
                    # 对专名更糟：GitHub 可能被译成"代码托管平台"。
                    # 整行都是专名/代号时直接原样返回，连接口都不发。
                    kept = text.strip()
                    results[index] = kept
                    self._cache[self._key(text, target, source)] = kept
                    proper_kept += 1
                    continue
                pending.append(index)

        self.stats["phrasebook_hits"] += phrasebook_hits
        self.stats["proper_noun_kept"] += proper_kept

        # 装饰（项目符号 / 省略号 / 快捷键尾巴）本地拆下来，只把核心送接口，
        # 拿到译文再原样贴回：接口会把 "Open Recent..." 翻成「最近开启......」
        # （省略号翻倍），也会把 "Ctrl+S" 里的字母译成中文让热键失效。
        send_texts: Dict[int, str] = {}
        decorations: Dict[int, Tuple[str, str]] = {}
        for index in pending:
            prefix, core, suffix = split_decorations(items[index])
            core = core.strip()
            if core:
                send_texts[index] = core
                decorations[index] = (prefix, suffix)
            else:
                send_texts[index] = items[index].strip()
                decorations[index] = ("", "")

        if not pending:
            self.stats["cache_hits"] += 1
            return TranslationOutcome(
                [r if r is not None else "" for r in results], "cache",
                (time.perf_counter() - started) * 1000, cached=True,
                from_phrasebook=phrasebook_hits,
            )

        attempted: List[str] = []
        last_error = ""
        failed_indices: List[int] = []
        for name in self.order:
            provider = self.providers[name]
            # 大模型接口没填 key 时直接跳过，不要白占一个失败名额
            if getattr(provider, "ready", True) is False:
                continue
            attempted.append(name)
            # 前一家可能只翻出一部分，这里只处理还空着、且不是因为超长被跳过的
            blocked = set(failed_indices)
            work = [i for i in pending if results[i] is None and i not in blocked]
            if not work:
                break
            try:
                # 超过单条上限的文本先单独标失败，绝不要把截断后的文本送出去：
                # 那样缓存键(key 取自原文)与译文会错位，之后永远命中错误结果。
                oversized = [i for i in work if len(send_texts[i]) > MAX_CHARS_PER_ITEM]
                if oversized:
                    last_error = (f"{provider.label}: {len(oversized)} 条超出单条长度上限 "
                                  f"{MAX_CHARS_PER_ITEM}")
                    failed_indices.extend(oversized)
                    blocked = set(failed_indices)
                    work = [i for i in work if i not in blocked]
                    if not work:
                        continue

                # 同一屏里重复出现的行只送一次：省配额，也避免接口对同一段
                # 文本给出前后不一致的结果
                unique_texts: List[str] = []
                owners: Dict[str, List[int]] = {}
                for index in work:
                    send = send_texts[index]
                    if send not in owners:
                        owners[send] = []
                        unique_texts.append(send)
                    owners[send].append(index)

                for batch in self._chunk(list(range(len(unique_texts))), unique_texts):
                    batch_texts = [unique_texts[i] for i in batch]
                    masked_texts: List[str] = []
                    tokens_of: Dict[str, List[str]] = {}
                    for send in batch_texts:
                        masked, tokens = mask_protected(send) if self.protect_tokens \
                            else (send, [])
                        # 全被挖空了就没必要发（剩下的都是占位符），保持原样发
                        leftover = strip_placeholders(masked)
                        if tokens and any(ch.isascii() and ch.isalpha()
                                          for ch in leftover):
                            masked_texts.append(masked)
                            tokens_of[send] = tokens
                        else:
                            masked_texts.append(send)
                    translated = provider.batch_translate(masked_texts, target, source)
                    if len(translated) != len(batch_texts):
                        raise ProviderError(
                            f"返回条数不符 {len(translated)} != {len(batch_texts)}")
                    need_plain: List[str] = []
                    with self._cache_lock:
                        for offset, send in enumerate(batch_texts):
                            value = str(translated[offset] or "").strip()
                            tokens = tokens_of.get(send)
                            if tokens:
                                restored = restore_protected(value, tokens)
                                if restored is None:
                                    # 占位符没原样回来，不敢硬拼；退回不带占位符重发
                                    need_plain.append(send)
                                    continue
                                value = restored
                            if _looks_untranslated(send, value, target):
                                # 原样退回 / 没有目标语言文字：算没翻，留给下一家
                                continue
                            value = normalize_menu_arrows(send, value)
                            for index in owners[send]:
                                prefix, suffix = decorations[index]
                                final = f"{prefix}{value}{suffix}"
                                results[index] = final
                                self._cache[
                                    self._key(items[index], target, source)] = final

                    if need_plain:
                        # 占位符往返失败的少数条目：不用占位符重发一次。
                        # 退化成「可能会把 origin/main 直译」，但绝不会丢内容。
                        plain = provider.batch_translate(need_plain, target, source)
                        with self._cache_lock:
                            for offset, send in enumerate(need_plain):
                                value = strip_placeholders(
                                    str(plain[offset] or "")).strip()
                                if _looks_untranslated(send, value, target):
                                    continue
                                value = normalize_menu_arrows(send, value)
                                for index in owners[send]:
                                    prefix, suffix = decorations[index]
                                    final = f"{prefix}{value}{suffix}"
                                    results[index] = final
                                    self._cache[
                                        self._key(items[index], target, source)] = final
            except Exception as exc:
                last_error = f"{provider.label}: {exc}"
                self.stats["failures"] += 1
                print(f"[translate] {name} 失败: {exc}")
                # 这一家中途失败：把它已经写进去的结果留用，其余交给下一家
                blocked = set(failed_indices)
                pending = [i for i in pending
                           if results[i] is None and i not in blocked]
                if not pending:
                    break
                continue

            self.stats["requests"] += 1
            blocked = set(failed_indices)
            remaining = [i for i in pending
                         if results[i] is None and i not in blocked]
            if remaining:
                # 这家没翻全（部分条目被判为"原样退回"），换下一家接着补
                last_error = f"{provider.label}: {len(remaining)} 条未给出译文"
                pending = remaining
                continue
            self.last_provider = provider.label
            self.last_error = ""
            # 部分失败（如个别超长文本被跳过）时错误信息不能残留，
            # 否则调用方会以为整批都没翻成。
            return TranslationOutcome(
                [r if r is not None else "" for r in results], provider.label,
                (time.perf_counter() - started) * 1000,
                error="" if not failed_indices else
                f"{len(failed_indices)} 条未能翻译（超出长度上限）",
                attempted=attempted, from_phrasebook=phrasebook_hits,
            )

        self.last_provider = ""
        self.last_error = last_error or "全部翻译接口不可用"
        return TranslationOutcome(
            [r if r is not None else "" for r in results], "",
            (time.perf_counter() - started) * 1000,
            error=self.last_error, attempted=attempted,
            from_phrasebook=phrasebook_hits,
        )

    # ------------------------------------------------------------ 接口探测
    def probe(self, target: str = "zh-CN", source: str = "") -> List[Dict[str, object]]:
        """逐个探测接口可用性，供设置面板的「测试接口」使用。"""
        report: List[Dict[str, object]] = []
        sample = ["Download failed", "The installer was unable to write to "
                  "the selected directory."]
        for name in self.order:
            provider = self.providers[name]
            started = time.perf_counter()
            try:
                out = provider.batch_translate(sample, target, source)
                joined = " / ".join(out)
                ok = bool(joined.strip())
                report.append({
                    "name": name, "label": provider.label, "ok": ok,
                    "ms": round((time.perf_counter() - started) * 1000),
                    "sample": joined[:120],
                    "error": "" if ok else "返回空译文（接口可能已失效）",
                })
            except Exception as exc:
                report.append({
                    "name": name, "label": provider.label, "ok": False,
                    "ms": round((time.perf_counter() - started) * 1000),
                    "sample": "", "error": str(exc)[:160],
                })
        return report
