"""应用服务层：桌面版与 TUI 版共用的任务管理中枢。

职责：任务创建/暂停/续跑/人工干预、Checkpoint 恢复、产物导出、
事件订阅转发（引擎线程 → UI 线程）。
"""
from __future__ import annotations

import csv
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from . import settings as app_settings
from .citation.gbt7714 import format_entry
from .config import CHECKPOINT_DIR
from .orchestrator.engine import (TaskContext, intervene, load_workflow, run,
                                  workflow_node_ids)

_tasks: dict[str, TaskContext] = {}
_lock = threading.Lock()
_AGENTS_READY = False   # 工作流动作是否已注册（惰性）
_swarm_workers: dict[str, threading.Thread] = {}
_swarm_worker_errors: dict[str, str] = {}
_swarm_lock = threading.RLock()


def _swarm_coordinator(plan_id: str | None = None):
    from .bridge import _runtime
    from .swarm import SwarmCoordinator
    offline = bool(app_settings.load().get('default_offline', True))
    if plan_id:
        saved = SwarmCoordinator(_runtime(offline=True)).snapshot(plan_id)
        offline = bool(saved.get('offline', True))
    return SwarmCoordinator(_runtime(offline=offline))


def plan_swarm(request: str, branches=None, max_parallel=None) -> dict[str, Any]:
    return _swarm_coordinator().plan(request, branches, max_parallel)


def get_swarm(plan_id: str) -> dict[str, Any]:
    result = _swarm_coordinator(plan_id).snapshot(plan_id)
    with _swarm_lock:
        if plan_id in _swarm_worker_errors:
            result.update(status='error', error=_swarm_worker_errors[plan_id])
    return result


def list_swarms() -> list[dict[str, Any]]:
    return _swarm_coordinator().list(limit=100)


def start_swarm(plan_id: str) -> dict[str, Any]:
    coordinator = _swarm_coordinator(plan_id)
    with _swarm_lock:
        previous = _swarm_workers.get(plan_id)
        if previous and previous.is_alive():
            return coordinator.snapshot(plan_id)
        state = coordinator.snapshot(plan_id)
        if state.get('status') not in {'planned'}:
            return state
        _swarm_worker_errors.pop(plan_id, None)

        def work():
            try:
                coordinator.start(plan_id)
            except Exception as exc:  # Surface failures without killing a UI event loop.
                from .workbench import redact
                with _swarm_lock:
                    _swarm_worker_errors[plan_id] = redact(str(exc))
            finally:
                with _swarm_lock:
                    _swarm_workers.pop(plan_id, None)

        worker = threading.Thread(target=work, name=f'scizhi-swarm-{plan_id}', daemon=True)
        _swarm_workers[plan_id] = worker
        worker.start()
        return {**state, 'status': 'queued'}


def cancel_swarm(plan_id: str) -> dict[str, Any]:
    return _swarm_coordinator(plan_id).cancel(plan_id)


def get_control_tower() -> dict[str, Any]:
    from .control_tower import snapshot
    return snapshot()


def _ensure_agents() -> None:
    """惰性注册工作流动作。

    导入 agents 会连带拉入 httpx / sklearn 等重依赖（实测 +2s）。桥与桌面
    的就绪握手不该为此等待——只有真的要跑工作流（建任务/续跑/重跑）时才付。
    """
    global _AGENTS_READY
    if _AGENTS_READY:
        return
    from .agents import extraction, literature, local_docs  # noqa: F401 —— 导入即注册动作
    from .citation import gbt7714  # noqa: F401 —— 同上
    _AGENTS_READY = True

STATUS_TEXT = {"pending": "排队中", "running": "运行中", "paused": "已暂停",
               "waiting_human": "待人工确认", "done": "已完成", "failed": "失败",
               "cancelled": "已取消"}

# 工作流节点 id（引擎在 artifacts 里还会放 _history/_lit_state 等内部键，
# 判断“哪些节点已完成”时要排除）
INTERNAL_KEYS = {"_history", "_lit_state"}

NODE_NAMES = {"lit_filter": "文献猎手", "human_review": "人工确认",
              "extract": "综述搭子·抽取", "review_frame": "综述框架",
              "citation": "格式管家"}


