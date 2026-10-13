"""F2 综述搭子：文献核心信息抽取与综述框架生成。

抽取优先走 LLM（配置后，JSON 协议 + 不合规自动重试），未配置时用规则兜底：
按摘要句法特征识别方法/创新点/结论句，保证离线可演示且句句有出处。
综述框架在配置 LLM 时走 plan→act→reflect 自主智能体分支，
引用题名逐字回池校验；该校验不证明原文支持论点。
"""
from __future__ import annotations

import re
from typing import Any

from .. import aigc, llm, prompts, research_context
from ..research_analysis import instruction_like, quote_provenance
from ..orchestrator.engine import TaskContext, action

_METHOD_HINT = ("we propose", "we present", "we introduce", "method", "approach",
                "framework", "model", "algorithm", "we design")
_RESULT_HINT = ("results show", "experiments show", "outperform", "achieves",
                "improve", "accuracy", "demonstrate")
_CONCLUSION_HINT = ("conclusion", "in summary", "we conclude", "overall", "结果表明", "结论")
_LIMITATION_HINT = ("future work", "limitation", "limited to", "局限", "限制", "未来工作")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text) if s.strip()]


def _rule_extract(work: dict[str, Any]) -> dict[str, str]:
    """规则抽取：按句法线索定位方法/创新/结论句；有全文时以全文为材料。"""
    material = str(work.get("full_text") or "").strip() or str(work.get("abstract") or "")
    if instruction_like(material):
        return {k: "" for k in ("method", "innovation", "conclusion", "limitation")}
    sents = _split_sentences(material[:20000])
    method = next((s for s in sents if any(h in s.lower() for h in _METHOD_HINT)), "")
    result = next((s for s in sents if any(h in s.lower() for h in _RESULT_HINT)), "")
    conclusion = next((s for s in sents if any(h in s.lower() for h in _CONCLUSION_HINT)), "")
    limitation = next((s for s in sents if any(h in s.lower() for h in _LIMITATION_HINT)), "")
    return {
        "method": method,
        "innovation": result,
        "conclusion": conclusion or result,
        "limitation": limitation,
    }


