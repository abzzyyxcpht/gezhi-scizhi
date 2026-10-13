"""术语抽取共享库：拉丁词元（去停用词）+ 中文二元组。

dashboard（科研看板）与 plugins/term_freq.py（术语词频命令）共用，
保证同一份统计口径。
"""
from __future__ import annotations

import re

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "and", "to", "with", "by",
    "is", "are", "was", "were", "be", "been", "we", "our", "this", "that",
    "these", "those", "it", "its", "as", "at", "from", "into", "via",
    "can", "may", "using", "based", "novel", "propose", "proposed",
    "paper", "study", "results", "show", "between", "their", "which",
    "also", "however", "such", "more", "most", "other", "than", "then",
}

_CJK = re.compile(r"[\u4e00-\u9fff]{2}")
_LATIN = re.compile(r"[a-z][a-z\-]{2,}")


def terms(text: str) -> list[str]:
    """从文本抽取候选术语（小写，已去停用词，剔除超长噪声）。"""
    text = (text or "").lower()
    latin = [t for t in _LATIN.findall(text)
             if t not in STOPWORDS and len(t) < 25]
    return latin + _CJK.findall(text)
