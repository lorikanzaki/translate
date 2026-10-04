"""扫一遍工作区，看还有没有硬编码的凭证（跑完就删）。"""
import os
import re

ROOT = r"C:\Users\asus\Desktop\translate"
SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules"}

PATTERNS = [
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Google OAuth secret", re.compile(r"GOCSPX-[0-9A-Za-z_\-]{20,}")),
    ("Google access token", re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("GitHub token", re.compile(r"ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{20,}")),
    ("OpenAI key", re.compile(r"sk-[0-9A-Za-z]{20,}")),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer token", re.compile(r"Bearer\s+[0-9A-Za-z._\-]{25,}")),
    ("含 apikey/key 的赋值", re.compile(
        r"""(?i)\b(api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret|token)\b"""
        r"""\s*[:=]\s*["']([^"'\s]{16,})["']""")),
    ("含 KEY 的大写变量赋值", re.compile(
        r"""(?m)^\s*[A-Z][A-Z0-9_]*KEY[A-Z0-9_]*\s*=\s*["']([^"'\s]{16,})["']""")),
]

EXT = {".py", ".json", ".md", ".txt", ".bat", ".cmd", ".ps1", ".yml", ".yaml", ".ini", ".cfg", ".toml", ".js", ".ts"}

hits = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for name in filenames:
        if os.path.splitext(name)[1].lower() not in EXT:
            continue
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, ROOT)
        try:
            text = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, rx in PATTERNS:
                m = rx.search(line)
                if m:
                    value = m.group(0)
                    if len(m.groups()) >= 2 and m.group(2):
                        value = m.group(2)
                    masked = value[:10] + "…" + value[-4:] if len(value) > 16 else value
                    hits.append((rel, lineno, label, masked, line.strip()[:90]))

print(f"扫了 {sum(1 for _ in EXT)} 种扩展名，命中 {len(hits)} 处：\n")
seen = set()
for rel, lineno, label, masked, snippet in hits:
    key = (rel, lineno, label)
    if key in seen:
        continue
    seen.add(key)
    print(f"  [{label}] {rel}:{lineno}\n      {masked}\n      {snippet}\n")
if not hits:
    print("  干净，没有发现硬编码凭证。")