@action("extract_pool")
def extract_pool(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """对文献池批量结构化抽取，生成摘要整理稿。

    配置 LLM 时走 prompts.extract_one（JSON 协议，不合规自动重试，失败逐篇
    降级规则抽取）；未配置时纯规则路径，离线可演示且句句有出处。
    """
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    limit = int(params.get("limit", 20))
    pool = pool[:limit]
    topic = ctx.params.get("topic", "")
    use_llm = not ctx.params.get("offline") and llm.available(tier="extraction")
    ctx.emit("node", node="extract",
             message=f"开始抽取 {len(pool)} 篇文献（{'智能体语义抽取' if use_llm else '规则抽取'}）")

    records: list[dict[str, Any]] = []
    llm_ok = 0
    for i, w in enumerate(pool, 1):
        ext: dict[str, str] | None = None
        abstract = str(w.get("abstract") or "")
        full_text = str(w.get("full_text") or "").strip()
        material = full_text or abstract
        material_field = "fulltext" if full_text else "abstract"
        quarantined = instruction_like(material)
        extraction_method = "quarantined" if quarantined else "rule"
        if use_llm and material and not quarantined:
            binding = ctx.params.get("research_context")
            key, msgs, manifest = prompts.extract_one(topic, w, context_scope=binding, return_manifest=True)
            try:
                obj = research_context.chat_json(msgs, node="extract", prompt_key=key, tier="extraction",
                                                 context_scope=binding, context_manifest=manifest)
            except Exception:  # noqa: BLE001 —— 单篇失败不影响整池
                obj = None
            if isinstance(obj, dict):
                ext = {k: obj[k].strip() if isinstance(obj.get(k), str) else ""
                       for k in ("method", "innovation", "conclusion", "limitation")}
                if any(ext.values()):
                    llm_ok += 1
                    extraction_method = "llm"
                else:
                    ext = None
        if not ext:
            ext = _rule_extract(w)
        demo = bool(w.get("demo") or w.get("source_type") == "synthetic_demo")
        provenance = {key: quote_provenance(material, value, material_field)
                      for key, value in ext.items()}
        record = {**{k: w.get(k) for k in
                     ("id", "title", "authors", "year", "venue", "doi", "score", "url", "source")},
                  **ext, "abstract": abstract, "demo": demo,
                  "source_type": "synthetic_demo" if demo else w.get("source_type") or
                                 ("fulltext" if full_text else
                                  ("abstract_only" if abstract else "metadata_only")),
                  "material_scope": material_field if material else "metadata",
                  "extraction_method": extraction_method, "provenance": provenance,
                  "flags": ["instruction_like_content"] if quarantined else []}
        if full_text:
            record["full_text_chars"] = len(full_text)
        records.append(record)
        ctx.emit("node", node="extract",
                 message=f"已抽取 {i}/{len(pool)}：{(w.get('title') or '')[:40]}")
    if use_llm:
        ctx.emit("node", node="extract",
                 message=f"语义抽取成功 {llm_ok}/{len(pool)} 篇，其余已降级规则路径")

    # LLM 可用时生成综述脉络分析（规则兜底在 build_review_frame 中）
    summary_md = _render_digest(records, topic)
    summary_md += aigc.footer_md(llm_used=use_llm,
                                 task_id=ctx.task_id, topic=topic)
    ctx.emit("node", node="extract",
             message="摘要整理稿已生成（含 AIGC 标识）")
    return {"records": records, "digest_md": summary_md}


def _render_digest(records: list[dict[str, Any]], topic: str) -> str:
    """渲染摘要整理稿（Markdown）。"""
    lines = [f"# 文献摘要整理稿：{topic}", ""]
    for i, r in enumerate(records, 1):
        authors = ", ".join(r.get("authors") or []) or "佚名"
        lines += [
            f"## [{i}] {r.get('title', '')}",
            f"- 作者：{authors}（{r.get('year') or '年份不详'}，{r.get('venue') or '来源不详'}）",
            f"- 方法：{r.get('method', '')}",
            f"- 创新点：{r.get('innovation', '')}",
            f"- 结论：{r.get('conclusion', '')}",
            f"- 局限或未来工作：{r.get('limitation') or '摘要中未定位'}",
            f"- 材料范围：{r.get('material_scope') or r.get('source_type', 'unknown')}"
            + (f"（全文 {r['full_text_chars']} 字符）" if r.get("full_text_chars") else "")
            + ("（合成演示，不能作为真实研究证据）" if r.get("demo") else "；仅核对所提供材料"),
            "- 核查：原文定位不等于论断支持；"
            + ("存在未逐字核验的抽取。" if any(p.get("quote") and p.get("status") != "exact_quote"
                for p in (r.get("provenance") or {}).values()) else "未进行蕴含或全文实验核验。"),
            f"- 出处：{r.get('url') or r.get('doi', '')}",
            "",
        ]
    return "\n".join(lines)


def _llm_review_frame(topic: str, records: list[dict[str, Any]], *, context_scope=None
                      ) -> tuple[list[dict[str, Any]], str] | None:
    """综述框架的自主智能体分支：plan（定分节策略）→ act（生成大纲）→
    reflect（校验引用题名存在于文献池，不判定论断支持关系）。

    planning 未配置或失败时使用规则计划；synthesis 失败返回 None。
    """
    titles = {r.get("title", "") for r in records if r.get("title")}
    plan = {"plan": ["按材料可见方法归类", "比较可追溯证据", "列出待核查事项"]}
    plan_source = "规则计划"
    if llm.available(tier="planning"):
        plan_key, plan_msgs, plan_manifest = prompts.review_frame_plan(
            topic, records, context_scope=context_scope, return_manifest=True)
        try:
            planned = research_context.chat_json(plan_msgs, node="review_frame",
                prompt_key=plan_key, tier="planning", context_scope=context_scope, context_manifest=plan_manifest)
        except Exception:  # noqa: BLE001 — planning failure must not disable synthesis
            planned = None
        if isinstance(planned, dict) and isinstance(planned.get("plan"), list):
            steps = [p[:200] for p in planned["plan"] if isinstance(p, str) and p.strip()][:3]
            if steps:
                plan = {"plan": steps}
                plan_source = "模型计划"
    try:
        act_key, act_msgs, act_manifest = prompts.review_frame(
            topic, records, plan=plan, context_scope=context_scope, return_manifest=True)
        obj = research_context.chat_json(act_msgs, node="review_frame", prompt_key=act_key, tier="synthesis",
                                         context_scope=context_scope, context_manifest=act_manifest)
    except Exception:  # noqa: BLE001 —— LLM 失败降级规则路径
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("sections"), list):
        return None
    sections = []
    for sec in (obj.get("sections") or [])[:6]:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "").strip()
        if not title:
            continue
        # reflect：支撑文献逐字核对题名，砍掉清单之外的幻觉引用
        raw_refs = sec.get("refs") if isinstance(sec.get("refs"), list) else []
        refs = list(dict.fromkeys(t for t in raw_refs if isinstance(t, str) and t in titles))[:5]
        sections.append({"title": title,
                         "points": str(sec.get("points") or "").strip(),
                         "refs": refs})
    if len(sections) < 3:
        return None
    plan_note = plan_source + "：" + "；".join(plan["plan"])
    return sections, plan_note


