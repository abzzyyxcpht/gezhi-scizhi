"""专项智能体执行体：每个 run() 都是可离线完成的科研动作。

子智能体在进程内执行，共享父会话工作区，
产物写回 workspace，供后续智能体与审计器消费。不经过 DAG 的 HITL 节点。
"""
from __future__ import annotations

import copy
import re
import uuid
from pathlib import Path
from typing import Any

from . import extraction, literature, local_docs, stats
from . import reproduce  # noqa: F401  导入即注册 reproduce actions
from .fixtures import demo_works
from .specs import ROSTER, get_spec, list_agents
from ..citation import gbt7714
from ..config import ROOT_DIR
from ..orchestrator.engine import TaskContext

_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[^\s\"']+\.(?:csv|xlsx|xls|txt)", re.I)

# 合成样例的显式诚实性水印：任何回落到 fixtures 的产物抬头都必须带这句，
# 防止演示数据被误当作真实科研证据引用。
DEMO_WATERMARK = "⚠️ 演示数据：在线检索不可用，以下为合成样例，不构成真实科研证据"


def _instruction_header(agent: str, instruction: str) -> str:
    """产物抬头：说明由哪个专家、按什么准则执行（spec.prompt 不再是死数据）。"""
    if not instruction:
        return ""
    return f"<!-- 执行专家：{agent}\n执行准则：{instruction} -->\n\n"


def _demo_pool(pool: list[dict[str, Any]]) -> bool:
    """判断文献池是否含合成演示样例（决定产物是否需要诚实性水印）。"""
    return any(isinstance(w, dict) and w.get("demo") for w in pool or [])


