"""一次性脚本：把术语表里"照抄英文"但中文用户更认中文的几条改掉。

原来由子代理写入的"保留英文专名"列表里有 bluetooth→Bluetooth、ram→RAM 这类，
英文界面照抄没问题，但本工具是给中文用户看软件界面的 —— 蓝牙/内存/固态硬盘
这些在大陆版 Windows 里本来就是中文，改掉更符合预期。
"""
import collections
import json
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "core", "glossary.json")

CHANGES = collections.OrderedDict([
    ("bluetooth", "蓝牙"),
    ("ram", "内存"),
    ("rom", "只读存储器"),
    ("ssd", "固态硬盘"),
    ("hdd", "硬盘"),
    ("ui", "界面"),
])


def main() -> int:
    data = json.loads(open(PATH, encoding="utf-8").read(),
                      object_pairs_hook=collections.OrderedDict)
    for key, value in CHANGES.items():
        old = data.get(key)
        if old is None:
            print(f"缺键，跳过：{key}")
            continue
        if old == value:
            print(f"已是目标值：{key} -> {value}")
            continue
        data[key] = value
        print(f"{key}: {old!r} -> {value!r}")

    with open(PATH, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    pairs = json.loads(open(PATH, encoding="utf-8").read(),
                       object_pairs_hook=lambda kv: kv)
    keys = [k for k, _ in pairs]
    body = [k for k in keys if not k.startswith("_")]
    same = [k for k, v in pairs if not k.startswith("_") and v.strip().lower() == k.strip().lower()]
    print(f"复验：总数 {len(body)}，重复键 {len(keys) - len(set(keys))}，"
          f"值=键 {len(same)} 条 {sorted(same)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
