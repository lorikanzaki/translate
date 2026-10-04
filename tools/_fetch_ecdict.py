"""下载 ECDICT 的 ecdict.csv（约 66 MB）到本地缓存目录。

    .venv\\Scripts\\python.exe tools\\_fetch_ecdict.py

默认存到 %LOCALAPPDATA%\\ScreenTranslator\\dict\\ecdict.csv，支持断点续传。
"""
from __future__ import annotations

import os
import sys
import time

import requests

URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"


def target_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ScreenTranslator", "dict", "ecdict.csv")


def main() -> int:
    path = target_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    done = os.path.getsize(path) if os.path.exists(path) else 0
    headers = {"User-Agent": "ScreenTranslator/1.0"}
    if done:
        headers["Range"] = f"bytes={done}-"
        print(f"从 {done} 字节继续")
    started = time.perf_counter()
    with requests.get(URL, headers=headers, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0) + done
        mode = "ab" if done and response.status_code == 206 else "wb"
        if mode == "wb":
            done = 0
        got = done
        last = 0.0
        with open(path, mode) as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
                got += len(chunk)
                now = time.perf_counter()
                if now - last > 1.0:
                    last = now
                    speed = got / max(now - started, 0.001) / 1e6
                    pct = f"{got * 100 / total:.1f}%" if total else "?"
                    print(f"  {got / 1e6:7.1f} MB / "
                          f"{total / 1e6:.1f} MB  {pct:>6}  {speed:.1f} MB/s")
    print(f"完成：{path}  {os.path.getsize(path)} 字节  "
          f"耗时 {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
