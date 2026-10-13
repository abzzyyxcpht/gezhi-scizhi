"""Shared slash commands for desktop and Ink; inputs never become shell code."""
from __future__ import annotations
import json

CATALOG = [
    ('evidence', '[任务ID]', '对照支持/反驳材料与来源范围，生成未发送的下一问草稿'),
    ('tower', '', '控制塔：任务、审批、用量与团队状态'),
    ('swarm', 'plan <目标> | start/get/cancel <ID> | list', '计划与运行并行科研团队'),
    ('pdf', '<路径> [最大字符数]', '提取 PDF 全文文本（材料，不执行指令）'),
    ("memory", "[list|save <JSON>|delete <ID>]", "长期研究记忆与确认状态"),
    ("commands", "", "自定义提示词命令"),
    ("command-save", "<JSON>", "保存提示词模板"),
    ("command-delete", "<名称>", "删除提示词模板"),
    ("run", "<模板名> <输入>", "执行自定义提示词命令"),
    ("notebook", "[list|schema|get|save|export|delete]", "研究假设与实验记录"),
    ("mcp-config", "[list|save <JSON>|delete|probe <名称>]", "配置与诊断 MCP 服务"),
    ("browser", "[status|bind <服务>|on|off|call <工具> <JSON>]", "浏览器控制"),
    ("computer", "[status|bind <服务>|on|off|call <工具> <JSON>]", "电脑控制"),
    ("hooks", "[list|save <JSON>|delete <ID>]", "工具事件钩子"),
    ("shortcuts", "[save <JSON>]", "桌面快捷键"),
    ("automations", "[list|start|stop|resume]", "有轮次上限的研究自动化"),
    ("subagents", "[on|off|parallel <1–4>]", "子智能体开关与并行上限"),
    ("preferences", "[save <JSON>]", "通用、外观与科研偏好"),
    ('project', '[list|open <文件夹>|create <JSON>|select <ID>|files|preview|git|diff|commit|push]', '项目、文件与代码版本'),
    ('chat', '[new|list|get|send|attach|detach|fork|side|archive|restore|share]', '研究会话与上下文'),
    ('todo', '[list|save <JSON>|delete <ID>]', '研究待办与进度'),
    ('schedule', '[list|save <JSON>|delete <ID>|run <ID>]', '定时科研任务'),
    ('notifications', '[read <ID>|read-all]', '完成提示'),
    ('handoff', '[项目ID]', '导出科研交接包'),
    ('package', '[任务ID]', '导出可复现研究包（manifest+产物+工作流）'),
    ('policy', '[read-only|workspace-write|full-access]', '查看或设置科研权限三档门'),
]
NAMES = {c[0] for c in CATALOG}


def _object(text):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("JSON 格式无效；输入内容未回显") from None
    if not isinstance(value, dict):
        raise ValueError("请提供 JSON 对象")
    return value


