"""通用智能体工作流引擎：DAG 编排 + 节点级 Checkpoint + 暂停/续跑 + 人工干预（HITL）。

设计要点：
- 工作流用 YAML 声明，节点类型：tool / llm / condition / human_approval；
- 每个节点执行完毕即落盘 Checkpoint（状态=已完成产物+当前指针），
  支持随时暂停、人工修改中间产物（intervene）、从断点续跑；
- 执行过程持续产出事件流（SSE 推送给前端，可视化每一步）。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Callable

import yaml

from ..config import CHECKPOINT_DIR

# 节点动作注册表：action 名 -> 可调用（在 agents 模块中注册）
ACTIONS: dict[str, Callable[["TaskContext", dict[str, Any]], Any]] = {}


class PauseRequested(Exception):
    """节点内部请求暂停（如递归筛选的轮次间隙），携带已落盘的中间态。"""


def action(name: str):
    """装饰器：注册节点动作。"""
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


class TaskContext:
    """单个工作流实例的运行时上下文。"""

    def __init__(self, task_id: str, workflow: str, params: dict[str, Any]):
        self.task_id = task_id
        self.workflow = workflow
        self.params = params
        self.status = "pending"          # pending/running/paused/waiting_human/done/failed/cancelled
        self.pointer: str | None = None  # 下一个待执行节点
        self.artifacts: dict[str, Any] = {}   # 各节点产物（可 JSON 序列化）
        self.node_durations: dict[str, float] = {}  # 各节点耗时（秒），UI 展示"哪一步慢"
        self.events: list[dict[str, Any]] = []  # 事件流（SSE 源）
        self.created_at = time.strftime("%Y-%m-%d %H:%M")  # 任务创建/最后活动时间
        self.started_at: float | None = None   # 最近一次 run() 开始的 epoch 秒
        self.finished_at: float | None = None  # 跑完/挂起/失败的 epoch 秒
        self._subscribers: list[Any] = []
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()  # 并发扇出时防止多线程交错写存档
        self._pause_flag = threading.Event()   # set = 请求暂停
        self._cancel_flag = threading.Event()  # set = 请求取消（硬中断）

    # ---- 事件 ----
    def emit(self, kind: str, node: str = "", message: str = "", **extra: Any) -> None:
        evt = {"kind": kind, "node": node, "message": message,
               "ts": time.strftime("%H:%M:%S"), **extra}
        with self._lock:
            self.events.append(evt)
            subs = list(self._subscribers)
        for q in subs:
            q.put(evt)

    def subscribe(self) -> Any:
        import queue
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Any) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # ---- 控制 ----
    def request_pause(self) -> None:
        self._pause_flag.set()

    def request_cancel(self) -> None:
        """硬取消：在最近的节点/轮次边界终止任务，不再可续跑。"""
        self._cancel_flag.set()
        self._pause_flag.set()

    @property
    def pause_requested(self) -> bool:
        return self._pause_flag.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_flag.is_set()

    def clear_pause(self) -> None:
        self._pause_flag.clear()

    def reset(self) -> None:
        """清空运行态（保留参数），用于失败/取消后的整体重跑。"""
        self.status = "pending"
        self.pointer = None
        self.artifacts = {}
        self.node_durations = {}
        self.events = []
        self.started_at = None
        self.finished_at = None
        self._pause_flag.clear()
        self._cancel_flag.clear()

    # ---- Checkpoint ----
    def save(self) -> None:
        if getattr(self, "_deleted", False):
            return  # 任务已被删除：引擎线程收尾时不再写回存档
        with self._save_lock:  # 扇出分支并行收尾时串行化写盘，避免存档交错损坏
            self.created_at = time.strftime("%Y-%m-%d %H:%M")
            path = CHECKPOINT_DIR / f"{self.task_id}.json"
            path.write_text(json.dumps({
                "task_id": self.task_id, "workflow": self.workflow,
                "params": self.params, "status": self.status,
                "pointer": self.pointer, "artifacts": self.artifacts,
                "node_durations": self.node_durations,
                "created_at": self.created_at,
                "started_at": self.started_at, "finished_at": self.finished_at,
            }, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, task_id: str) -> "TaskContext":
        path = CHECKPOINT_DIR / f"{task_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        ctx = cls(data["task_id"], data["workflow"], data["params"])
        ctx.status = data["status"]
        ctx.pointer = data["pointer"]
        ctx.artifacts = data["artifacts"]
        ctx.node_durations = data.get("node_durations", {})
        # 旧存档没有 created_at 字段：用存档文件的修改时间兜底，
        # 否则任务列表/时间账里这些行会缺日期（分隔符悬空）。
        ctx.created_at = data.get("created_at") or time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        ctx.started_at = data.get("started_at")
        ctx.finished_at = data.get("finished_at")
        return ctx


@lru_cache(maxsize=8)
def _load_workflow_cached(name: str) -> dict[str, Any]:
    """工作流 YAML 只读缓存。

    运行期 YAML 不变；此前每次 list_tasks()（每任务一次）都要重读重解析，
    92 个任务实测 202ms/次——桌面首屏、桌面轮询（0.8s 一次）与 TUI /tasks 都在吃这个亏。
    返回的对象**只读**，调用方不得原地修改。
    """
    from ..config import ROOT_DIR
    path = ROOT_DIR / "workflows" / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_workflow(name: str) -> dict[str, Any]:
    """加载 workflows/ 下的 YAML 工作流定义（带缓存，勿原地修改返回值）。"""
    return _load_workflow_cached(name)


@lru_cache(maxsize=8)
def _node_ids_cached(name: str) -> tuple[str, ...]:
    return tuple(n["id"] for n in _load_workflow_cached(name)["nodes"])


def workflow_node_ids(name: str) -> list[str]:
    """返回工作流节点 id 序列（前端 DAG 步骤条用）；每次返回新列表，避免污染缓存。"""
    return list(_node_ids_cached(name))


def clear_workflow_cache() -> None:
    """清空工作流缓存（改了 YAML 或测试需要时调用）。"""
    _load_workflow_cached.cache_clear()
    _node_ids_cached.cache_clear()


# ---- 并行扇出/汇合 ----
# 分支并发上限：科研动作多为 IO/LLM 调用，4 路足够且避免打爆外部服务配额
_FANOUT_MAX_WORKERS = 4


def _fanout_branches(node: dict[str, Any]) -> list[str] | None:
    """若节点声明了并行扇出（next 为多元素字符串列表），返回分支起点序列；否则 None。

    单元素列表 next: [a] 视为普通串行，等同字符串 next，此处返回 None。
    """
    nxt = node.get("next")
    if isinstance(nxt, list) and len(nxt) > 1:
        return list(nxt)
    return None


def _flow_target(ctx: TaskContext, node: dict[str, Any], nid: str) -> str | None:
    """计算节点执行完毕后的流转目标：condition 按 branch 选边，其余取 next。

    next 为多元素列表（扇出）时返回 None——扇出由主循环整体接管。
    """
    if node["type"] == "condition":
        branch = ctx.artifacts.get(nid, {}).get("branch", False)
        return node.get("on_true") if branch else node.get("on_false")
    nxt = node.get("next")
    if isinstance(nxt, list):
        return nxt[0] if len(nxt) == 1 else None
    return nxt


def _resolve_path(ctx: TaskContext, dotted: str) -> Any:
    """按 dotted.path 取值：默认在 ctx.artifacts 上逐段下钻，params.xxx 前缀取任务参数。

    路径不存在（某段非 dict 或缺键）一律返回 None，由调用方按 falsy 处理，不报错。
    """
    if dotted.startswith("params."):
        cur: Any = ctx.params
        parts = dotted[len("params."):].split(".")
    else:
        cur = ctx.artifacts
        parts = dotted.split(".")
    for key in parts:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _eval_condition(ctx: TaskContext, node: dict[str, Any]) -> None:
    """condition 节点真实求值：读 if 路径的值，按 op 判定真假，结果落 artifacts。

    产物形如 {"branch": bool, "value": 取到的值}，保留 on_true/on_false 既有读取方式。
    支持 op：truthy（默认）/ non_empty（非空列表、非空串、非 None）/ eq（需配 value）。
    """
    nid = node["id"]
    path = node.get("if", "")
    value = _resolve_path(ctx, path) if path else None
    op = node.get("op", "truthy")
    if op == "truthy":
        branch = bool(value)
    elif op == "non_empty":
        branch = value is not None and value != "" and value != [] and value != {}
    elif op == "eq":
        branch = value == node.get("value")
    else:
        raise ValueError(f"condition 节点 {nid} 使用了未知 op：{op}")
    ctx.artifacts[nid] = {"branch": branch, "value": value}


def _run_node(ctx: TaskContext, nodes: dict[str, dict[str, Any]], nid: str) -> str | None:
    """执行单个节点并返回流转目标（主循环与并行分支共用的单节点执行逻辑）。

    - 幂等加速：artifacts 与 node_durations 均有记录时视为已完成，直接返回流转目标、
      不重复执行动作。并发段的恢复粒度 = 整个扇出节点，崩溃/暂停后续跑时已完成的分支
      节点靠此跳过；
    - condition 节点不走 ACTIONS，只做真实条件求值，产物同样落 artifacts 便于审计
      （兼容旧式 condition：声明 action 而未声明 if 时仍走动作产物中的 branch 标记）；
    - 扇出节点（next 为多元素列表）的动作也在此执行，流转目标返回 None；
    - 可能抛出 PauseRequested（节点内部暂停）或动作异常，由调用方统一收尾。
    """
    node = nodes[nid]
    if nid in ctx.artifacts and nid in ctx.node_durations:
        return _flow_target(ctx, node, nid)
    ctx.emit("node", node=nid, message=f"开始执行节点：{nid}", status="running")
    if node["type"] == "condition" and "if" in node:
        # 声明 if 的 condition 节点做真实条件求值（不走 ACTIONS，产物落 artifacts 便于审计）
        _t_node = time.perf_counter()
        _eval_condition(ctx, node)
        ctx.node_durations[nid] = round(time.perf_counter() - _t_node, 1)
        ctx.emit("node", node=nid, message=f"节点 {nid} 完成", status="done")
        return _flow_target(ctx, node, nid)
    fn = ACTIONS[node["action"]]
    _t_node = time.perf_counter()
    result = fn(ctx, node.get("params", {}))
    ctx.node_durations[nid] = round(time.perf_counter() - _t_node, 1)
    ctx.artifacts[nid] = result
    ctx.emit("node", node=nid, message=f"节点 {nid} 完成", status="done")
    return _flow_target(ctx, node, nid)


def _run_branch(ctx: TaskContext, nodes: dict[str, dict[str, Any]],
                start_id: str, join_id: str | None) -> None:
    """沿分支链线性执行节点，直到 next 为 None（分支完成）或走到汇合点 join_id。

    汇合点自身不在分支内执行（由主循环统一执行一次）；分支内撞见嵌套扇出
    （next 多元素列表）时该链即终止——不支持扇出嵌套，属工作流设计约束。
    尊重取消：request_cancel 后各分支在节点边界协作式退出。
    """
    nid: str | None = start_id
    while nid is not None and nid != join_id:
        if ctx.cancel_requested:
            return
        nid = _run_node(ctx, nodes, nid)


def _run_fanout(ctx: TaskContext, nodes: dict[str, dict[str, Any]],
                node: dict[str, Any], branches: list[str]) -> None:
    """执行扇出节点自身动作，然后并行执行各分支，全部完成后汇合。

    - 存档策略：扇出开始前 save 一次（pointer=扇出节点，状态 running），
      汇合后由主循环 save（pointer=then）；分支内单节点完成不各自 save，
      避免部分状态被误当作可恢复断点；
    - PauseRequested/分支异常自线程池传播给主循环统一收尾，中间态（各分支已写入
      ctx.artifacts 的产物）随主循环 save 一并落盘，续跑按幂等跳过已完成节点；
    - request_cancel 由各分支在节点边界协作式退出，最终状态由主循环取消检查收尾。
    """
    nid = node["id"]
    then: str | None = node.get("then")
    # 扇出节点自身动作（续跑时幂等跳过）
    _run_node(ctx, nodes, nid)
    ctx.emit("workflow", node=nid,
             message=f"节点 {nid} 扇出 {len(branches)} 条分支并行执行", status="running")
    ctx.pointer = nid
    ctx.status = "running"
    ctx.save()  # 并发段开始前落盘：崩溃/暂停的恢复粒度 = 整个扇出节点
    with ThreadPoolExecutor(max_workers=_FANOUT_MAX_WORKERS,
                            thread_name_prefix=f"fanout-{nid}") as pool:
        futures = {pool.submit(_run_branch, ctx, nodes, start, then): start
                   for start in branches}
        for fut, start in futures.items():
            fut.result()  # 分支异常/PauseRequested 传播给主循环
            ctx.emit("node", node=start, message=f"分支 {start} 完成",
                     status="done", branch=start)
    if then:
        ctx.emit("workflow", node=nid,
                 message=f"分支全部完成，汇合到 {then}", status="running")
    else:
        ctx.emit("workflow", node=nid,
                 message="分支全部完成，工作流结束", status="running")


def run(ctx: TaskContext) -> None:
    """执行工作流主循环。支持从 ctx.pointer 断点续跑。"""
    wf = load_workflow(ctx.workflow)
    nodes = {n["id"]: n for n in wf["nodes"]}
    ctx.pointer = ctx.pointer or wf["nodes"][0]["id"]
    ctx.status = "running"
    if not ctx.started_at or ctx.pointer == wf["nodes"][0]["id"]:
        ctx.started_at = time.time()
    ctx.finished_at = None
    ctx.emit("workflow", message=f"工作流 {ctx.workflow} 开始/续跑", status="running")
    ctx.save()

    while ctx.pointer:
        if ctx.cancel_requested:
            ctx.status = "cancelled"
            ctx.finished_at = time.time()
            ctx.emit("workflow", message="任务已取消，产物与断点保留备查", status="cancelled")
            ctx.save()
            return

        if ctx.pause_requested:
            ctx.status = "paused"
            ctx.finished_at = time.time()
            ctx.emit("workflow", message="已暂停，可修改参数后续跑", status="paused")
            ctx.save()
            return

        node = nodes[ctx.pointer]
        nid = node["id"]
        try:
            if node["type"] == "human_approval":
                # HITL 检查点：挂起等待人工确认（保留原开始事件，事件流兼容）
                ctx.emit("node", node=nid, message=f"开始执行节点：{nid}", status="running")
                ctx.status = "waiting_human"
                ctx.pointer = nid
                ctx.finished_at = time.time()
                ctx.emit("human", node=nid,
                         message=node.get("message", "等待人工确认"), status="waiting_human")
                ctx.save()
                return
            branches = _fanout_branches(node)
            if branches is not None:
                # 并行扇出：扇出节点动作 + 并行分支 + 汇合，完成后 pointer 跳到 then
                _run_fanout(ctx, nodes, node, branches)
                ctx.pointer = node.get("then")
            else:
                ctx.pointer = _run_node(ctx, nodes, nid)
        except PauseRequested:
            # 节点内部主动暂停：中间态已由节点自行落盘，指针保持在当前节点
            ctx.status = "paused"
            ctx.finished_at = time.time()
            ctx.emit("workflow", node=nid,
                     message=f"节点 {nid} 内部暂停，中间产物已保存", status="paused")
            ctx.save()
            return
        except Exception as e:  # noqa: BLE001
            ctx.status = "failed"
            ctx.finished_at = time.time()
            ctx.emit("error", node=nid, message=f"节点 {nid} 失败：{e}", status="failed")
            ctx.save()
            return

        ctx.save()

    ctx.status = "done"
    ctx.finished_at = time.time()
    ctx.emit("workflow", message="工作流执行完毕", status="done")
    ctx.save()


def intervene(ctx: TaskContext, node_id: str, patch: dict[str, Any]) -> None:
    """人工干预：修改指定节点的产物（版本化留痕）。

    before 必须是深拷贝快照：否则后续 update 会连同"修改前"记录一起被改，
    版本树不可回滚（unittest: test_intervene_versions_history 回归锚点）。
    """
    before = ctx.artifacts.get(node_id)
    history = ctx.artifacts.setdefault("_history", [])
    history.append({"node": node_id,
                    "before": json.loads(json.dumps(before)),
                    "patch_keys": list(patch.keys()),
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    target = ctx.artifacts.setdefault(node_id, {})
    target.update(patch)
    ctx.emit("human", node=node_id, message=f"人工修改了节点 {node_id} 的产物")
    ctx.save()
