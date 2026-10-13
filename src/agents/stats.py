"""F3 数据速览：实验数据初步统计与可视化。

支持 CSV / Excel / 纯文本（空白分隔）上传，输出描述性统计、异常值标记、
相关性分析与图表建议；图表用 matplotlib 渲染为 PNG 存至产物目录。

重依赖（pandas / numpy / matplotlib）一律惰性导入：桥/桌面冷启动只付
轻量模块的代价，工作台首命令不再等 4 秒（实测省约 3.5s）。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..config import ARTIFACT_DIR
from ..logging_util import log_tool

_MPL_READY = False


def _mpl():
    """惰性加载 matplotlib（Agg 后端 + 中文字体回退链），返回 pyplot。"""
    global _MPL_READY
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not _MPL_READY:
        # Windows 下中文字体；缺失时退回英文标签
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        _MPL_READY = True
    return plt


def load_table(path: Path):
    """按扩展名加载数据文件（pandas 惰性导入）。"""
    import pandas as pd
    suf = path.suffix.lower()
    if suf == ".csv":
        return pd.read_csv(path)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path)
    # 纯文本：尝试空白/制表符分隔
    return pd.read_csv(path, sep=r"\s+", engine="python")


def analyze(path: Path) -> dict[str, Any]:
    """统计主流程：返回结构化结果与图表文件路径。"""
    import numpy as np
    df = load_table(path)
    num = df.select_dtypes(include=[np.number])
    result: dict[str, Any] = {
        "shape": list(df.shape),
        "columns": df.columns.tolist(),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "missing": {c: int(df[c].isna().sum()) for c in df.columns if df[c].isna().sum() > 0},
        "describe": {},
        "outliers": {},
        "correlations": [],
        "charts": [],
        "chart_advice": [],
    }

    if not num.empty:
        desc = num.describe().round(4)
        result["describe"] = {c: {k: float(v) for k, v in desc[c].items()}
                              for c in desc.columns}

        # 异常值：IQR 规则
        for c in num.columns:
            q1, q3 = num[c].quantile(0.25), num[c].quantile(0.75)
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            idx = num[(num[c] < lo) | (num[c] > hi)].index.tolist()
            if idx:
                result["outliers"][c] = {
                    "rows": idx[:20], "rule": "IQR×1.5",
                    "bounds": [round(float(lo), 4), round(float(hi), 4)],
                }

        # 相关性
        if num.shape[1] >= 2:
            corr = num.corr().round(3)
            pairs = []
            cols = corr.columns.tolist()
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    v = corr.iloc[i, j]
                    if abs(v) >= 0.5:
                        pairs.append({"pair": [cols[i], cols[j]], "r": float(v)})
            result["correlations"] = pairs

        result["charts"] = _render_charts(num, path.stem)
        result["chart_advice"] = _chart_advice(df, num)

    log_tool("stats", "pandas", f"file={path.name} shape={df.shape}",
             f"outlier_cols={list(result['outliers'])}", 0, "success")
    return result


def _render_charts(num, stem: str) -> list[str]:
    """渲染直方图与相关性热图（品牌主题），返回文件名列表。"""
    from .. import chartstyle
    plt = _mpl()
    chartstyle.apply(plt)
    files: list[str] = []
    out_dir = ARTIFACT_DIR / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    for c in num.columns[:3]:  # 直方图最多 3 列，控制产物量
        series = num[c].dropna()
        fig, ax = plt.subplots(figsize=(6.4, 4.0))
        ax.hist(series, bins=20, color=chartstyle.INDIGO,
                edgecolor="white", linewidth=0.8, alpha=0.95, zorder=2)
        ax.margins(y=0.16)
        # 参考线：均值（朱砂实线）/ 中位数（竹青虚线）；标签放顶部留白带，
        # 两者近重合时合并成一条，避免标签互相压线
        mean, med = float(series.mean()), float(series.median())
        import matplotlib.transforms as mtrans
        trans = mtrans.blended_transform_factory(ax.transData, ax.transAxes)
        span = series.max() - series.min() or 1.0
        if abs(mean - med) < 0.015 * span:
            ax.axvline(mean, color=chartstyle.CINNABAR, lw=1.6, zorder=3)
            ax.annotate(f"均值≈中位数 {mean:.2f}", (mean, 1.0),
                        xycoords=trans, xytext=(5, 4), textcoords="offset points",
                        fontsize=9, color=chartstyle.CINNABAR, va="top")
        else:
            ax.axvline(mean, color=chartstyle.CINNABAR, lw=1.6, zorder=3)
            ax.axvline(med, color=chartstyle.BAMBOO, lw=1.4, ls="--", zorder=3)
            ax.annotate(f"均值 {mean:.2f}", (mean, 1.0), xycoords=trans,
                        xytext=(5, 4), textcoords="offset points",
                        fontsize=9, color=chartstyle.CINNABAR, va="top")
            ax.annotate(f"中位数 {med:.2f}", (med, 1.0), xycoords=trans,
                        xytext=(-5, 4), textcoords="offset points",
                        fontsize=9, color=chartstyle.BAMBOO, va="top",
                        ha="right")
        ax.set_title(f"「{c}」分布")
        ax.annotate(f"n = {len(series)}", (1.0, -0.14), xycoords="axes fraction",
                    ha="right", va="top", fontsize=9, color=chartstyle.INK_SOFT)
        ax.set_xlabel(c)
        ax.set_ylabel("频数")
        chartstyle.style_axes(ax, grid_axis="y")
        fname = f"hist_{stem}_{re.sub(r'[^A-Za-z0-9一-鿿]', '_', c)}_{uuid.uuid4().hex[:6]}.png"
        chartstyle.save_fig(fig, out_dir / fname)
        plt.close(fig)
        files.append(f"stats/{fname}")

    if num.shape[1] >= 2:
        corr = num.corr()
        k = len(num.columns)
        fig, ax = plt.subplots(figsize=(6.2, 5.2))
        im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
        # 白格线：imshow 不接受 edgecolor/linewidth，手绘网格线（格子分明更好读）
        for edge in range(k + 1):
            ax.axvline(edge - 0.5, color="white", lw=0.7, zorder=2)
            ax.axhline(edge - 0.5, color="white", lw=0.7, zorder=2)
        ax.set_xticks(range(k), num.columns, rotation=40, ha="right")
        ax.set_yticks(range(k), num.columns)
        ax.set_title("变量相关系数")
        ax.annotate(f"n = {len(num)}", (1.0, -0.18), xycoords="axes fraction",
                    ha="right", va="top", fontsize=9, color=chartstyle.INK_SOFT)
        # 小矩阵直接标数值，读图不用对色卡
        if k <= 8:
            for i in range(k):
                for j in range(k):
                    v = float(corr.iloc[i, j])
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=8, zorder=3,
                            color="white" if abs(v) > 0.55 else chartstyle.INK)
        cb = fig.colorbar(im, ax=ax, shrink=0.82, pad=0.02,
                          ticks=[-1, -0.5, 0, 0.5, 1])
        cb.set_label("Pearson r", fontsize=9, color=chartstyle.INK_SOFT)
        cb.outline.set_visible(False)
        ax.tick_params(length=0)
        for side in ax.spines.values():
            side.set_visible(False)
        fname = f"corr_{stem}_{uuid.uuid4().hex[:6]}.png"
        chartstyle.save_fig(fig, out_dir / fname)
        plt.close(fig)
        files.append(f"stats/{fname}")
    return files


def _chart_advice(df, num) -> list[str]:
    """图表类型建议（规则式）。"""
    import numpy as np
    advice: list[str] = []
    cat_cols = df.select_dtypes(exclude=[np.number]).columns.tolist()
    if num.shape[1] >= 2:
        advice.append("数值变量较多：建议散点图或相关系数热图观察变量间关系")
    if cat_cols and not num.empty:
        advice.append(f"存在类别列（{cat_cols[0]}）：建议分组箱线图或分组柱状图比较组间差异")
    if num.shape[1] == 1:
        advice.append("单变量：直方图 + 箱线图观察分布与异常值")
    if df.shape[0] > 50 and not num.empty:
        advice.append("样本量较大：可尝试折线图观察顺序趋势（若行序有意义）")
    return advice or ["建议先补充数值型变量再进行可视化"]
