"""AIGC 显式标识：对 AI 辅助生成的科研产物加注标识与生成元数据。

依据《人工智能生成合成内容标识办法》（2025-09-01 施行）的显式标识要求：
- 产物正文携带标识区（生成服务、生成时间、生成方式、任务标识与提示词版本）；
- 导出目录生成 manifest（每个文件的 AI 参与度与溯源信息）；
- 规则引擎产物同样标注（确定性模板仍属系统生成内容，明确"引用前需人工复核"）。
"""
from __future__ import annotations

import time
from typing import Any

MARK = "※ AI 辅助生成内容"


def _provenance(llm_used: bool, task_id: str, topic: str) -> str:
    from . import prompts
    method = "LLM 语义抽取 + 规则模板" if llm_used else "确定性规则引擎"
    parts = [f"生成方式：{method}", f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}"]
    if topic:
        parts.append(f"研究方向：{topic}")
    if task_id:
        parts.append(f"任务标识：{task_id}")
    parts.append(f"提示词版本：v{prompts.PROMPT_VERSION}")
    return " ｜ ".join(parts)


def footer_md(*, llm_used: bool, task_id: str = "", topic: str = "") -> str:
    """Markdown 产物（摘要整理稿/综述框架）正文末尾的显式标识区。"""
    return (
        "\n---\n"
        f"> {MARK}\n"
        f"> {_provenance(llm_used, task_id, topic)}\n"
        "> 本文档所有条目均回链文献池原始记录；引用前请人工复核，不构成最终学术结论。\n")


def export_header(*, kind: str, llm_used: bool, task_id: str = "",
                  topic: str = "") -> str:
    """导出文本文件头部的标识块（纯文本格式）。"""
    lines = [MARK, _provenance(llm_used, task_id, topic),
             "引用前请人工复核，不构成最终学术结论。", ""]
    if kind:
        lines.insert(1, f"文件类型：{kind}")
    return "\n".join(lines)


def manifest_text(entries: list[dict[str, Any]], *, task_id: str = "",
                  topic: str = "") -> str:
    """导出清单：逐文件说明 AI 参与度与溯源信息。

    entries: [{"file": 文件名, "kind": 内容类型, "ai": 是否 AI 参与, "note": 备注}]
    """
    lines = ["# 导出清单（含 AI 参与度说明）", ""]
    if topic:
        lines.append(f"- 研究方向：{topic}")
    if task_id:
        lines.append(f"- 任务标识：{task_id}")
    lines.append(f"- 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("| 文件 | 内容类型 | AI 参与 | 说明 |")
    lines.append("|---|---|---|---|")
    for e in entries:
        lines.append(f"| {e.get('file', '')} | {e.get('kind', '')} "
                     f"| {'是' if e.get('ai') else '否（确定性排版）'} "
                     f"| {e.get('note', '')} |")
    lines.append("")
    lines.append(f"{MARK}——清单所列 AI 参与文件均带正文标识区，供复核与审计。")
    return "\n".join(lines)
