"""等宽终端文本排版：按**显示宽度**（东亚全角计 2 格）裁切与补白。

看板、证据图谱、插件输出都是直接打印到终端的等宽文本，若按字符数排版会出现
两类可见缺陷：① 中文与拉丁混排的列错位（`"摘要"` 与 `"DOI"` 补到同一字符数后
显示宽度差 2 格，条形图整体右移）；② 长题名硬切在第 N 个字符上，既不等宽
也不像有意省略，读起来像渲染故障。这里给出两端共用的一份口径。

（TUI 侧对应 `tui/src/commands.js` 的 `padWide()/clipWide()`，口径一致。）
"""
from __future__ import annotations

import unicodedata

_WIDE = ("W", "F")   # East_Asian_Width: Wide / Fullwidth


def width(text: str) -> int:
    """字符串的终端显示宽度（全角字符计 2 格）。"""
    return sum(2 if unicodedata.east_asian_width(c) in _WIDE else 1
               for c in str(text or ""))


def clip(text: str, limit: int, mark: str = "…") -> str:
    """按显示宽度裁切，超出时以 mark 收尾（返回宽度 ≤ limit）。"""
    text = str(text or "")
    if width(text) <= limit:
        return text
    budget = max(0, limit - width(mark))
    out: list[str] = []
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in _WIDE else 1
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + mark


def pad(text: str, limit: int) -> str:
    """先按显示宽度裁切，再右侧补白到 limit（用于列对齐）。"""
    text = clip(text, limit)
    return text + " " * max(0, limit - width(text))
