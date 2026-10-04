"""一次性脚本：给 core/glossary.json 追加"菜单/系统项"补充条目。

只追加缺失的键，不覆盖任何已有条目；写回时保持 UTF-8、无 BOM、2 空格缩进。
"""
import collections
import json
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "core", "glossary.json")

EXTRA = collections.OrderedDict([
    # 菜单 / 文件操作
    ("open recent", "最近打开"),
    ("recent", "最近"),
    ("open recent files", "打开最近使用的文件"),
    ("recently used", "最近使用"),
    ("open file location", "打开文件所在位置"),
    ("pin to taskbar", "固定到任务栏"),
    ("unpin from taskbar", "从任务栏取消固定"),
    ("pin to quick access", "固定到快速访问"),
    ("eject", "弹出"),
    ("eject device", "弹出设备"),
    ("safely remove hardware", "安全删除硬件"),
    # 右键 / 属性
    ("run as administrator", "以管理员身份运行"),
    ("troubleshoot compatibility", "兼容性疑难解答"),
    ("compatibility mode", "兼容模式"),
    ("open with", "打开方式"),
    ("share with", "共享给"),
    ("send to", "发送到"),
    ("restore previous versions", "还原以前的版本"),
    # Windows 系统项
    ("safe mode", "安全模式"),
    ("power options", "电源选项"),
    ("default apps", "默认应用"),
    ("system information", "系统信息"),
    ("device specifications", "设备规格"),
    ("windows update", "Windows 更新"),
    ("advanced options", "高级选项"),
    ("reset this pc", "重置此电脑"),
    ("disk cleanup", "磁盘清理"),
    ("startup apps", "启动应用"),
    ("scheduled tasks", "任务计划程序"),
    ("command prompt", "命令提示符"),
    ("sign out", "注销"),
    ("shut down", "关机"),
    ("sleep", "睡眠"),
    ("hibernate", "休眠"),
    # 常用对话框
    ("don't save", "不保存"),
    ("keep changes", "保留更改"),
    ("apply to all", "全部应用"),
    ("do not ask again", "不再询问"),
    ("show more options", "显示更多选项"),
    ("learn more", "了解详细信息"),
    ("view details", "查看详细信息"),
    ("hide details", "隐藏详细信息"),
    ("copy details", "复制详细信息"),
    ("report this problem", "报告此问题"),
])


def main() -> int:
    raw = open(PATH, encoding="utf-8").read()
    data = json.loads(raw, object_pairs_hook=collections.OrderedDict)
    print(f"原有 {len([k for k in data if not k.startswith('_')])} 条")

    added = []
    for key, value in EXTRA.items():
        if key in data:
            continue
        if key != key.strip().lower():
            print(f"键不合规，跳过: {key!r}")
            continue
        data[key] = value
        added.append(key)

    with open(PATH, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"新增 {len(added)} 条：{added}")

    pairs = json.loads(open(PATH, encoding="utf-8").read(),
                       object_pairs_hook=lambda kv: kv)
    keys = [k for k, _ in pairs]
    body = [k for k in keys if not k.startswith("_")]
    print(f"复验：总数 {len(body)}，重复键 {len(keys) - len(set(keys))}，"
          f"键不规范 {sum(1 for k in body if k != k.strip().lower())}，"
          f"空值 {sum(1 for _, v in pairs if not v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
