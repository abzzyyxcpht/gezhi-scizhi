# -*- coding: utf-8 -*-
"""可复现研究包：把一次科研任务的上下文、工具、环境与数据版本打成 zip。

导出包保留证据链和产物链，记录
prompt / context / 工具 / 环境 / 数据版本。

导出范围与交接包一致：
- 只整理**已记录**的状态，不重跑实验、不验证科学真伪、不补全缺失材料；
- 密钥脱敏（settings 里的 API Key 只保留「是否配置」布尔值）；
- 缺失/失败/模拟材料进 ``manifest.deficits`` 如实标注，不静默填充。
"""
from __future__ import annotations

import json
import platform
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import config, service
from .prompts import PROMPT_VERSION
from .version import APP_VERSION

SCHEMA_VERSION = 1

# settings 里永不进导出包的键：只保留是否配置的布尔值。
_SECRET_KEYS = ("llm_api_key", "embedding_api_key")

# 产物键 → 人类可读名（manifest 的产物清单用）。
_ARTIFACT_LABELS = {
    "lit_filter": "文献筛选池",
    "extract": "证据抽取",
    "digest": "摘要稿",
    "outline": "综述框架",
    "citation": "引用格式",
    "review": "证据审计",
}


def _sanitized_settings() -> dict[str, Any]:
    """Export a detached safe projection and retain only credential presence."""
    from . import settings as app_settings
    from .workbench import redact

    original = app_settings.load()
    snapshot = redact(original)
    for key in _SECRET_KEYS:
        if key in original:
            snapshot[key] = bool(original[key])
    for key in ('llm_base_url', 'embedding_base_url'):
        if key in original:
            snapshot[key] = _safe_endpoint(snapshot[key])
    connections = original.get('model_connections', [])
    if isinstance(connections, list):
        for connection, safe in zip(connections, snapshot.get('model_connections', [])):
            if isinstance(connection, dict) and isinstance(safe, dict):
                safe['api_key'] = safe['has_api_key'] = bool(connection.get('api_key'))
                if 'base_url' in connection:
                    safe['base_url'] = _safe_endpoint(safe['base_url'])
    return snapshot


def _safe_endpoint(value: Any) -> str:
    """Keep endpoint identity; legacy URLs may contain embedded credentials."""
    if not value:
        return ''
    if not isinstance(value, str):
        return '[REDACTED]'
    try:
        url = urlsplit(value)
        if url.scheme not in {'http', 'https'} or not url.netloc:
            return '[REDACTED]'
        return urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, '', ''))
    except ValueError:
        return '[REDACTED]'


def _deficits(ctx: Any, art: dict[str, Any]) -> list[dict[str, str]]:
    """缺失与风险清单：与 research_planning 交接包同口径，逐条可读。"""
    deficits: list[dict[str, str]] = []
    if not art.get("lit_filter", {}).get("pool"):
        deficits.append({"code": "no_pool", "message": "文献池为空，证据链无从核对。"})
    if not art.get("extract", {}).get("digest_md"):
        deficits.append({"code": "no_digest", "message": "没有摘要稿，综述缺初筛依据。"})
    if not art.get("review_frame", {}).get("outline_md"):
        deficits.append({"code": "no_outline", "message": "没有综述框架，结论缺组织结构。"})
    if not art.get("citations"):
        deficits.append({"code": "no_citations", "message": "没有引用条目，格式未受规则引擎约束。"})
    failed = [node for node, info in (ctx.node_durations or {}).items()
              if isinstance(info, dict) and info.get("status") == "failed"]
    for node in failed:
        deficits.append({"code": "node_failed", "message": f"节点 {node} 执行失败，请保留原因与下一步。"})
    synthetic = [w for w in art.get("lit_filter", {}).get("pool", [])
                 if w.get("source") == "synthetic"]
    if synthetic:
        deficits.append({"code": "synthetic_material",
                         "message": f"文献池含 {len(synthetic)} 条模拟材料，不能作为真实实证证据。"})
    return deficits


def _materials(art: dict[str, Any]) -> list[dict[str, Any]]:
    """文献池材料清单：本地 PDF 带 sha256 与页数，检索条目只带身份字段。"""
    rows = []
    for work in art.get("lit_filter", {}).get("pool", []):
        row: dict[str, Any] = {
            "id": str(work.get("id") or ""),
            "title": str(work.get("title") or "")[:200],
            "year": work.get("year"),
            "source": str(work.get("source") or ""),
        }
        if work.get("sha256"):
            row["sha256"] = str(work["sha256"])
        if work.get("pages"):
            row["pages"] = work["pages"]
        rows.append(row)
    return rows