def _normalize_research_context(value: dict[str, Any] | None) -> dict[str, str] | None:
    """Keep only the journal-owned binding carried into workflow checkpoints."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("research_context 必须是对象")
    allowed = {"project_id", "workspace_mode", "source_session_id", "session_root"}
    if set(value) - allowed:
        raise ValueError("research_context 包含不支持的字段")
    out: dict[str, str] = {}
    for key in allowed:
        item = value.get(key, "")
        if item is None:
            item = ""
        if not isinstance(item, str) or len(item) > 4096:
            raise ValueError(f"research_context.{key} 必须是文本")
        out[key] = item
    return out


def init() -> None:
    """启动初始化：应用持久化设置 + 插件/MCP 工具注册 + Checkpoint 恢复。"""
    app_settings.apply_at_startup()
    from . import plugins as plugin_registry
    plugin_registry.load()
    try:
        from . import mcp
        mcp.register_actions()  # 在线 MCP 服务器的工具 → 工作流动作
    except Exception:  # noqa: BLE001 —— MCP 离线不影响主流程
        pass
    restore_tasks()


def restore_tasks() -> None:
    """Load missing checkpoints without replacing this process's live contexts.

    A checkpoint marked running belongs to an earlier process and has no worker
    here. Restore it paused, requiring an explicit human resume.
    """
    for f in CHECKPOINT_DIR.glob("*.json"):
        try:
            with _lock:
                if f.stem in _tasks:
                    continue
                ctx = TaskContext.load(f.stem)
                if ctx.status in {"running", "pending"}:
                    ctx.status = "paused"
                    ctx.emit("workflow", message="进程已重启，断点已恢复；请手动续跑", status="paused")
                _tasks.setdefault(ctx.task_id, ctx)
        except Exception:  # noqa: BLE001
            continue


def node_sequence(workflow: str = "research_pipeline") -> list[dict[str, str]]:
    """工作流节点序列（含中文名），供 UI 画步骤条。"""
    return [{"id": nid, "name": NODE_NAMES.get(nid, nid)}
            for nid in workflow_node_ids(workflow)]


def create_task(topic: str, keywords: str = "", year_from: int | None = None,
                year_to: int | None = None, cite_style: str = "gbt7714",
                workflow: str = "research_pipeline", *, offline: bool = False,
                path: str = "", research_context: dict[str, Any] | None = None) -> str:
    """创建任务并后台启动，返回 task_id。"""
    if not topic.strip():
        raise ValueError("研究方向不能为空")
    if not isinstance(workflow, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", workflow):
        raise ValueError("无效的工作流标识")
    load_workflow(workflow)
    _ensure_agents()   # 真要跑流水线了，此刻才付重依赖的导入代价
    task_id = uuid.uuid4().hex[:12]
    params = {
        "topic": topic.strip(), "keywords": keywords.strip(),
        "year_from": year_from, "year_to": year_to, "cite_style": cite_style,
        "offline": bool(offline), "path": path,
    }
    binding = _normalize_research_context(research_context)
    if binding is not None:
        params["research_context"] = binding
    ctx = TaskContext(task_id, workflow, params)
    with _lock:
        _tasks[task_id] = ctx
    ctx.save()
    _start_task_worker(ctx)
    return task_id


def _start_task_worker(ctx: TaskContext, *, operation: str = "start") -> None:
    """Claim execution before changing its cursor or starting a background thread."""
    if not ctx._worker_lock.acquire(blocking=False):
        if operation == "resume":
            return  # Repeated confirmation while its worker is active is harmless.
        raise ValueError("任务正在运行，请等待暂停或取消完成后再重跑")
    worker_started = False
    try:
        approval = None
        if operation == "resume":
            if ctx.status not in {"paused", "waiting_human"}:
                raise ValueError("仅暂停或等待人工确认的任务可以续跑；已结束任务请使用重跑")
            if ctx.status == "waiting_human":
                nodes = {n["id"]: n for n in load_workflow(ctx.workflow)["nodes"]}
                approval = nodes.get(ctx.pointer)
                if not approval or approval.get("type") != "human_approval":
                    raise ValueError("人工确认节点与断点不一致；未跳过节点，请核对工作流后重试")
        elif operation == "restart":
            if ctx.status == "running":
                raise ValueError("任务正在运行，请先暂停或取消再重跑")
            topic = ctx.params.get("topic", "")
            ctx.reset()
            ctx.emit("workflow", message=f"重跑任务：{topic}", status="running")
            ctx.save()

        def work():
            nonlocal worker_started
            worker_started = True
            try:
                if operation == "resume" and not ctx.cancel_requested:
                    if approval is not None:
                        ctx.pointer = approval.get("next")
                        ctx.emit("human", message="人工已确认，继续执行")
                    ctx.clear_pause()
                run(ctx, resume=operation == "resume")
            finally:
                ctx._worker_lock.release()

        threading.Thread(target=work, daemon=True).start()
    except Exception:
        if not worker_started:
            ctx._worker_lock.release()
        raise


def get(task_id: str) -> TaskContext | None:
    return _tasks.get(task_id)


def task_progress(ctx: TaskContext) -> tuple[int, int]:
    """Return completed/total stages from the engine cursor, not artifact count.

    The cursor identifies the next unfinished stage, including a paused or
    failed stage that may already have partial artifacts. Walking the selected
    workflow path also counts approved human stages and excludes skipped
    condition branches. This works after checkpoint reload without event logs.
    """
    sequence = load_workflow(ctx.workflow)["nodes"]
    nodes = {node["id"]: node for node in sequence}
    total = len(nodes)
    if ctx.status == "done":
        return total, total
    if ctx.status == "pending" or ctx.pointer not in nodes:
        return 0, total
    completed: set[str] = set()
    cursor = sequence[0]["id"]
    while cursor in nodes and cursor != ctx.pointer and cursor not in completed:
        completed.add(cursor)
        node = nodes[cursor]
        if node["type"] == "condition":
            branch = (ctx.artifacts.get(cursor) or {}).get("branch", False)
            cursor = node.get("on_true") if branch else node.get("on_false")
        else:
            cursor = node.get("next")
    return len(completed), total


def list_tasks() -> list[dict[str, Any]]:
    """任务列表（新→旧），带创建时间与完成进度，供 UI 档案页直接消费。"""
    out = []
    with _lock:
        tasks = list(_tasks.values())
    for t in reversed(tasks):
        done, total = task_progress(t)
        out.append({"task_id": t.task_id, "status": t.status,
                    "status_text": STATUS_TEXT.get(t.status, t.status),
                    "topic": t.params.get("topic", ""), "pointer": t.pointer,
                    "workflow_id": t.workflow, "offline": bool(t.params.get("offline")),
                    "created_at": t.created_at,
                    "done_nodes": done, "total_nodes": total})
    return out


def pause(task_id: str) -> None:
    ctx = _tasks[task_id]
    ctx.request_pause()


def cancel(task_id: str) -> None:
    """硬取消：在最近的节点边界终止（区别于暂停：取消后不可续跑，可整体重跑）。

    引擎线程已挂起（暂停/待人工确认）时没有"边界"可等，直接落取消态。
    """
    ctx = _tasks[task_id]
    ctx.request_cancel()
    ctx.emit("human", message="已请求取消任务")
    if ctx.status in ("paused", "waiting_human"):
        ctx.status = "cancelled"
        ctx.emit("workflow", message="任务已取消，产物与断点保留备查", status="cancelled")
        ctx.save()


def restart(task_id: str) -> str:
    """整体重跑：保留研究方向等参数，清空产物从第一个节点重新执行。

    仅在任务不在运行中时允许（运行中请先暂停或取消）。
    """
    ctx = _tasks[task_id]
    _ensure_agents()
    _start_task_worker(ctx, operation="restart")
    return task_id


def delete_task(task_id: str) -> bool:
    """删除任务：移出内存并删除断点存档文件；运行中的任务先取消再删。"""
    with _lock:
        ctx = _tasks.get(task_id)
        if ctx is None:
            return False
        ctx.request_cancel()
        with ctx._save_lock:
            f = CHECKPOINT_DIR / f"{task_id}.json"
            f.unlink(missing_ok=True)
            # 删除成功后再移出注册表；文件占用等错误允许用户重试。
            ctx._deleted = True
            _tasks.pop(task_id)
        return True


def resume(task_id: str) -> None:
    """续跑：人工审批节点视为已确认，指针推进到下一节点。"""
    _ensure_agents()
    ctx = _tasks[task_id]
    _start_task_worker(ctx, operation="resume")


def drop_works(task_id: str, drop_ids: list[str]) -> int:
    """人工干预：按 OpenAlex ID 从文献池剔除文献，返回剔除数。"""
    ctx = _tasks[task_id]
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    kept = [w for w in pool if w.get("id") not in set(drop_ids)]
    intervene(ctx, "lit_filter", {"pool": kept})
    return len(pool) - len(kept)


def artifacts_rev(ctx: TaskContext) -> str:
    """产物轻量版本号：内容变化则变化，用于轮询增量下发（不序列化重字段）。

    覆盖口径：已完成节点数、文献池篇数与轮次数、摘要稿长度与抽取条数、
    综述框架长度、参考文献条数。人工干预（drop/intervene）会改变篇数或
    节点数，因此同样触发版本变化。
    """
    lit = ctx.artifacts.get("lit_filter", {}) or {}
    ex = ctx.artifacts.get("extract", {}) or {}
    rf = ctx.artifacts.get("review_frame", {}) or {}
    ct = ctx.artifacts.get("citation", {}) or {}
    return ":".join(str(x) for x in (
        len([k for k in ctx.artifacts if not k.startswith("_")]),
        len(lit.get("pool") or []), len(lit.get("rounds") or []),
        len(ex.get("digest_md") or ""), len(ex.get("records") or []),
        len(rf.get("outline_md") or ""),
        len(ct.get("entries") or []),
    ))


def artifacts_of(task_id: str) -> dict[str, Any]:
    """整理产物视图（供 UI 直接消费）。"""
    ctx = _tasks[task_id]
    art = ctx.artifacts
    records = art.get("extract", {}).get("records", [])
    return {
        "topic": ctx.params.get("topic", ""),
        "pool": art.get("lit_filter", {}).get("pool", []),
        "rounds": art.get("lit_filter", {}).get("rounds", []),
        "records": records,
        "sections": art.get("review_frame", {}).get("sections", []),
        "claims": art.get("claims", []),
        "search_metadata": art.get("lit_filter", {}).get("search_metadata") or art.get("search_metadata", {}),
        "material_scope": sorted({r["material_scope"] for r in records
                                  if isinstance(r, dict) and isinstance(r.get("material_scope"), str)}),
        "verification_scope": art.get("review_frame", {}).get("verification_scope", "not_recorded"),
        "digest_md": art.get("extract", {}).get("digest_md", ""),
        "outline_md": art.get("review_frame", {}).get("outline_md", ""),
        "citations": art.get("citation", {}).get("entries", []),
        "events": list(ctx.events),
        "status": ctx.status,
        "status_text": STATUS_TEXT.get(ctx.status, ctx.status),
        "pointer": ctx.pointer,
    }


def research_audit(task_id: str) -> dict[str, Any]:
    """Read-only traceability audit of existing artifacts; never invent search history."""
    from .research_analysis import analyze_artifacts
    ctx = _tasks[task_id]
    return analyze_artifacts(ctx.artifacts, topic=ctx.params.get("topic", ""))


def evidence_graph(task_id: str, force: bool = False) -> dict[str, Any]:
    """证据图谱：文献池内部引用网络 PNG + 统计。

    结果缓存在任务 artifacts（键 _evidence_graph），文献池不变时直接复用；
    force=True 强制重渲染。
    """
    from .agents import graph as graph_mod
    ctx = _tasks[task_id]
    cached = ctx.artifacts.get("_evidence_graph")
    if cached and not force:
        return dict(cached, cached=True)
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    if not pool:
        return {"nodes": 0, "edges": 0, "coverage": 0, "isolated": 0,
                "file": "", "items": []}
    r = graph_mod.build_evidence_graph(pool, topic=ctx.params.get("topic", ""))
    ctx.artifacts["_evidence_graph"] = r
    ctx.save()
    return r


def export_artifacts(task_id: str, out_dir: str | Path) -> list[str]:
    """导出科研产物、确定性证据审计与 JSON 台账，以及实际文件清单。

    AI 参与生成的文件带 AIGC 显式标识（aigc.py），manifest 逐文件说明参与度。
    """
    from . import aigc
    ctx = _tasks[task_id]
    art = ctx.artifacts
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    topic = ctx.params.get("topic", "")

    digest = art.get("extract", {}).get("digest_md", "")
    llm_extract = any(e.get("kind") == "node" and e.get("node") == "extract"
                      and "智能体语义抽取" in (e.get("message") or "")
                      for e in ctx.events)
    if digest:
        if aigc.MARK not in digest:  # 旧任务产物无标识：导出时补齐
            digest += aigc.footer_md(llm_used=llm_extract,
                                     task_id=task_id, topic=topic)
        p = out / "文献摘要整理稿.md"
        p.write_text(digest, encoding="utf-8")
        written.append(str(p))
    outline = art.get("review_frame", {}).get("outline_md", "")
    if outline:
        if aigc.MARK not in outline:
            outline += aigc.footer_md(llm_used=False,
                                      task_id=task_id, topic=topic)
        p = out / "文献综述框架.md"
        p.write_text(outline, encoding="utf-8")
        written.append(str(p))

    pool = art.get("lit_filter", {}).get("pool", [])
    if pool:
        header = aigc.export_header(kind="参考文献排版", llm_used=False,
                                    task_id=task_id, topic=topic)
        lines = [header, "GB/T 7714—2015："] + \
                [format_entry(w, "gbt7714") for w in pool]
        lines += ["", "APA 7："] + [format_entry(w, "apa") for w in pool]
        p = out / "参考文献.txt"
        p.write_text("\n".join(lines), encoding="utf-8")
        written.append(str(p))
        p = out / "文献池.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            wtr = csv.writer(f)
            wtr.writerow(["题名", "作者", "年份", "来源", "DOI", "相关度", "去留理由"])
            for w in pool:
                wtr.writerow([w.get("title", ""), "; ".join(w.get("authors") or []),
                              w.get("year", ""), w.get("venue", ""), w.get("doi", ""),
                              w.get("score", ""), w.get("reason", "")])
        written.append(str(p))

    # 证据图谱 PNG：随包导出已生成的确定性布局图像。
    graph_meta = art.get("_evidence_graph") or {}
    graph_src = graph_meta.get("file")
    if graph_src and Path(graph_src).exists():
        import shutil
        gdst = out / "证据图谱.png"
        shutil.copyfile(graph_src, gdst)
        written.append(str(gdst))

    # Same read-only ledger is available through service.research_audit and exports.
    from .research_analysis import render_report
    audit = research_audit(task_id)
    p = out / "科研证据审计.md"
    p.write_text(render_report(audit), encoding="utf-8")
    written.append(str(p))
    p = out / "科研证据台账.json"
    p.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(str(p))

    # 导出清单：AI 参与度与溯源说明（审计/合规材料）
    manifest_entries = [
        {"file": "文献摘要整理稿.md", "kind": "摘要整理稿", "ai": True,
         "note": "结构化抽取自文献摘要，条目回链出处"},
        {"file": "文献综述框架.md", "kind": "综述框架", "ai": True,
         "note": "大纲框架与待核对引用映射，未验证论断支持关系"},
        {"file": "参考文献.txt", "kind": "引用排版", "ai": False,
         "note": "GB/T 7714 / APA 规则引擎排版，零格式幻觉"},
        {"file": "文献池.csv", "kind": "证据数据", "ai": False,
         "note": "检索-评分-筛选原始记录，含去留理由"},
        {"file": "科研证据审计.md", "kind": "证据可追溯性审计", "ai": False,
         "note": "确定性检查引用身份与原文定位；非论断真伪裁决，非全文或实验复现核验"},
        {"file": "科研证据台账.json", "kind": "结构化证据台账", "ai": False,
         "note": "保留来源类型、演示标识、重复与歧义、未核验状态；可追溯性不代表支持关系成立"},
    ]
    if graph_src and Path(graph_src).exists():
        manifest_entries.append(
            {"file": "证据图谱.png", "kind": "引用网络可视化", "ai": False,
             "note": f"池内引用边 {graph_meta.get('edges', 0)} 条，确定性环形布局渲染"})
    written_names = {Path(path).name for path in written}
    manifest_entries = [entry for entry in manifest_entries if entry["file"] in written_names]
    manifest = aigc.manifest_text(manifest_entries, task_id=task_id, topic=topic)
    p = out / "manifest.md"
    p.write_text(manifest, encoding="utf-8")
    written.append(str(p))
    return written
