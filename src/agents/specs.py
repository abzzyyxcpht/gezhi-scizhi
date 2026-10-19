"""专项智能体编制表：定义角色、工具范围与执行入口。

每个智能体是可独立验收的执行单元：有名字、职责、工具白名单、权限默认值
和角色提示词。主智能体 `research` 走原有 DAG+HITL 流水线；`pipeline`
只通过子智能体组合完成科研链路，不经过人工审批节点。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    """一份可注册的专项智能体说明。"""

    name: str
    title: str
    description: str
    mode: str  # primary | subagent
    tools: tuple[str, ...]
    permissions: dict[str, str]
    capability: str
    feature: str
    prompt: str

    def as_dict(self) -> dict[str, Any]:
        """序列化为 Bridge / CLI / 桌面编制表行。"""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "mode": self.mode,
            "tools": list(self.tools),
            "permissions": dict(self.permissions),
            "capability": self.capability,
            "feature": self.feature,
            "prompt": self.prompt,
        }


_PLAN_GUIDANCE = (
    "复杂多步任务可先用 plan.read 读取本回合标识和版本，再用 plan.update 建立并维护执行计划。"
    "执行步骤完成时关联本回合真实成功工具回执；解释步骤须说明依据，不把计划文字当作执行证明。"
    "结束前核对计划状态；受阻须如实保留原因。简单问答无需建立计划。"
)


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="general",
        title="通用智能体",
        description="操作项目文件、编写代码、执行终端任务与调用授权扩展",
        mode="primary",
        tools=("file.read", "file.write", "file.edit", "file.str_replace", "file.glob", "file.grep",
               "shell.run", "workspace.read", "doctor.run", "pdf.extract", "skills.load",
               "extensions.list", "plugin.call", "mcp.call", "mcp.describe", "control.status", "control.call",
               "plan.read", "plan.update"),
        permissions={"plan.update": "allow"},
        capability="write",
        feature="通用",
        prompt="你是格致 SciZhi 的通用智能体。直接完成用户的代码、文件和日常任务；不要把普通请求转为文献检索流水线。" + _PLAN_GUIDANCE,
    ),
    AgentSpec(
        name="research",
        title="科研总调度",
        description="创建任务、执行 DAG 流水线并导出产物；含 HITL 人工确认",
        mode="primary",
        tools=("*",),
        permissions={"plan.update": "allow"},
        capability="write",
        feature="F1–F5",
        prompt="你是格致 SciZhi 的主科研智能体，负责把自然语言研究请求规划为工具序列。" + _PLAN_GUIDANCE,
    ),
    AgentSpec(
        name="pipeline",
        title="流水线调度",
        description="进程内串行派发文献→抽取→综述→引用→审计，不经过 HITL 节点",
        mode="primary",
        tools=("literature.run", "extraction.run", "synthesis.run",
               "citation.run", "critic.run", "subagent.spawn",
               "subagent.fanout", "workspace.read", "plan.read", "plan.update"),
        permissions={
            "plan.update": "allow",
            "extraction.run": "allow",
            "synthesis.run": "allow",
            "citation.run": "allow",
            "critic.run": "allow",
            "workspace.read": "allow",
        },
        capability="write",
        feature="调度",
        prompt="你是科研流水线调度器：只派发已注册专项智能体，不发明工具。" + _PLAN_GUIDANCE,
    ),
    AgentSpec(
        name="literature",
        title="文献猎手",
        description="检索、TF-IDF×BM25 评分、语义终审与引用滚雪球",
        mode="subagent",
        tools=("literature.run", "workspace.read"),
        permissions={"workspace.read": "allow"},
        capability="network",
        feature="F1",
        prompt="你是文献猎手。只筛选与研究方向相关的文献，每篇必须带分数和去留理由。",
    ),
    AgentSpec(
        name="extraction",
        title="综述搭子·抽取",
        description="从文献池抽取方法 / 创新点 / 结论，生成摘要整理稿",
        mode="subagent",
        tools=("extraction.run", "workspace.read"),
        permissions={"extraction.run": "allow", "workspace.read": "allow"},
        capability="write",
        feature="F2",
        prompt="你是信息抽取智能体。句句有出处；LLM 失败时降级规则抽取。",
    ),
    AgentSpec(
        name="synthesis",
        title="综述搭子·框架",
        description="plan→act→reflect 生成综述大纲，支撑文献必须真实存在于池中",
        mode="subagent",
        tools=("synthesis.run", "workspace.read"),
        permissions={"synthesis.run": "allow", "workspace.read": "allow"},
        capability="write",
        feature="F2",
        prompt="你是综述框架智能体。禁止幻觉引用，reflect 阶段逐字核对题名。",
    ),
    AgentSpec(
        name="stats",
        title="数据速览",
        description="描述统计、IQR 异常值、相关性与图表建议",
        mode="subagent",
        tools=("stats.analyze", "workspace.read"),
        permissions={"stats.analyze": "allow", "workspace.read": "allow"},
        capability="read",
        feature="F3",
        prompt="你是实验数据速览智能体。只做可复核的描述统计，不下学术结论。",
    ),
    AgentSpec(
        name="citation",
        title="格式管家",
        description="GB/T 7714—2015 / APA 7 纯规则排版，零格式幻觉",
        mode="subagent",
        tools=("citation.run", "workspace.read"),
        permissions={"citation.run": "allow", "workspace.read": "allow"},
        capability="write",
        feature="F4",
        prompt="你是参考文献格式管家。禁止用大模型生成格式，只调用规则引擎。",
    ),
    AgentSpec(
        name="critic",
        title="证据审计",
        description="只读检查文献池、摘要出处、综述引用与权限拒绝记录",
        mode="subagent",
        tools=("critic.run", "workspace.read"),
        permissions={"critic.run": "allow", "workspace.read": "allow"},
        capability="read",
        feature="审计",
        prompt="你是只读审计智能体。不修改产物，只报告缺口、风险与证据断裂。",
    ),
    AgentSpec(
        name="explore",
        title="只读探测",
        description="列出编制表、工作区快照与可选系统自检，无副作用",
        mode="subagent",
        tools=("explore.run", "workspace.read", "doctor.run"),
        permissions={"*": "allow"},
        capability="read",
        feature="系统",
        prompt="你是只读探测智能体。只观察系统与工作区，不写文件、不访问文献网。",
    ),
)

# Output readers only access data already produced inside the current session.
# They remain subject to explicit global and per-agent read permission overrides.
_OUTPUT_READ_TOOLS = ('tool_output.list', 'tool_output.read', 'tool_output.search')
ROSTER: dict[str, AgentSpec] = {spec.name: spec if spec.tools == ('*',) else
    replace(spec, tools=spec.tools + _OUTPUT_READ_TOOLS) for spec in _SPECS}


def get_spec(name: str | None) -> AgentSpec:
    """按名称取编制表项；空名称回落 research。"""
    key = (name or "research").strip().lower() or "research"
    spec = ROSTER.get(key)
    if spec is None:
        known = "、".join(ROSTER)
        raise ValueError(f"未知智能体：{name}。可用：{known}")
    return spec


def list_agents() -> list[dict[str, Any]]:
    """编制表的可序列化清单。"""
    return [spec.as_dict() for spec in ROSTER.values()]
