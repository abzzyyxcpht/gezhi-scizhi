"""格致 stdio 桥：供 Node.js TUI 调用的 JSON Lines 协议服务。

协议：每行一个 JSON 请求，响应一行 JSON。
  {"cmd":"create","topic":"...","keywords":"...","cite_style":"gbt7714"}
  {"cmd":"poll","task_id":"...","since":0,"art_rev":"…","full":false}
                                               → 状态/进度/增量事件；产物版本未变时省略重字段
  {"cmd":"pause"/"resume"/"cancel"/"restart","task_id":"..."}
  {"cmd":"delete","task_id":"..."}             → 删除任务与断点存档
  {"cmd":"drop","task_id":"...","ids":["https://openalex.org/W..."]}
  {"cmd":"export","task_id":"...","dir":"..."}
  {"cmd":"list"}
  {"cmd":"stats","path":"..."}
  {"cmd":"settings_get"} / {"cmd":"settings_save","settings":{...}}
  {"cmd":"llm_status"}                         → LLM 端点/模型路由/可用模型清单
  {"cmd":"plugin_commands"} / {"cmd":"plugin_call","name":..,"args":..,"task_id":..}
  {"cmd":"plugins_summary","reload":false}     → 插件清单（可强制重扫）
  {"cmd":"skills_list"} / {"cmd":"skill_toggle","name":..,"on":true}
  {"cmd":"context_status"} / {"cmd":"context_init"}   → GEZHI.md 研究上下文
  {"cmd":"mcp_list"}                           → MCP 服务器与工具探测
  {"cmd":"git_cmd","sub":"log|status"}         → 研究档案库
  {"cmd":"session_rewind","session_id":"...","turns":1} → 回滚会话最后 N 个用户轮次
  {"cmd":"board","task_id":"..."}              → 科研看板（年份/评分/收敛/覆盖率 ASCII 图）
  {"cmd":"graph","task_id":"..."}              → 证据图谱（锚定率/孤儿文献/缺证据章节）
  {"cmd":"ledger"}                             → 跨任务研究时间账
  {"cmd":"nodes"}                              → 工作流节点序列
  {"cmd":"doctor"}                             → 系统自检
  {"cmd":"usage"}                              → 用量统计（模型与工具调用聚合）
  {"cmd":"demo_seed" / "demo_clean"}           → 注入/清除一条离线演示任务（外观预览脚本专用）
启动：python -m src.bridge
"""
from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

# 就绪路径上只保留轻量模块：doctor/llm/agent_runtime 一律按需导入
# （它们会连带 httpx、sklearn 等重依赖，实测拖慢就绪 2s+）
from . import config, context_file, mcp, plugins, prompts, service
from . import settings as app_settings
from . import skills as skills_mod
from .agents import stats as stats_agent
from .config import ROOT_DIR

_agent_runtime = None
_agent_runtime_key = ""
_runtime_lock = threading.Lock()
_runtimes: dict[str, Any] = {}


def _runtime(offline: bool = True):
    """惰性构造 AgentRuntime（首次 /agent、/plan、/review 等命令时才导入）。

    单例按（离线标志 + 持久化权限策略签名）重建：设置页保存策略后
    下次调用即生效；跨进程审批恢复走 JSONL 会话（_recover_pending），重建不丢审批。
    """
    global _agent_runtime, _agent_runtime_key
    from .agent_runtime import AgentRuntime
    perms = app_settings.load().get("tool_permissions") or {}
    key = f"{offline}|{json.dumps(perms, sort_keys=True)}"
    with _runtime_lock:
        if key not in _runtimes:
            _runtimes[key] = AgentRuntime(offline=offline, llm_enabled=not offline,
                                         permissions=perms)
        _agent_runtime, _agent_runtime_key = _runtimes[key], key
        return _agent_runtime


