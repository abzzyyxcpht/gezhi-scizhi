"""F7 证据图谱：文献池内部引用网络的确定性可视化（离线、零新依赖）。

节点 = 文献池成员（按相关度排序编号，节点大小与得分正相关），
边 = 池内互相引用关系（referenced_works 双向命中），滚雪球的核心证据链。
布局用环形排序（得分降序顺时针），弧线边 + 光晕节点 + 题名卡片图例；
输出 PNG 至产物目录，供桌面「成果」页与导出清单使用。
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from ..chartstyle import BAMBOO, INDIGO, INK, INK_SOFT, SILVER, apply, save_fig
from ..config import ARTIFACT_DIR

plt = None  # 惰性导入 matplotlib（桥/桌面冷启动不付秒级代价）


def _plt():
    global plt
    if plt is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as _plt
        plt = _plt
        apply(_plt)
    return plt


def internal_edges(pool: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """池内引用边：referenced_works 命中池内成员（无向去重，返回索引对）。"""
    ids = [w.get("id", "") for w in pool]
    pos = {wid: i for i, wid in enumerate(ids) if wid}
    edges: set[tuple[int, int]] = set()
    for i, w in enumerate(pool):
        for ref in w.get("referenced_works") or []:
            j = pos.get(ref)
            if j is not None and j != i:
                edges.add((min(i, j), max(i, j)))
    return sorted(edges)


def _tier_color(score: float, smax: float) -> str:
    if smax <= 0:
        return SILVER
    ratio = score / smax
    if ratio >= 0.66:
        return BAMBOO
    if ratio >= 0.33:
        return INDIGO
    return SILVER


def _fit_text(ax, text: str, budget: float, fontsize: float,
              renderer, px_per_unit: float, weight: str = "normal") -> str:
    """按**真实渲染宽度**裁切题名（先量后切，超宽以省略号收尾）。

    早前按"字符宽度估算"裁切，字体差异会让估算偏差累积，题名会溢出卡片；
    这里用 matplotlib 的 renderer 实测文字宽度（数据单位换算），二分找最长可容纳前缀。
    """
    probe = ax.text(0, 0, "测", fontsize=fontsize, weight=weight)

    def width_units(s: str) -> float:
        probe.set_text(s)
        return probe.get_window_extent(renderer=renderer).width / px_per_unit

    try:
        if width_units(text) <= budget:
            return text
        lo, hi = 0, len(text)
        while lo < hi:                      # 二分：最长且加省略号仍不超宽的前缀
            mid = (lo + hi + 1) // 2
            if width_units(text[:mid] + "…") <= budget:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"
    finally:
        probe.remove()


def build_evidence_graph(pool: list[dict[str, Any]], out_dir: Path | None = None,
                         topic: str = "", max_nodes: int = 30) -> dict[str, Any]:
    """渲染证据图谱 PNG，返回 {nodes, edges, coverage, file, isolated, items}。

    pool 需为相关度降序列表（lit_filter.pool 原样传入即可）；
    超出 max_nodes 时截断（保高相关），isolated 为无池内引用边的文献数。
    """
    pool_total = len(pool)                 # 截断前的池规模（副标题如实标注）
    pool = pool[:max_nodes]
    edges = internal_edges(pool)
    n = len(pool)
    if not n:
        return {"nodes": 0, "edges": 0, "coverage": 0, "isolated": 0,
                "file": "", "items": [], "pool_total": pool_total}

    out_dir = Path(out_dir) if out_dir else ARTIFACT_DIR / "graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"evidence_{uuid.uuid4().hex[:6]}.png"
    out_path = out_dir / fname

    m = _plt()
    from matplotlib.patches import Circle, FancyBboxPatch, FancyArrowPatch

    # 环形布局：整体左移避让右侧题名卡片；得分降序从正上方顺时针
    # 环心略偏左下，给左上标题/副标题留出不穿环的干净通道
    cx, cy, r = -0.50, -0.18, 0.95
    if n == 1:
        # 单节点时环形退化为一点：直接放进环形区域中心，避免缩在左上角
        xs, ys = [cx], [cy]
    else:
        angles = [math.pi / 2 - 2 * math.pi * i / n for i in range(n)]
        xs = [cx + r * math.cos(a) for a in angles]
        ys = [cy + r * math.sin(a) for a in angles]
    scores = [float(w.get("score") or 0) for w in pool]
    smax = max(scores) or 1.0

    fig, ax = m.subplots(figsize=(9.6, 6.6))
    ax.set_xlim(-1.84, 2.44)
    ax.set_ylim(-1.46, 1.38)
    ax.set_aspect("equal")
    ax.axis("off")

    # ---- 弧线边（曲率随序号交错，避免直线的机械感） ----
    # 边密集时（引用滚雪球常见）降低透明度，否则 25 节点 140+ 条边
    # 会糊成"毛球"，环形结构反而看不清
    edge_alpha = 0.30 if len(edges) <= 60 else max(0.14, 0.30 * 60 / len(edges))
    for k, (i, j) in enumerate(edges):
        rad = 0.14 if k % 2 == 0 else 0.22
        arrow = FancyArrowPatch(
            (xs[i], ys[i]), (xs[j], ys[j]),
            connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-", color=INDIGO, alpha=edge_alpha,
            lw=0.6 + 1.0 * min(scores[i], scores[j]) / smax,
            zorder=1, capstyle="round")
        ax.add_patch(arrow)

    # ---- 光晕 + 节点 + 编号 ----
    # 节点尺寸随文献数收缩：环形弧长 ≈ 2πr/n 必须容得下相邻两节点的直径，
    # 否则大文献池（如 25 篇）会看到节点相互压盖
    arc = 2 * math.pi * r / max(n, 1)
    dia_cap = max(0.055, min(0.32, arc - 0.030))     # 直径上限（留 0.03 间隙）
    base_r = min(0.085, dia_cap * 0.31)
    grow_r = max(0.0, min(0.075, dia_cap / 2 - base_r))
    for i, w in enumerate(pool):
        s = scores[i]
        color = _tier_color(s, smax)
        node_r = base_r + grow_r * (s / smax)
        # 光晕半径收敛一档：衬出节点即可，大面积交叠会把环形糊成串
        halo_r = min(node_r * 1.35, arc / 2)
        ax.add_patch(Circle((xs[i], ys[i]), halo_r,
                            color=color, alpha=0.14, lw=0, zorder=2))
        ax.add_patch(Circle((xs[i], ys[i]), node_r,
                            facecolor=color, edgecolor="white",
                            linewidth=1.4, zorder=3))
        label = str(i + 1)
        size = max(6.0, (7.5 + 3 * (s / smax)) * min(1.0, node_r / 0.14))   # 小节点配小字号（下限保可读）
        if len(label) > 1:                     # 两位数在圆里容易挤，略缩
            size = max(6.0, size * 0.86)       # 下限在缩放后再取，否则 6.0×0.86 反而不可读
        # 银灰（低相关档）底上白字对比不足，改用墨色
        ax.annotate(label, (xs[i], ys[i]), ha="center", va="center",
                    fontsize=size, weight="bold", zorder=4,
                    color=INK if color == SILVER else "white")

    # ---- 右侧题名卡片（圆角纸片 + 得分色点 + 题名 + 右对齐分数列） ----
    # 卡片顶与标题区顶同高（1.24），左右两块视觉基线对齐
    shown = min(n, 12)
    card_x, card_w = 0.80, 1.52
    card_h = 0.30 + shown * 0.088
    card_top = 1.24
    card = FancyBboxPatch((card_x, card_top - card_h), card_w, card_h,
                          boxstyle="round,pad=0.03,rounding_size=0.045",
                          facecolor="white", edgecolor="#E3DCCB",
                          linewidth=1.0, zorder=5)
    ax.add_patch(card)
    score_x = card_x + card_w - 0.12
    title_x = card_x + 0.20
    # 真实测量所需的换算：必须用坐标轴实际像素宽（figure 宽含默认边距，
    # 直接用画布宽会把单位像素高估 ~29%，题名尾部溢出到分数列）
    renderer = fig.canvas.get_renderer()
    px_per_unit = ax.get_window_extent(renderer=renderer).width / (2.44 - (-1.84))
    # 分数列按真实渲染宽度预留：此前固定扣 0.10，但 7pt 分数串约 0.22 单位，
    # 题名尾部会被分数串叠压。先量分数列宽度，再定题名可用宽。
    probe_score = ax.text(0, 0, "0.00", fontsize=7.0, family="DejaVu Sans")
    score_w = probe_score.get_window_extent(renderer=renderer).width / px_per_unit
    probe_score.remove()
    avail_w = (score_x - score_w - 0.08) - title_x     # 题名可用宽度（数据单位）
    for i in range(shown):
        y = card_top - 0.24 - i * 0.088
        ax.add_patch(Circle((card_x + 0.13, y), 0.015,
                            color=_tier_color(scores[i], smax), zorder=6))
        raw = pool[i].get("title") or ""
        fitted = _fit_text(ax, f"{i + 1}. {raw}", avail_w, 7.3, renderer, px_per_unit)
        ax.annotate(fitted,
                    (title_x, y), ha="left", va="center", fontsize=7.3,
                    color=INK, zorder=6)
        ax.annotate(f"{scores[i]:.2f}", (score_x, y), ha="right", va="center",
                    fontsize=7.0, color=INK_SOFT, zorder=6,
                    family="DejaVu Sans")

    # ---- 标题与统计行（位于环形上方留白带；与右侧题名卡片顶对齐） ----
    isolated = sum(1 for i in range(n) if all(i not in e for e in edges))
    title_text = f"证据图谱 · {topic or '文献池'}"
    ax.text(-1.76, 1.24,
            _fit_text(ax, title_text, 2.44 - (-1.76) - 0.06, 15,
                      renderer, px_per_unit, weight="bold"),
            fontsize=15, weight="bold", color=INK, ha="left", va="top")
    ax.text(-1.76, 1.04,
            (f"文献池 {pool_total} 篇"
             + (f"（图示相关度 Top {n}）" if pool_total > n else "")
             + f" · 池内引用 {len(edges)} 条 · 孤立 {isolated} 篇"),
            fontsize=9, color=INK_SOFT, ha="left", va="top")
    # 编码说明：颜色与大小表示相关度、连线表示池内引用（不让人猜图例）
    ax.annotate("节点大小与颜色 = 相关度（绿 ≥66% · 蓝 33–66% · 灰 <33%）　连线 = 池内引用",
                (card_x + 0.02, card_top - card_h - 0.12), ha="left",
                va="top", fontsize=7.4, color=INK_SOFT, zorder=6)
    ax.text(2.38, -1.40, "格致 SciZhi · 证据账本",
            fontsize=9, color=INK_SOFT, ha="right", va="bottom", alpha=0.75)

    save_fig(fig, out_path)
    m.close(fig)

    return {
        "nodes": n,
        "edges": len(edges),
        "coverage": round(len(edges) / max(n, 1), 2),
        "isolated": isolated,
        "file": str(out_path),
        "pool_total": pool_total,
        "items": [{"i": i + 1, "title": (pool[i].get("title") or "")[:60]}
                  for i in range(min(n, 12))],
    }
