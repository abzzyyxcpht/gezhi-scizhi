"""Shared slash-command interface for the desktop palette and scripting."""
from __future__ import annotations

import json
import shlex
from typing import Any

from .feature_commands import CATALOG as FEATURE_CATALOG
from .workspace_modes import MODES, default_agent, normalize_mode

COMMANDS = [
    {"name": name, "args": args, "description": description}
    for name, args, description in [
        ("help", "", "查看命令"), ("mode", "[general|research]", "查看或切换通用／科研模式"),
        ("tasks", "", "查看研究任务"),
        ("processes", "", "任务进程与 Loop 状态"), ("open", "<任务ID>", "打开任务"),
        ("new", "<研究方向>", "开始研究"), ("status", "[任务ID]", "研究进度"),
        ("pause", "[任务ID]", "暂停研究"), ("resume", "[任务ID]", "断点继续"),
        ("confirm", "[任务ID]", "确认文献并继续"), ("cancel", "[任务ID]", "取消研究"),
        ("workflows", "", "科研工作流目录"),
        ("workflow", "<模板ID> <课题>", "从工作流启动研究"),
        ("loop", "start <模板ID> <间隔秒> <轮数> <课题> | list | stop/resume <ID>", "有界循环研究"),
        ("sessions", "", "智能体会话"), ("session", "<ID>", "查看会话计划和状态"),
        ("session-export", "<ID> [markdown|json]", "导出会话"),
        ("terminal", "", "打开本机终端"), ("remote", "start [端口] | status | stop", "手机局域网控制"),
        ("plan", "<请求>", "只读规划"), ("agent", "<请求>", "智能体执行"),
        ("agents", "", "专项智能体"), ("approve", "<会话ID> <调用ID>", "批准工具"),
        ("deny", "<会话ID> <调用ID>", "拒绝工具"), ("export", "[任务ID]", "导出研究成果"),
        ("settings", "", "查看设置（脱敏）"), ("toggle", "<开关> <on|off>", "修改功能开关"),
        ("model", "[模型名] | default/endpoint/key/fast/strong <值> | clear-key/clear-endpoint", "模型设置与检测"),
        ("plugins", "[reload|enable/disable <名称>]", "插件管理"),
        ("skills", "", "查看技能"), ("skill", "<名称> <on|off>", "启停技能"),
        ("mcp", "", "外部工具服务"), ("usage", "", "用量统计"),
        ("doctor", "", "系统自检"), ("board", "[任务ID]", "证据质量"),
        ("graph", "[任务ID]", "证据图谱"), ("ledger", "", "研究时间账"),
        ("pool", "[前N篇]", "当前文献池"), ("digest", "", "阅读摘要"),
        ("outline", "", "阅读综述框架"), ("cites", "", "阅读参考文献"),
        ("read", "<digest|outline|cites|board|graph|ledger>", "阅读研究内容"),
        ("stats", "<文件路径>", "实验数据分析"), ("rerun", "[任务ID]", "从头重跑"),
        ("spawn", "<智能体> <请求>", "专项智能体执行"), ("review", "<请求>", "只读证据审查"),
        ("context", "", "研究上下文"), ("init", "", "生成研究上下文模板"),
        ("git", "[log|status]", "研究档案历史"), ("clear", "", "清空命令输出"),
        ("connections", "", "模型服务连接"), ("connection", "save <JSON> | delete <ID>", "保存或删除模型连接"),
        ("models", "", "模型配置与阶段路由"), ("profile", "save <JSON> | delete <ID>", "保存或删除模型配置"),
        ("route", "[阶段 模型配置ID]", "查看或指定科研阶段使用的模型"),
        ("reasoning", "[auto|off|minimal|low|medium|high|xhigh|max]", "默认连接思考强度"),
        ("audit", "[export]", "科研证据审计与导出"), ("skills-selection", "[阶段]", "查看阶段技能与上下文预算"),
        ("continue", "<会话ID>", "继续等待中的模型会话"), ("cancel-agent", "<会话ID>", "请求停止模型会话"),
    ]
]


COMMANDS.extend({"name": name, "args": args, "description": description} for name, args, description in FEATURE_CATALOG)

