"""科研看板与时间账：把文献池与流水线过程变成可读的科研态势图。

board(ctx)   —— 单任务看板：年份分布 / 评分分布 / 收敛曲线 / 元数据覆盖 /
                池健康度 / Top 来源 / Top 术语 / 单任务时间账。
ledger(tasks)—— 跨任务时间账：累计文献、轮次与省下的时间（评审"实现成效"
                与"社会效益"的直接佐证，源自项目自身的量化目标）。
纯离线计算，零外部依赖；渲染走 ASCII 图，TUI / 桌面 / 报告均可复用。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .textfmt import pad
from .textstats import terms

# 时间账口径（与计划书"文献调研从按周算压到按小时算"一致，TUI 菜单同公式）
MINUTES_PER_WORK = 6      # 人工读摘要+记录一篇的估计耗时
MINUTES_PER_ROUND = 25    # 人工完成一轮检索/筛选/整理的估计耗时


def saved_minutes(n_works: int, n_rounds: int) -> int:
    return n_works * MINUTES_PER_WORK + n_rounds * MINUTES_PER_ROUND


def _fmt_minutes(minutes: int) -> str:
    if minutes >= 60:
        return f"{minutes / 60:.1f} 小时"
    return f"{minutes} 分钟"


def _bar(n: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(n / total * width)))
    return "█" * filled + "░" * (width - filled)


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 40) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    peak = max(vals) or 1.0
    out = "".join(_SPARK[min(len(_SPARK) - 1, int(v / peak * (len(_SPARK) - 1)))]
                  for v in vals[:width])
    return out


def board(ctx: Any) -> dict[str, Any]:
    """单任务科研看板（ctx 为 TaskContext）。"""
    art = ctx.artifacts or {}
    pool = art.get("lit_filter", {}).get("pool", []) or []
    rounds = art.get("lit_filter", {}).get("rounds", []) or []

    years = sorted({int(w["year"]) for w in pool if str(w.get("year") or "").isdigit()})
    year_hist = Counter(int(w["year"]) for w in pool if str(w.get("year") or "").isdigit())
    median_year = years[len(years) // 2] if years else None

    score_hist = [0] * 10
    for w in pool:
        s = w.get("score")
        if isinstance(s, (int, float)):
            score_hist[max(0, min(9, int(float(s) * 10)))] += 1

    n = len(pool) or 1
    coverage = {
        "doi": round(100 * sum(1 for w in pool if w.get("doi")) / n),
        "abstract": round(100 * sum(1 for w in pool if w.get("abstract")) / n),
        "venue": round(100 * sum(1 for w in pool if w.get("venue")) / n),
        "pages": round(100 * sum(1 for w in pool if w.get("pages")) / n),
    }
    health = round(coverage["doi"] * 0.35 + coverage["abstract"] * 0.35
                   + coverage["venue"] * 0.2 + coverage["pages"] * 0.1)

    venue_top = Counter(w.get("venue") for w in pool if w.get("venue")).most_common(5)
    text = " ".join(f"{w.get('title', '')} {w.get('abstract', '')}" for w in pool)
    term_top = Counter(terms(text)).most_common(10)

    total_saved = saved_minutes(len(pool), len(rounds))
    return {
        "task_id": getattr(ctx, "task_id", ""),
        "topic": (ctx.params or {}).get("topic", ""),
        "pool_size": len(pool),
        "year_hist": dict(sorted(year_hist.items())),
        "year_span": (years[0], years[-1]) if years else None,
        "median_year": median_year,
        "score_hist": score_hist,
        "rounds": [{"round": r.get("round"), "candidates": r.get("candidates", 0),
                    "new_relevant": r.get("new_relevant", 0),
                    "ratio": r.get("ratio", 0.0)} for r in rounds],
        "coverage": coverage,
        "health": health,
        "venue_top": venue_top,
        "term_top": term_top,
        "saved_minutes": total_saved,
        "saved_text": _fmt_minutes(total_saved),
        "status": getattr(ctx, "status", ""),
    }


def ledger(tasks: list[dict[str, Any]], pool_sizes: dict[str, int] | None = None,
           round_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """跨任务时间账（tasks 为 service.list_tasks() 的风格化列表）。"""
    pool_sizes = pool_sizes or {}
    round_counts = round_counts or {}
    n_works = sum(pool_sizes.get(t["task_id"], 0) for t in tasks)
    n_rounds = sum(round_counts.get(t["task_id"], 0) for t in tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    total = saved_minutes(n_works, n_rounds)
    return {
        "tasks": len(tasks), "done": done,
        "works": n_works, "rounds": n_rounds,
        "saved_minutes": total, "saved_text": _fmt_minutes(total),
        "recent": [{"task_id": t["task_id"], "topic": t.get("topic", ""),
                    "status_text": t.get("status_text", ""),
                    "works": pool_sizes.get(t["task_id"], 0),
                    "created_at": t.get("created_at", "")} for t in tasks[:5]],
    }


# ---------------------------------------------------------------------------
# 渲染（ASCII 图，供 TUI 直接逐行打印 / 阅读器分页）
# ---------------------------------------------------------------------------

def render_board(b: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(f"科研看板 · {b['topic'] or b['task_id']}（{b['status'] or '—'}）")
    lines.append("─" * 46)
    if not b["pool_size"]:
        lines.append("文献池为空——任务尚未跑到文献猎手节点，或本轮未筛出相关文献。")
        return lines

    span = f"{b['year_span'][0]}—{b['year_span'][1]}" if b["year_span"] else "年份缺失"
    lines.append(f"文献池 {b['pool_size']} 篇 · 年份跨度 {span} · 中位年 {b['median_year'] or '—'}")
    lines.append("")

    lines.append("年份分布（近 8 年）")
    yh = b["year_hist"]
    recent = list(yh.items())[-8:]
    top = max((v for _, v in recent), default=1)
    for y, cnt in recent:
        lines.append(f"  {y}  {_bar(cnt, top, 20)} {cnt}")
    lines.append("")

    if b["rounds"]:
        lines.append("递归收敛曲线（各轮新增相关占比）")
        ratios = [r["ratio"] for r in b["rounds"]]
        lines.append(f"  占比 {_sparkline(ratios)}  阈值 5%")
        for r in b["rounds"]:
            lines.append(f"  第 {r['round']} 轮：候选 {r['candidates']:>3} → 保留 "
                         f"{r['new_relevant']:>2}（{r['ratio'] * 100:.1f}%）")
        lines.append("")

    lines.append("相关度分布")
    sh = b["score_hist"]
    top = max(sh) or 1
    for i, cnt in enumerate(sh):
        lines.append(f"  {i / 10:.1f}–{(i + 1) / 10:.1f}  {_bar(cnt, top, 20)} {cnt}")
    lines.append("")

    cov = b["coverage"]
    lines.append("元数据完整度")
    for key, label in (("doi", "DOI"), ("abstract", "摘要"),
                       ("venue", "来源"), ("pages", "页码")):
        lines.append(f"  {pad(label, 4)} {_bar(cov[key], 100, 18)} {cov[key]:>3}%")
    lines.append(f"  池健康度 {b['health']}/100"
                 + ("（良好，可支撑综述写作）" if b["health"] >= 75
                    else "（建议用 DOI 反查补全缺失元数据）"))
    lines.append("")

    if b["venue_top"]:
        lines.append("来源 Top")
        top = b["venue_top"][0][1]
        for i, (venue, cnt) in enumerate(b["venue_top"], 1):
            lines.append(f"  {i}. {pad(str(venue), 34)} {_bar(cnt, top, 10)} {cnt}")
        lines.append("")

    if b["term_top"]:
        lines.append("研究热词 Top 10")
        top = b["term_top"][0][1]
        for term, cnt in b["term_top"]:
            lines.append(f"  {pad(term, 20)} {_bar(cnt, top, 16)} {cnt}")
        lines.append("")

    lines.append(f"时间账：本任务已为你省下约 {b['saved_text']} 事务时间")
    return lines


def render_ledger(ledger: dict[str, Any]) -> list[str]:
    lines = ["研究时间账（跨任务累计）", "─" * 46]
    if not ledger["tasks"]:
        lines.append("暂无任务档案——输入研究方向即开工，完成后回来看你的时间账。")
        return lines
    lines.append(f"任务 {ledger['tasks']} 项（已完成 {ledger['done']}）· 文献累计 {ledger['works']} 篇 "
                 f"· 递归 {ledger['rounds']} 轮")
    lines.append(f"累计省下约 {ledger['saved_text']} 事务时间")
    lines.append("")
    lines.append("最近任务")
    for r in ledger["recent"]:
        lines.append(f"  {r['created_at']:<16} {pad(r['topic'], 26)} "
                     f"{r['works']:>3} 篇 · {r['status_text']}")
    return lines
