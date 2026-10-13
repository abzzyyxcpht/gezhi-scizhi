"""证据图谱：把"证据账本"从口号变成能看见的图。

回答三个科研写作中最要紧、也最容易被忽略的问题：
1. **锚定率**：文献池里有多少篇真的被综述框架引用了？
2. **孤儿文献**：哪些高分文献进了池、却没进任何章节（漏引还是该剔除）？
3. **缺证据章节**：哪些章节一个支撑文献都没有（写到这里会无话可说）？

输入是流水线已有的三个产物（lit_filter.pool → extract.records → review_frame.sections），
不引入任何新数据源，因此对历史任务同样可用。
"""
from __future__ import annotations

from typing import Any

from .textfmt import clip

# 章节 refs 与文献题名的匹配：先精确，再退化到"归一化包含"（容忍 LaTeX/标点差异）
_PUNCT = str.maketrans("", "", " 　,，.。:：;；-—_()（）[]【】\"'“”‘’")


def _norm(text: str) -> str:
    return (text or "").translate(_PUNCT).lower()


def build_graph(ctx: Any) -> dict[str, Any]:
    """构建证据图谱（ctx 为 TaskContext，读 artifacts，不写）。"""
    return build_graph_from_art(getattr(ctx, "artifacts", {}) or {},
                                getattr(ctx, "task_id", ""),
                                (getattr(ctx, "params", {}) or {}).get("topic", ""))


def build_graph_from_art(art: dict[str, Any], task_id: str = "",
                         topic: str = "") -> dict[str, Any]:
    """同一实现的产物入口：桌面版只有 artifacts 视图，不需要 TaskContext。"""
    pool = (art.get("lit_filter", {}) or {}).get("pool", []) or []
    records = (art.get("extract", {}) or {}).get("records", []) or []
    sections = (art.get("review_frame", {}) or {}).get("sections", []) or []

    title_index = {_norm(w.get("title", "")): w for w in pool if w.get("title")}
    norm_titles = list(title_index)

    nodes: list[dict[str, Any]] = []
    anchored: set[str] = set()
    for sec in sections:
        refs = [t for t in (sec.get("refs") or []) if t]
        hit: list[str] = []
        for ref in refs:
            key = _norm(ref)
            if key in title_index:                       # 精确
                hit.append(title_index[key].get("title", ref))
                continue
            for nt in norm_titles:                       # 退化：包含关系
                if key and (key in nt or nt in key):
                    hit.append(title_index[nt].get("title", ref))
                    break
        anchored.update(hit)
        nodes.append({
            "title": sec.get("title", ""),
            "points": sec.get("points", ""),
            "refs": hit,
            "claimed": len(refs),
            "missing": len(hit) == 0,          # 缺证据章节
            "dropped": len(refs) - len(hit),   # 上游校验砍掉的幻觉引用
        })

    orphans = [w for w in pool if w.get("title") and w.get("title") not in anchored]
    extracted_titles = {_norm(r.get("title", "")) for r in records if r.get("title")}
    not_extracted = [w for w in pool
                     if w.get("title") and _norm(w["title"]) not in extracted_titles]

    pool_n = len(pool)
    return {
        "task_id": task_id,
        "topic": topic,
        "pool": pool_n,
        "anchored": len(anchored),
        "orphans": orphans,
        "sections": nodes,
        "missing_sections": [n["title"] for n in nodes if n["missing"]],
        "dropped_refs": sum(n["dropped"] for n in nodes),
        "not_extracted": not_extracted,
        "anchor_rate": round(100 * len(anchored) / pool_n) if pool_n else 0,
        "orphan_rate": round(100 * len(orphans) / pool_n) if pool_n else 0,
    }


def _bar(n: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = max(1, min(width, round(n / total * width))) if n else 0
    return "█" * filled + "░" * (width - filled)


def render_graph(g: dict[str, Any]) -> list[str]:
    """ASCII 证据树：章节 → 支撑文献；外挂孤儿文献与缺证据告警。"""
    lines = [f"证据图谱 · {g['topic'] or g['task_id']}", "─" * 46]
    if not g["pool"]:
        lines.append("文献池为空——先跑完文献猎手节点。")
        return lines

    anchored = g["anchored"]
    lines.append(f"文献池 {g['pool']} 篇 · 被框架锚定 {anchored} 篇（锚定率 {g['anchor_rate']}%）"
                 f" · 孤儿 {len(g['orphans'])} 篇（{g['orphan_rate']}%）")
    lines.append(f"框架 {len(g['sections'])} 节"
                 + (f" · 缺证据 {len(g['missing_sections'])} 节" if g["missing_sections"] else "")
                 + (f" · 防幻觉校验砍掉 {g['dropped_refs']} 条引用" if g["dropped_refs"] else ""))
    lines.append("")

    if not g["sections"]:
        lines.append("尚无综述框架（extract / review_frame 节点未完成），只能展示文献池侧信息。")
    else:
        lines.append("框架节 → 支撑文献")
        for i, sec in enumerate(g["sections"]):
            mark = "○" if sec["missing"] else "●"
            tail = "　← 缺证据，写作前需补" if sec["missing"] else ""
            lines.append(f"  {mark} {sec['title']}（{len(sec['refs'])} 篇）"
                         f"{' ' + _bar(len(sec['refs']), max(1, max(len(s['refs']) for s in g['sections'])))}"
                         f"{tail}")
            shown = sec["refs"][:6]
            more = len(sec["refs"]) - len(shown)
            for i, t in enumerate(shown):
                last = (i == len(shown) - 1) and more == 0
                lines.append(f"      {'└' if last else '├'} {clip(t, 56)}")
            if more:
                lines.append(f"      └ … 另有 {more} 篇")
        lines.append("")

    if g["orphans"]:
        lines.append("孤儿文献（未被任何章节引用：补进框架，或按低相关剔除）")
        for w in sorted(g["orphans"], key=lambda x: -(x.get("score") or 0))[:10]:
            score = f"{w.get('score'):.2f}" if isinstance(w.get("score"), (int, float)) else "—"
            lines.append(f"  · [{score}] {clip(w.get('title'), 60)}")
        if len(g["orphans"]) > 10:
            lines.append(f"  · … 另有 {len(g['orphans']) - 10} 篇（/pool 查看全部）")
        lines.append("")

    if g["not_extracted"]:
        lines.append(f"未参与抽取的文献 {len(g['not_extracted'])} 篇"
                     f"（超出抽取上限，建议调大 extract 节点 limit）")

    return lines