def _generate_sections(ctx: TaskContext, topic: str, records: list[dict[str, Any]],
                       use_llm: bool) -> tuple[list[dict[str, Any]], str | None]:
    """生成综述框架分节：LLM 走 plan→act→reflect 自主智能体分支，否则规则兜底。

    返回 (sections, plan_note)；plan_note 为 None 表示规则兜底路径，
    供调用方判断框架是否由模型生成（AIGC 标识与事件文案）。
    """
    llm_result = (_llm_review_frame(topic, records, context_scope=ctx.params.get("research_context"))
                  if use_llm else None)
    if llm_result:
        sections, plan_note = llm_result
        ctx.emit("node", node="review_frame",
                 message=f"智能体分节策略（plan）：{plan_note or 'LLM 直出'}")
        ctx.emit("node", node="review_frame",
                 message="已生成综述框架并核对引用题名在池中（未核验论断支持关系）")
        return sections, plan_note or "LLM 直出"
    return _rule_sections(topic, records), None


def _frame_md(topic: str, sections: list[dict[str, Any]], llm_used: bool,
              task_id: str) -> str:
    """渲染综述框架 Markdown（含 AIGC 标识）。"""
    md = [f"# 文献综述框架：{topic}", ""]
    for s in sections:
        md.append(f"## {s['title']}")
        md.append(f"写作要点：{s['points']}")
        md.append("待核对引用（题名在池中，论断支持关系未验证）：")
        md.extend(f"- {t}" for t in s["refs"])
        md.append("")
    md.append(aigc.footer_md(llm_used=llm_used, task_id=task_id, topic=topic))
    return "\n".join(md)


def _minimal_record(work: dict[str, Any]) -> dict[str, Any]:
    """为补检入池文献生成最小抽取记录（复用 extract 记录 schema）。

    要点字段留空、reason 标注来源，使下游证据图谱不会把它们计为
    not_extracted；论断内容仍需人工补抽，不可直接引用。
    """
    abstract = str(work.get("abstract") or "")
    return {
        **{k: work.get(k) for k in
           ("id", "title", "authors", "year", "venue", "doi", "score", "url", "source")},
        "method": "", "innovation": "", "conclusion": "", "limitation": "",
        "abstract": abstract, "demo": False,
        "source_type": work.get("source_type") or
                       ("abstract_only" if abstract else "metadata_only"),
        "material_scope": "abstract" if abstract else "metadata",
        "extraction_method": "gap_refill", "provenance": {}, "flags": [],
        "reason": "缺证据补检入池",
    }