COMMAND_GROUPS = [
    {'id': 'workspace', 'label': '工作台与会话'},
    {'id': 'agent', 'label': '智能体与执行'},
    {'id': 'project', 'label': '项目与终端'},
    {'id': 'research', 'label': '科研与证据'},
    {'id': 'model', 'label': '模型与连接'},
    {'id': 'extensions', 'label': '技能与扩展'},
    {'id': 'settings', 'label': '设置与诊断'},
]
_RESEARCH_COMMANDS = frozenset({'tasks', 'open', 'new', 'status', 'pause', 'resume', 'confirm', 'cancel',
    'workflows', 'workflow', 'loop', 'export', 'board', 'graph', 'ledger', 'pool', 'digest', 'outline',
    'cites', 'read', 'stats', 'rerun', 'git', 'audit', 'evidence', 'swarm', 'memory', 'notebook',
    'automations', 'todo', 'schedule', 'handoff'})
_COMMAND_CATEGORIES = {
    **{name: 'workspace' for name in ('help', 'mode', 'sessions', 'session', 'session-export', 'chat', 'clear')},
    **{name: 'agent' for name in ('plan', 'agent', 'agents', 'approve', 'deny', 'spawn', 'review',
                                 'continue', 'cancel-agent', 'subagents', 'processes', 'tower')},
    **{name: 'project' for name in ('project', 'terminal', 'context', 'init', 'pdf')},
    **{name: 'research' for name in _RESEARCH_COMMANDS},
    **{name: 'model' for name in ('model', 'models', 'connections', 'connection', 'profile', 'route', 'reasoning')},
    **{name: 'extensions' for name in ('plugins', 'skills', 'skill', 'skills-selection', 'mcp', 'mcp-config',
                                      'browser', 'computer', 'hooks', 'commands', 'command-save', 'command-delete', 'run')},
}
_BUSY_ALLOWED = frozenset({'mode', 'cancel-agent', 'steer', 'reviews', 'history', 'outputs', 'queue'})
_REMOTE_COMMANDS = frozenset({'help', 'clear', 'tasks', 'processes', 'open', 'new', 'status', 'pause',
    'resume', 'confirm', 'cancel', 'workflows', 'workflow', 'loop', 'automations', 'sessions', 'session',
    'session-export', 'approve', 'deny', 'usage', 'pool', 'digest', 'outline', 'cites', 'evidence',
    'continue', 'cancel-agent', 'connections', 'models', 'commands'})
_COMMAND_EFFECTS = {
    'mode': '新会话默认用途；已有会话与审批保持原身份',
    'plan': '生成只读计划', 'review': '启动只读审查会话', 'agent': '启动当前工作台的执行会话',
    'cancel-agent': '请求当前执行在安全边界停止', 'approve': '仅授权指定工具调用',
    'deny': '拒绝指定工具调用', 'steer': '补充当前回合', 'queue': '管理下一轮消息',
    'model': '用于后续请求；已保存模型会话保持原模型合同', 'help': '读取命令目录',
}


def _command_metadata(item: dict) -> dict:
    name = item['name']
    category = _COMMAND_CATEGORIES.get(name, 'extensions' if item.get('plugin') else 'settings')
    label = next(row['label'] for row in COMMAND_GROUPS if row['id'] == category)
    return {**item, 'category': category, 'category_label': label, 'group': category,
            'modes': ['research'] if name in _RESEARCH_COMMANDS else ['general', 'research'],
            'busy_allowed': name in _BUSY_ALLOWED,
            'shortcut': {'mode': 'Ctrl+G', 'help': '/help'}.get(name, ''),
            'effect': _COMMAND_EFFECTS.get(name, item.get('description') or '工作台操作')}


COMMANDS = [_command_metadata(item) for item in COMMANDS]


def _remote_available(name: str, args: str) -> bool:
    """Match the phone allowlist at the slash subcommand boundary.

    This is a presentation contract. The remote server validates its own
    request and field allowlists again before dispatch.
    """
    verb = args.strip().split(None, 1)[0] if args.strip() else ''
    if name in _REMOTE_COMMANDS:
        return True
    if name == 'model':
        return args.strip() == 'capabilities'
    if name == 'route':
        return not args.strip()
    if name in {'project', 'chat', 'notebook', 'todo', 'schedule', 'hooks'}:
        return verb in {'', 'list'} or name == 'project' and verb == 'files'
    if name == 'read':
        return verb in {'digest', 'outline', 'cites'}
    if name == 'audit':
        return not args.strip()
    return False


