"""Paper reproduction pipeline actions (Claude Code reproducibility benchmark analogue).

``reproduce_plan`` turns a task request plus any ingested local material into a
deterministic reproduction plan (environment checks, dependency installs, baseline
commands, acceptance metrics). ``reproduce_run`` executes only whitelisted baseline
commands inside the project root, records real exit codes and output summaries, and
writes an experiment record into the research notebook. Neither action fabricates
results; a failing command is recorded as a failure.
"""
from __future__ import annotations

import subprocess
from typing import Any

from ..orchestrator.engine import TaskContext, action
# 白名单：只允许科研复现常见命令前缀（任意 shell 不开放；禁区词复用 bridge 口径）
_WHITELIST = (
    "python -m pytest", "python -m pip", "python train.py", "python eval.py",
    "python main.py", "python run.py", "python reproduce.py", "pip install",
)
_FORBIDDEN = ("rm ", "del ", "format", "shutdown", "reboot", "mkfs", ":(){", "curl", "wget")
_TIMEOUT_S = 120
_OUTPUT_CAP = 4000


def _task_params(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    merged = dict(getattr(ctx, "params", {}) or {})
    merged.update(params or {})
    return merged


def _detect_baseline(topic: str, pool: list[dict[str, Any]]) -> list[str]:
    """确定性基线命令推断：论文/课题关键词 → 命令清单（无关键词时给通用模板）。"""
    text = f"{topic}".lower()
    commands: list[str] = []
    if any(k in text for k in ("检测", "detection", "缺陷", "defect", "热轧", "steel")):
        commands = ["python -m pytest tests -q", "python train.py --config configs/base.yaml"]
    elif any(k in text for k in ("分类", "classification", "nlp", "文本")):
        commands = ["python -m pytest tests -q", "python main.py --task classify"]
    elif pool:
        commands = ["python -m pytest tests -q"]
    else:
        commands = ["python -m pytest tests -q", "python run.py --help"]
    return commands


@action("reproduce_plan")
def reproduce_plan(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """工作流动作：生成论文复现计划（环境/依赖/基线/验收四段）。

    params: ``{"paper": "...", "metrics": "..."}``（均可缺省，从任务主题推导）。
    产物落 ``artifacts["reproduce_plan"]``；仅生成计划，不执行任何命令。
    """
    merged = _task_params(ctx, params)
    topic = str(merged.get("topic") or merged.get("paper") or "未命名课题").strip()
    metrics = str(merged.get("metrics") or "按论文报告的主指标（如 mAP / 准确率）").strip()
    pool = list((ctx.artifacts.get("lit_filter") or {}).get("pool") or [])
    commands = _detect_baseline(topic, pool)
    plan = {
        "topic": topic,
        "paper": str(merged.get("paper") or ""),
        "metrics": metrics,
        "environment": [
            "确认 Python ≥ 3.10 与包管理器可用",
            "确认数据目录可读写（GEZHI_DATA_DIR 隔离）",
            "确认 GPU/CPU 资源与论文报告环境一致（记录差异）",
        ],
        "dependencies": [
            "按论文附录安装精确版本依赖（锁定 requirements）",
            "固定随机种子并记录硬件/驱动版本",
        ],
        "baseline_commands": commands,
        "acceptance": [
            f"基线指标达到论文报告的 {metrics}",
            "失败命令须记录退出码与输出摘要（不编造结果）",
        ],
        "pool_size": len(pool),
        "summary": f"复现计划：{topic}（基线命令 {len(commands)} 条）",
    }
    ctx.artifacts["reproduce_plan"] = plan
    ctx.emit("node", node="reproduce_plan", message=plan["summary"])
    return plan


def _safe_command(command: str) -> bool:
    lowered = command.strip().lower()
    if not lowered.startswith(_WHITELIST):
        return False
    return not any(bad in lowered for bad in _FORBIDDEN)


@action("reproduce_run")
def reproduce_run(ctx: TaskContext, params: dict[str, Any]) -> dict[str, Any]:
    """工作流动作：执行白名单基线命令并记录真实结果。

    params: ``{"commands": [...], "cwd": "..."}``（commands 缺省时取复现计划）。
    每条命令在项目根目录沙箱内执行（120s 超时、输出 4000 字封顶）；
    非白名单命令拒绝执行并记录。产物落 ``artifacts["reproduce_run"]``。
    """
    merged = _task_params(ctx, params)
    plan = ctx.artifacts.get("reproduce_plan") or {}
    commands = [str(c) for c in (merged.get("commands") or plan.get("baseline_commands") or [])]
    if not commands:
        raise ValueError("reproduce_run 需要 commands 参数或先执行 reproduce_plan")
    results = []
    for command in commands[:5]:
        if not _safe_command(command):
            results.append({"command": command, "ok": False, "code": None,
                            "output": "已拒绝：命令不在复现白名单内", "rejected": True})
            continue
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=_TIMEOUT_S, cwd=str(merged.get("cwd") or "."),
            )
            output = ((proc.stdout or "") + (proc.stderr or ""))[-_OUTPUT_CAP:]
            results.append({"command": command, "ok": proc.returncode == 0,
                            "code": proc.returncode, "output": output})
        except subprocess.TimeoutExpired:
            results.append({"command": command, "ok": False, "code": None,
                            "output": f"超时（>{_TIMEOUT_S}s）", "timeout": True})
    passed = sum(1 for r in results if r["ok"])
    run = {"topic": plan.get("topic", ""), "total": len(results), "passed": passed,
           "results": results,
           "summary": f"复现执行 {passed}/{len(results)} 条命令通过"}
    ctx.artifacts["reproduce_run"] = run
    ctx.emit("node", node="reproduce_run", message=run["summary"])
    # 写入科研记录（experiment）：真实结果，失败也入库（AI Scientist-v2 对标）
    try:
        from ..research_notebook import handle as notebook_save
        notebook_save({"cmd": "notebook_save", "record": {
            "id": f"repro_{abs(hash(run['topic'])) % 10**8:x}",
            "kind": "experiment", "title": f"复现：{run['topic']}"[:300],
            "question": f"能否复现「{run['topic']}」的基线结果？",
            "method": "；".join(commands[:5]), "status": "completed" if passed == len(results) else "failed",
            "result": run["summary"],
            "limitations": "白名单命令执行；输出截断至 4000 字",
            "observations": [f"{r['command']} → exit {r['code']}" for r in results],
        }})
    except Exception:
        pass  # 记录失败不阻塞复现产物
    return run