def _gap_refill(ctx: TaskContext, params: dict[str, Any], topic: str,
                sections: list[dict[str, Any]], use_llm: bool
                ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """缺证据驱动补检：框架存在无支撑文献的章节时，定向补检索并重写框架。

    触发条件（全部满足）：存在缺证据章节；非离线（真实检索需要网络）；
    本轮尚未补检过（断点续跑/重放最多补一次）；params 无 no_refill 开关。
    补检检索失败或返回空结果不报错，如实记录 triggered:false；
    二次生成后仍缺证据也只记录，不循环补检。

    返回 (gap_refill 结果, sections)：sections 在触发重写时更新为新框架。
    """
    from .. import config, evidence
    from . import literature

    def _skip(reason: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {"triggered": False, "reason": reason}, sections

    prev = ctx.artifacts.get("review_frame") or {}
    if prev.get("_gap_refill_done") or (prev.get("gap_refill") or {}).get("_gap_refill_done"):
        return _skip("本轮已补检过（断点续跑/重放不二次触发）")
    if ctx.params.get("offline"):
        return _skip("离线模式不触发补检")
    if params.get("no_refill"):
        return _skip("已传 no_refill 关闭开关")
    art = {**ctx.artifacts, "review_frame": {"sections": sections}}
    graph = evidence.build_graph_from_art(art, ctx.task_id, topic)
    missing = list(graph["missing_sections"])
    if not missing:
        return _skip("无缺证据章节")

    query = " ".join(missing[:3]).strip()[:200]
    ctx.emit("node", node="review_frame",
             message=f"框架缺证据章节：{'、'.join(missing[:3])} → 触发补充检索")
    tau = round(float(config.LIT_SCORE_THRESHOLD) * 0.8, 4)
    try:
        candidates, _src_counts = literature._dual_source_search(
            query, max(1, config.LIT_PER_PAGE // 2),
            ctx.params.get("year_from"), ctx.params.get("year_to"),
            node="review_frame")
    except Exception as exc:  # noqa: BLE001 —— 补检失败不阻断主流程
        return _skip(f"补检检索失败：{exc}")
    scored = literature.score_pool(query, candidates, tau, context_scope=ctx.params.get("research_context"))
    pool_new = list(scored.get("pool") or [])
    if not pool_new:
        return _skip("补检无结果")

    lit = ctx.artifacts.setdefault("lit_filter", {})
    old_pool = list(lit.get("pool") or [])
    seen = {(w.get("doi") or w.get("id") or "").lower() for w in old_pool}
    added = [w for w in pool_new
             if (w.get("doi") or w.get("id") or "").lower() and
             (w.get("doi") or w.get("id") or "").lower() not in seen]
    if not added:
        return _skip("补检无结果")
    # 并入文献池（按相关度降序重排），并同步最小抽取记录
    lit["pool"] = sorted(old_pool + added,
                         key=lambda w: -float(w.get("score") or 0))
    extract = ctx.artifacts.setdefault("extract", {})
    extract["records"] = list(extract.get("records") or []) + \
                         [_minimal_record(w) for w in added]

    # 用更新后的 pool + records 重新生成框架（二次生成仍走 reflect 校验），
    # 防幻觉标准不变：引用题名必须逐字回池核对
    anchor_before = graph["anchor_rate"]
    new_sections, _ = _generate_sections(ctx, topic, list(extract["records"]), use_llm)
    graph_after = evidence.build_graph_from_art(
        {**ctx.artifacts, "review_frame": {"sections": new_sections}},
        ctx.task_id, topic)
    anchor_after = graph_after["anchor_rate"]
    tau_eff = scored.get("threshold", tau)
    ctx.emit("node", node="review_frame",
             message=f"补检入池 {len(added)} 篇（相关度 ≥ {tau_eff}）")
    ctx.emit("node", node="review_frame",
             message=f"框架已按增补文献重写，锚定率 {anchor_before}% → {anchor_after}%")
    refill: dict[str, Any] = {
        "triggered": True, "sections": missing[:3], "added": len(added),
        "anchor_rate_before": anchor_before, "anchor_rate_after": anchor_after,
        "_gap_refill_done": True,
    }
    if graph_after["missing_sections"]:
        refill["still_missing"] = graph_after["missing_sections"]
    return refill, new_sections


@action("build_review_frame")
def build_review_frame(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """基于抽取结果生成综述大纲与初稿框架（分节论点 + 支撑文献映射）。

    配置 LLM 时走 plan→act→reflect 自主智能体分支，未配置或失败时规则兜底。
    框架产出并完成引用校验后，若存在缺证据章节则触发缺证据驱动补检，
    补检入池的新文献会促使框架重写（见 _gap_refill）。
    """
    records = ctx.artifacts.get("extract", {}).get("records", [])
    topic = ctx.params.get("topic", "")
    if not records:
        return {"outline_md": "文献池为空，无法生成综述框架。", "sections": []}

    use_llm = not ctx.params.get("offline") and llm.available(tier="synthesis")
    sections, plan_note = _generate_sections(ctx, topic, records, use_llm)
    gap_refill, sections = _gap_refill(ctx, params, topic, sections, use_llm)

    ctx.emit("node", node="review_frame",
             message="综述大纲与初稿框架已生成（含 AIGC 标识）")
    return {
        "outline_md": _frame_md(topic, sections, plan_note is not None, ctx.task_id),
        "sections": sections,
        "verification_scope": "citation_identity_only",
        "gap_refill": gap_refill,
    }


def _rule_sections(topic: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则兜底：高频主题词分类 + 年代脉络的固定四节框架（sklearn 惰性导入）。"""
    from sklearn.feature_extraction.text import CountVectorizer

    # 高频主题词（方法分类依据）
    corpus = [f"{r.get('title', '')} {r.get('method', '')}" for r in records]
    try:
        vec = CountVectorizer(stop_words="english", max_features=8, ngram_range=(1, 2))
        vec.fit(corpus)
        hot_terms = vec.get_feature_names_out().tolist()
    except ValueError:
        hot_terms = []

    years = sorted({r.get("year") for r in records if r.get("year")})
    span = f"{years[0]}—{years[-1]}" if years else "年份不详"
    top = sorted(records, key=lambda r: -(r.get("score") or 0))[:5]

    return [
        {"title": "1 研究背景与问题提出",
         "points": f"梳理{topic}领域的发展脉络与核心问题（覆盖 {span} 年文献 {len(records)} 篇）。",
         "refs": [r["title"] for r in records[:5]]},
        {"title": "2 主要技术路线与方法分类",
         "points": "按方法范式归类：" + ("、".join(hot_terms[:5]) if hot_terms else "待补充"),
         "refs": [r["title"] for r in top]},
        {"title": "3 代表性成果与创新点比较",
         "points": "横向比较高相关度文献的创新点与实验结论。",
         "refs": [r["title"] for r in top[:3]]},
        {"title": "4 证据缺口与待核查方向",
         "points": "核对现有材料中的结论与局限，列出需补充全文或实验的问题；不能据此证明研究空白。",
         "refs": [r["title"] for r in records[-3:]]},
    ]