def merge_pools(*pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按文献 id 合并并行检索结果，保留更高分。"""
    by_id: dict[str, dict[str, Any]] = {}
    for pool in pools:
        for work in pool or []:
            key = str(work.get("id") or work.get("title") or "")
            if not key:
                continue
            old = by_id.get(key)
            if old is None or float(work.get("score") or 0) > float(old.get("score") or 0):
                by_id[key] = work
    return sorted(by_id.values(), key=lambda x: -float(x.get("score") or 0))


def _ctx(topic: str, artifacts: dict[str, Any] | None = None, *, context_scope=None) -> TaskContext:
    """为专项智能体构造一次性 TaskContext，不落工作流 HITL。"""
    ctx = TaskContext(
        "spec-" + uuid.uuid4().hex[:8],
        "research_pipeline",
        {"topic": topic, "cite_style": "gbt7714",
         "search_query": topic},
    )
    if artifacts:
        ctx.artifacts.update(artifacts)
    if context_scope is not None:
        ctx.params["research_context"] = copy.deepcopy(context_scope)
    return ctx


def _topic_of(request: str, workspace: dict[str, Any]) -> str:
    return str(workspace.get("topic") or request or "").strip()


def run(name: str, request: str, *, workspace: dict[str, Any] | None = None,
        offline: bool = True, extra: dict[str, Any] | None = None, context_scope=None) -> dict[str, Any]:
    """执行命名专项智能体，写回 workspace，返回可审计摘要。

    spec.prompt 作为执行准则注入：LLM 路径由 runtime 用作子代理 system
    前缀；本确定性路径则写入产物抬头（digest/outline）与文献池 notice，
    保证编制表角色提示词在任何路径下都不是被忽略的死数据。
    """
    spec = get_spec(name)
    ws = workspace if workspace is not None else {}
    extra = extra or {}
    instruction = str(extra.get("instruction") or spec.prompt or "")
    if spec.name == "literature":
        result = _run_literature(request, ws, offline, extra, context_scope=context_scope)
        if result.get("ok") and instruction:
            notice = str(ws.get("literature_notice") or "")
            header = _instruction_header(spec.title, instruction)
            if header and not notice.startswith("<!-- 执行专家："):
                ws["literature_notice"] = header + notice
                result["notice"] = ws["literature_notice"]
        return result
    if spec.name == "extraction":
        result = _run_extraction(request, ws, offline, context_scope=context_scope)
    elif spec.name == "synthesis":
        result = _run_synthesis(request, ws, offline, context_scope=context_scope)
    elif spec.name == "citation":
        return _run_citation(request, ws, extra)
    elif spec.name == "stats":
        return _run_stats(request, ws, extra)
    elif spec.name == "critic":
        return _run_critic(request, ws, extra)
    elif spec.name == "explore":
        return _run_explore(request, ws, extra)
    else:
        return {"ok": False, "agent": spec.name,
                "error": f"{spec.name} 是调度智能体，请通过 runtime 规划执行"}
    if result.get("ok") and instruction:
        _stamp_artifact(ws, spec, instruction)
        result = dict(result)
        result["instruction"] = instruction
    return result


def _stamp_artifact(workspace: dict[str, Any], spec, instruction: str) -> None:
    """给文本产物（摘要稿/综述框架）加抬头与（如适用的）演示水印。"""
    header = _instruction_header(spec.title, instruction)
    pool = list(workspace.get("pool") or [])
    watermark = (DEMO_WATERMARK + "\n\n") if _demo_pool(pool) else ""
    for key in ("digest_md", "outline_md"):
        text = str(workspace.get(key) or "")
        if not text:
            continue
        prefix = watermark + header
        if prefix and not text.startswith("<!-- 执行专家：") and not text.startswith(DEMO_WATERMARK):
            workspace[key] = prefix + text


def _run_literature(request: str, workspace: dict[str, Any],
                    offline: bool, extra: dict[str, Any] | None = None, *, context_scope=None) -> dict[str, Any]:
    topic = _topic_of(request, workspace) or "large language model agents"
    workspace["topic"] = topic
    def sample_pool() -> dict[str, Any]:
        works = [{**work, "demo": True, "source_type": "synthetic_demo",
                  "title": "【离线演示样例】" + str(work.get("title") or "")}
                 for work in demo_works(topic)]
        result = literature.score_pool(topic, works, offline=True)
        result["notice"] = (DEMO_WATERMARK + "\n"
                            "合成演示样例，仅验证流程；不可作为真实科研证据。")
        result["source"] = "synthetic_demo"
        return result
    if offline:
        scored = sample_pool()
    else:
        ctx = _ctx(topic, context_scope=context_scope)
        try:
            scored = literature.literature_recursive_filter(ctx, {"threshold": 0.18})
        except Exception as exc:  # noqa: BLE001 —— 在线失败回落离线样例
            scored = sample_pool()
            scored["degraded"] = True
            scored["error"] = str(exc)
    pool = list(scored.get("pool") or [])
    # 本地全文材料（用户显式提供的 PDF/文本）：并入池尾，带 full_text 供全文抽取
    local_entries, local_skipped = local_docs.load_entries((extra or {}).get("local_docs"))
    if local_entries or local_skipped:
        pool = local_docs.merge_into_pool(pool, local_entries)
        scored["local_docs"] = {"ingested": len(local_entries), "skipped": local_skipped}
    workspace["pool"] = pool
    workspace["lit_rounds"] = scored.get("rounds") or []
    workspace["rounds"] = workspace["lit_rounds"]
    workspace["search_metadata"] = scored.get("search_metadata") or {}
    workspace["literature_notice"] = scored.get("notice") or ""
    return {"ok": True, "agent": "literature", "summary": scored.get("summary", ""),
            "pool_count": len(pool), "rounds": workspace["lit_rounds"],
            "threshold": scored.get("threshold"),
            "degraded": bool(scored.get("degraded")),
            "source_type": "synthetic_demo" if scored.get("source") == "synthetic_demo" else "retrieved_metadata",
            "notice": scored.get("notice", "")}


def _run_extraction(request: str, workspace: dict[str, Any], offline: bool = False, *, context_scope=None) -> dict[str, Any]:
    topic = _topic_of(request, workspace)
    pool = list(workspace.get("pool") or [])
    if not pool:
        return {"ok": False, "agent": "extraction",
                "error": "文献池为空，请先运行 literature 智能体"}
    ctx = _ctx(topic, {"lit_filter": {"pool": pool}}, context_scope=context_scope)
    ctx.params["offline"] = offline
    payload = extraction.extract_pool(ctx, {"limit": 20})
    workspace["records"] = payload.get("records") or []
    workspace["digest_md"] = payload.get("digest_md") or ""
    return {"ok": True, "agent": "extraction",
            "summary": f"已抽取 {len(workspace['records'])} 篇",
            "record_count": len(workspace["records"]),
            "digest_chars": len(workspace["digest_md"])}


def _run_synthesis(request: str, workspace: dict[str, Any], offline: bool = False, *, context_scope=None) -> dict[str, Any]:
    topic = _topic_of(request, workspace)
    records = list(workspace.get("records") or [])
    if not records:
        return {"ok": False, "agent": "synthesis",
                "error": "抽取记录为空，请先运行 extraction 智能体"}
    ctx = _ctx(topic, {"extract": {"records": records}}, context_scope=context_scope)
    ctx.params["offline"] = offline
    payload = extraction.build_review_frame(ctx, {})
    workspace["sections"] = payload.get("sections") or []
    workspace["outline_md"] = payload.get("outline_md") or ""
    workspace["verification_scope"] = payload.get("verification_scope", "not_recorded")
    return {"ok": True, "agent": "synthesis",
            "summary": f"综述框架 {len(workspace['sections'])} 节",
            "section_count": len(workspace["sections"]),
            "outline_chars": len(workspace["outline_md"])}


def _run_citation(request: str, workspace: dict[str, Any],
                  extra: dict[str, Any]) -> dict[str, Any]:
    topic = _topic_of(request, workspace)
    pool = list(workspace.get("pool") or [])
    if not pool:
        return {"ok": False, "agent": "citation",
                "error": "文献池为空，请先运行 literature 智能体"}
    style = str(extra.get("style") or workspace.get("cite_style") or "gbt7714")
    ctx = _ctx(topic, {"lit_filter": {"pool": pool}})
    ctx.params["cite_style"] = style
    payload = gbt7714.format_references(ctx, {"limit": 20})
    workspace["citations"] = payload.get("entries") or []
    workspace["cite_style"] = payload.get("style") or style
    return {"ok": True, "agent": "citation",
            "summary": f"{workspace['cite_style']} 已排版 {len(workspace['citations'])} 条",
            "citation_count": len(workspace["citations"]),
            "style": workspace["cite_style"]}


def _run_stats(request: str, workspace: dict[str, Any],
               extra: dict[str, Any]) -> dict[str, Any]:
    path = str(extra.get("path") or "")
    if not path:
        found = _PATH_RE.search(request or "")
        path = found.group(0) if found else ""
    if not path and ("示例" in (request or "") or extra.get("use_demo")):
        path = str(ROOT_DIR / "examples" / "experiment_demo.csv")
    if not path:
        return {"ok": False, "agent": "stats", "error": "缺少数据文件路径"}
    result = stats.analyze(Path(path))
    workspace["stats"] = {
        "shape": result.get("shape"),
        "columns": result.get("columns"),
        "outlier_cols": list((result.get("outliers") or {}).keys()),
        "charts": result.get("charts") or [],
    }
    return {"ok": True, "agent": "stats", "summary": f"统计 {result.get('shape')}",
            **{k: result.get(k) for k in
               ("shape", "columns", "outliers", "correlations",
                "charts", "chart_advice")}}


def _run_critic(request: str, workspace: dict[str, Any],
                extra: dict[str, Any]) -> dict[str, Any]:
    from ..research_analysis import analyze_artifacts
    audit = analyze_artifacts(workspace, _topic_of(request, workspace))
    findings: list[dict[str, Any]] = []
    pool = list(workspace.get("pool") or [])
    records = list(workspace.get("records") or [])
    sections = list(workspace.get("sections") or [])
    cites = list(workspace.get("citations") or [])
    events = list(extra.get("events") or [])

    if not pool:
        findings.append({"severity": "high", "id": "A0",
                         "title": "文献池为空",
                         "detail": "没有可复核的文献证据，综述与引用均不可信"})
    for work in pool:
        if work.get("score") is None:
            findings.append({"severity": "medium", "id": "A1",
                             "title": "文献缺相关性分数",
                             "detail": str(work.get("title") or work.get("id") or "")})
        if not str(work.get("reason") or "").strip():
            findings.append({"severity": "medium", "id": "A2",
                             "title": "文献缺去留理由",
                             "detail": str(work.get("title") or "")})

    for rec in records:
        if not (rec.get("method") or rec.get("conclusion")):
            findings.append({"severity": "medium", "id": "C1",
                             "title": "抽取字段空缺",
                             "detail": str(rec.get("title") or "")})
    digest = str(workspace.get("digest_md") or "")
    if records and digest and "出处" not in digest:
        findings.append({"severity": "low", "id": "C2",
                         "title": "摘要稿未标注出处栏",
                         "detail": "digest.md 应含出处字段以便回链"})

    for reference in audit["reference_audit"]["items"]:
        if reference["location"].startswith("sections[") and reference["status"] != "resolved":
            findings.append({"severity": "high", "id": "B1",
                             "title": "综述引用未唯一匹配文献池",
                             "detail": str(reference["reference"])})
    if pool and not cites:
        findings.append({"severity": "low", "id": "D1",
                         "title": "尚未排版参考文献",
                         "detail": "可运行 citation 智能体生成 GB/T 7714 条目"})
    if any(e.get("type") == "permission_denied" for e in events):
        findings.append({"severity": "medium", "id": "E1",
                         "title": "会话中存在被拒绝的工具调用",
                         "detail": "请检查权限策略是否误伤必要步骤"})

    high = sum(1 for f in findings if f["severity"] == "high")
    verdict = "pass" if high == 0 else "continue"
    cov, refs, claims = audit["coverage"], audit["reference_audit"], audit["claim_audit"]
    audit_summary = {
        "schema_version": audit["schema_version"], "assessment": "traceability_only",
        "unique_papers": cov["unique_papers"], "demo_papers": cov["demo_papers"],
        "eligible_papers": cov["eligible_papers"], "duplicate_papers": cov["duplicate_papers"],
        "unresolved_references": refs["unresolved_count"], "ambiguous_references": refs["ambiguous_count"],
        "duplicate_references": refs["duplicate_count"], "claim_count": len(claims["items"]),
        "supported_claims": claims["supported_count"], "gap_count": len(audit["research_gaps"]),
    }
    verification_scope = "structure_and_traceability_only"
    verdict_note = "pass 仅表示既有结构检查无高风险项，不代表科研结论通过；论断支持关系仍未验证。"
    workspace["research_audit"] = audit
    workspace["critic"] = {"findings": findings, "verdict": verdict,
                           "request": request, "research_audit": audit_summary,
                           "verification_scope": verification_scope, "verdict_note": verdict_note}
    return {"ok": True, "agent": "critic", "verdict": verdict,
            "summary": f"结构审计 {len(findings)} 项，高风险 {high}；论断支持关系未验证",
            "verification_scope": verification_scope, "verdict_note": verdict_note,
            "research_audit": audit_summary,
            "findings": findings, "high": high,
            "pool_count": len(pool), "record_count": len(records),
            "section_count": len(sections), "citation_count": len(cites)}


def _run_explore(request: str, workspace: dict[str, Any],
                 extra: dict[str, Any]) -> dict[str, Any]:
    roster = [{"name": s["name"], "title": s["title"], "mode": s["mode"],
               "feature": s["feature"]} for s in list_agents()]
    snapshot = {
        "topic": workspace.get("topic", ""),
        "keys": sorted(workspace.keys()),
        "pool_count": len(workspace.get("pool") or []),
        "record_count": len(workspace.get("records") or []),
        "section_count": len(workspace.get("sections") or []),
        "citation_count": len(workspace.get("citations") or []),
    }
    payload: dict[str, Any] = {
        "ok": True, "agent": "explore",
        "summary": f"编制表 {len(ROSTER)} 个智能体；工作区 {len(workspace)} 键",
        "roster": roster, "workspace": snapshot, "request": request,
    }
    if extra.get("doctor") or ("自检" in (request or "")):
        from .. import doctor
        payload["doctor"] = doctor.run_doctor()
    return payload
