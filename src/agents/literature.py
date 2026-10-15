"""F1 文献猎手：文献自动化检索与递归筛选。

递归闭环：检索 → 相关性评分 → 低相关过滤 → 引用/被引滚雪球扩展 → 再评分，
直至收敛（新增相关占比低于阈值）或达到最大轮次。每篇文献的去留均带分数与理由。
未配置 LLM 时使用 TF-IDF 余弦 × BM25 融合评分（确定性、可复现、离线可运行）；
三级漏斗：lexical 级（既有融合分）→ 向量级（Embedding 余弦 × RRF 融合，可选）
→ 语义级（LLM 终审）。向量级未配置或失败时自动降级，离线行为不劣化。
"""
from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor
from typing import Any

from .. import config, embedding, llm, prompts, ranking, research_context
from .. import settings as app_settings
from ..connectors import openalex
from ..orchestrator.engine import PauseRequested, TaskContext, action
from ..research_analysis import normalize_doi


class LiteratureSearchError(RuntimeError):
    """已启用检索源全部失败，携带本次调用的数据源诊断。"""

    def __init__(self, message: str, search_metadata: dict[str, Any]):
        super().__init__(message)
        self.search_metadata = search_metadata


def merge_sources(*batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """多源召回并集：按 DOI（缺则 id）去重，保持传入顺序优先级。"""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in batches:
        for w in batch:
            key = normalize_doi(w.get("doi")) or str(w.get("doi") or w.get("id") or "").strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(w)
    return merged


def _dual_source_search(query: str, per_page: int,
                        year_from: int | None, year_to: int | None,
                        node: str, *, diagnostics: dict[str, Any] | None = None
                        ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """首轮双源并行召回：OpenAlex 与 CrossRef 并发请求，并集去重。

    单源失败保留另一源结果；全部启用源失败时抛异常。空命中仍视为成功。
    diagnostics 可接收各源状态与降级警告，计数返回协议保持不变。
    """
    use_crossref = bool(getattr(config, "LIT_ENABLE_CROSSREF", True)
                        and app_settings.load().get("enable_crossref", True))

    def _oa() -> list[dict[str, Any]]:
        return openalex.search(query, per_page=per_page,
                               year_from=year_from, year_to=year_to, node=node)

    def _cr() -> list[dict[str, Any]]:
        from ..connectors import crossref
        return crossref.search(query, per_page=per_page,
                               year_from=year_from, year_to=year_to, node=node)

    batches: dict[str, list[dict[str, Any]]] = {"openalex": [], "crossref": []}
    statuses = {"openalex": "pending", "crossref": "pending" if use_crossref else "disabled"}
    failures: dict[str, Exception] = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {"openalex": ex.submit(_oa)}
        if use_crossref:
            futures["crossref"] = ex.submit(_cr)
        for source, future in futures.items():
            try:
                batches[source] = future.result()
                statuses[source] = "success"
            except (PermissionError, CancelledError, PauseRequested):
                raise
            except Exception as exc:  # noqa: BLE001 —— 分别记录数据源失败
                failures[source] = exc
                statuses[source] = "failed"
    counts = {source: len(works) for source, works in batches.items()}
    labels = {"openalex": "OpenAlex", "crossref": "CrossRef"}
    warnings = [f"{labels[source]} 检索失败，本次结果未包含该源。" for source in failures]
    metadata = {"source_counts": counts, "source_status": statuses,
                "degraded": bool(failures), "warnings": warnings}
    if diagnostics is not None:
        diagnostics.update(metadata)
    if not any(status == "success" for status in statuses.values()):
        names = "、".join(labels[source] for source in failures)
        raise LiteratureSearchError(
            f"所有已启用数据源检索失败（{names}），请稍后重试。", metadata
        ) from next(iter(failures.values()))
    return merge_sources(batches["openalex"], batches["crossref"]), counts


def _tfidf_score(query: str, works: list[dict[str, Any]]) -> list[float]:
    """融合评分：TF-IDF 余弦（权重 0.6）+ 饱和归一 BM25（权重 0.4）。

    两者均为 [0,1]，融合后阈值语义与历史版本兼容；BM25 补足词频饱和
    与长文档长度归一，是确定性检索漏斗的 lexical 级。
    sklearn 惰性导入：桥/桌面冷启动不为评分付出秒级代价。
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    docs = [f"{w.get('title', '')} {w.get('abstract', '')}" for w in works]
    if not docs:
        return []
    vec = TfidfVectorizer(max_features=20000, stop_words="english")
    mat = vec.fit_transform([query, *docs])
    cos = cosine_similarity(mat[0:1], mat[1:]).flatten().tolist()
    bm = ranking.bm25_normalized(query, docs)
    return ranking.fuse(cos, bm)


# 混合融合权重：lexical 融合分与 embedding 余弦各半。两者均为 [0,1] 的
# 校准连续分数，线性加权后仍在 [0,1] 且保持区分度，与既有阈值语义兼容。
# 不用 RRF（ranking.rrf_fuse 保留为无校准分时的备选工具）：RRF 只利用排名
# 信息，且除以理论最大值归一后分数压缩在 0.93–1.0，0.18 阈值几乎失去筛选力。
_HYBRID_W_LEXICAL = 0.5
_HYBRID_W_EMB = 0.5


def _rank_candidates(query: str, works: list[dict[str, Any]], *, offline: bool = False) -> tuple[list[float], dict[str, Any]]:
    """三级漏斗第二级（向量级）融合评分。

    先算既有 lexical 融合分（TF-IDF 余弦 × BM25，确定性、离线可复现）；
    embedding provider 可用时，再对 query 与文档（title + abstract，与 lexical
    同文本）取向量、余弦打分，两路做线性加权融合::

        final = _HYBRID_W_LEXICAL × lexical + _HYBRID_W_EMB × cosine

    返回 (scores, info)；info 含 tier（"lexical" / "lexical+embedding"）。
    provider 未配置、网络失败或向量维度不一致时静默降级，分数与 lexical 完全一致。
    """
    lexical = _tfidf_score(query, works)
    info: dict[str, Any] = {"tier": "lexical"}
    if not works or offline:
        return lexical, info
    provider = embedding.get_provider()
    if provider is None:
        return lexical, info
    docs = [f"{w.get('title', '')} {w.get('abstract', '')}" for w in works]
    vectors = provider.embed([query, *docs])
    if not vectors or len(vectors) != len(works) + 1 or not vectors[0]:
        return lexical, info
    cos = embedding.cosine(vectors[0], vectors[1:])
    fused = [max(0.0, min(1.0, _HYBRID_W_LEXICAL * lex + _HYBRID_W_EMB * c))
             for lex, c in zip(lexical, cos)]
    info = {"tier": "lexical+embedding",
            "embedding_model": str(getattr(provider, "model", ""))}
    return fused, info


def _funnel(recalled: int, kept_by_score: int, vetoed: int, tier: str) -> dict[str, Any]:
    """漏斗口径：候选数 → 过阈值数 → 终审否决数，附评分层级。"""
    return {"recalled": recalled, "kept_by_score": kept_by_score,
            "vetoed": vetoed, "tier": tier}


def _llm_refine(query: str, works: list[dict[str, Any]],
                scores: list[float], *, context_scope=None) -> tuple[dict[int, str], set[int]]:
    """LLM 语义终审（prompts.lit_review + JSON 协议 + 不合规自动重试）。

    返回 ({索引: 一句理由}, {索引: 终审否决})；失败静默降级为纯评分。
    """
    reasons: dict[int, str] = {}
    veto: set[int] = set()
    if not llm.available(tier="review"):
        return reasons, veto
    for start in range(0, min(len(works), 20), 10):  # 最多审 20 篇，控制成本
        chunk = works[start:start + 10]
        key, msgs, manifest = prompts.lit_review(query, chunk, context_scope=context_scope, return_manifest=True)
        try:
            obj = research_context.chat_json(msgs, node="score", prompt_key=key, tier="review",
                                             context_scope=context_scope, context_manifest=manifest)
        except Exception:  # noqa: BLE001 —— LLM 失败不影响主流程
            break
        if not obj:
            continue
        for rv in obj.get("reviews", []):
            try:
                idx = int(rv.get("idx"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(chunk):
                continue
            gid = start + idx
            reason = str(rv.get("reason") or "").strip()
            if reason:
                reasons[gid] = reason
            if rv.get("keep") is False:
                veto.add(gid)
    return reasons, veto


def score_pool(query: str, works: list[dict[str, Any]],
               tau: float | None = None, *, offline: bool = False, context_scope=None) -> dict[str, Any]:
    """对已有候选评分并过滤；offline 时跳过模型和向量服务。

    复用递归筛选同一套阈值、自适应放宽、语义终审否决与空池保护。
    """
    tau = float(config.LIT_SCORE_THRESHOLD if tau is None else tau)
    if not works:
        return {"pool": [], "dropped": [], "rounds": [],
                "summary": "无候选文献", "threshold": tau,
                "funnel": _funnel(0, 0, 0, "lexical")}
    scores, rank_info = _rank_candidates(query, works, offline=offline)
    reasons, veto = ({}, set()) if offline else _llm_refine(query, works, scores, context_scope=context_scope)
    tau_eff = tau
    if scores:
        mx = max(scores)
        if mx < tau and mx >= 0.05:
            tau_eff = round(mx * 0.7, 4)
    by_score = {i for i, _ in enumerate(works)
                if (round(scores[i], 4) if i < len(scores) else 0.0) >= tau_eff}
    vetoes = veto & by_score
    if by_score and len(by_score) - len(vetoes) < 5:
        vetoes = set()
    pool: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for i, work in enumerate(works):
        item = dict(work)
        score = round(scores[i], 4) if i < len(scores) else 0.0
        item["score"] = score
        if i in vetoes:
            item["decision"] = "剔除"
            item["reason"] = reasons.get(i) or "语义终审判定与研究方向不相关"
            dropped.append(item)
        elif score >= tau_eff:
            item["decision"] = "保留"
            item["reason"] = reasons.get(i) or (
                f"相关度得分 {score:.3f} ≥ 阈值 {tau_eff}"
                + ("（自适应放宽）" if tau_eff < tau else ""))
            pool.append(item)
        else:
            item["decision"] = "剔除"
            item["reason"] = reasons.get(i) or f"相关度得分 {score:.3f} < 阈值 {tau_eff}"
            dropped.append(item)
    pool.sort(key=lambda x: -float(x.get("score") or 0))
    funnel = _funnel(len(works), len(by_score), len(vetoes), str(rank_info.get("tier", "lexical")))
    return {
        "pool": pool,
        "dropped": dropped,
        "rounds": [{"round": 1, "candidates": len(works),
                    "new_relevant": len(pool),
                    "ratio": round(len(pool) / max(len(works), 1), 3),
                    "funnel": funnel}],
        "summary": f"评分 {len(works)} 篇，保留 {len(pool)} 篇",
        "threshold": tau_eff,
        "funnel": funnel,
    }


@action("literature_recursive_filter")
def literature_recursive_filter(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """递归筛选主流程，产出精准文献池。支持轮次级断点：暂停后从下一轮继续。"""
    topic = ctx.params.get("topic", "")
    keywords = ctx.params.get("keywords", "")
    search_topic = ctx.params.get("search_query") or topic
    query = f"{search_topic} {keywords}".strip()
    year_from = ctx.params.get("year_from")
    year_to = ctx.params.get("year_to")
    tau = float(params.get("threshold", config.LIT_SCORE_THRESHOLD))

    if ctx.params.get("offline"):
        from .fixtures import demo_works
        candidates = demo_works(topic)
        candidates = [work for work in candidates
                      if (year_from is None or work["year"] >= year_from)
                      and (year_to is None or work["year"] <= year_to)]
        for work in candidates:
            work["demo"] = True
            work["source_type"] = "synthetic_demo"
            work["title"] = "【离线演示样例】" + work["title"]
        result = score_pool(query, candidates, tau, offline=True)
        if result.get("funnel", {}).get("tier") == "lexical+embedding":
            provider = embedding.get_provider()
            ctx.emit("node", node="lit_filter",
                     message=f"语义向量级评分已启用（模型 {getattr(provider, 'model', '')}）")
        result.update(offline=True, source="synthetic_demo",
                      notice="离线合成样例，仅供验证流程；题名、作者和 DOI 不可作为真实科研证据。")
        ctx.emit("node", node="lit_filter", message=result["notice"])
        return result

    # 恢复中间态（若上次在轮次间隙被暂停）
    state = ctx.artifacts.get("_lit_state", {})
    pool: dict[str, dict[str, Any]] = state.get("pool", {})
    seeds: list[str] = state.get("seeds", [])
    rounds: list[dict[str, Any]] = state.get("rounds", [])
    search_metadata: dict[str, Any] = state.get("search_metadata", {})
    warnings: list[str] = list(state.get("warnings", []))
    start_round = state.get("next_round", 1)
    rank_info: dict[str, Any] = {"tier": "lexical"}
    if start_round > 1:
        ctx.emit("node", node="lit_filter",
                 message=f"从第 {start_round} 轮断点续跑（已有文献池 {len(pool)} 篇）")

    for rnd in range(start_round, config.LIT_MAX_ROUND + 1):
        if ctx.pause_requested:
            # 轮次间隙暂停：落盘中间态后挂起
            ctx.artifacts["_lit_state"] = {
                "pool": pool, "seeds": seeds, "rounds": rounds, "next_round": rnd,
                "search_metadata": search_metadata, "warnings": warnings,
            }
            ctx.emit("node", node="lit_filter",
                     message=f"第 {rnd} 轮前暂停，已完成 {len(rounds)} 轮")
            raise PauseRequested()
        # 1) 获取候选：首轮双源并行检索，后续轮滚雪球
        if rnd == 1:
            candidates, src_counts = _dual_source_search(
                query, config.LIT_PER_PAGE, year_from, year_to, node="search",
                diagnostics=search_metadata)
            for warning in search_metadata.get("warnings", []):
                warnings.append(warning)
                ctx.emit("node", node="lit_filter", message=warning)
            extra = (f"（OpenAlex {src_counts['openalex']}"
                     f" + CrossRef {src_counts['crossref']}，双源并行去重）"
                     if src_counts["crossref"]
                     else f"（OpenAlex {src_counts['openalex']} 篇）")
            ctx.emit("node", node="lit_filter",
                     message=f"第 {rnd} 轮：检索到 {len(candidates)} 篇候选{extra}")
        else:
            if search_metadata.get("source_status", {}).get("openalex") == "failed":
                ctx.emit("node", node="lit_filter", message="OpenAlex 不可用，跳过引用扩展，保留已检索文献。")
                break
            seeds = [sid for sid in seeds if pool.get(sid, {}).get("source") != "crossref"]
            if not seeds:
                ctx.emit("node", node="lit_filter", message="无可用 OpenAlex 引用种子，保留已检索文献。")
                break
            refs: list[str] = []
            for sid in seeds[:config.LIT_SNOWBALL_TOPK]:
                w = pool.get(sid)
                if w:
                    refs.extend(w.get("referenced_works", [])[:5])
            try:
                expanded = openalex.fetch_many(refs[:50], node="expand")
                expanded.extend(openalex.cited_by_batch(seeds[:5], per_page=10, node="expand"))
            except (PermissionError, CancelledError, PauseRequested):
                raise
            except Exception:  # noqa: BLE001 —— 扩展失败保留此前结果
                warning = f"OpenAlex 引用扩展失败，已保留前 {len(rounds)} 轮文献。"
                warnings.append(warning)
                search_metadata["expansion_status"] = "failed"
                ctx.emit("node", node="lit_filter", message=warning)
                break
            existing = merge_sources(list(pool.values()))
            candidates = merge_sources(existing, expanded)[len(existing):]
            ctx.emit("node", node="lit_filter",
                     message=f"第 {rnd} 轮：沿引用网络扩展出 {len(candidates)} 篇新候选")

        if not candidates:
            ctx.emit("node", node="lit_filter", message="无新候选，提前收敛")
            break

        # 2) 评分（自适应阈值：批次最高分低于阈值时按 0.7×最高分 放宽，避免空池）
        scores, rank_info = _rank_candidates(query, candidates)
        if rnd == 1 and rank_info.get("tier") == "lexical+embedding":
            ctx.emit("node", node="lit_filter",
                     message=f"语义向量级评分已启用（模型 {rank_info.get('embedding_model', '')}）")
        tau_eff = tau
        if scores:
            mx = max(scores)
            if mx < tau and mx >= 0.05:
                tau_eff = round(mx * 0.7, 4)
                ctx.emit("node", node="lit_filter",
                         message=f"批次最高分 {mx:.3f} 低于阈值，自适应放宽至 {tau_eff}")
        reasons, veto = _llm_refine(query, candidates, scores, context_scope=ctx.params.get("research_context"))

        # 3) 过滤入池（语义终审可否决评分误留；否决后不足 5 篇则放弃否决，防空池）
        by_score = {i for i, w in enumerate(candidates)
                    if (round(scores[i], 4) if i < len(scores) else 0.0) >= tau_eff}
        vetoes = veto & by_score
        if by_score and len(by_score) - len(vetoes) < 5:
            vetoes = set()
        new_relevant = 0
        for i, w in enumerate(candidates):
            s = round(scores[i], 4) if i < len(scores) else 0.0
            w["score"] = s
            if i in vetoes:
                w["decision"] = "剔除"
                w["reason"] = reasons.get(i) or "语义终审判定与研究方向不相关"
            elif s >= tau_eff:
                w["decision"] = "保留"
                w["reason"] = reasons.get(i) or (
                    f"相关度得分 {s:.3f} ≥ 阈值 {tau_eff}" +
                    ("（自适应放宽）" if tau_eff < tau else ""))
                pool[w["id"]] = w
                new_relevant += 1
            else:
                w["decision"] = "剔除"
                w["reason"] = reasons.get(i) or f"相关度得分 {s:.3f} < 阈值 {tau_eff}"
        if vetoes:
            ctx.emit("node", node="lit_filter",
                     message=f"语义终审否决 {len(vetoes)} 篇评分误留文献")

        ratio = new_relevant / len(candidates)
        rounds.append({"round": rnd, "candidates": len(candidates),
                       "new_relevant": new_relevant, "ratio": round(ratio, 3),
                       "funnel": _funnel(len(candidates), len(by_score), len(vetoes),
                                         str(rank_info.get("tier", "lexical")))})
        ctx.emit("node", node="lit_filter",
                 message=f"第 {rnd} 轮：保留 {new_relevant}/{len(candidates)} 篇"
                         f"（新增相关占比 {ratio:.1%}），文献池累计 {len(pool)} 篇")

        # 4) 收敛判断与种子更新
        if ratio < config.LIT_CONVERGE_RATIO:
            ctx.emit("node", node="lit_filter", message="达到收敛条件，递归结束")
            break
        seeds = [w["id"] for w in
                 sorted(pool.values(), key=lambda x: -x["score"])
                 if w.get("source") != "crossref"][:config.LIT_SNOWBALL_TOPK]

    ctx.artifacts.pop("_lit_state", None)  # 跑完即清除中间态
    search_metadata.update(degraded=bool(warnings), warnings=warnings)
    result = {
        "pool": sorted(pool.values(), key=lambda x: -x["score"]),
        "rounds": rounds,
        "summary": f"递归 {len(rounds)} 轮，文献池共 {len(pool)} 篇",
        "search_metadata": search_metadata,
        "degraded": bool(warnings),
        "warnings": warnings,
        "notice": "\n".join(warnings),
        "funnel": {
            "recalled": sum(r["candidates"] for r in rounds),
            "kept_by_score": sum(r.get("funnel", {}).get("kept_by_score", 0) for r in rounds),
            "vetoed": sum(r.get("funnel", {}).get("vetoed", 0) for r in rounds),
            "tier": str(rank_info.get("tier", "lexical")),
        },
    }
    ctx.emit("node", node="lit_filter", message=result["summary"])
    return result
