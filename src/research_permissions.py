# -*- coding: utf-8 -*-
"""科研权限三档门：read-only / workspace-write / full-access × 引用/数据/代码三类科研风险。

每个副作用动作显示目标、影响、原因和可逆路径；
低于档位的动作直接拒绝并给原因；拒绝可重试且不污染上下文。

三档是**门槛**，不是开关：档位只决定「哪些动作允许进入审批流」，
具体 allow/ask/deny 仍由既有 PermissionPolicy 与逐工具覆盖决定。
"""
from __future__ import annotations

from typing import Any

# ---- 档位与风险类 ----
TIERS = ("read-only", "workspace-write", "full-access")
TIER_RANK = {name: index for index, name in enumerate(TIERS)}
TIER_LABELS = {"read-only": "只读", "workspace-write": "工作区写入", "full-access": "完全访问"}
DEFAULT_TIER = "workspace-write"

RISK_LABELS = {"citation": "引用", "data": "数据", "code": "代码"}

# ---- 工具规则：(模式, 所需档位, 风险类, 目标, 影响, 可逆) ----
# 未命中的工具按 capability 回落：read/network → 只读·引用，其余 → 工作区写入·数据。
_RULES: tuple[tuple[str, str, str, str, str, bool], ...] = (
    ("shell.run", "full-access", "code",
     "在工作区执行 shell 命令", "可修改文件、访问网络、改变系统状态；输出封顶 4000 字符", False),
    ("control.call", "full-access", "code",
     "调用已绑定的浏览器/电脑控制工具", "真实操作浏览器或桌面，副作用不可自动撤销", False),
    ("mcp.call", "full-access", "code",
     "调用外部 MCP 服务工具", "外部服务副作用由服务方决定，本地不可撤销", False),
    ("mcp.describe", "workspace-write", "data",
     "连接 MCP 服务读取工具参数定义", "只读schema；不调用业务工具", True),
    ("file.write", "workspace-write", "data",
     "写入工作区文件", "新建或覆盖文件；研究档案另走 Git 归档", True),
    ("file.edit", "workspace-write", "data",
     "编辑工作区文件", "就地修改文件内容", True),
    ("file.str_replace", "workspace-write", "data",
     "替换工作区文件片段", "就地修改文件内容", True),
    ("notebook.save", "workspace-write", "data",
     "保存研究假设/实验记录", "写入研究记录（带版本与 provenance）", True),
    ("research.export", "workspace-write", "data",
     "导出科研产物", "写出产物文件与台账", True),
    ("research.create", "workspace-write", "data",
     "创建科研任务并启动流水线", "建任务后可取消；文献池写入留痕", True),
    ("subagent.spawn", "workspace-write", "data",
     "派发进程内子智能体", "子会话继承本会话授权，不反向污染", True),
    ("plugin.call", "workspace-write", "data",
     "调用已注册插件命令", "插件行为由插件定义，调用前可见名称与参数", False),
    ("research.run", "read-only", "citation",
     "联网检索文献并更新文献池", "只读网络检索；文献池快照留痕", True),
    ("subagent.fanout", "read-only", "citation",
     "并行派发文献子任务", "多路只读检索，单路失败自动降级", True),
)

_CAPABILITY_FALLBACK = {
    "read": ("read-only", "citation", "读取科研状态或材料", "只读，无副作用", True),
    "network": ("read-only", "citation", "联网检索科研材料", "只读网络请求", True),
}


def _rule_for(tool_name: str) -> tuple[str, str, str, str, str, bool] | None:
    for pattern, tier, risk, target, impact, reversible in _RULES:
        if tool_name == pattern:
            return pattern, tier, risk, target, impact, reversible
    return None


def classify(tool_name: str, capability: str,
             arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一个工具调用归类为（所需档位, 风险类, 目标, 影响, 可逆）。

    目标描述尽量带上参数里的关键标识（路径/任务/服务），让审批面板
    「将要做什么、影响什么」在调用前就可见。
    """
    rule = _rule_for(tool_name)
    if rule is not None:
        _pattern, tier, risk, target, impact, reversible = rule
    else:
        tier, risk, target, impact, reversible = _CAPABILITY_FALLBACK.get(
            capability, ("workspace-write", "data", f"调用工具 {tool_name}",
                         "写入或外部副作用，调用前请核对参数", False))
    detail = _target_detail(tool_name, arguments)
    return {
        "tool": tool_name,
        "capability": capability,
        "tier": tier,
        "risk": risk,
        "target": f"{target}（{detail}）" if detail else target,
        "impact": impact,
        "reversible": reversible,
        "reason": f"{TIER_LABELS[tier]}档位 · {RISK_LABELS[risk]}类科研动作",
    }


def _target_detail(tool_name: str, arguments: dict[str, Any] | None) -> str:
    if not isinstance(arguments, dict):
        return ""
    for key in ("path", "out_dir", "task_id", "server", "tool", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value[:80]
    if tool_name == "shell.run":
        command = arguments.get("command")
        if isinstance(command, str) and command:
            return command[:80]
    return ""


def exceeds(required: str, configured: str) -> bool:
    """所需档位是否严格高于当前档位。"""
    return TIER_RANK.get(required, 0) > TIER_RANK.get(configured, 0)


def normalize_tier(value: Any) -> str:
    return value if value in TIER_RANK else DEFAULT_TIER


def gate(tool_name: str, capability: str, arguments: dict[str, Any] | None,
         configured_tier: str) -> tuple[str, dict[str, Any]]:
    """三档门判定：返回 ("allow"|"ask"|"deny", action)。

    deny 只由档位不足产生——「当前档位不覆盖这类动作」，可调档位后重试；
    档位覆盖内的动作交回既有策略（allow/ask），本模块不改变其语义。
    """
    action = classify(tool_name, capability, arguments)
    tier = normalize_tier(configured_tier)
    if exceeds(action["tier"], tier):
        action["denied_because"] = (
            f"当前权限档位「{TIER_LABELS[tier]}」不覆盖{RISK_LABELS[action['risk']]}类动作"
            f"（需要 {TIER_LABELS[action['tier']]}）；请到 设置·工具权限 调整档位后重试")
        return "deny", action
    return "ask", action


def matrix() -> list[dict[str, Any]]:
    """三档 × 三类风险的放行矩阵（设置页与 /policy 展示用）。"""
    rows = []
    for tier in TIERS:
        row = {"tier": tier, "label": TIER_LABELS[tier], "risks": {}}
        for risk in ("citation", "data", "code"):
            row["risks"][risk] = not exceeds(
                {"citation": "read-only", "data": "workspace-write",
                 "code": "full-access"}[risk], tier)
        rows.append(row)
    return rows


def render_text(configured_tier: str) -> str:
    """纯文本矩阵（TUI /policy 与桥接回执）。"""
    tier = normalize_tier(configured_tier)
    lines = [f"科研权限档位：{TIER_LABELS[tier]}（{tier}）", ""]
    for row in matrix():
        cells = "  ".join(
            f"{RISK_LABELS[risk]}:{'放行' if ok else '拦截'}"
            for risk, ok in row["risks"].items())
        lines.append(f"  {row['label']:<6} {cells}")
    lines += [
        "",
        "档位只决定哪些动作进入审批流；具体允许/询问/拒绝仍按工具权限策略执行。",
        "被拦截的动作会说明所需档位，调整后即可重试，不污染上下文。",
    ]
    return "\n".join(lines)