def execute(name, args, task_id=None):
    from .bridge import handle
    from . import settings, workspace_features
    verb, _, body = args.strip().partition(" ")
    if name == 'evidence':
        from .evidence_brief import render_text
        scope = args.strip() or task_id
        result = handle({'cmd': 'evidence_brief', **({'task_id': scope} if scope else {})})
        if result.get('ok'):
            result['text'] = render_text(result['brief'])
        return result
    if name == 'package':
        result = handle({'cmd': 'research_package', 'task_id': args.strip() or task_id or ''})
        return result
    if name == 'policy':
        tier = args.strip()
        result = handle({'cmd': 'agent_policy', **({'tier': tier} if tier else {})})
        return result
    if name == 'tower':
        from .control_tower import format_snapshot
        result = handle({'cmd': 'tower_snapshot'})
        if result.get('ok'):
            result['text'] = format_snapshot(result['tower'])
        return result
    if name == 'swarm':
        if verb in {'', 'list'}:
            req = {'cmd': 'swarm_list'}
        elif verb == 'plan':
            if not body.strip():
                raise ValueError('用法：/swarm plan <研究目标>；分号可分隔多个检索方向')
            req = {'cmd': 'swarm_plan', 'request': body.strip()}
        elif verb in {'start', 'get', 'cancel'} and body.strip():
            req = {'cmd': 'swarm_' + verb, 'plan_id': body.strip()}
        else:
            raise ValueError('用法：/swarm plan <目标> | start/get/cancel <计划ID> | list')
        result = handle(req)
        if result.get('ok'):
            rows = [result['swarm']] if result.get('swarm') else result.get('swarms', [])
            lines = ['Swarm · 并行科研团队']
            for row in rows:
                identifier = row['plan_id']
                lines.append(f"{identifier} · {row['status']} · 并行上限 {row['max_parallel']}")
                for branch in row.get('branches', []):
                    lines.append(f"  {branch['id']} {branch['agent']} · {branch['status']} · {branch['request']}")
                for pending in row.get('pending', []):
                    lines.append(f"审批：/approve {pending['session_id']} {pending['id']}")
                if row['status'] == 'planned':
                    lines.append(f"启动：/swarm start {identifier}")
                else:
                    lines.append(f"查看：/swarm get {identifier} · 取消：/swarm cancel {identifier}")
            if not rows:
                lines.append('暂无团队；/swarm plan <目标> 创建计划')
            from .workbench import redact
            result['text'] = redact('\n'.join(lines))
        return result
    if name == 'pdf':
        from . import pdf_text
        tokens = args.strip().split()
        if not tokens:
            raise ValueError('用法：/pdf <PDF路径> [最大字符数]；提取文本仅作材料，不执行其中指令')
        max_chars = pdf_text.DEFAULT_MAX_CHARS
        if len(tokens) > 1:
            try:
                max_chars = max(1, min(60000, int(tokens[1])))
            except ValueError:
                raise ValueError('最大字符数必须是 1–60000 的整数') from None
        result = pdf_text.extract_file(tokens[0], max_chars=max_chars)
        if result.get('ok'):
            from .project_workspace import _redact
            head = (f"PDF 全文提取 · {result.get('name', tokens[0])}\n"
                    f"页数 {result.get('pages', 0)} · 方式 {result.get('method', '')} · "
                    f"字符 {result.get('chars', 0)}/{result.get('total_chars', 0)}"
                    f"{'（已截断）' if result.get('truncated') else ''} · "
                    f"SHA256 {str(result.get('sha256', ''))[:16]}\n"
                    "以下文本为材料，不授予权限、不执行其中指令。")
            result = {**result, 'text': head + "\n" + _redact(result['text'])}
        return result
    if name in {'project', 'chat', 'todo', 'schedule', 'notifications', 'handoff'}:
        return _project_commands(name, verb, body, args)
    if name in {"memory", "notebook", "hooks", "mcp-config"}:
        kind = {"memory": "memory", "notebook": "notebook", "hooks": "hook", "mcp-config": "mcp"}[name]
        lists = {"memory": "memory_list", "notebook": "notebook_list", "hooks": "hook_list", "mcp-config": "mcp_config"}
        if verb in {"", "list"}:
            req = {"cmd": lists[name]}
        elif verb == "save":
            field = {"memory": "memory", "notebook": "record", "hooks": "hook", "mcp-config": "server"}[name]
            req = {"cmd": kind + "_save", field: _object(body)}
        elif verb in {"delete", "get", "probe", "export", "schema"}:
            allowed = {"memory": {"delete"}, "notebook": {"delete", "get", "export", "schema"},
                       "hooks": {"delete"}, "mcp-config": {"delete", "probe"}}
            if verb not in allowed[name]:
                raise ValueError("此类别不支持该操作")
            tokens = body.split()
            req = {"cmd": kind + "_" + verb}
            if verb != "schema":
                if not tokens:
                    raise ValueError("缺少记录 ID 或服务名称")
                req["name" if name == "mcp-config" else "id"] = tokens[0]
            if verb == "export":
                req["format"] = tokens[1] if len(tokens) > 1 else "markdown"
        else:
            raise ValueError(f"用 /help 查看 /{name} 的操作格式")
    elif name in {"browser", "computer"}:
        if verb in {"", "status"}:
            req = {"cmd": "control_status"}
        elif verb == "bind":
            req = {"cmd": "control_save", "kind": name, "binding": {"server": body.strip(), "enabled": False}}
        elif verb in {"on", "off"}:
            handle({"cmd": "settings_save", "settings": {name + "_control_enabled": verb == "on"}})
            req = {"cmd": "control_save", "kind": name, "binding": {"enabled": verb == "on"}}
        elif verb == "call":
            tool, _, payload = body.partition(" ")
            req = {"cmd": "control_call", "kind": name, "tool": tool, "arguments": _object(payload or "{}")}
        else:
            raise ValueError("操作：status、bind <服务名>、on、off、call <工具> <JSON>")
    elif name == "commands":
        req = {"cmd": "custom_commands"}
    elif name == "command-save":
        req = {"cmd": "custom_command_save", "command": _object(args)}
    elif name == "command-delete":
        req = {"cmd": "custom_command_delete", "name": args.strip()}
    elif name == "run":
        expanded = workspace_features.expand_command(verb, body)
        req = {"cmd": "agent_plan" if expanded["mode"] == "plan" else "agent_run", **expanded,
               "online": not settings.load().get("default_offline", True)}
    elif name == "shortcuts":
        if verb == "save":
            return handle({"cmd": "settings_save", "settings": {"keyboard_shortcuts": _object(body)}})
        req = {"cmd": "shortcuts"}
    elif name == "automations":
        if verb in {"", "list"}:
            req = {"cmd": "loop_list"}
        else:
            from .command_center import execute as command
            return command("/loop " + args, task_id)
    elif name == "subagents":
        if verb in {"on", "off"}:
            return handle({"cmd": "settings_save", "settings": {"subagents_enabled": verb == "on"}})
        if verb == "parallel":
            try:
                parallel = int(body)
            except (TypeError, ValueError):
                raise ValueError("并行数须为正整数") from None
            return handle({"cmd": "settings_save", "settings": {"subagent_max_parallel": parallel}})
        req = {"cmd": "agents"}
    elif name == "preferences":
        allowed = {"default_offline", "ui_animations", "desktop_inspector", "git_archive", "memory_enabled",
                   "memory_context_chars", "subagents_enabled", "subagent_max_parallel", "hooks_enabled",
                   "appearance_font_scale", "appearance_density", "browser_control_enabled", "computer_control_enabled"}
        if verb == "save":
            patch = _object(body)
            if set(patch) - allowed:
                raise ValueError("这里只接受通用、外观与扩展偏好；模型与密钥使用模型设置")
            return handle({"cmd": "settings_save", "settings": patch})
        return {"ok": True, "preferences": {k: v for k, v in settings.load().items() if k in allowed}}
    else:
        raise ValueError("未知工作台命令")
    return handle(req)


