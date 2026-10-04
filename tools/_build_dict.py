"""把下载好的 ECDICT CSV 建成 SQLite 词典库（命令行版，设置面板里也走同一个函数）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dictdb import build_db, csv_path, db_path


def main() -> int:
    source, target = csv_path(), db_path()
    if not os.path.exists(source):
        print(f"没找到 {source}，先跑 tools\\_fetch_ecdict.py")
        return 1
    print(f"源：{source}")
    print(f"目标：{target}")
    last = {"t": 0.0}

    def progress(message: str) -> None:
        """进度回调只给一句现成的文字，直接打出来。"""
        last["t"] = time.perf_counter()
        print(message, flush=True)

    started = time.perf_counter()
    kept, forms = build_db(source, target, progress=progress, force=True)
    print(f"完成：收录 {kept} 条，词形 {forms} 条，"
          f"耗时 {time.perf_counter() - started:.1f}s")
    print(f"库文件：{target}  {os.path.getsize(target) / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