def build_manifest(ctx: Any) -> dict[str, Any]:
    """研究包 manifest：上下文、工具、环境、数据版本与缺口，全部来自已记录状态。"""
    art = ctx.artifacts or {}
    workflow_id = str(getattr(ctx, "workflow", "") or "")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "task_id": ctx.task_id,
        "topic": str(ctx.params.get("topic") or ""),
        "created_at": ctx.started_at,
        "app_version": APP_VERSION,
        "prompt": {
            "prompt_version": PROMPT_VERSION,
            "prompt_keys": ["lit_review.v2", "research_pipeline"],
            "note": "prompt_key 为提示词注册表键；每次模型调用以 prompts.PROMPT_VERSION 为准。",
        },
        "context": {
            "workflow": workflow_id,
            "nodes": sorted((ctx.node_durations or {}).keys()),
            "workspace_mode": str(ctx.params.get("workspace_mode") or "research"),
            "cite_style": str(ctx.params.get("cite_style") or "gbt7714"),
        },
        "tools": {
            "workflow_file": f"workflows/{workflow_id}.yaml" if workflow_id else "",
            "reproduce_plan": "workflows/reproduce.yaml",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "settings": _sanitized_settings(),
        "data": {
            "materials": _materials(art),
            "artifacts": [{"key": key, "label": _ARTIFACT_LABELS.get(key, key)}
                          for key in _ARTIFACT_LABELS if art.get(key)],
        },
        "evidence": {
            "audit_available": bool(art.get("review")),
            "pool_size": len(art.get("lit_filter", {}).get("pool", [])),
            "citations": len(art.get("citations") or []),
        },
        "deficits": _deficits(ctx, art),
        "boundary": "本包只整理已记录状态：不重跑实验、不验证科学真伪、不补全缺失材料；"
                    "复现请按 workflows/ 与 manifest.tools 执行，失败也请记录。",
    }
    return manifest


def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        f"# 可复现研究包 · {manifest['topic'] or manifest['task_id']}",
        "",
        f"- 任务：`{manifest['task_id']}`（应用版本 {manifest['app_version']}）",
        f"- 提示词版本：`{manifest['prompt']['prompt_version']}`",
        f"- 工作流：`{manifest['context']['workflow']}`",
        f"- 环境：Python {manifest['environment']['python']} · {manifest['environment']['platform']}",
        "",
        "## 目录",
        "",
        "- `manifest.json` —— 本包全部元数据（上下文/工具/环境/数据版本/缺口）",
        "- `artifacts/` —— 科研产物、证据审计与 JSON 台账（export_artifacts 输出）",
        "- `workflows/` —— 研究工作流与复现流水线 YAML",
        "",
        "## 复现步骤",
        "",
        "1. 按 `manifest.json` 的 `environment` 准备相同 Python 与依赖；",
        "2. 按 `manifest.json` 的 `prompt.prompt_version` 锁定提示词版本；",
        "3. 按 `workflows/<工作流>.yaml` 与 `workflows/reproduce.yaml` 的节点顺序重跑，"
        "材料以 `data.materials` 的 sha256 核对；",
        "4. 失败也记录原因与下一步，不编造结果。",
        "",
        "## 缺口与风险",
        "",
    ]
    if manifest["deficits"]:
        lines += [f"- [{d['code']}] {d['message']}" for d in manifest["deficits"]]
    else:
        lines.append("- 无（本包导出时未发现缺失项）")
    lines += ["", manifest["boundary"], ""]
    return "\n".join(lines)


def build_package(task_id: str, out_dir: str | Path | None = None) -> dict[str, Any]:
    """打出可复现研究包 zip，返回路径与 manifest 摘要。任务不存在时明确报错。"""
    ctx = service.get(task_id)
    if ctx is None:
        raise ValueError(f"任务不存在：{task_id}")
    manifest = build_manifest(ctx)

    exports = Path(out_dir) if out_dir else config.DATA_DIR / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stem = f"research_package_{uuid.uuid4().hex}"
    staging = exports / stem
    zip_path = exports / f"{stem}.zip"

    written = service.export_artifacts(task_id, staging / "artifacts")
    workflows_dir = staging / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    for name in (manifest["context"]["workflow"], "reproduce"):
        source = config.ROOT_DIR / "workflows" / f"{name}.yaml"
        if source.is_file():
            (workflows_dir / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8")
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (staging / "README.md").write_text(_readme(manifest), encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    for path in sorted(staging.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    staging.rmdir()

    return {
        "ok": True,
        "task_id": task_id,
        "zip_path": str(zip_path.resolve()),
        "artifact_files": [str(Path(p).name) for p in written],
        "manifest": manifest,
        "text": f"研究包已导出：{zip_path.name}"
                f"（产物 {len(written)} 个文件，缺口 {len(manifest['deficits'])} 条）",
    }


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """bridge 入口：{"cmd": "research_package", "task_id": "..."}。"""
    task_id = str(req.get("task_id") or "")
    if not task_id:
        return {"ok": False, "error": "请提供任务 ID（先打开一项研究）"}
    try:
        return build_package(task_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 —— 桥接层统一转错误回执，不泄露堆栈
        return {"ok": False, "error": f"研究包导出失败：{exc}"}