def _session_runtime(session_id: str):
    from .agent_runtime import SessionStore
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        raise ValueError("无效的会话 ID")
    messages = [e for e in SessionStore().read(session_id) if e.get("type") == "user_message"]
    offline = (messages[-1].get("payload") or {}).get("offline", True) if messages else True
    return _runtime(offline=bool(offline))


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, **kw}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "error": msg}


def handle(req: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(req, dict):
        return _err("请求必须为 JSON 对象")
    result = _handle({key: value for key, value in req.items() if key != "request_id"})
    if "request_id" in req:
        result = {**result, "request_id": req["request_id"]}
    return result


def _handle(req: dict[str, Any]) -> dict[str, Any]:
    cmd = req.get("cmd")
    try:
        if cmd == "workbench_mode":
            from .workspace_modes import MODES, mode_catalog, normalize_mode
            with app_settings.transaction() if 'mode' in req else nullcontext():
                current = app_settings.load()
                revision = current.get('workspace_mode_revision', 0)
                if type(revision) is not int or revision < 0:
                    revision = 0
                if 'mode' in req:
                    mode = normalize_mode(req['mode'])
                    expected = req.get('expected_revision')
                    if expected is not None and (type(expected) is not int or expected != revision):
                        return _err('工作台模式已被其他窗口修改，请刷新后重试') | {'code': 'stale_revision', 'revision': revision}
                    if mode != normalize_mode(current.get('workspace_mode', 'research')):
                        app_settings.save({'workspace_mode': mode})
                        revision += 1
                elif req.get('action', 'get') != 'get':
                    raise ValueError('设置工作台模式时须提供 mode')
                mode = normalize_mode(app_settings.load().get('workspace_mode', 'research'))
            item = MODES[mode]
            return _ok(mode=mode, agent=item['agent'], label=item['label'], modes=mode_catalog(),
                       revision=revision,
                       text=f"当前工作台：{item['label']}。新会话使用此模式；已有会话保持原角色。")
        if cmd in {"model_control_catalog", "model_control_apply"}:
            from .model_controls import model_control_catalog, model_control_apply
            if cmd == "model_control_catalog":
                return _ok(catalog=model_control_catalog(req.get("stage", "planning")))
            catalog = model_control_apply(req.get("stage", "planning"), req.get("profile_id"),
                req.get("expected_revision"), req.get("effort"))
            return _ok(catalog=catalog, settings=app_settings.public_settings())
        if cmd == "session_activity":
            from .session_activity import session_activity
            return _ok(activity=session_activity(req.get("session_id"), limit=req.get("limit", 40)))
        if cmd == 'tower_snapshot':
            return _ok(tower=service.get_control_tower())
        if cmd == 'peer_review':
            from .research_review import handle as peer_review
            return peer_review(req)
        if cmd == 'evidence_brief':
            from .evidence_brief import handle as evidence_brief
            return evidence_brief(req)
        if cmd in {'steer_add', 'steer_status'}:
            from .session_steering import handle as steer
            return steer(req)
        if cmd in {'session_history', 'session_history_message'}:
            from .session_history import handle as history
            return history(req)
        if cmd in {'session_tool_outputs', 'session_tool_output_read', 'session_tool_output_search'}:
            from .tool_output_runtime import handle as outputs
            return outputs(req)
        if cmd in {'review_start', 'review_list', 'review_get', 'review_cancel', 'review_resume',
                   'review_accept', 'review_dismiss', 'review_events'}:
            from .evidence_reviews import handle as reviews
            return reviews(req)
        if cmd in {'queue_add', 'queue_list', 'queue_remove', 'queue_discard',
                   'queue_pause', 'queue_resume', 'queue_reorder'}:
            from .session_queue import handle as session_queue
            return session_queue(req)
        if cmd == 'swarm_plan':
            return _ok(swarm=service.plan_swarm(req.get('request', ''), req.get('branches'), req.get('max_parallel')))
        if cmd == 'swarm_list':
            return _ok(swarms=service.list_swarms())
        if cmd in {'swarm_start', 'swarm_get', 'swarm_cancel'}:
            operation = {'swarm_start': service.start_swarm, 'swarm_get': service.get_swarm,
                         'swarm_cancel': service.cancel_swarm}[cmd]
            return _ok(swarm=operation(req.get('plan_id', '')))
        if isinstance(cmd, str) and cmd.startswith('project_'):
            from . import project_workspace
            return project_workspace.handle(req)
        if isinstance(cmd, str) and cmd.startswith('chat_'):
            from . import session_workspace
            return session_workspace.handle(req)
        if cmd == "session_rewind":
            from . import session_workspace
            return session_workspace.handle(req)
        if isinstance(cmd, str) and (cmd.startswith(('todo_', 'schedule_', 'notification')) or cmd in {'board_snapshot', 'research_handoff'}):
            from . import research_planning
            return research_planning.handle(req)
        from . import workspace_features
        if cmd in workspace_features.COMMANDS:
            return workspace_features.handle(req)
        if isinstance(cmd, str) and cmd.startswith("notebook_"):
            from . import research_notebook
            return research_notebook.handle(req)
        if isinstance(cmd, str) and cmd.startswith("world_"):
            from . import research_world
            return research_world.handle(req)
        if cmd in {"mcp_config", "mcp_save", "mcp_delete", "mcp_probe", "control_status", "control_save", "control_call"}:
            from . import control_integrations
            return control_integrations.handle(req)
        if cmd in {"hook_list", "hook_save", "hook_delete"}:
            from . import lifecycle_hooks
            return lifecycle_hooks.handle(req)
        if cmd in {"workbench_status", "workflows", "workflow_run", "loop_start",
                   "loop_list", "loop_stop", "loop_resume", "session_export", "terminal_open"}:
            from . import workbench
            return workbench.handle(req)
        if cmd == "command":
            from .command_center import execute
            return execute(req.get("text", ""), task_id=req.get("task_id"))
        if cmd == "command_catalog":
            from .command_center import catalog, COMMAND_GROUPS
            from .workspace_modes import normalize_mode
            mode = normalize_mode(req.get('workspace_mode') or req.get('mode') or app_settings.load().get('workspace_mode', 'research'))
            context = req.get('context') or {}
            if not isinstance(context, dict):
                raise ValueError('命令显示上下文须为对象')
            return _ok(commands=catalog(req.get('query', ''), mode, context=context), groups=COMMAND_GROUPS, mode=mode)
        if cmd in {"remote_start", "remote_status", "remote_stop"}:
            from . import remote_control
            if cmd == "remote_start":
                return _ok(**remote_control.start_server(handle, host=req.get("host", "127.0.0.1"),
                                                          port=int(req.get("port", 0))))
            return _ok(**(remote_control.stop_server() if cmd == "remote_stop" else remote_control.server_status()))
        if cmd == "create":
            tid = service.create_task(
                req.get("topic", ""), req.get("keywords", ""),
                req.get("year_from"), req.get("year_to"),
                req.get("cite_style", "gbt7714"))
            return _ok(task_id=tid)
        if cmd == "poll":
            ctx = service.get(req["task_id"])
            if not ctx:
                return _err("任务不存在")
            art = service.artifacts_of(req["task_id"])   # 仅组装引用，成本极小
            since = int(req.get("since", 0))
            events = art["events"]
            done_nodes, total_nodes = service.task_progress(ctx)
            elapsed_s = None
            if ctx.started_at:
                elapsed_s = round((ctx.finished_at or time.time()) - ctx.started_at, 1)
            rev = service.artifacts_rev(ctx)
            node_durations = dict(ctx.node_durations)
            payload = dict(status=art["status"], status_text=art["status_text"],
                           pointer=art["pointer"], events=events[since:],
                           total_events=len(events), created_at=ctx.created_at,
                           done_nodes=done_nodes, total_nodes=total_nodes,
                           workflow_id=ctx.workflow, elapsed_s=elapsed_s,
                           art_rev=rev, node_durations=node_durations)
            # 增量下发：产物版本未变（或客户端显式要 full）时不重复传重字段。
            # 池 17 篇时全量响应 131KB；稳态降到 1KB 级，轮询不再产生常驻流量。
            if req.get("full") or req.get("art_rev") != rev:
                payload.update(pool=art["pool"], rounds=art["rounds"],
                               digest_md=art["digest_md"],
                               outline_md=art["outline_md"],
                               citations=art["citations"])
            return _ok(**payload)
        if cmd == "pause":
            service.pause(req["task_id"])
            return _ok()
        if cmd == "resume":
            service.resume(req["task_id"])
            return _ok()
        if cmd == "cancel":
            service.cancel(req["task_id"])
            return _ok()
        if cmd == "restart":
            service.restart(req["task_id"])
            return _ok()
        if cmd == "delete":
            deleted = service.delete_task(req["task_id"])
            return _ok(deleted=deleted) if deleted else _err("任务不存在")
        if cmd == "drop":
            n = service.drop_works(req["task_id"], req.get("ids", []))
            return _ok(dropped=n)
        if cmd == "export":
            out = req.get("dir") or str(ROOT_DIR / "data" / "exports" / req["task_id"])
            files = service.export_artifacts(req["task_id"], out)
            commit = None
            if files and app_settings.load().get("git_archive", True):
                from . import research_repo
                meta = next((t for t in service.list_tasks()
                             if t["task_id"] == req["task_id"]), None)
                commit = research_repo.commit_task(req["task_id"],
                                                   (meta or {}).get("topic", ""))
            return _ok(files=files, dir=out, commit=commit)
        if cmd == "plugin_commands":
            return _ok(commands=plugins.commands())
        if cmd == "plugin_call":
            return plugins.run_command_result(req["name"], req.get("args", ""), req.get("task_id"))
        if cmd == "plugins_summary":
            plugins.load(force=bool(req.get("reload")))
            return _ok(plugins=plugins.summary())
        if cmd == "plugin_toggle":
            return _ok(plugins=plugins.set_enabled(req["name"], req["on"]))
        if cmd == "skills_list":
            return _ok(skills=[{k: v for k, v in row.items() if k != 'body'}
                               for row in skills_mod.list_skills()])
        if cmd == "skills_selection":
            selection = skills_mod.select_for_stage(req.get("stage"))
            for group in ('selected', 'excluded'):
                selection[group] = [{k: v for k, v in row.items() if k != 'body'}
                                    for row in selection.get(group, [])]
            return _ok(selection=selection)
        if cmd in {"model_registry", "model_capabilities", "model_connection_save", "model_profile_save",
                   "model_connection_delete", "model_profile_delete", "model_route_set"}:
            from . import model_registry as registry, llm as llm_mod
            if cmd == "model_capabilities":
                return _ok(capabilities=llm_mod.capabilities())
            if cmd == "model_connection_save":
                result = registry.save_connection(req.get("connection", {}))
            elif cmd == "model_profile_save":
                result = registry.save_profile(req.get("profile", {}))
            elif cmd == "model_connection_delete":
                result = registry.delete_connection(req["id"])
            elif cmd == "model_profile_delete":
                result = registry.delete_profile(req["id"])
            elif cmd == "model_route_set":
                result = registry.set_route(req["stage"], req["profile_id"])
            else:
                result = registry.describe()
            return _ok(registry=result)
        if cmd == "research_audit":
            from .research_analysis import render_report
            tid = str(req.get("task_id") or "")
            if not service.get(tid):
                return _err("请先打开有效的研究任务")
            analysis = service.research_audit(tid)
            report = render_report(analysis)
            result = _ok(analysis=analysis, report=report)
            if req.get("export"):
                folder = config.DATA_DIR / "exports" / tid
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "科研证据审计.md"
                path.write_text(report, encoding="utf-8")
                result["path"] = str(path.resolve())
            return result
        if cmd == "research_package":
            from . import research_package
            return research_package.handle(req)
        if cmd == "agent_policy":
            from . import research_permissions
            tier = research_permissions.normalize_tier(
                (req.get("tier") if req.get("tier") else
                 app_settings.load().get("agent_permission_tier")))
            if req.get("tier"):
                if tier != req.get("tier"):
                    return _err("档位必须是 read-only / workspace-write / full-access")
                app_settings.save({"agent_permission_tier": tier})
            return _ok(tier=tier, matrix=research_permissions.matrix(),
                       text=research_permissions.render_text(tier))
        if cmd == "skill_toggle":
            return _ok(active=skills_mod.set_active(req["name"], bool(req.get("on"))))
        if cmd == "skill_mode":
            return _ok(skill=skills_mod.set_mode(req["name"], req["mode"]))
        if cmd == "context_status":
            return _ok(context=context_file.status())
        if cmd == "context_init":
            try:
                return _ok(path=str(context_file.init_template()))
            except Exception as e:  # noqa: BLE001
                return _err(str(e))
        if cmd == "mcp_list":
            enabled = app_settings.load().get("mcp_enabled", True)
            return _ok(servers=mcp.status_all() if enabled else [], enabled=enabled)
        if cmd == "git_cmd":
            sub = req.get("sub", "log")
            from . import research_repo
            text = (research_repo.status_text() if sub == "status"
                    else research_repo.log_text(int(req.get("limit", 10))))
            return _ok(text=text, available=research_repo.git_available())
        if cmd == "shell_exec":
            # 终端直达命令同样持有进程树，采集预算与显示长度分别封顶。
            from .shell_execution import run_command
            command = str(req.get("command", "")).strip()
            if not command:
                return _err("空命令")
            lowered = command.lower()
            banned = ("rm ", "rm -", "sudo", "shutdown", "reboot", "mkfs", "dd if", "format ", "del ", "rd /")
            if any(b in lowered for b in banned):
                return _err("命令包含安全禁区词，已拒绝执行")
            result = run_command(command, ROOT_DIR, timeout=10, capture_limit=8192)
            out = "".join(result["streams"].get(name, {}).get("text", "")
                          for name in ("stdout", "stderr"))
            truncated = not result["capture_complete"] or len(out) > 4000
            if truncated:
                notice = "\n[输出已截断或不完整，仅显示已捕获的前段。]"
                out = out[:4000-len(notice)] + notice
            response = {"text": out, "code": result["exit_code"], "truncated": truncated,
                        "timed_out": result["timed_out"], "cancelled": result["cancelled"]}
            if result["error"]:
                return {**_err(result["error"]), **response}
            if not result["tree_empty"]:
                return {**_err("未能确认命令的子进程已全部停止"), **response}
            if result["timed_out"]:
                return {**_err("命令超时（10s）"), **response}
            if result["cancelled"]:
                return {**_err("命令已取消"), **response}
            return _ok(**response)
        if cmd == "list":
            return _ok(tasks=service.list_tasks())
        if cmd == "demo_seed":
            # 供预览/演示脚本注入一条「离线演示任务」（走 fixtures，不碰网络）。
            # 脚本用完必须 demo_clean：否则真实档案里混进演示任务，
            # 时间账与 /tasks 都会虚高。首次调用会加载 agents 重依赖（与建任务同价）。
            from .agents import fixtures
            from .agents import literature as lit
            from .config import CHECKPOINT_DIR
            from .orchestrator.engine import TaskContext
            service.init()
            ctx = TaskContext("demoprev0001", "research_pipeline",
                              {"topic": req.get("topic", "大模型智能体"),
                               "keywords": "", "cite_style": "gbt7714",
                               "search_query": "large language model agents"})
            # 评分用英文检索式（fixtures 正文是英文），题名标签用中文展示主题
            pool = lit.score_pool(ctx.params["search_query"],
                                  fixtures.demo_works(ctx.params["topic"]))["pool"]
            ctx.artifacts["lit_filter"] = {
                "pool": pool,
                "rounds": [{"round": 1, "candidates": 6, "new_relevant": len(pool),
                            "ratio": 1.0}],
                "summary": f"离线演示池共 {len(pool)} 篇",
            }
            ctx.status = "waiting_human"
            ctx.pointer = "human_review"
            ctx.emit("workflow", message="文献猎手完成（离线演示池），等待人工确认",
                     status="waiting_human")
            service._tasks[ctx.task_id] = ctx
            ctx.save()
            return _ok(task_id=ctx.task_id, status=ctx.status)
        if cmd == "demo_clean":
            from .config import CHECKPOINT_DIR
            ctx = service._tasks.pop("demoprev0001", None)
            if ctx is not None:
                ctx._deleted = True          # 收尾线程不再写回存档
                (CHECKPOINT_DIR / "demoprev0001.json").unlink(missing_ok=True)
            return _ok(cleaned=ctx is not None)
        if cmd == "stats":
            r = stats_agent.analyze(Path(req["path"]))
            return _ok(stats=r)
        if cmd == "settings_get":
            return _ok(settings=app_settings.public_settings())
        if cmd == "settings_save":
            app_settings.save(req.get("settings", {}))
            plugins.load()
            if "mcp_enabled" in req.get("settings", {}):
                mcp.register_actions()
            return _ok(settings=app_settings.public_settings())
        if cmd == "llm_status":
            # LLM 工作台状态：端点/模型路由/可用模型清单（不含密钥）
            from . import llm as llm_mod   # 按需导入：就绪路径不为 httpx 买单
            actual = llm_mod.resolved_model_config()
            return _ok(configured=llm_mod.available("planning"),
                       base_url=actual.get("base_url", ""),
                       model=actual.get("model", ""), protocol=actual.get("protocol", ""),
                       reasoning_effort=actual.get("reasoning_effort", "auto"),
                       thinking_style=actual.get("thinking_style", "auto"),
                       connection_id=actual.get("connection_id", "legacy"),
                       model_fast=llm_mod.model_for("fast"),
                       model_strong=llm_mod.model_for("strong"),
                       models=llm_mod.list_models()[:20],
                       prompt_version=prompts.PROMPT_VERSION)
        if cmd == "board":
            ctx = service.get(req["task_id"])
            if not ctx:
                return _err("任务不存在")
            from . import dashboard
            b = dashboard.board(ctx)
            return _ok(lines=dashboard.render_board(b), board=b)
        if cmd == "graph":
            ctx = service.get(req["task_id"])
            if not ctx:
                return _err("任务不存在")
            from . import evidence
            g = evidence.build_graph(ctx)
            return _ok(lines=evidence.render_graph(g), graph=g)
        if cmd == "ledger":
            from . import dashboard
            tasks = service.list_tasks()
            pools: dict[str, int] = {}
            rounds: dict[str, int] = {}
            for t in tasks:
                ctx = service.get(t["task_id"])
                if ctx:
                    lit = ctx.artifacts.get("lit_filter", {}) or {}
                    pools[t["task_id"]] = len(lit.get("pool", []))
                    rounds[t["task_id"]] = len(lit.get("rounds", []))
            led = dashboard.ledger(tasks, pools, rounds)
            return _ok(ledger=led, lines=dashboard.render_ledger(led))
        if cmd == "nodes":
            return _ok(nodes=service.node_sequence(req.get("workflow_id", "research_pipeline")))
        if cmd == "doctor":
            from . import doctor   # 按需导入
            return _ok(doctor=doctor.run_doctor())
        if cmd == "usage":
            from . import logging_util
            return _ok(usage=logging_util.usage_summary(),
                       reasoning=logging_util.usage_summary().get("reasoning", {}),
                       text=logging_util.format_usage_text())
        if cmd == "agents":
            return _ok(agents=_runtime().list_agents())
        if cmd == "agent_plan":
            from .workspace_modes import default_agent, normalize_mode
            online = req.get("online", not app_settings.load().get("default_offline", True))
            purpose = normalize_mode(req.get('workspace_mode') or app_settings.load().get('workspace_mode', 'research'))
            return _ok(plan=_runtime(offline=not bool(online)).plan(
                req.get("request", ""), "plan", req.get("agent") or default_agent(purpose), workspace_mode=purpose))
        if cmd == "agent_run":
            from .workspace_modes import default_agent, normalize_mode
            if req.get('session_id'):
                from .session_workspace import _turn
                return _turn(req)
            online = req.get("online", not app_settings.load().get("default_offline", True))
            purpose = normalize_mode(req.get('workspace_mode') or app_settings.load().get('workspace_mode', 'research'))
            return _ok(result=_runtime(offline=not bool(online)).start(
                req.get("request", ""), req.get("mode", "agent"),
                req.get("agent") or default_agent(purpose), workspace_mode=purpose))
        if cmd == "agent_launch":
            from .agent_launch import launcher
            from .workspace_modes import default_agent, normalize_mode
            sid = req.get('session_id')
            if sid:
                runtime = _session_runtime(sid)
                snapshot = runtime.sessions.snapshot(sid)
                if 'online' in req or not snapshot.get('request'):
                    online = req.get('online', not app_settings.load().get('default_offline', True))
                    runtime = _runtime(offline=not bool(online))
                return _ok(result=launcher.launch(runtime, req.get('request', ''),
                    req.get('mode') or snapshot.get('mode') or 'agent',
                    req.get('agent') or snapshot.get('agent') or 'research',
                    launch_id=req.get('launch_id'), session_id=sid))
            online = req.get("online", not app_settings.load().get("default_offline", True))
            purpose = normalize_mode(req.get('workspace_mode') or app_settings.load().get('workspace_mode', 'research'))
            return _ok(result=launcher.launch(_runtime(offline=not bool(online)), req.get("request", ""),
                req.get("mode", "agent"), req.get("agent") or default_agent(purpose), launch_id=req.get("launch_id"), workspace_mode=purpose))
        if cmd == "agent_launch_status":
            from .agent_launch import launcher
            return _ok(**launcher.status(req.get("session_id", ""), fallback_store=_runtime().sessions))
        if cmd in {"agent_approve", "agent_deny"}:
            decision = "allow" if cmd == "agent_approve" else "deny"
            if cmd == "agent_approve" and req.get("scope") == "session":
                decision = "allow_session"
            return _ok(result=_session_runtime(req["session_id"]).approve(req["session_id"],
                                                 req["tool_call_id"], decision))
        if cmd in {"agent_continue", "agent_cancel"}:
            runtime = _session_runtime(req["session_id"])
            return _ok(result=(runtime.continue_session(req["session_id"]) if cmd == "agent_continue"
                               else runtime.cancel_session(req["session_id"])))
        if cmd == "sessions":
            return _ok(sessions=_runtime().sessions.list())
        if cmd == 'product_readiness':
            from .product_readiness import readiness
            return _ok(readiness=readiness())
        if cmd == 'product_demo':
            from .product_readiness import start_demo
            return start_demo()
        if cmd == 'session_plan':
            from .execution_plan import read_plan
            return _ok(plan=read_plan(_runtime().sessions, req.get('session_id')))
        if cmd in {'session_status', 'session_compact'}:
            from .session_controls import session_status, compact_session
            sid = req.get('session_id')
            store = _runtime().sessions
            if cmd == 'session_status':
                return _ok(status=session_status(sid, store=store))
            result = compact_session(sid, store=store)
            return _ok(result=result, status=session_status(sid, store=store))
        if cmd == "session_snapshot":
            sid = str(req.get("session_id", ""))
            if not sid or not all(c.isalnum() or c in "-_" for c in sid):
                return _err("无效的会话 ID")
            return _ok(snapshot=_runtime().sessions.snapshot(sid))
        if cmd == 'session_events':
            from .workbench import redact
            page = _runtime().sessions.event_page(req.get('session_id'), cursor=req.get('cursor'),
                                                 limit=req.get('limit', 200), tail=req.get('tail', False))
            return _ok(**{**page, 'events': redact(page['events'])})
        return _err(f"未知命令：{cmd}")
    except Exception as e:  # noqa: BLE001 —— 桥层兜底，错误透传给前端
        return _err(str(e))


def process_requests(input_stream, output_stream, dispatch=handle) -> None:
    """ID requests can complete independently; legacy requests stay FIFO."""
    from concurrent.futures import ThreadPoolExecutor
    output_lock = threading.Lock()
    slots = threading.BoundedSemaphore(64)
    fast = {"poll", "list", "nodes", "settings_get", "workbench_status", "loop_list",
            "tower_snapshot", "swarm_list", "swarm_get", "swarm_cancel",
            "remote_status", "shell_exec", "sessions", "session_snapshot", "session_status", "session_plan", "session_events", "agent_launch_status", "command_catalog", "workbench_mode", "agent_cancel",
            "model_registry", "model_capabilities", "skills_selection", "product_readiness", "evidence_brief", "steer_add", "steer_status", "world_fact_list", "world_fact_get", "world_fact_schema", "world_fact_support", "world_fact_challenge",
            'review_start', 'review_list', 'review_get', 'review_cancel', 'review_resume', 'review_accept', 'review_dismiss', 'review_events',
            "queue_add", "queue_list", "queue_remove", "queue_discard", "queue_pause", "queue_resume", "queue_reorder"}

    def reply(req):
        try:
            result = dispatch(req)
        except Exception as exc:
            result = _err(str(exc))
        if isinstance(req, dict) and "request_id" in req:
            result = {**result, "request_id": req["request_id"]}
        with output_lock:
            # Use the captured protocol stream even if a tool redirects sys.stdout.
            output_stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            output_stream.flush()

    def worker(req):
        try:
            reply(req)
        finally:
            slots.release()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="research") as work, \
            ThreadPoolExecutor(max_workers=2, thread_name_prefix="status") as status:
        for line in input_stream:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                req = None
            if not isinstance(req, dict) or "request_id" not in req:
                reply(req)
            elif slots.acquire(blocking=False):
                (status if req.get("cmd") in fast else work).submit(worker, req)
            else:
                with output_lock:
                    output_stream.write(json.dumps({**_err("请求队列已满，请稍后重试"),
                                                    "request_id": req["request_id"]}) + "\n")
                    output_stream.flush()


def main() -> None:
    # 裸命令行调试时 GBK 控制台会打碎中文 JSON；TUI 侧已设
    # PYTHONIOENCODING=utf-8，这里对无环境变量的调用兜底
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    service.init()
    # 就绪握手：前端在收到这一行前排队的请求不会丢；冷启动（依赖导入 +
    # 断点恢复）期间界面显示"引擎启动中"，不再出现命令发出后永久无响应
    print(json.dumps({"ok": True, "ready": True}, ensure_ascii=False), flush=True)
    from .research_planning import start_scheduler, stop_scheduler
    start_scheduler()
    try:
        process_requests(sys.stdin, sys.stdout)
    finally:
        from .remote_control import stop_server
        from .session_queue import close as close_queue
        from .evidence_reviews import close as close_reviews
        close_reviews()
        close_queue()
        stop_server()
        stop_scheduler()


if __name__ == "__main__":
    main()
