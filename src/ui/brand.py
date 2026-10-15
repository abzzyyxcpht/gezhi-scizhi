"""格致品牌与桌面色阶。文房五色用于品牌物料；桌面以清晰阅读为先。

冷白工作面、雾灰外壳、蓝色操作色。语义色仅表达状态，不为面板染色。
文本与按钮组合由 tests/test_brand_contrast.py 验证。
"""
from __future__ import annotations

# ---- 文房五色（品牌层）----
PAPER = "#F7F3E9"      # 宣纸
INK = "#2B3440"        # 墨色
CINNABAR = "#C23531"   # 朱砂（印章红）
INDIGO = "#1F4E79"     # 黛蓝
BAMBOO = "#3A7D5D"     # 竹青
SILVER = "#8A94A6"     # 银灰

# ---- 浅色研究工作台 ----
BG = "#F8F8F8"          # 外壳与导航
SURFACE = "#FFFFFF"     # 工作面
SURFACE_2 = "#F5F5F5"   # 次级分组
SURFACE_3 = "#EBEBEB"   # 表头、轻量标签
INPUT = "#FFFFFF"
CARD = "#FFFFFF"
MENU = "#FFFFFF"
POPOVER = "#FFFFFF"
SELECTED = "#ECECEE"
PRIMARY = "#000000"
ON_PRIMARY = "#FFFFFF"
BORDER = "#E5E5E5"
# 输入框描边：白底上 ≥3:1（#A3A3A3 仅 2.52，输入边界在白卡上糊掉）
BORDER_2 = "#8A8A8A"
HOVER = "#E8ECEE"
ACTIVE = "#EBF4FF"
TEXT = "#171717"
TEXT_2 = "#525252"
TEXT_3 = "#737373"
ACCENT = "#2563EB"
ACCENT_TEXT = "#1D4ED8"
ON_ACCENT = "#FFFFFF"
SUCCESS = "#33704F"
WARN = "#84611D"
DANGER = "#AD423D"
INFO = "#3C6079"
ACCENT_BG = "#DBEAFE"
SUCCESS_BG = "#E8F1EA"
WARN_BG = "#F6EFDF"
DANGER_BG = "#F9EAE8"
INFO_BG = "#EAF0F5"


class SemanticColor(str):
    """A normal serializable color string which retains its design role.

    White inputs, cards and primary labels share a hex value in light mode,
    but must become different colors in dark mode.  Preserve that meaning
    through Flet controls instead of trying to reverse-engineer it from hex.
    """

    def __new__(cls, value: str, role: str = ""):
        color = super().__new__(cls, value)
        color.role = role
        return color

    def __reduce__(self):
        return type(self), (str(self), self.role)


COLOR_ROLES = (
    "BG", "SURFACE", "SURFACE_2", "SURFACE_3", "INPUT", "CARD", "MENU",
    "POPOVER", "SELECTED", "PRIMARY", "ON_PRIMARY", "BORDER", "BORDER_2",
    "HOVER", "ACTIVE", "TEXT", "TEXT_2", "TEXT_3", "ACCENT", "ACCENT_TEXT",
    "ON_ACCENT", "SUCCESS", "WARN", "DANGER", "INFO", "ACCENT_BG",
    "SUCCESS_BG", "WARN_BG", "DANGER_BG", "INFO_BG",
)
for _role in COLOR_ROLES:
    globals()[_role] = SemanticColor(globals()[_role], _role)

QUOTE = "致知在格物，物格而后知至。——《礼记·大学》"
SLOGAN = "格物致知，智启科研"
SUB_SLOGAN = "把事务交给智能体，把思考还给科研人"

# 小型终端中的书页标记，与图形品牌保持同一轮廓。
SEAL = [
    "╭─┬─╮  SciZhi · 格致",
    "╰─┴─╯  科研工作台",
]

FEATURE_NAMES = {
    "lit_filter": "文献猎手",
    "human_review": "人工确认",
    "extract": "综述搭子",
    "review_frame": "综述框架",
    "citation": "格式管家",
}


def time_saved_text(pool_size: int, rounds: int) -> str:
    """“科研时间账”彩蛋：按文献量估算省下的事务时间。

    估算口径：人工粗读摘要并摘录要点约 6 分钟/篇，初筛扫读约 1 分钟/篇。
    """
    if pool_size <= 0:
        return ""
    saved_min = pool_size * 6 + rounds * 25 * 1
    hours = saved_min / 60
    if hours >= 1:
        return f"时间账：本次科研流水线约为你省下 {hours:.1f} 小时事务时间——去读文献背后的问题吧。"
    return f"时间账：本次约为你省下 {saved_min} 分钟事务时间。"