def catalog(query: str | None = "", workspace_mode: str | None = None, *, context: dict | None = None) -> list[dict[str, Any]]:
    """Current built-ins and enabled plugin commands, without executing handlers."""
    from . import plugins, settings
    mode = normalize_mode(workspace_mode or settings.load().get('workspace_mode', 'research'))
    context = context or {}
    rows = [dict(item, modes=list(item['modes'])) for item in COMMANDS]
    names = {item['name'] for item in rows} | {'ps'}
    for item in plugins.commands():
        if item['name'] in names:
            continue
        names.add(item['name'])
        rows.append(_command_metadata({'name': item['name'], 'args': '',
                     'description': item.get('help') or '插件命令',
                     'plugin': item.get('plugin', '')}))
    for row in rows:
        selected = context.get('command') or {}
        args = selected.get('args', '') if selected.get('name') == row['name'] else ''
        remote_blocked = context.get('remote') is True and (bool(row.get('plugin'))
            or not _remote_available(row['name'], args))
        busy_blocked = context.get('busy') is True and not row['busy_allowed']
        row['available'] = not (remote_blocked or busy_blocked)
        row['disabled_reason'] = ('远程未开放此操作，请在主机本地桌面或 TUI 操作' if remote_blocked else
                                  '上一操作仍在执行；可先取消、补充或排队' if busy_blocked else '')
        row['relevant'] = mode in row['modes']
    common = {name: index for index, name in enumerate(MODES[mode]['shortcuts'])}
    order = {group['id']: index for index, group in enumerate(COMMAND_GROUPS)}
    rows.sort(key=lambda row: (not row['relevant'], row['name'] not in common,
                              common.get(row['name'], 99), order[row['category']]))
    terms = (query or '').strip().lstrip('/').casefold().split()
    return [item for item in rows if all(term in ' '.join(
        item.get(key, '') for key in ('name', 'description', 'args', 'plugin', 'category_label')).casefold()
        for term in terms)]


def _args(text: str) -> list[str]:
    return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
            for part in shlex.split(text, posix=False)]


def display_result(result: dict[str, Any]) -> str:
    from .workbench import redact
    result = redact(result)
    if not result.get("ok", True):
        return str(result.get("error", "执行失败"))
    for key in ("text", "output", "report"):
        if isinstance(result.get(key), str):
            return result[key]
    if result.get("opened"):
        return f"已打开本机终端：{result.get('path', '')}"
    if result.get("path"):
        return f"已导出：{result['path']}"
    if result.get("task_id"):
        return f"研究任务 {result['task_id']} 已创建"
    visible = {k: v for k, v in result.items() if k not in {"ok", "request_id", "token"}}
    return json.dumps(visible, ensure_ascii=False, indent=2)[:20000]


