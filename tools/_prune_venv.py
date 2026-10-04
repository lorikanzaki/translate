"""把 .venv 里「Qt Widgets 程序用不到」的东西挪出去，给项目瘦身。

用法：
    .venv\\Scripts\\python.exe tools\\_prune_venv.py             # 先看清单（不动手）
    .venv\\Scripts\\python.exe tools\\_prune_venv.py apply       # 真的挪
    .venv\\Scripts\\python.exe tools\\_prune_venv.py restore     # 全部挪回来
    .venv\\Scripts\\python.exe tools\\_prune_venv.py apply fc    # 只挪第一组

**只挪不删**。所有东西都挪到 `%TEMP%\\translate_prune\\<时间戳>\\` 里保留原目录
结构，`restore` 能原样搬回来；确认没问题后你自己删那个临时目录即可。

判断依据：本程序只用到 PySide6 的 **QtCore / QtGui / QtWidgets** 三件套
（`PySide6.QtWebEngine* / QtQml / QtQuick / QtMultimedia / QtPdf / QtSql …`
一个都没 import）。以下是**故意保留**的，别以为漏了：
  · `opengl32sw.dll` —— 软件 OpenGL 兜底。删掉省 20 MB，但万一显卡驱动出问题
    就会整窗全黑，不划算。
  · `Qt6Network / Qt6Svg / Qt6Xml / Qt6OpenGL / Qt6PrintSupport / Qt6DBus` ——
    体积小（合计约 13 MB）且 Qt6Widgets/Qt6Gui 有静态引用，留着最稳。
  · `plugins/` 里的 `platforms`（qwindows 平台插件，**没它起不来**）、
    `styles`、`imageformats`（背景图要读 jpg/png）、`iconengines`、
    `platforminputcontexts`（中文输入法要用）、`generic`。
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, ".venv", "Lib", "site-packages")
PYSIDE = os.path.join(SITE, "PySide6")
STAMP = os.path.join(os.environ.get("TEMP", "."), "translate_prune")

# 每一组：名字 -> (要挪走的东西, 要整体挪走的子目录, 要保留的前缀例外)
# 「要挪走的东西」写成 (前缀, 后缀) 列表 —— 用 startswith 判前缀、endswith 判后缀。
# **不能用「前缀 + \.」的正则**：那样 `Qt6WebEngineCore.dll` 匹配不上
# `Qt6WebEngine\.`（中间还有个 Core），第一版就踩了这个坑，WebEngine 那 147 MB
# 一点没挪走。
WEBENGINE = [
    (prefix, suffix)
    for prefix in (
        "Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets", "Qt6WebView",
        "Qt6Positioning", "Qt6Location", "Qt6Nfc", "Qt6NetworkAuth",
        "QtWebEngine", "QtWebChannel", "QtWebSockets", "QtWebView",
        "pyside6qml.abi3",
    )
    for suffix in (".pyd", ".dll", ".exe", ".pyi", ".abi3.dll")
]
QML = [
    (prefix, suffix)
    for prefix in (
        "Qt63D", "Qt3D", "Qt6Bluetooth", "QtBluetooth", "Qt6Charts", "QtCharts",
        "Qt6DataVisualization", "QtDataVisualization",
        "Qt6Designer", "QtDesigner", "Qt6Graphs", "QtGraphs",
        "QtExampleIcons", "QtAxContainer",
        "Qt6Help", "QtHelp", "Qt6HttpServer", "QtHttpServer", "Qt6Labs",
        "Qt6Multimedia", "QtMultimedia", "Qt6Pdf", "QtPdf",
        "Qt6Qml", "QtQml", "Qt6Quick", "QtQuick",
        "Qt6RemoteObjects", "QtRemoteObjects", "Qt6Scxml", "QtScxml",
        "Qt6Sensors", "QtSensors", "Qt6SerialBus", "QtSerialBus",
        "Qt6SerialPort", "QtSerialPort", "Qt6ShaderTools",
        "Qt6SpatialAudio", "QtSpatialAudio", "Qt6Sql", "QtSql",
        "Qt6StateMachine", "QtStateMachine", "Qt6Test", "QtTest",
        "Qt6TextToSpeech", "QtTextToSpeech", "Qt6UiTools", "QtUiTools",
        "Qt6VirtualKeyboard", "QtVirtualKeyboard",
    )
    for suffix in (".pyd", ".dll", ".pyi")
]
DEVTOOLS = [
    (prefix, ".exe")
    for prefix in (
        "assistant", "balsam", "balsamui", "designer", "linguist", "lrelease",
        "lupdate", "qmlcachegen", "qmlformat", "qmlimportscanner", "qmllint",
        "qmlls", "qmltyperegistrar", "qsb", "rcc", "svgtoqml", "uic",
    )
]
FFMPEG = [
    (prefix, suffix)
    for prefix in ("avcodec", "avformat", "avutil", "swresample", "swscale")
    for suffix in (".dll",)
]

GROUPS = {
    # ① 浏览器内核（QtWebEngine）：147 MB 的 Qt6WebEngineCore.dll 就在这儿。
    #    程序用的是 QTextBrowser（QtWidgets 自带的富文本），跟 WebEngine 无关。
    "webengine": (WEBENGINE, ("resources",)),
    # ② QML / Quick / Qt3D / 图表 / 多媒体 / PDF / 数据库驱动……全都没用。
    "qml": (QML, ("qml", "metatypes", "QtAsyncio")),
    # ③ 开发工具与类型提示文件：只在写代码时用，运行时一个字都不读。
    "devtools": (DEVTOOLS + [("*", ".pyi")],
                 ("glue", "include", "scripts", "support", "doc", "typesystems")),
    # ④ Qt 自带的界面翻译文件：程序从来没 QTranslator.load() 过，全是死重。
    #    只留 qtbase_zh_CN.qm（130 KB），以后想让 Qt 内置对话框说中文随时能用。
    "translations": ([], ()),  # 特殊处理，见 prune_translations()
    # ⑤ ffmpeg 动态库：QtMultimedia / WebEngine 音视频解码用。
    "ffmpeg": (FFMPEG, ()),
    # ⑥ 纯 Python 的数学符号库：onnxruntime 的依赖，但只是 tools 里的
    #    symbolic shape inference 会 import，推理本身用不到。
    "sympy": ([], ()),  # 特殊处理，见 prune_packages()
}

EXTRA_PACKAGES = ("sympy", "mpmath")


def human(count: int) -> str:
    return f"{count / 1048576:,.1f} MB"


def dir_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def move(src: str, dest: str, apply: bool) -> int:
    """挪一个文件或目录，返回字节数。"""
    if not os.path.exists(src):
        return 0
    size = dir_size(src) if os.path.isdir(src) else os.path.getsize(src)
    if apply:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            return 0
        shutil.move(src, dest)
    return size


def prune_translations(box: str, apply: bool) -> int:
    folder = os.path.join(PYSIDE, "translations")
    if not os.path.isdir(folder):
        return 0
    keep = {"qtbase_zh_CN.qm"}
    total = 0
    for name in sorted(os.listdir(folder)):
        if name in keep:
            continue
        total += move(os.path.join(folder, name),
                      os.path.join(box, "translations", name), apply)
    if apply and not os.listdir(folder):
        os.rmdir(folder)
    return total


def prune_packages(box: str, apply: bool) -> int:
    total = 0
    for name in EXTRA_PACKAGES:
        total += move(os.path.join(SITE, name),
                      os.path.join(box, "packages", name), apply)
    return total


def prune_pycache(apply: bool) -> int:
    """__pycache__ 直接删（会自动重新生成，没有恢复的必要）。"""
    total = 0
    for root in (ROOT, SITE):
        for base, dirs, _files in os.walk(root):
            if os.path.basename(base) == "__pycache__":
                continue
            if "__pycache__" in dirs:
                target = os.path.join(base, "__pycache__")
                total += dir_size(target)
                if apply:
                    shutil.rmtree(target, ignore_errors=True)
                dirs.remove("__pycache__")
    return total


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    only = sys.argv[2:] if len(sys.argv) > 2 else []
    apply = mode in ("apply", "restore")

    if mode == "restore":
        if not os.path.isdir(STAMP):
            print(f"没有找到隔离目录 {STAMP}")
            return 1
        stamps = sorted(d for d in os.listdir(STAMP)
                        if os.path.isdir(os.path.join(STAMP, d)))
        if not stamps:
            print(f"{STAMP} 下没有可恢复的内容")
            return 1
        box = os.path.join(STAMP, stamps[-1])
        print(f"从 {box} 恢复……")
        restored = 0
        for base, dirs, files in os.walk(box):
            rel = os.path.relpath(base, box)
            if rel == ".":
                continue
            head = rel.split(os.sep)[0]
            if head == "translations":
                target = os.path.join(PYSIDE, "translations", *rel.split(os.sep)[1:])
            elif head == "packages":
                target = os.path.join(SITE, *rel.split(os.sep)[1:])
            else:
                target = os.path.join(PYSIDE, rel)
            os.makedirs(target, exist_ok=True)
            for name in files:
                src = os.path.join(base, name)
                dst = os.path.join(target, name)
                if not os.path.exists(dst):
                    shutil.move(src, dst)
                    restored += 1
        print(f"恢复 {restored} 个文件")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    box = os.path.join(STAMP, stamp)
    print("=" * 78)
    print(f"模式：{'真挪' if apply else '预演（加 apply 才动手）'}")
    print(f"隔离目录：{box}")
    print("=" * 78)

    # site-packages 的大小（保持索引稳定）
    before = dir_size(SITE)
    rows = []

    if not only or "pycache" in only:
        size = prune_pycache(apply)
        rows.append(("__pycache__（直接删，会自动重建）", size))

    if not only or "translations" in only:
        rows.append(("PySide6/translations（只留 qtbase_zh_CN.qm）",
                     prune_translations(box, apply)))

    if not only or "sympy" in only:
        rows.append(("sympy + mpmath（onnxruntime 推理不需要）",
                     prune_packages(box, apply)))

    for group, (rules, subdirs) in GROUPS.items():
        if group in ("translations", "sympy"):
            continue
        if only and group not in only:
            continue
        total = 0
        for name in sorted(os.listdir(PYSIDE)):
            full = os.path.join(PYSIDE, name)
            if not os.path.isfile(full):
                continue
            if any(name.startswith(prefix) and name.endswith(suffix)
                   for prefix, suffix in rules):
                total += move(full, os.path.join(box, "PySide6", name), apply)
        for name in subdirs:
            total += move(os.path.join(PYSIDE, name),
                          os.path.join(box, "PySide6", name), apply)
        rows.append((f"{group}", total))

    print(f"{'项目':<44}{'可省':>12}")
    print("-" * 78)
    for label, size in rows:
        print(f"{label:<44}{human(size):>12}")
    total = sum(size for _label, size in rows)
    print("-" * 78)
    print(f"{'合计':<44}{human(total):>12}")

    if apply:
        after = dir_size(SITE)
        print("-" * 78)
        print(f"site-packages：{human(before)}  ->  {human(after)}"
              f"（省了 {human(before - after)}）")
        print(f"\n不满意就运行：python tools\\_prune_venv.py restore")
        print(f"确认没问题后可以删掉：{STAMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
