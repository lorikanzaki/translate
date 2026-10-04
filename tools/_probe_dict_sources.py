"""词典数据源可达性侦察。

    .venv\\Scripts\\python.exe tools\\_probe_dict_sources.py
"""
from __future__ import annotations

import time

import requests

URLS = [
    ("ECDICT stardict.csv（GitHub raw）",
     "https://raw.githubusercontent.com/skywind3000/ECDICT/master/stardict.csv"),
    ("ECDICT 仓库页", "https://github.com/skywind3000/ECDICT"),
    ("ECDICT release", "https://github.com/skywind3000/ECDICT/releases"),
    ("jsdelivr 镜像 ECDICT", "https://cdn.jsdelivr.net/gh/skywind3000/ECDICT@master/stardict.csv"),
    ("ghproxy 镜像", "https://ghproxy.net/https://raw.githubusercontent.com/skywind3000/ECDICT/master/stardict.csv"),
    ("gitee 搜索", "https://gitee.com/api/v5/search/repositories?q=ECDICT"),
    ("huggingface", "https://huggingface.co"),
    ("huggingface ECDICT 数据集", "https://huggingface.co/datasets?search=ecdict"),
    ("modelscope", "https://www.modelscope.cn"),
    ("PyPI ecdict", "https://pypi.org/pypi/ecdict/json"),
    ("PyPI ecdict-search", "https://pypi.org/simple/ecdict/"),
    ("free dictionary api", "https://api.dictionaryapi.dev/api/v2/entries/en/hello"),
    ("有道 jsonapi", "https://dict.youdao.com/jsonapi?q=hello"),
    ("有道 suggest", "https://dict.youdao.com/suggest?q=hello&num=1&doctype=json"),
    ("金山词霸", "https://dict.iciba.com/dictionary/word/suggestion?word=hello"),
    ("百度翻译词典", "https://fanyi.baidu.com/sug"),
]


def main() -> int:
    for name, url in URLS:
        started = time.perf_counter()
        try:
            response = requests.head(url, timeout=8, allow_redirects=True)
            if response.status_code >= 400:
                response = requests.get(url, timeout=8, stream=True)
            elapsed = (time.perf_counter() - started) * 1000
            size = response.headers.get("content-length") or "?"
            dtype = response.headers.get("content-type", "")
            print(f"  [{response.status_code}] {elapsed:7.0f}ms  {name}")
            print(f"           size={size}  type={dtype[:60]}")
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  [失败] {elapsed:7.0f}ms  {name}")
            print(f"           {type(exc).__name__}: {str(exc)[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