def execute(text: str, task_id: str | None = None) -> dict[str, Any]:
    from .bridge import handle
    from . import service, settings
    try:
        raw_name, _, tail = text.strip().partition(" ")
        name = raw_name.lstrip('/').lower()
        if name not in {item['name'] for item in COMMANDS} | {'ps'}:
            from . import plugins
            if any(item['name'] == name for item in plugins.commands()):
                result = plugins.run_command_result(name, tail, task_id)
                return {**result, 'text': display_result(result)}
        from . import feature_commands
        if raw_name.lstrip("/").lower() in feature_commands.NAMES:
            result = feature_commands.execute(raw_name.lstrip("/").lower(), tail, task_id)
            return {**result, "text": display_result(result)}
        if raw_name.lstrip("/").lower() in {"connection", "profile"}:
            kind = raw_name.lstrip("/").lower()
            verb, _, payload = tail.strip().partition(" ")
            if verb == "save":
                row = json.loads(payload)
                result = handle({"cmd": "model_" + kind + "_save", kind: row})
            elif verb == "delete" and payload:
                result = handle({"cmd": "model_" + kind + "_delete", "id": payload.strip()})
            else:
                raise ValueError(f"用法：/{kind} save <JSON> 或 delete <ID>")
            return {**result, "text": display_result(result)}
        args = _args(text.strip())
        if not args:
            return {"ok": False, "error": "请输入命令"}
        name, rest = args[0].lstrip("/").lower(), args[1:]
        joined = " ".join(rest)
        if name == "clear":
            return {"ok": True, "text": ""}
        if name == "mode":
            if len(rest) > 1:
                raise ValueError('用法：/mode [general|research]')
            req = {'cmd': 'workbench_mode', **({'action': 'set', 'mode': normalize_mode(joined)} if rest else {})}
            result = handle(req)
            return {**result, 'text': display_result(result)}
        if name == "read":
            if joined not in {"digest", "outline", "cites", "board", "graph", "ledger"}:
                raise ValueError("可阅读：digest / outline / cites / board / graph / ledger")
            return execute("/" + joined, task_id)
        if name in {"pool", "digest", "outline", "cites"}:
            if not task_id or not service.get(task_id):
                raise ValueError("请先打开研究任务")
            art = service.artifacts_of(task_id)
            if name == "pool":
                limit = min(100, max(1, int(rest[0]) if rest else 20))
                text = "\n".join(f"{i}. {w.get('title', '')} · {w.get('year') or '—'}" for i, w in enumerate(art.get("pool", [])[:limit], 1))
            elif name == "cites":
                from .citation.gbt7714 import format_entry
                text = "\n".join(format_entry(w, "gbt7714") for w in art.get("pool", []))
            else:
                text = art.get("digest_md" if name == "digest" else "outline_md", "")
            return {"ok": True, "text": text or "当前任务尚无此项产物。"}
        if name == "help":
            commands = catalog()
            return {"ok": True, "commands": commands,
                    "text": "\n".join(f"/{c['name']} {c['args']}  — {c['description']}" for c in commands)}
        if name == "open":
            if not rest or not service.get(rest[0]):
                raise ValueError("任务不存在，先用 /tasks 查看")
            return {"ok": True, "task_id": rest[0], "navigate": 1, "text": f"已打开 {rest[0]}"}
        simple = {"tasks": "list", "ps": "workbench_status", "processes": "workbench_status",
                  "workflows": "workflows", "sessions": "sessions", "terminal": "terminal_open",
                  "settings": "settings_get", "agents": "agents", "mcp": "mcp_list",
                  "skills": "skills_list", "usage": "usage", "doctor": "doctor", "ledger": "ledger"}
        simple.update(context="context_status", init="context_init")
        simple.update(connections="model_registry", models="model_registry")
        if name in simple:
            req = {"cmd": simple[name]}
        elif name == "new":
            req = {"cmd": "workflow_run", "workflow_id": "research_pipeline", "topic": joined,
                   "offline": settings.load().get("default_offline", True)}
        elif name == "workflow":
            if len(rest) < 2:
                raise ValueError("用法：/workflow <模板ID> <课题>")
            req = {"cmd": "workflow_run", "workflow_id": rest[0], "topic": " ".join(rest[1:]),
                   "offline": settings.load().get("default_offline", True)}
        elif name in {"pause", "resume", "confirm", "cancel", "status", "export", "board", "graph", "rerun"}:
            tid = rest[0] if rest else task_id
            if not tid:
                raise ValueError("请先打开研究任务，或在命令后输入任务 ID")
            req = {"cmd": {"confirm": "resume", "status": "poll", "rerun": "restart"}.get(name, name), "task_id": tid}
        elif name == "stats":
            if not joined:
                raise ValueError("请提供数据文件路径")
            req = {"cmd": "stats", "path": joined}
        elif name == "git":
            req = {"cmd": "git_cmd", "sub": joined or "log"}
        elif name in {"plan", "agent", "spawn", "review"}:
            if not joined:
                raise ValueError("请补充研究请求")
            req = {"cmd": "agent_plan" if name == "plan" else "agent_run", "request": joined,
                   "online": not settings.load().get("default_offline", True), 'agent': default_agent()}
            if name == "spawn":
                if len(rest) < 2:
                    raise ValueError("用法：/spawn <智能体> <请求>")
                req.update(agent=rest[0], request=" ".join(rest[1:]))
            elif name == "review":
                req["mode"] = "review"
        elif name in {"approve", "deny"}:
            if len(rest) != 2:
                raise ValueError("用法：/approve 或 /deny <会话ID> <工具调用ID>")
            req = {"cmd": "agent_" + name, "session_id": rest[0], "tool_call_id": rest[1]}
        elif name in {"session", "session-export"}:
            if not rest:
                raise ValueError("请提供会话 ID")
            req = {"cmd": "session_snapshot" if name == "session" else "session_export", "session_id": rest[0]}
            if name == "session-export":
                req["format"] = rest[1] if len(rest) > 1 else "markdown"
        elif name == "loop":
            sub = rest[0] if rest else "list"
            if sub == "start" and len(rest) >= 5:
                req = {"cmd": "loop_start", "workflow_id": rest[1], "interval_seconds": float(rest[2]),
                       "max_iterations": int(rest[3]), "topic": " ".join(rest[4:]),
                       "offline": settings.load().get("default_offline", True)}
            elif sub in {"stop", "resume"} and len(rest) == 2:
                req = {"cmd": "loop_" + sub, "loop_id": rest[1]}
            elif sub == "list":
                req = {"cmd": "loop_list"}
            else:
                raise ValueError("用法：/loop start <模板ID> <间隔秒> <轮数> <课题>，或 /loop list/stop/resume")
        elif name == "remote":
            sub = rest[0] if rest else "status"
            if sub not in {"start", "status", "stop"}:
                raise ValueError("用法：/remote start [端口] | status | stop")
            req = {"cmd": "remote_" + sub}
            if sub == "start":
                req.update(host="0.0.0.0", port=int(rest[1]) if len(rest) > 1 else 8765)
        elif name == "toggle":
            if len(rest) != 2 or rest[0] not in settings.BOOLEAN_SETTINGS or rest[1] not in {"on", "off"}:
                raise ValueError("可用开关：" + ", ".join(sorted(settings.BOOLEAN_SETTINGS)))
            req = {"cmd": "settings_save", "settings": {rest[0]: rest[1] == "on"}}
        elif name == "route":
            if not rest:
                req = {"cmd": "model_registry"}
            elif len(rest) == 2:
                req = {"cmd": "model_route_set", "stage": rest[0], "profile_id": rest[1]}
            else:
                raise ValueError("用法：/route <default|fast|strong|planning|extraction|synthesis|review> <模型配置ID>")
        elif name == "reasoning":
            req = ({"cmd": "settings_save", "settings": {"llm_reasoning_effort": joined}} if rest else {"cmd": "model_registry"})
        elif name == "skills-selection":
            req = {"cmd": "skills_selection", "stage": joined or "planning"}
        elif name == "audit":
            if not task_id:
                raise ValueError("请先打开研究任务")
            req = {"cmd": "research_audit", "task_id": task_id, "export": joined == "export"}
        elif name in {"continue", "cancel-agent"}:
            if len(rest) != 1:
                raise ValueError(f"用法：/{name} <会话ID>")
            req = {"cmd": "agent_continue" if name == "continue" else "agent_cancel", "session_id": rest[0]}
        elif name == "model":
            if not rest:
                req = {"cmd": "llm_status"}
            elif joined == "capabilities":
                req = {"cmd": "model_capabilities"}
            else:
                fields = {"default": "llm_model", "endpoint": "llm_base_url", "key": "llm_api_key", "fast": "llm_model_fast", "strong": "llm_model_strong",
                          "protocol": "llm_protocol", "reasoning": "llm_reasoning_effort", "thinking": "llm_thinking_style", "budget": "llm_thinking_budget"}
                clear = {'clear-key': 'llm_api_key', 'clear-endpoint': 'llm_base_url'}
                if rest[0] in clear:
                    if len(rest) != 1:
                        raise ValueError('用法：/model clear-key 或 clear-endpoint')
                    key, value = clear[rest[0]], ''
                elif rest[0] in fields:
                    if len(rest) != 2:
                        raise ValueError('用法：/model <设置名> <值>；敏感输入不会回显')
                    key, value = fields[rest[0]], rest[1]
                else:
                    key, value = 'llm_model', joined
                if key == "llm_thinking_budget":
                    value = int(value)
                req = {"cmd": "settings_save", "settings": {key: value}}
        elif name == "plugins":
            if len(rest) == 2 and rest[0] in {"enable", "disable"}:
                req = {"cmd": "plugin_toggle", "name": rest[1], "on": rest[0] == "enable"}
            else:
                req = {"cmd": "plugins_summary", "reload": rest == ["reload"]}
        elif name == "skill" and len(rest) == 2 and rest[1] in {"on", "off"}:
            req = {"cmd": "skill_toggle", "name": rest[0], "on": rest[1] == "on"}
        else:
            raise ValueError(f"未知命令 /{name}，用 /help 查看命令")
        result = handle(req)
        if name == 'model' and req.get('settings', {}).get('llm_api_key'):
            from .workbench import redact
            secret = req['settings']['llm_api_key']
            result = redact(result)
            for field in ('error', 'text', 'output', 'report'):
                if isinstance(result.get(field), str):
                    result[field] = result[field].replace(secret, '[已隐藏]')
        return {**result, "text": display_result(result)}
    except (ValueError, IndexError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
