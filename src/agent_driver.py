"""Bounded model/tool loop with durable conversations and explicit approvals.

Provider-native assistant blocks stay in the private driver state for replay.
The public event log carries content and call identities, not opaque signatures.

底座强化（四项）：
1. 上下文压缩——达到保守容量阈值的比例线时先折叠早期消息，而非超限即死；
2. 工具失败自愈——可重试错误转为修复提示回喂模型，修复轮数用尽才终止；
3. 单步并行——队首连续只读且免审批的调用用线程池并行执行，主线程按序记账；
4. 中断恢复对账——resume 时依据 JSONL 中的 tool_result 补账或合成 interrupted
   失败结果，交给自愈循环兜底，不再直接报错终止。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import uuid

from . import context_compaction, llm, model_protocols, model_registry, settings
from . import prompts  # noqa: F401  test_session_rewind 猴补 src.agent_driver.prompts.agent_system
from .state_store import write_json

# [DEPRECATED] 旧的字节级硬阈值因子（硬阈值 = context_window * 3 字节，把字节当 token）。
# 已被基于 _estimate_tokens 的 token 估算取代；保留仅为兼容历史 driver 状态可读性，
# 新代码不得再引用。
CONTEXT_HARD_FACTOR = 3
# 首次触发压缩的软阈值比例；已压过一次后用更高比例再压，避免摘要反复折叠。
COMPRESS_SOFT_RATIO = 0.7
COMPRESS_RECOMPRESS_RATIO = 0.9
# 单步最多并行执行的只读调用数。
PARALLEL_READ_BATCH = 4
# 单批工具调用的最大修复轮数。
MAX_REPAIR_ROUNDS = 2
# 压缩摘要的最大长度（字符），在线与确定性摘要共用该上限。
COMPRESS_SUMMARY_MAX_CHARS = 2000
# 智能体单步请求的模型输出上限（token）；沿用历史 llm.chat 调用的硬编码语义。
AGENT_STEP_MAX_TOKENS = 2048
# 流式输出开关默认值；仅当当前模型协议声明原生流式（Chat Completions）时生效。
STREAM_ENABLED_DEFAULT = True


class AdaptiveDriver:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.sessions

    def path(self, sid: str) -> Path:
        return self.store.root / f"{sid}.driver.json"

    def _save(self, state: dict) -> None:
        write_json(self.path(state["session_id"]), state)

    def _load(self, sid: str) -> dict:
        state = json.loads(self.path(sid).read_text(encoding="utf-8"))
        if state.get("schema_version") != 1 or state.get("session_id") != sid:
            raise ValueError("执行状态版本或会话身份不匹配")
        self.runtime._restore_session_meta(sid)
        if state.pop("model_inflight", None):
            # The provider may have billed the interrupted request; zero known
            # tokens do not mean a free request. Keep its consumed call slot.
            state["usage_complete"] = False
        if "original_request" not in state:
            request = next((e.get("payload", {}).get("text") for e in self.store.read(sid)
                            if e.get("type") == "user_message"), None)
            if request is not None:
                state["original_request"] = request
                original = {"role": "user", "content": request}
                if state["messages"][1:2] != [original]:
                    state["messages"].insert(1, original)
        # Narrow schema migration for saved sessions: archived observations must
        # remain readable after upgrading. Add only these session-scoped readers
        # when currently permitted; leave every other frozen tool unchanged.
        if isinstance(state.get('tool_schemas'), list):
            from .tool_output_runtime import OUTPUT_TOOLS
            present = {schema.get('function', {}).get('name') for schema in state['tool_schemas']}
            added, updated = [], []
            for name in OUTPUT_TOOLS:
                spec = self.runtime.tools.get(name)
                if (name not in present and spec is not None
                        and self.runtime._agent_gate(sid, name, spec.capability) != 'deny'):
                    state['tool_schemas'].append({'type': 'function', 'function': {
                        'name': name, 'description': spec.description, 'parameters': copy.deepcopy(spec.parameters)}})
                    added.append(name)
            file_read = self.runtime.tools.get('file.read')
            if file_read and self.runtime._agent_gate(sid, 'file.read', file_read.capability) != 'deny':
                for schema in state['tool_schemas']:
                    function = schema.get('function') or {}
                    if (function.get('name') == 'file.read'
                            and 'byte_offset' not in function.get('parameters', {}).get('properties', {})):
                        function.update(description=file_read.description, parameters=copy.deepcopy(file_read.parameters))
                        updated.append('file.read')
            if added or updated:
                self.store.append(sid, 'driver_tools_updated', {'added': added, 'updated': updated,
                                                              'reason': 'session_output_readers'})
                self._save(state)
        return state

    @staticmethod
    def _identity():
        cfg = model_registry.resolve("planning")
        identity = {k: cfg.get(k) for k in ("connection_id", "profile_id", "protocol", "model", "thinking_style",
                                           "reasoning_effort", "thinking_budget", "base_url", "auth_style")}
        identity["credential_fingerprint"] = hashlib.sha256(str(cfg.get("api_key") or "").encode()).hexdigest()
        return identity

    def _require_open_journal(self, sid: str) -> None:
        events = self.store.read(sid)
        start = next((i for i in range(len(events)-1, -1, -1) if events[i].get("type") == "user_message"), 0)
        if any(event.get("type") in {"completed", "error"} for event in events[start:]):
            raise ValueError("持久日志已记录会话结束，不会重放已完成的执行")

    def reply(self, sid: str, request: str, *, display_text: str | None = None, queue_ticket: str | None = None) -> dict:
        """Begin an explicit new turn; retain native messages and frozen prefix.

        Called under the runtime session lock. A terminal failed execution must
        be forked, so an unknown previous write can never be silently retried.
        """
        state = self._load(sid)
        if state['status'] != 'done' or state.get('queue') or state.get('unresolved'):
            raise ValueError('仅成功结束的模型回合可以追加消息；失败或中断请分叉核查')
        if state['model_identity'] != self._identity():
            raise ValueError('模型连接已改变，请分叉会话以使用新模型')
        state.setdefault('turn_usage', []).append({key: state.get(key, 0) for key in
            ('steps', 'tokens', 'model_calls', 'summary_calls', 'usage_complete')})
        options = settings.load()
        state.update(status='running', steps=0, tokens=0, model_calls=0, summary_calls=0,
            usage_complete=True, content='', repair_rounds=0, pending=None,
            max_steps=options.get('agent_max_steps', 16), token_budget=options.get('agent_token_budget', 100000))
        state['messages'].append({'role': 'user', 'content': request})
        user_payload = {'text': display_text or request,
            'mode': self.store.snapshot(sid).get('mode') or 'agent', 'agent': state['agent'],
            'workspace_mode': state.get('workspace_mode') or self.store.snapshot(sid).get('workspace_mode')
                              or ('general' if state['agent'] == 'general' else 'research'), 'offline': False}
        if queue_ticket:
            from .session_queue import record_queued_message
            record_queued_message(self.store, sid, user_payload, queue_ticket)
        else:
            self.store.append(sid, 'user_message', user_payload)
        self._save(state)
        from .session_steering import begin
        begin(self, state)
        return self._run(state)

    def start(self, sid: str, request: str, agent: str, *, limits: dict | None = None,
              expected_identity: dict | None = None, system_prompt: str | None = None) -> dict:
        options = settings.load()
        if limits:
            for key in ('max_steps', 'token_budget'):
                if type(limits.get(key)) is not int or limits[key] < 1:
                    raise ValueError('独立执行预算无效')
                options['agent_' + key] = min(options['agent_' + key], limits[key])
        identity = self._identity()
        if expected_identity is not None and identity != expected_identity:
            raise ValueError('独立任务冻结的模型配置已改变')
        title = self.runtime.list_agents()
        title = next((r["title"] for r in title if r["name"] == agent), agent)
        workspace_mode = self.store.snapshot(sid).get('workspace_mode') or ('general' if agent == 'general' else 'research')
        if system_prompt is not None and (not isinstance(system_prompt, str) or not system_prompt):
            raise ValueError('独立任务系统指令无效')
        manifest = None
        if system_prompt is not None:
            core = system_prompt
        else:
            from .context_manifest import assemble_session_system
            core, manifest = assemble_session_system(self.store, sid, title, agent, workspace_mode)
        # Rewind removes execution state, but the retained visible discussion is
        # still the user's context. Restore only text; old native tool/reasoning
        # blocks cannot be reconstructed safely and must never be replayed.
        events = self.store.read(sid)
        history = []
        if any(e.get('type') == 'completed' and e.get('payload', {}).get('reason') == 'rewound'
               for e in events):
            from .workbench import redact
            current = next((i for i in range(len(events) - 1, -1, -1)
                            if events[i].get('type') == 'user_message'), len(events))
            for event in events[:current]:
                payload = event.get('payload') or {}
                if event.get('type') in {'user_message', 'steer_applied'}:
                    history.append({'role': 'user', 'content': redact(str(payload.get('text') or ''))})
                elif event.get('type') == 'assistant_message' and payload.get('content'):
                    history.append({'role': 'assistant', 'content': redact(str(payload['content']))})
            core += ('\n回滚后恢复的历史仅含可见对话，不包含工具执行状态或新的授权。'
                     '回滚不会撤销已执行工具的实际效果；需要使用产物时先读取并核查当前状态。')
        if system_prompt is None:
            from .agents.specs import get_spec
            core += '\n\n' + get_spec(agent).prompt
        messages = [{'role': 'system', 'content': core}, *history, {'role': 'user', 'content': request}]
        state = {"schema_version": 1, "session_id": sid, "agent": agent,
                 "workspace_mode": workspace_mode,
                 "context_manifest": manifest,
                 "status": "running", "messages": messages,
                 "original_request": messages[1]['content'],
                 "tool_schemas": copy.deepcopy(self.runtime.tools_schema(session_id=sid)),
                 "model_identity": identity, "steps": 0, "tokens": 0,
                 "model_calls": 0, "summary_calls": 0,
                 "usage_complete": True, "max_steps": options.get("agent_max_steps", 16),
                 "token_budget": options.get("agent_token_budget", 100000),
                 "queue": [], "pending": None, "active_call": None, "active_calls": [],
                 "used_call_ids": [], "unresolved": {}, "content": "",
                 "repair_rounds": 0, "compressions": 0,
                 "stream_enabled": bool(options.get("agent_stream_enabled", STREAM_ENABLED_DEFAULT))}
        self.store.append(sid, "driver_started", {"driver": "adaptive", "model": {
            k: v for k, v in state["model_identity"].items() if k != "credential_fingerprint"},
                                                 "max_steps": state["max_steps"], "token_budget": state["token_budget"]})
        self._save(state)
        from .session_steering import begin
        begin(self, state)
        return self._run(state)

    def _result(self, state: dict, **extra):
        return {"session_id": state["session_id"], "status": state["status"], "driver": "adaptive",
                "agent": state["agent"], "workspace_mode": state.get('workspace_mode') or self.store.snapshot(state['session_id']).get('workspace_mode') or ("general" if state["agent"] == "general" else "research"),
                "steps": state["steps"], "tokens": state["tokens"],
                "usage_complete": state["usage_complete"], "content": state.get("content", ""),
                "context": {"bytes": self._context_bytes(state),
                            "tokens": self._estimate_tokens(state["messages"]),
                            "limit_bytes": state.get("context_limit_bytes"),
                            "limit_tokens": state.get("context_limit_tokens"),
                            "compressions": state.get("compressions", 0),
                            "measurement": "heuristic_tokens"},
                "budget": {"token_limit": state["token_budget"],
                           "remaining_tokens": max(0, state["token_budget"] - state["tokens"]),
                           "model_calls": state.get("model_calls", state["steps"]),
                           "summary_calls": state.get("summary_calls", 0),
                           "max_steps": state["max_steps"]},
                "workspace": self.runtime._workspace_snapshot(state["session_id"]), **extra}

    @staticmethod
    def _context_bytes(state: dict) -> int:
        """序列化 messages 的 UTF-8 字节数。

        仅作为结果上报/落盘的字节统计，不再参与压缩阈值判断；
        容量判断一律走 `_estimate_tokens`（token 口径）。
        """
        return len(json.dumps(state["messages"], ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def _estimate_tokens(value: "str | list[dict]") -> int:
        """启发式 token 估算（非 tokenizer）：ASCII 约 4 字符/token，CJK 约 1.5 字符/token。

        对 messages 列表先 JSON 序列化（ensure_ascii=False）再按字符分类估算，
        各部分向上取整后求和，整体偏保守（高估），仅用于容量阈值比较。
        """
        import math

        text = (json.dumps(value, ensure_ascii=False)
                if isinstance(value, list) else str(value))
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        cjk_chars = len(text) - ascii_chars
        return int(math.ceil(ascii_chars / 4) + math.ceil(cjk_chars / 1.5))

    @staticmethod
    def _account_usage(state: dict, result: dict) -> dict:
        state.pop("model_inflight", None)
        usage = result.get("usage") or {}
        counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
        valid = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts)
        state["usage_complete"] = bool(state["usage_complete"] and valid
                                       and usage.get("usage_reported", bool(usage)))
        state["tokens"] += sum(n for n in counts if isinstance(n, int) and not isinstance(n, bool) and n >= 0)
        return usage

    @staticmethod
    def _operation_key(call: dict) -> str:
        return json.dumps([call["name"], call.get("arguments") or {}], sort_keys=True, ensure_ascii=False)

    def _finish(self, state: dict, status: str, reason: str = "", message: str = ""):
        from .session_steering import seal
        if not seal(self, state, status):
            # A pending inbox cannot reopen a turn ended by another publisher.
            if self.runtime._terminal_status(state["session_id"]) is None:
                return self._run(state)
        # Cancellation can arrive while a long model/tool call owns the approval
        # lock. Serialize only terminal publication with the control lane so a
        # request after ``seal`` cannot be overwritten by a late success event.
        from .session_lock import session_terminal_lock
        sid = state["session_id"]
        with session_terminal_lock(self.store._path(sid)):
            committed = self.runtime._terminal_status(sid)
            if committed is not None:
                status = committed
                events = self.store.read(sid)
                start = next((i for i in range(len(events) - 1, -1, -1)
                              if events[i].get("type") == "user_message"), 0)
                first = next(event for event in events[start:] if event.get("type") in {"completed", "error"})
                payload = first.get("payload") or {}
                reason = str(payload.get("reason") or ("cancelled" if status == "cancelled" else ""))
                message = str(payload.get("message") or payload.get("error") or "")
            else:
                if (self.store.root / f"{sid}.cancel").exists() and status != "cancelled":
                    status, reason, message = "cancelled", "cancelled", "已在终态边界停止"
                self.store.append(sid, "error" if status == "error" else "completed",
                                  {"status": status, "reason": reason, "message": message})
            state.update(status=status, pending=None)
            self._save(state)
            return self._result(state, reason=reason, error=message if status == "error" else "")

    def _observe(self, state: dict, call: dict, result: dict):
        from .tool_output_runtime import prepare_result
        summary = prepare_result(self.runtime, state['session_id'], call, result)
        if call['name'] == 'skills.load':
            from .context_manifest import record_skill_read
            record_skill_read(state, summary)
        encoded = json.dumps(summary, ensure_ascii=False, default=str)
        state["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                  "name": call["name"], "content": encoded})
        if summary.get('control_state_error'):
            state['fatal_output_error'] = str(summary.get('error') or '工具返回的控制状态无效')[:1600]
        key = self._operation_key(call)
        if summary.get("ok") is False:
            if not (summary.get("non_blocking") is True and summary.get("executed") is False):
                state["unresolved"][key] = {"id": call["id"], "name": call["name"],
                    "arguments": copy.deepcopy(call.get("arguments") or {}),
                    "error": str(summary.get("error") or "工具执行失败")[:1600],
                    **{flag: summary[flag] for flag in ("executed", "retry_safe") if flag in summary}}
            # A skipped/malformed call has no effect, but cannot erase the
            # unresolved evidence of an earlier execution of the same operation.
        else:
            state["unresolved"].pop(key, None)
            if summary.get("ok") is not False and summary.get("executed") is True:
                # A corrected invocation has a new argument key. Retire one
                # preceding validation failure for this tool only after an
                # actual successful execution; prose never clears it.
                invalid_key = next((old_key for old_key, failure in reversed(list(state["unresolved"].items()))
                                    if isinstance(failure, dict) and failure.get("name") == call["name"]
                                    and failure.get("error") == "invalid_arguments"
                                    and failure.get("executed") is False), None)
                if invalid_key is not None:
                    state["unresolved"].pop(invalid_key)
        state["queue"] = [c for c in state["queue"] if c["id"] != call["id"]]
        if (state.get("active_call") or {}).get("id") == call["id"]:
            state["active_call"] = None
        state["active_calls"] = [cid for cid in state.get("active_calls", []) if cid != call["id"]]
        if result.get("status") in {"running", "pending", "waiting_human", "paused"} and result.get("task_id"):
            # The observation and its wait must be durable in the same snapshot.
            state.update(status="waiting_task", task_id=result["task_id"])
        if result.get("status") == "waiting_children":
            state.update(status="waiting_children", children_waiting=list(result.get("children_waiting") or []))
        self._save(state)

    def _retry_failure(self, state: dict, call: dict):
        """Reject replay when a prior failed operation may already have effects."""
        spec = self.runtime.tools[call["name"]]
        if spec.capability == "read" or spec.retry_safe is True:
            return None
        candidates = []
        for operation, failure in state["unresolved"].items():
            entry = failure if isinstance(failure, dict) else {"error": failure}
            name = entry.get("name")
            if not name:
                try:
                    identity = json.loads(operation)
                    name = identity[0] if isinstance(identity, list) and len(identity) == 2 else operation
                except (ValueError, TypeError):
                    name = operation  # Older states used plain tool-name keys.
            if name == call["name"]:
                candidates.append(entry)
        unsafe = next((failure for failure in candidates
                       if failure.get("executed") is not False and failure.get("retry_safe") is not True), None)
        if unsafe is None:
            return None
        interrupted = unsafe.get("error") == "interrupted"
        return self._finish(state, "error", "interrupted_tool" if interrupted else "unsafe_tool_retry",
                            "此前操作可能已产生副作用；请核验实际产物，不能自动重复执行")

    def _execute(self, state: dict, call: dict):
        from .agent_runtime import ToolCall
        rejected = self._retry_failure(state, call)
        if rejected:
            return rejected
        spec = self.runtime.tools[call["name"]]
        state["active_call"] = call
        state["active_calls"] = []
        self._save(state)
        result = self.runtime._execute_call(state["session_id"], ToolCall(call["id"], call["name"], call["arguments"], spec.capability), recoverable=True)
        self._observe(state, call, result)
        if state.get('fatal_output_error'):
            return self._finish(state, 'error', 'invalid_tool_result', state['fatal_output_error'])
        if (self.store.root / f"{state['session_id']}.cancel").exists():
            return self._finish(state, "cancelled", "cancelled", "已在工具边界停止")
        if result.get("status") == "waiting_children":
            self.store.append(state["session_id"], "driver_paused", {"status": "waiting_children"})
            return self._result(state, children_waiting=state["children_waiting"],
                                reason="处理子智能体审批后继续")
        if result.get("status") in {"running", "pending", "waiting_human", "paused"} and result.get("task_id"):
            state.update(status="waiting_task", task_id=result["task_id"])
            self.store.append(state["session_id"], "driver_paused", {"status": "waiting_task", "task_id": result["task_id"]})
            self._save(state)
            return self._result(state, task_id=result["task_id"], reason="等待科研任务；处理任务后继续会话")
        return None

    def _collect_read_batch(self, state: dict) -> list[dict]:
        """从队首连续收集可并行执行的只读调用（至多 PARALLEL_READ_BATCH 个）。

        遇到未知工具、非 read 能力或需要审批（ask）的调用即截断，交还原有
        串行路径处理，保证审批流一次一个、语义不变。
        """
        queue = state["queue"]
        first = queue[0]
        if self.runtime.tools[first["name"]].capability != "read":
            return [first]
        batch = [first]
        sid = state["session_id"]
        for other in queue[1:PARALLEL_READ_BATCH]:
            spec = self.runtime.tools.get(other["name"])
            if spec is None or other.get("error") or spec.capability != "read":
                break
            if self.runtime._agent_gate(sid, spec.name, spec.capability, other["arguments"]) != "allow":
                break
            batch.append(other)
        return batch

    def _execute_parallel(self, state: dict, batch: list[dict]):
        """线程池并行执行只读批次，收集结果后主线程按原顺序逐个记账。

        保证 messages 中 tool 消息顺序与 assistant tool_calls 声明顺序一致；
        批次结束后照常检查 cancel 标记与 waiting_task 挂起语义。
        """
        from .agent_runtime import ToolCall
        sid = state["session_id"]
        state["active_calls"] = [c["id"] for c in batch]
        self._save(state)

        def invoke(call: dict) -> tuple[dict, dict]:
            try:
                spec = self.runtime.tools[call["name"]]
                tool_call = ToolCall(call["id"], call["name"], call["arguments"], spec.capability)
                return call, self.runtime._execute_call(sid, tool_call, recoverable=True)
            except Exception as exc:  # noqa: BLE001 —— 单点失败不拖垮整批
                return call, {"ok": False, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=min(PARALLEL_READ_BATCH, len(batch))) as pool:
            observed = list(pool.map(invoke, batch))
        for call, result in observed:
            self._observe(state, call, result)
        if state.get('fatal_output_error'):
            return self._finish(state, 'error', 'invalid_tool_result', state['fatal_output_error'])
        if (self.store.root / f"{sid}.cancel").exists():
            return self._finish(state, "cancelled", "cancelled", "已在工具边界停止")
        for _, result in observed:
            if result.get("status") in {"running", "pending", "waiting_human", "paused"} and result.get("task_id"):
                state.update(status="waiting_task", task_id=result["task_id"])
                self.store.append(sid, "driver_paused", {"status": "waiting_task", "task_id": result["task_id"]})
                self._save(state)
                return self._result(state, task_id=result["task_id"], reason="等待科研任务；处理任务后继续会话")
        return None

    def _repair_unresolved(self, state: dict) -> bool:
        """把未解决工具错误写成修复提示追加进 messages；返回是否发起了修复轮。

        副作用状态随失败反馈保留，重放仍由执行门逐次检查。
        修复成功后由主循环在干净的批次边界把 repair_rounds 归零，
        即「新一轮批量重新获得修复配额」。
        """
        unresolved = state.get("unresolved") or {}
        failures = [entry if isinstance(entry, dict) else {"name": name, "error": entry}
                    for name, entry in unresolved.items()]
        if not failures or state.get("repair_rounds", 0) >= MAX_REPAIR_ROUNDS:
            return False
        detail = "；".join(f"{entry['name']} {json.dumps(entry.get('arguments', {}), ensure_ascii=False)}：{entry['error']}"
                          for entry in failures)
        state["messages"].append({"role": "user", "content":
            f"工具执行反馈（运行数据）：以下调用尚未成功：{detail}。"
            "只有对应操作的成功工具结果才能解除失败；文字说明不能作为成功证据。"
            "可先调用只读工具核验实际状态。只读工具、明确未执行的操作、明确声明可安全重试的工具可以重试；"
            "其他失败操作可能已有副作用，不得重复执行；无法修复时如实说明。"})
        state["repair_rounds"] = state.get("repair_rounds", 0) + 1
        self._save(state)
        return True

    @staticmethod
    def _tail_boundary(messages: list[dict]) -> int | None:
        """从尾向前找压缩切点：最近一个含 tool_calls 的 assistant 下标。

        以该 assistant 起刀的尾部必然配对合法——它是序列中最后一个携带
        tool_calls 的 assistant，其后的 tool 消息之父必在尾部之内。
        """
        for i in range(len(messages) - 1, 0, -1):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return i
        return None

    def _compress_context(self, state: dict, cfg: dict) -> bool:
        """把早期消息折叠成一条 user 运行摘要；返回是否发生了压缩。

        保留 system、全部用户消息与尾部（切点之后）消息，中间消息摘要留痕：
        state 记录 compressions 计数与最近摘要，JSONL 追加 context_compressed。
        """
        messages = state["messages"]
        boundary = self._tail_boundary(messages)
        if boundary is None or boundary <= 2:
            return False
        sid = state["session_id"]
        middle = messages[2:boundary]
        if not any(message.get("role") != "user" for message in middle):
            return False  # User turns and prior summaries alone are not new work to compact.
        goal = state.setdefault("original_request", str(messages[1].get("content") or ""))
        before_tokens = self._estimate_tokens(messages)
        fingerprint = hashlib.sha256(json.dumps({
            "messages": messages, "boundary": boundary, "goal": goal,
            "input_tokens": before_tokens, "context_window": cfg["context_window"],
            "summary_limit_chars": COMPRESS_SUMMARY_MAX_CHARS,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        previous = state.get("compression_skip")
        if isinstance(previous, dict) and previous.get("input_sha256") == fingerprint:
            if previous.get("reason") == "attempt_started":
                if state.pop("model_inflight", None):
                    state["usage_complete"] = False
                previous["reason"] = "attempt_interrupted"
                # _load clears an interrupted inflight flag in memory; make
                # that reconciliation durable even when compression is skipped.
                self._save(state)
            return False

        def defer(reason):
            state["compression_skip"] = {"input_sha256": fingerprint, "reason": reason,
                                         "input_tokens": before_tokens}
            self._save(state)
            return False

        if context_compaction.summary_room(messages[2:], COMPRESS_SUMMARY_MAX_CHARS) <= 0:
            return defer("summary_capacity")
        # Persisted with the model's inflight record before any paid request.
        # An interrupted attempt must not be billed again for unchanged input.
        # Usage counters are deliberately excluded from the fingerprint: the
        # attempt itself changes them without changing the compression input.
        state["compression_skip"] = {"input_sha256": fingerprint, "reason": "attempt_started",
                                     "input_tokens": before_tokens}
        model_summary = self._model_summary(state, middle, goal, cfg)
        summary = model_summary or self._deterministic_summary(sid, middle, goal)
        compacted = context_compaction.compact_messages(
            messages, boundary, summary, max_summary_chars=COMPRESS_SUMMARY_MAX_CHARS)
        if compacted is None:
            return defer("summary_capacity")
        candidate, archived, compaction_meta = compacted
        # A user-authored turn is never traded away for a smaller summary.  If
        # preserving those turns produces no reduction, leave the live state and
        # archive untouched; the next capacity check reports the hard limit.
        if self._estimate_tokens(candidate) >= before_tokens:
            return defer("no_token_reduction")
        from_messages = len(messages)
        # Keep the system and full original request byte-for-byte stable for caching.
        # Archived native blocks remain private; only the retained tail is replayed.
        state.setdefault("context_archive", []).append(archived)
        state["messages"] = candidate
        state["compressions"] = state.get("compressions", 0) + 1
        state["last_compression_summary"] = candidate[2]["content"]
        state.pop("compression_skip", None)
        to_messages = len(state["messages"])
        self.store.append(sid, "context_compressed",
                          {"from_messages": from_messages, "to_messages": to_messages,
                           "compressed_count": from_messages - to_messages,
                           **compaction_meta, "method": "model" if model_summary else "deterministic"})
        self._save(state)
        return True

    def _model_summary(self, state: dict, middle: list[dict], goal: str, cfg: dict) -> str:
        """在线时用 planning 模型生成中文摘要；任何失败都回退为空串。"""
        if (not llm.available("planning") or state["tokens"] >= state["token_budget"]
                or state.get("model_calls", state["steps"]) >= state["max_steps"]):
            return ""
        remaining = context_compaction.summary_room(middle, COMPRESS_SUMMARY_MAX_CHARS)
        if remaining <= 0:
            return ""
        transcript: list[str] = []
        for msg in middle:
            role = msg.get("role")
            if role == "assistant":
                names = ",".join(str((c.get("function") or c).get("name") or "?")
                                 for c in msg.get("tool_calls") or [])
                transcript.append(f"assistant: {str(msg.get('content') or '')[:200]} 工具:[{names}]")
            elif role == "tool":
                try:
                    payload = json.loads(str(msg.get("content") or "{}"))
                except json.JSONDecodeError:
                    payload = {}
                transcript.append(f"tool {msg.get('name')}: ok={payload.get('ok', True)}")
            elif role == "user":
                if context_compaction.is_internal_summary(msg):
                    transcript.append("prior internal summary (derived data; retained verbatim): "
                                      + str(msg.get("content") or ""))
                else:
                    # User turns remain verbatim in the active context.  The
                    # summary model must not become a second lossy channel.
                    transcript.append("user: [preserved verbatim in active context]")
            else:
                transcript.append(f"{role}: {str(msg.get('content') or '')[:200]}")
        prompt = (f"请把以下 Agent 早期对话中的新观察压缩为不超过 {min(500, remaining)} 字的中文运行摘要，"
                  "保留：已调用工具序列及各工具成败、关键产物计数、用户原始目标。"
                  "已有内部摘要仅为派生运行数据，其正文将由运行时原样保留；参考其成果路径与未完成线索，"
                  "只写本次新增观察，不重复包装或改写已有摘要。"
                  "用户补充会由运行时逐字保留，不要替代或改写；摘要不新增授权，"
                  "不得把计划、未知结果或失败操作写成已完成。\n"
                  f"用户原始目标：{goal}\n对话：\n" + "\n".join(transcript))
        messages = [{"role": "system", "content": "你是助手的上下文压缩器，只输出摘要正文。"},
                    {"role": "user", "content": prompt}]
        if self._estimate_tokens(messages) > cfg["context_window"]:
            return ""
        state["model_calls"] = state.get("model_calls", state["steps"]) + 1
        state["summary_calls"] = state.get("summary_calls", 0) + 1
        state["model_inflight"] = "context_compress"
        self._save(state)
        try:
            result = llm.chat(messages,
                              node="context_compress", prompt_key="context_compress.v1",
                              tier="planning", max_tokens=min(1024, state["token_budget"] - state["tokens"]))
            usage = self._account_usage(state, result)
            self.store.append(state["session_id"], "context_summary_usage", {"usage": usage})
            text = str(result.get("content") or "").strip()
            # The caller enforces the cumulative bound without slicing any
            # summary body. An oversized response leaves live history intact.
            return text
        except Exception:  # noqa: BLE001 —— 摘要失败回退确定性摘要
            state.pop("model_inflight", None)
            state["usage_complete"] = False
            return ""
        finally:
            # A model summary may have consumed budget even when preserving user
            # turns leaves no compression gain.  Recovery must see the actual
            # usage and a cleared inflight flag in that no-op case as well.
            self._save(state)

    def _deterministic_summary(self, sid: str, middle: list[dict], goal: str) -> str:
        """离线（或模型摘要失败时）的确定性折叠摘要。"""
        names: list[str] = []
        oks: list[bool] = []
        preserved_users = 0
        for msg in middle:
            if msg.get("role") == "assistant":
                for c in msg.get("tool_calls") or []:
                    names.append(str((c.get("function") or c).get("name") or "?"))
            elif msg.get("role") == "tool":
                try:
                    payload = json.loads(str(msg.get("content") or "{}"))
                except json.JSONDecodeError:
                    payload = {}
                oks.append(bool(payload.get("ok", True)))
            elif msg.get("role") == "user" and not context_compaction.is_internal_summary(msg):
                preserved_users += 1
        snap = self.runtime._workspace_snapshot(sid)
        counts = ", ".join(f"{k}={snap[k]}" for k in ("pool_count", "record_count", "section_count", "citation_count")
                           if snap.get(k)) or "无"
        prefix = (f"[上下文压缩] 早期 {len(middle)} 条消息已折叠。"
                  f"原始目标与 {preserved_users} 条用户补充逐字保留。产物：{counts}。工具序列：")
        observations = [f"{name}{'✓' if ok else '✗'}" for name, ok in zip(names, oks)]
        # Bound only the newly generated observation list, at whole entries.
        # The shared helper separately preserves all old summary bodies intact.
        omission = f"；另有 {len(observations)} 项工具记录未列出，完整历史保留。"
        room = max(0, COMPRESS_SUMMARY_MAX_CHARS - len(prefix) - len(omission))
        kept = []
        used = 0
        for entry in observations:
            cost = len(entry) + (3 if kept else 0)
            if used + cost > room:
                break
            kept.append(entry)
            used += cost
        sequence = " → ".join(kept) or "无"
        omitted = len(observations) - len(kept)
        suffix = f"；另有 {omitted} 项工具记录未列出，完整历史保留。" if omitted else "。"
        return prefix + sequence + suffix

    def _stream_ready(self, state: dict, cfg: dict) -> bool:
        """流式前置条件：会话开关开启且当前协议声明原生流式（Chat Completions）。"""
        if not bool(state.get("stream_enabled", STREAM_ENABLED_DEFAULT)):
            return False
        try:
            return model_protocols.protocol_of(cfg) == "chat_completions"
        except ValueError:
            return False

    def _stream_step(self, state: dict, messages: list[dict], tools: list[dict],
                     max_tokens: int) -> dict[str, Any] | None:
        """流式单步：内容增量写 assistant_delta 事件，最终以完整 result 返回。

        首个增量产出前的任何失败返回 None，交由调用方回退同步路径（此时没有
        内容外泄，重试安全）；首个增量之后的异常直接上抛，由主循环按
        model_error 终止，保证 assistant_message 要么完整写入、要么不写入。
        """
        sid = state["session_id"]
        step = state["steps"] + 1
        started = False
        try:
            for event in llm.chat_stream_agent(messages, node="agent_step", tier="planning",
                                               tools=tools, prompt_key="agent_step.v2",
                                               max_tokens=max_tokens):
                if "delta" in event:
                    delta = str(event["delta"])
                    if delta:
                        self.store.append(sid, "assistant_delta", {"step": step, "delta": delta})
                        started = True
                else:
                    return event["result"]
        except Exception:
            if started:
                raise
            return None
        return None

    def _request_model(self, state: dict, cfg: dict) -> dict[str, Any]:
        """单步模型请求：满足流式条件时增量渲染，任何不确定均回退同步调用。"""
        max_tokens = min(AGENT_STEP_MAX_TOKENS, state["token_budget"] - state["tokens"])
        messages = copy.deepcopy(state["messages"])
        tools = copy.deepcopy(state["tool_schemas"])
        if self._stream_ready(state, cfg):
            result = self._stream_step(state, messages, tools, max_tokens)
            if result is not None:
                return result
        return llm.chat(messages, node="agent_step", tier="planning", tools=tools,
                        prompt_key="agent_step.v2", max_tokens=max_tokens)

    def _run(self, state: dict):
        from .session_steering import consume
        sid = state["session_id"]
        while True:
            if state.get('fatal_output_error'):
                return self._finish(state, 'error', 'invalid_tool_result', state['fatal_output_error'])
            if (self.store.root / f"{sid}.cancel").exists():
                return self._finish(state, "cancelled", "cancelled", "已在安全边界停止")
            if state["model_identity"] != self._identity():
                return self._finish(state, "error", "model_changed", "会话执行中模型配置已改变，请新建会话")
            consume(self, state)
            while state["queue"]:
                if consume(self, state):
                    break
                call = state["queue"][0]
                spec = self.runtime.tools.get(call["name"])
                if spec is None:
                    # 兼容部分模型对工具名的系统性变体（实测 glm-5.3-flash 会把
                    # research.create 发成 research_create_<16位hex>）：剥掉尾缀并
                    # 还原点号后若在注册表中存在，则按归一化名字执行；事件日志中的
                    # assistant_message 仍保留模型原始输出，便于审计。
                    fixed = re.sub(r"_[0-9a-f]{16}$", "", call["name"]).replace("_", ".")
                    if fixed != call["name"] and fixed in self.runtime.tools:
                        call["name"] = fixed
                        spec = self.runtime.tools[fixed]
                if spec is None or call.get("error"):
                    # 未知工具名/参数解析失败不直接判死：合成失败结果走自愈回路，
                    # 把错误回喂模型自我修正（受修复轮数与步数预算约束）。
                    reason = (f"未知工具：{call['name']}。请只调用系统提示的工具清单中列出的工具，"
                              f"注意工具名中的点号（如 research.create）。" if spec is None else
                              f"工具 {call['name']} 的参数不是合法 JSON 对象（{call.get('error')}），"
                              f"请修正参数后重试。")
                    # 未执行任何操作，无副作用可言：non_blocking 使其不占用
                    # unresolved 修复配额——后续任何成功调用或模型答复即可推进。
                    self._observe(state, call, {"ok": False, "error": reason,
                                                "non_blocking": True, "executed": False})
                    continue
                rejected = self._retry_failure(state, call)
                if rejected:
                    return rejected
                decision = self.runtime._agent_gate(sid, spec.name, spec.capability, call["arguments"])
                if decision == "deny":
                    self.store.append(sid, "permission_denied", {"tool": spec.name, "tool_call_id": call["id"]})
                    return self._finish(state, "blocked", "permission_denied", "工具被权限策略禁止")
                if decision == "ask":
                    state.update(status="waiting_approval", pending=call)
                    self._save(state)
                    self.store.append(sid, "permission_requested", {"tool": spec.name, "tool_call_id": call["id"],
                        "capability": spec.capability, "arguments": call["arguments"], "driver": "adaptive"})
                    return self._result(state, pending={"id": call["id"], "tool": call["name"],
                                                        "capability": spec.capability, "arguments": call["arguments"]})
                batch = self._collect_read_batch(state)
                if len(batch) >= 2:
                    paused = self._execute_parallel(state, batch)
                else:
                    paused = self._execute(state, call)
                if paused:
                    return paused
                if (self.store.root / f"{sid}.cancel").exists():
                    return self._finish(state, "cancelled", "cancelled", "已在工具边界停止")
            if state["unresolved"]:
                # 自愈循环：可重试错误回喂模型修正，轮数用尽或不可重试才终止。
                if not self._repair_unresolved(state):
                    return self._finish(state, "error", "unresolved_tool_errors", "仍存在未修复的工具错误")
            else:
                # 上一批工具全部成功：新一轮批量重新获得修复配额。
                state["repair_rounds"] = 0
            if state.get("model_calls", state["steps"]) >= state["max_steps"]:
                return self._finish(state, "error", "step_budget", "已达到模型步骤上限")
            if state["tokens"] >= state["token_budget"]:
                return self._finish(state, "error", "token_budget", "已达到报送的 token 预算")
            if state["model_identity"] != self._identity():
                return self._finish(state, "error", "model_changed", "会话执行中模型配置已改变，请新建会话")
            cfg = model_registry.resolve("planning")
            # 容量检查走启发式 token 估算（非 tokenizer）：硬阈值 = context_window，
            # 软阈值 0.7 / 再压缩 0.9 保留；字节数仅作结果上报。
            context_tokens = self._estimate_tokens(state["messages"])
            hard_limit = int(cfg["context_window"])
            state["context_limit_tokens"] = hard_limit
            state["context_limit_bytes"] = self._context_bytes(state)
            trigger = hard_limit * (COMPRESS_RECOMPRESS_RATIO if state.get("compressions")
                                    else COMPRESS_SOFT_RATIO)
            if context_tokens > trigger:
                self._compress_context(state, cfg)
                context_tokens = self._estimate_tokens(state["messages"])
                if context_tokens > hard_limit:
                    return self._finish(state, "error", "context_budget",
                                        "上下文超过保守容量检查；请缩小研究范围或新建会话")
            # Summarization is a model request and can consume the final budget.
            if state["tokens"] >= state["token_budget"]:
                return self._finish(state, "error", "token_budget", "上下文摘要已达到报送的 token 预算")
            if state.get("model_calls", state["steps"]) >= state["max_steps"]:
                return self._finish(state, "error", "step_budget", "上下文摘要已达到模型步骤上限")
            if (self.store.root / f"{sid}.cancel").exists():
                return self._finish(state, "cancelled", "cancelled", "已在摘要边界停止")
            state["model_calls"] = state.get("model_calls", state["steps"]) + 1
            state["model_inflight"] = "agent_step"
            if "tool_schemas" not in state:
                state["tool_schemas"] = copy.deepcopy(self.runtime.tools_schema(session_id=sid))
            self._save(state)
            try:
                result = self._request_model(state, cfg)
            except Exception as exc:
                state.pop("model_inflight", None)
                state["usage_complete"] = False
                return self._finish(state, "error", "model_error", str(exc))
            state["steps"] += 1
            usage = self._account_usage(state, result)
            state["content"] = str(result.get("content") or "")
            calls = result.get("tool_calls") or []
            if not isinstance(calls, list) or any(not isinstance(c, dict) or not isinstance(c.get("name"), str)
                                                or not isinstance(c.get("arguments"), dict) for c in calls):
                return self._finish(state, "error", "invalid_tool_call", "模型工具调用结构无效")
            self.store.append(sid, "assistant_message", {"content": state["content"], "tool_calls": calls,
                                                         "step": state["steps"], "usage": usage})
            state["messages"].append(copy.deepcopy(result.get("assistant_message") or {"role": "assistant", "content": state["content"]}))
            if (self.store.root / f"{sid}.cancel").exists():
                return self._finish(state, "cancelled", "cancelled", "模型请求已结束，后续工具未执行")
            if state["tokens"] > state["token_budget"] or (calls and state["tokens"] == state["token_budget"]):
                return self._finish(state, "error", "token_budget", "本次报送用量达到预算；后续工具未执行")
            if not calls:
                if state["unresolved"]:
                    return self._finish(state, "error", "unresolved_tool_errors", "仍存在未修复的工具错误")
                if not state["content"]:
                    return self._finish(state, "error", "empty_model_result", "模型未返回内容或工具调用")
                return self._finish(state, "done")
            if len(calls) > 8:
                return self._finish(state, "error", "tool_budget", "单步工具请求超过上限 8")
            for call in calls:
                call["id"] = str(call.get("id") or uuid.uuid4().hex[:12])
                if call["id"] in state["used_call_ids"]:
                    return self._finish(state, "error", "duplicate_tool_call", "模型重复使用了工具调用 ID")
                state["used_call_ids"].append(call["id"])
            state["queue"] = calls
            state["status"] = "running"
            self._save(state)

    def approve(self, sid: str, call_id: str, decision: str):
        self._require_open_journal(sid)
        state = self._load(sid)
        if (self.store.root / f"{sid}.cancel").exists():
            return self._finish(state, "cancelled", "cancelled", "已取消，待审批工具未执行")
        pending = state.get("pending")
        if state["status"] != "waiting_approval" or not pending or pending["id"] != call_id:
            raise ValueError("待审批调用不存在或已经处理")
        if state["model_identity"] != self._identity():
            return self._finish(state, "error", "model_changed", "连接或模型配置已改变；待审批工具未执行，请新建会话")
        from .session_steering import has_pending
        if decision != 'deny' and has_pending(self, state):
            raise ValueError('当前回合有新的补充要求；请继续以重新规划，旧工具审批不会执行')
        tool = self.runtime.tools.get(pending["name"])
        if tool is None or self.runtime._agent_gate(sid, tool.name, tool.capability, pending["arguments"]) == "deny":
            self.store.append(sid, "permission_denied", {"tool_call_id": call_id, "tool": pending["name"]})
            return self._finish(state, "blocked", "permission_denied", "当前策略已禁止该工具，旧审批未执行")
        events = self.store.read(sid)
        if any(e["type"] in {"permission_granted", "permission_denied"} and
               e.get("payload", {}).get("tool_call_id") == call_id for e in events):
            raise ValueError("该调用已经处理；不可重复审批")
        if decision == "deny":
            self.store.append(sid, "permission_denied", {"tool_call_id": call_id, "tool": pending["name"]})
            return self._finish(state, "blocked", "permission_denied", "用户拒绝执行")
        if decision == "allow_session":
            self.runtime.policy.allow_for_session(self.runtime._permission_key(pending["name"], pending["arguments"]), sid)
        self.store.append(sid, "permission_granted", {"tool_call_id": call_id, "tool": pending["name"],
            "permission_key": self.runtime._permission_key(pending["name"], pending["arguments"]), "scope": decision})
        state.update(status="running", pending=None)
        paused = self._execute(state, pending)
        return paused or self._run(state)

    def _reconcile_interrupted(self, state: dict) -> None:
        """崩溃对账：有 tool_result 则补记账，否则合成 interrupted 失败走自愈。

        已记账（messages 中已有对应 tool 消息）的调用跳过，避免重复观察；
        崩溃时未开始的调用保守合成失败结果，不重放副作用。
        """
        sid = state["session_id"]
        ids = ([state["active_call"]["id"]] if state.get("active_call") else [])
        ids += [str(c) for c in (state.get("active_calls") or [])]
        observed = {m.get("tool_call_id") for m in state["messages"] if m.get("role") == "tool"}
        events = self.store.read(sid)
        started = {e.get("payload", {}).get("id") for e in events if e.get("type") == "tool_call"}
        ids += [c["id"] for c in state["queue"] if c["id"] in started]
        ids = list(dict.fromkeys(ids))
        meta: dict[str, dict] = {}
        logged: dict[str, dict] = {}
        # A crash may precede the tool_call journal write. Saved declarations and
        # the queue still identify the operation without changing native blocks.
        for message in state["messages"]:
            for declared in message.get("tool_calls") or []:
                function = declared.get("function") or declared
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                meta[declared.get("id")] = {"name": function.get("name"),
                                            "arguments": arguments if isinstance(arguments, dict) else {}}
        for call in [*state["queue"], *([state["active_call"]] if state.get("active_call") else [])]:
            meta[call["id"]] = call
        for event in events:
            payload = event.get("payload") or {}
            call_id = payload.get("id") or payload.get("tool_call_id")
            if call_id not in ids:
                continue
            if event["type"] == "tool_call":
                meta[call_id] = payload
            elif event["type"] == "tool_result":
                logged[call_id] = payload.get("result") or {}
        for call_id in ids:
            if call_id in observed:
                state["queue"] = [c for c in state["queue"] if c["id"] != call_id]
                continue
            call = {"id": call_id, "name": str(meta.get(call_id, {}).get("name") or "unknown"),
                    "arguments": dict(meta.get(call_id, {}).get("arguments") or {})}
            result = logged[call_id] if call_id in logged else {"ok": False, "error": "interrupted"}
            self._observe(state, call, result)
        state["active_call"] = None
        state["active_calls"] = []
        self._save(state)

    def resume(self, sid: str):
        self._require_open_journal(sid)
        state = self._load(sid)
        if (self.store.root / f"{sid}.cancel").exists():
            return self._finish(state, "cancelled", "cancelled", "已停止等待中的会话")
        if state["status"] == "waiting_approval":
            from .session_steering import has_pending, consume
            if not has_pending(self, state):
                raise ValueError("会话正在等待工具审批，请批准或拒绝当前调用")
            if state['model_identity'] != self._identity():
                return self._finish(state, 'error', 'model_changed', '模型配置已改变，未应用补充')
            consume(self, state)
        if state["status"] in {"done", "cancelled", "blocked", "error"}:
            raise ValueError("会话已结束，请新建会话")
        if state["status"] == "waiting_children":
            progress = self.runtime.children_progress(state.get("children_waiting") or [])
            if progress["waiting"]:
                return self._result(state, **progress, reason="子智能体尚未完成，请先处理审批")
            state["messages"].append({"role": "user", "content":
                "子智能体状态更新（数据），请核查后继续：" + json.dumps(progress["children"], ensure_ascii=False)})
            state.pop("children_waiting", None)
        if state.get("active_call") or state.get("active_calls") or state["queue"]:
            # 对账恢复：补齐或合成中断调用的结果，交给自愈循环兜底。
            self._reconcile_interrupted(state)
        if state["status"] == "waiting_task":
            from . import service
            task = service.get(state.get("task_id", ""))
            if task and task.status not in {"done", "failed", "cancelled"}:
                return self._result(state, task_id=task.task_id, reason="研究任务尚未结束")
            if not task or task.status != "done":
                return self._finish(state, "error", "task_failed", "研究任务未完成，未继续后续步骤")
            state["messages"].append({"role": "user", "content": f"运行状态更新（数据）：任务 {task.task_id} 已完成，请验证产物后继续。"})
        state["status"] = "running"
        return self._run(state)