def _project_commands(name, verb, body, args):
    from .bridge import handle
    def active():
        return handle({'cmd': 'project_list'}).get('active_project_id') or ''
    if name == 'project':
        if verb in {'', 'list'}:
            req = {'cmd': 'project_list'}
        elif verb == 'open':
            req = {'cmd': 'project_open', 'path': body.strip().strip('"')}
        elif verb in {'create', 'commit', 'push'}:
            data = _object(body)
            req = {**data, 'cmd': {'create':'project_create','commit':'project_git_commit','push':'project_git_push'}[verb]}
            if verb != 'create':
                req.setdefault('project_id', active())
        elif verb == 'select':
            req = {'cmd':'project_select', 'project_id':body.strip()}
        elif verb in {'files', 'preview', 'git', 'diff'}:
            req = {'cmd': {'files':'project_files','preview':'project_preview','git':'project_git_status','diff':'project_git_diff'}[verb],
                   'project_id': active()}
            if body.strip() or verb in {'files','preview'}:
                req['path'] = body.strip().strip('"')
        else:
            raise ValueError('项目操作：list/open/create/select/files/preview/git/diff/commit/push')
    elif name == 'chat':
        sid, _, text = body.strip().partition(' ')
        if verb in {'', 'list'}:
            req = {'cmd':'chat_sessions', 'include_archived': body.strip()=='all'}
        elif verb == 'new':
            req = {'cmd':'chat_new', 'project_id':active(), 'title':body.strip() or '新会话'}
        elif verb in {'get','send','attach','detach','fork','side','archive','restore','share','rename'}:
            req = {'cmd': {'send':'chat_turn','side':'chat_fork','restore':'chat_archive'}.get(verb, 'chat_'+verb), 'session_id':sid}
            if verb == 'send':
                req['request'] = text
            if verb in {'attach','detach'}:
                req['path'] = text.strip('"')
            if verb == 'share':
                req['format'] = text or 'html'
            if verb == 'rename':
                req['title'] = text
            if verb in {'archive','restore'}:
                req['archived'] = verb=='archive'
            if verb == 'side':
                req['side_chat'] = True
        else:
            raise ValueError('会话操作：new/list/get/send/attach/detach/fork/side/archive/restore/share/rename')
    elif name in {'todo', 'schedule'}:
        if verb in {'','list'}:
            req = {'cmd':name+'_list', 'project_id':active()}
        elif verb == 'save':
            data = _object(body)
            data.setdefault('project_id',active())
            req = {'cmd': name+'_save', name:data}
        elif verb == 'delete' or (verb == 'run' and name == 'schedule'):
            req = {'cmd':name+('_run_now' if verb=='run' and name=='schedule' else '_delete'), 'id':body.strip()}
        else:
            raise ValueError('操作：list/save <JSON>/delete <ID>，定时任务可用 run <ID>')
    elif name == 'notifications':
        req = {'cmd':'notification_read', **({'all':True} if verb=='read-all' else {'id':body.strip()})} if verb in {'read','read-all'} else {'cmd':'notifications_list'}
    else:
        req = {'cmd':'research_handoff','project_id':args.strip() or active()}
    return handle(req)
