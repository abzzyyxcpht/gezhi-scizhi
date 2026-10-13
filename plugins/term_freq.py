"""内置插件：术语词频统计（综述焦点分析）。

命令：/freq <文本或CSV路径> [topN]
统计口径与科研看板共用 src/textstats.py，保证同一份术语表。
文献池 CSV（如导出目录下的 文献池.csv）自动只统计题名/理由/摘要列。
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from src.orchestrator.engine import TaskContext
from src.textfmt import pad
from src.textstats import terms as _terms


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            rows = list(csv.DictReader(f))
            cols = [c for c in (rows[0].keys() if rows else {})
                    if any(k in c for k in ("题名", "理由", "title", "摘要"))]
            if not cols:
                cols = list(rows[0].keys())[:2] if rows else []
            return "\n".join(" ".join(str(r.get(c, "")) for c in cols) for r in rows)
    return path.read_text(encoding="utf-8", errors="replace")


def run(args: str, ctx: TaskContext | None) -> str:
    parts = (args or "").split()
    if not parts:
        return "用法：/freq <文本或CSV路径> [topN]（如 /freq data/exports/xxx/文献摘要整理稿.md 20）"
    path = Path(parts[0])
    if not path.is_absolute():
        path = Path.cwd() / path
    top_n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
    if not path.exists():
        return f"文件不存在：{path}"
    counter = Counter(_terms(_read_text(path)))
    if not counter:
        return "未统计到有效术语（文件为空或全是停用词）"
    lines = [f"术语词频 Top {min(top_n, len(counter))}（{path.name}）"]
    for i, (term, n) in enumerate(counter.most_common(top_n), 1):
        bar = "▇" * max(1, min(20, n))
        lines.append(f"  {i:>2}. {pad(term, 20)} {bar} {n}")
    return "\n".join(lines)


PLUGIN = {
    "name": "termfreq",
    "version": "1.1",
    "description": "术语词频统计（与科研看板同口径），辅助综述焦点分析",
    "commands": {"freq": {"help": "术语词频统计（与科研看板同口径）：<文本或CSV路径> [topN]", "run": run}},
}
