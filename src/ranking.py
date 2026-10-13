"""轻量排序层：科研检索的确定性打分器（离线可复现，无外部依赖）。

当前提供 Okapi BM25（roadmap「三级漏斗」的 lexical 级）：
- k1=1.5 控制词频饱和，b=0.75 做文档长度归一；
- 输出归一到 [0,1]（s/(s+K) 饱和映射），可与余弦相似度直接融合。
"""
from __future__ import annotations

import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")

_K1 = 1.5
_B = 0.75
_SAT_K = 6.0  # 饱和常数：BM25 原始分 6 分时归一值约 0.5


def tokenize(text: str) -> list[str]:
    """英文按词、中文按单字切分（二元组过粗，单字召回稳）。"""
    return _TOKEN_RE.findall((text or "").lower())


def bm25_scores(query: str, docs: list[str],
                k1: float = _K1, b: float = _B) -> list[float]:
    """Okapi BM25：返回每篇文档的原始 BM25 分（未归一）。"""
    q_tokens = tokenize(query)
    if not q_tokens or not docs:
        return [0.0] * len(docs)
    doc_tokens = [tokenize(d) for d in docs]
    n = len(docs)
    avgdl = sum(len(t) for t in doc_tokens) / n or 1.0

    df: dict[str, int] = {}
    tf: list[dict[str, int]] = []
    for toks in doc_tokens:
        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        tf.append(counts)
        for term in counts:
            df[term] = df.get(term, 0) + 1

    scores: list[float] = []
    for counts, toks in zip(tf, doc_tokens):
        dl = len(toks)
        s = 0.0
        for term in q_tokens:
            f = counts.get(term)
            if not f:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def bm25_normalized(query: str, docs: list[str],
                    sat_k: float = _SAT_K) -> list[float]:
    """饱和归一的 BM25：s/(s+sat_k) ∈ [0,1)，可与其他 [0,1] 分数融合。"""
    return [s / (s + sat_k) if s > 0 else 0.0
            for s in bm25_scores(query, docs)]


def rrf_fuse(*rank_orders: list[int], k: int = 60) -> list[float]:
    """Reciprocal Rank Fusion：多路排序结果融合为归一 [0,1] 分。

    每个 ``rank_orders[i]`` 是「按该路打分降序的文档下标序列」（第 1 名下标排最前）。
    每篇文档的 RRF 分为各路倒数名次之和::

        RRF(d) = Σ_i 1 / (k + rank_i(d))

    其中 rank 从 1 起计（RRF, Cormack et al., CIKM 2009）。输出按下标对齐，
    并除以理论最大值 len(orders)/(k+1) 归一到 [0,1]——所有路一致第 1 的文档得 1.0。
    """
    if not rank_orders:
        return []
    n = 0
    for order in rank_orders:
        if order:
            n = max(n, max(order) + 1)
    scores = [0.0] * n
    for order in rank_orders:
        for rank, doc in enumerate(order):
            if 0 <= doc < n:
                scores[doc] += 1.0 / (k + rank + 1)
    top = len(rank_orders) / (k + 1.0)
    return [s / top if top else 0.0 for s in scores]


def fuse(cosine: list[float], bm25: list[float],
         w_cos: float = 0.6, w_bm25: float = 0.4) -> list[float]:
    """线性融合：TF-IDF 余弦（语义重叠）+ 归一 BM25（词频/长度感知）。

    权重偏余弦：保持既有阈值语义稳定，BM25 负责补足词频饱和与长文归一。
    """
    out = []
    for c, m in zip(cosine, bm25):
        out.append(max(0.0, min(1.0, w_cos * c + w_bm25 * m)))
    return out
