"""离线演示用公开语义样例。

答辩现场无网络时，文献猎手 / 流水线调度仍能跑通完整证据链。
每调用一次返回新列表，避免评分流程原地改写共享对象。
"""
from __future__ import annotations

from typing import Any


def demo_works(topic: str = "large language model agents") -> list[dict[str, Any]]:
    """构造 6 篇可复现的公开语义样例（含摘要句法线索，供规则抽取命中）。"""
    label = (topic or "large language model agents").strip() or "large language model agents"
    titles = [
        f"{label} · Planning large language model agents with tool use",
        f"{label} · Evaluating large language model agents in research workflows",
        f"{label} · Memory augmented large language model agents",
        f"{label} · Multi agent collaboration for large language model agents",
        f"{label} · Reliable retrieval for large language model agents",
        f"{label} · Human oversight of large language model agents",
    ]
    works: list[dict[str, Any]] = []
    for i, title in enumerate(titles, 1):
        works.append({
            "id": f"https://openalex.org/GEZHI{i}",
            "title": title,
            "authors": [f"Gezhi Author {i}"],
            "year": 2020 + i,
            "venue": "Journal of Agentic Research",
            "volume": "1", "issue": str(i), "pages": f"{i}-{i + 8}",
            "doi": f"10.5555/gezhi.{i}",
            "abstract": (
                "We propose a reproducible framework for large language model agents. "
                "Experiments show improved accuracy and reliable tool use. "
                "The conclusion highlights human oversight and future work."
            ),
            "referenced_works": [], "cited_by_count": 10 - i,
            "url": f"https://openalex.org/GEZHI{i}", "type": "article",
        })
    return works
