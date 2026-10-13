"""格致图表主题：把"文房五色"落到 matplotlib 默认参数之外。

所有导出 PNG（统计直方图 / 相关热图 / 证据图谱）共用同一套视觉：
宣纸底、墨色文字、黛蓝主色、朱砂点睛、竹青辅助；顶右脊柱隐藏、
浅色横向网格、无衬线中文回退链。图表因此可以直接进答辩材料，
不需要后期调色。
"""
from __future__ import annotations

from pathlib import Path

# ---- 文房五色（与 ui/brand.py 同源；本模块不依赖 flet，可被引擎侧使用） ----
PAPER = "#FBF8F1"      # 图底：比界面宣纸更亮一档，投影/打印不发灰
INK = "#2B3440"        # 文字与坐标轴
INK_SOFT = "#6B7280"   # 次级文字
INDIGO = "#1F4E79"     # 黛蓝（主数据色）
CINNABAR = "#C23531"   # 朱砂（点睛：均值线、警示）
BAMBOO = "#3A7D5D"     # 竹青（正向/次数据色）
SILVER = "#9AA3AF"     # 银灰（弱数据）
GRID = "#E9E3D5"       # 网格（比宣纸深一档，轻对比不抢数据）

# ---- 字号阶梯（pt）：标题 15 / 轴 10.5 / 刻度 9.5 / 注脚 9 ----
FS_TITLE = 15.0
FS_AXIS = 10.5
FS_TICK = 9.5
FS_FOOT = 9.0

EXPORT_DPI = 200       # 统一导出分辨率（答辩投屏不虚）

_FONT_STACK = ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC",
               "DejaVu Sans"]


def apply(plt) -> None:
    """把品牌主题写进 pyplot 全局 rc（幂等，可反复调用）。"""
    plt.rcParams.update({
        "font.sans-serif": _FONT_STACK,
        "font.family": "sans-serif",
        "axes.unicode_minus": False,
        "figure.facecolor": PAPER,
        "axes.facecolor": PAPER,
        "savefig.facecolor": PAPER,
        "text.color": INK,
        "axes.edgecolor": "#CFC8B8",
        "axes.labelcolor": INK,
        "axes.labelsize": FS_AXIS,
        "axes.titlesize": FS_TITLE,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "xtick.color": INK_SOFT,
        "ytick.color": INK_SOFT,
        "xtick.labelsize": FS_TICK,
        "ytick.labelsize": FS_TICK,
        "legend.fontsize": FS_FOOT,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "grid.alpha": 0.8,
        "legend.frameon": False,
    })


def style_axes(ax, grid_axis: str = "y") -> None:
    """单轴收尾：藏顶右脊柱、浅网格、刻度色。"""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#CFC8B8")
    ax.grid(True, axis=grid_axis, alpha=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def save_fig(fig, path: Path, dpi: int = EXPORT_DPI) -> Path:
    """统一出图出口：紧边距 + 宣纸底固定，返回路径（调用方负责 close）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight",
                facecolor=PAPER, edgecolor="none")
    return path


# 旧名保留：历史调用方与文档里的 finish() 语义不变
finish = save_fig
