"""Persistent research questions and experiment records with explicit provenance.

This service records submitted assertions. It neither executes experiments nor verifies
scientific support, novelty, source authenticity, or reproducibility.
"""
from __future__ import annotations

import copy
import html
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from . import config
from .research_analysis import normalize_doi
from .session_lock import session_lock
from .state_store import write_json


SCHEMA_VERSION = "1.0"
KINDS = ["hypothesis", "experiment"]
STATUSES = ["planned", "running", "completed", "failed"]
RELATIONS = ["supports", "challenges", "context"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_TEXT_FIELDS = ("title", "question", "prediction", "method", "metrics", "result",
                "limitations", "next_action", "task_id")
_READ_ONLY = {"created_at", "updated_at", "version", "schema_version", "provenance"}
_EDITABLE = set(_TEXT_FIELDS) | {"id", "kind", "status", "evidence", "related_ids",
                                       "observations", "synthetic"}
_SOURCE_FIELDS = {"id", "title", "doi", "url", "source_type", "synthetic"}
_EVIDENCE_FIELDS = {"relation", "source", "quote", "scope", "locator", "note"}
_BOUNDARY = ("研究关系来自录入内容，未验证来源真实性、科学支持、创新性或可复现性。"
             "完成表示已录入结果；模拟材料不能作为真实实证证据。")


class NotebookError(ValueError):
    def __init__(self, message: str, code: str = "invalid_data"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: Any, name: str, *, required: bool = False, maximum: int = 100_000) -> str:
    if not isinstance(value, str):
        raise NotebookError(f"{name} 必须为字符串。")
    value = value.strip()
    if (required and not value) or len(value) > maximum or "\x00" in value:
        raise NotebookError(f"{name} 不能为空或超出长度限制。")
    return value


def _identifier(value: Any, name: str = "id", *, optional: bool = False) -> str:
    value = _text(value, name, required=not optional, maximum=80)
    if value and not _ID.fullmatch(value):
        raise NotebookError(f"{name} 只能包含字母、数字、下划线和连字符，且以字母或数字开头。")
    return value


def _enum(value: Any, values: list[str], name: str) -> str:
    if not isinstance(value, str) or value not in values:
        raise NotebookError(f"{name} 必须为 {' / '.join(values)}。")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise NotebookError(f"{name} 必须为布尔值。")
    return value


def _rows(value: Any, name: str, maximum: int = 500) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise NotebookError(f"{name} 必须为不超过 {maximum} 项的数组。")
    return value


def _source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _SOURCE_FIELDS:
        raise NotebookError("证据 source 必须为来源对象，且只能包含公开来源字段。")
    source = {name: _text(value.get(name, ""), f"source.{name}", maximum=2000)
              for name in ("id", "title", "doi", "url", "source_type")}
    source["source_type"] = source["source_type"] or "user_provided"
    if source["doi"]:
        doi = normalize_doi(source["doi"])
        if not doi:
            raise NotebookError("source.doi 格式无效；格式检查不验证文献真实性。")
        source["doi"] = doi
    if source["url"]:
        try:
            parts = urlsplit(source["url"])
            credential_keys = {"token", "api_key", "apikey", "key", "secret", "password",
                               "access_token", "authorization", "signature", "x-amz-signature"}
            if (parts.scheme not in {"https", "http"} or not parts.hostname or
                    parts.username is not None or parts.password is not None or
                    any(k.casefold() in credential_keys for k, _ in parse_qsl(parts.query)) or
                    any(c.isspace() for c in source["url"])):
                raise ValueError
            _ = parts.port
        except ValueError:
            raise NotebookError("source.url 必须为不含凭据的公开 HTTP(S) 地址。") from None
    if not any(source[name] for name in ("id", "doi", "url")):
        raise NotebookError("证据来源至少需要 id、doi 或 url；题名不能单独作为来源身份。")
    source["synthetic"] = (_bool(value.get("synthetic", False), "source.synthetic") or
                           source["source_type"].casefold() == "synthetic_demo")
    return source


def _evidence(value: Any) -> list[dict[str, Any]]:
    result = []
    for item in _rows(value, "evidence"):
        if not isinstance(item, dict) or set(item) - _EVIDENCE_FIELDS:
            raise NotebookError("每条 evidence 必须为关系、来源、原文和范围组成的对象。")
        entry = {"relation": _enum(item.get("relation"), RELATIONS, "evidence.relation"),
                 "source": _source(item.get("source"))}
        for name in ("quote", "scope", "locator", "note"):
            entry[name] = _text(item.get(name, ""), f"evidence.{name}",
                                required=name in {"quote", "scope"}, maximum=20_000)
        result.append(entry)
    return result


def _template(kind: str) -> dict[str, Any]:
    return {"kind": kind, **{field: "" for field in _TEXT_FIELDS}, "status": "planned",
            "evidence": [], "related_ids": [], "observations": [], "synthetic": False}


def _validate(record: dict[str, Any], known_ids: set[str], *, origin: str | None = None) -> dict[str, Any]:
    if set(record) - (_EDITABLE | _READ_ONLY):
        raise NotebookError("record 包含不支持的字段；请使用 notebook_schema 中的字段。")
    output = {"id": _identifier(record["id"]),
              "kind": _enum(record.get("kind"), KINDS, "kind"),
              "status": _enum(record.get("status"), STATUSES, "status")}
    for name in _TEXT_FIELDS:
        output[name] = _text(record.get(name, ""), name, required=name in {"title", "question"},
                             maximum=300 if name == "title" else 100_000)
    output["task_id"] = _identifier(output["task_id"], "task_id", optional=True)
    output["synthetic"] = _bool(record.get("synthetic", False), "synthetic")
    output["evidence"] = _evidence(record.get("evidence", []))
    output["observations"] = [_text(o, "observations", required=True, maximum=20_000)
                              for o in _rows(record.get("observations", []), "observations")]
    related = [_identifier(ref, "related_ids")
               for ref in _rows(record.get("related_ids", []), "related_ids", 200)]
    if output["id"] in related:
        raise NotebookError("related_ids 不能引用记录自身。")
    if set(related) - known_ids:
        raise NotebookError("related_ids 包含不存在的科研记录。", "reference_not_found")
    output["related_ids"] = list(dict.fromkeys(related))
    if output["status"] == "completed" and not output["result"]:
        raise NotebookError("completed 状态必须记录实际结果；计划和预测不能替代结果。")
    output.update({name: record[name] for name in _READ_ONLY - {"provenance"}})
    if origin is None:
        provenance = record.get("provenance", {})
        if not isinstance(provenance, dict):
            raise NotebookError("记录来源字段损坏。")
        origin = provenance.get("origin", "user_recorded")
    origin = _enum(origin, ["user_recorded", "agent_recorded"], "provenance.origin")
    output["provenance"] = {
        "origin": origin,
        "evidence_relations": "agent_asserted" if origin == "agent_recorded" else "user_asserted",
        "scientific_verification": "not_performed",
        "source_identity_verification": "not_performed",
        "task_reference_validation": "format_only",
        "contains_synthetic_evidence": any(e["source"]["synthetic"] for e in output["evidence"]),
    }
    return output


def _path() -> Path:
    path = Path(config.DATA_DIR) / "research_notebook.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError
        raw = payload.get("records")
        if not isinstance(raw, dict):
            raise ValueError
        records = {}
        for key, value in raw.items():
            if not isinstance(value, dict) or value.get("id") != key:
                raise ValueError
            if (value.get("schema_version") != SCHEMA_VERSION or
                    type(value.get("version")) is not int or value["version"] < 1):
                raise ValueError
            for field in ("created_at", "updated_at"):
                moment = datetime.fromisoformat(value[field].replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    raise ValueError
            records[key] = _validate(value, set(raw))
        return records
    except (ValueError, TypeError, KeyError, AttributeError):
        raise NotebookError("科研记录文件损坏或版本不兼容，已保留原文件；请恢复备份后重试。",
                            "corrupt_store") from None


def _write(path: Path, records: dict[str, dict[str, Any]]) -> None:
    write_json(path, {"schema_version": SCHEMA_VERSION, "records": records})


def schema() -> dict[str, Any]:
    """Return machine-readable fields and blank, non-fabricated research templates."""
    source = {"type": "object", "additionalProperties": False,
              "properties": {name: {"type": "string", "maxLength": 2000}
                             for name in sorted(_SOURCE_FIELDS - {"synthetic"})},
              "anyOf": [{"required": [name], "properties": {name: {"minLength": 1, "pattern": r"\S"}}}
                        for name in ("id", "doi", "url")]}
    source["properties"]["synthetic"] = {"type": "boolean", "default": False}
    evidence = {"type": "object", "additionalProperties": False,
                "required": ["relation", "source", "quote", "scope"],
                "properties": {"relation": {"type": "string", "enum": RELATIONS}, "source": source,
                               **{field: {"type": "string", "maxLength": 20_000,
                                          "minLength": 1 if field in {"quote", "scope"} else 0}
                                  for field in ("quote", "scope", "locator", "note")}}}
    properties = {name: {"type": "string", "maxLength": 100_000} for name in _TEXT_FIELDS}
    properties.update({
        "id": {"type": "string", "pattern": _ID.pattern},
        "kind": {"type": "string", "enum": KINDS, "default": "hypothesis"},
        "status": {"type": "string", "enum": STATUSES, "default": "planned"},
        "synthetic": {"type": "boolean", "default": False},
        "evidence": {"type": "array", "items": evidence, "maxItems": 500},
        "observations": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 20_000},
                         "maxItems": 500},
        "related_ids": {"type": "array", "items": {"type": "string", "pattern": _ID.pattern}, "maxItems": 200},
        "version": {"type": "integer", "minimum": 1, "readOnly": True},
        "schema_version": {"type": "string", "const": SCHEMA_VERSION, "readOnly": True},
        "provenance": {"type": "object", "readOnly": True},
        **{field: {"type": "string", "format": "date-time", "readOnly": True}
           for field in ("created_at", "updated_at")},
    })
    properties["title"].update(minLength=1, maxLength=300)
    properties["question"].update(minLength=1)
    properties["task_id"].update(maxLength=80, pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_-]{0,79})?$")
    return {"ok": True, "schema_version": SCHEMA_VERSION,
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["title", "question"], "properties": properties,
                       "allOf": [{"if": {"properties": {"status": {"const": "completed"}}, "required": ["status"]},
                                  "then": {"required": ["result"], "properties": {"result": {"minLength": 1}}}}]},
            "templates": {kind: _template(kind) for kind in KINDS},
            "rules": ["新记录必须有 title 和 question；更新按字段合并，数组按字段整体替换。",
                      "completed 必须有实际 result；状态及支持/反驳关系仅为录入者判断。",
                      "来源需 id/doi/url 之一以及 quote、scope；来源与科学结论不自动验证。",
                      "related_ids 必须存在；被引用记录须先解除关联才能删除。",
                      "task_id 仅校验格式，可关联归档或当前未加载的工作流任务。",
                      "synthetic 标记记录自身结果是否为模拟；来源的 synthetic 单独保留。"],
            "boundary": _BOUNDARY}


def _md(value: Any) -> str:
    value = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_[\]|])", r"\\\1", value).replace("\r", "")


def render_markdown(record: dict[str, Any]) -> str:
    """Render all research content and public provenance without reading other state."""
    lines = [f"# {_md(record['title'])}", "", _BOUNDARY, "",
             f"ID: {record['id']} · kind: {record['kind']} · status: {record['status']}",
             f"version: {record['version']} · schema_version: {SCHEMA_VERSION}",
             f"created_at: {record['created_at']} · updated_at: {record['updated_at']}",
             f"synthetic: {str(record['synthetic']).lower()} · task_id: {_md(record['task_id'] or '未关联')}", ""]
    for field, label in (("question", "研究问题"), ("prediction", "可检验预测"), ("method", "实验方法"),
                         ("metrics", "评价指标"), ("result", "实际结果"), ("limitations", "局限"),
                         ("next_action", "下一步行动")):
        lines.extend([f"## {label}", "", _md(record[field]) or "未记录", ""])
    lines.extend(["## 观察记录", ""])
    lines.extend([f"- {_md(o)}" for o in record["observations"]] or ["未记录"])
    lines.extend(["", "## 证据与来源", ""])
    for index, entry in enumerate(record["evidence"], 1):
        source = entry["source"]
        lines.extend([f"### {index}. {entry['relation']}（{record['provenance']['evidence_relations']}）", ""])
        lines.extend(f"- {field}: {_md(source[field])}" for field in sorted(_SOURCE_FIELDS))
        lines.extend(["", "原文：", "", *["> " + _md(line) for line in entry["quote"].splitlines()], "",
                      "适用范围：" + _md(entry["scope"]), "",
                      "定位：" + (_md(entry["locator"]) or "未记录"), "",
                      "备注：" + (_md(entry["note"]) or "未记录"), ""])
    if not record["evidence"]:
        lines.extend(["未关联证据；不能据此判定支持、反驳或创新。", ""])
    lines.extend(["## 关联记录", "", ", ".join(record["related_ids"]) or "无", "",
                  "## 来源与核查状态", ""])
    # Provenance values are fixed service enums/booleans, never user content.
    lines.extend(f"- {field}: {value}" for field, value in record["provenance"].items())
    return "\n".join(lines) + "\n"


def _export(record: dict[str, Any], fmt: str) -> Path:
    # Caller paths are never used. Resolve before writing to reject redirected directories.
    data_root = Path(config.DATA_DIR).resolve()
    export_root = data_root / "exports"
    target = export_root / f"notebook_{record['id']}.{'json' if fmt == 'json' else 'md'}"
    if not target.resolve().is_relative_to(data_root):
        raise NotebookError("导出位置必须位于数据目录内。", "invalid_path")
    export_root.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        write_json(target, {"schema_version": SCHEMA_VERSION, "exported_at": _now(),
                            "boundary": _BOUNDARY, "record": record})
    else:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(render_markdown(record), encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.resolve()


def handle(req: dict, *, actor: str = "user") -> dict:
    """Handle notebook CRUD, schema and fixed-directory Markdown/JSON export.

    Errors are returned as ``{ok: False, error: str, code: str}``; no operation
    contacts external services, executes experiments, or changes workflow state.
    ``actor`` is trusted application context, never a field accepted from ``req``.
    It identifies the last saving actor; it never indicates human review.
    """
    try:
        _enum(actor, ["user", "agent"], "actor")
        if not isinstance(req, dict):
            raise NotebookError("请求必须为对象。")
        cmd = req.get("cmd")
        if not isinstance(cmd, str):
            raise NotebookError("cmd 必须为字符串。")
        if cmd == "notebook_schema":
            return schema()
        if cmd not in {"notebook_list", "notebook_save", "notebook_get", "notebook_delete", "notebook_export"}:
            raise NotebookError("未知科研记录命令。", "unknown_command")
        path = _path()
        with session_lock(path):
            records = _read(path)
            if cmd == "notebook_list":
                filters = {}
                for field, choices in (("kind", KINDS), ("status", STATUSES)):
                    if req.get(field) is not None:
                        filters[field] = _enum(req[field], choices, field)
                if req.get("task_id") is not None:
                    filters["task_id"] = _identifier(req["task_id"], "task_id", optional=True)
                found = [r for r in records.values() if all(r[field] == value for field, value in filters.items())]
                found.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
                return {"ok": True, "records": found, "count": len(found), "schema_version": SCHEMA_VERSION}
            if cmd == "notebook_save":
                incoming = req.get("record")
                if not isinstance(incoming, dict):
                    raise NotebookError("record 必须为对象。")
                identifier = (_identifier(incoming["id"]) if incoming.get("id") is not None
                              else "nb_" + uuid.uuid4().hex)
                existing = records.get(identifier)
                expected = req.get("expected_version")
                if expected is not None and (type(expected) is not int or expected != (existing or {}).get("version", 0)):
                    raise NotebookError("科研记录已变更，请重新读取后提交。", "version_conflict")
                stamp = _now()
                record = copy.deepcopy(existing) if existing else _template("hypothesis")
                record.update({k: copy.deepcopy(v) for k, v in incoming.items() if k not in _READ_ONLY})
                record.update(id=identifier, schema_version=SCHEMA_VERSION, updated_at=stamp,
                              created_at=existing["created_at"] if existing else stamp,
                              version=existing["version"] + 1 if existing else 1)
                record = _validate(record, set(records), origin="agent_recorded" if actor == "agent" else "user_recorded")
                records[identifier] = record
                _write(path, records)
                return {"ok": True, "record": record, "created": existing is None}
            identifier = _identifier(req.get("id"))
            if identifier not in records:
                raise NotebookError("未找到科研记录。", "not_found")
            record = records[identifier]
            if cmd == "notebook_get":
                return {"ok": True, "record": record}
            if cmd == "notebook_delete":
                references = [r["id"] for r in records.values() if identifier in r["related_ids"]]
                if references:
                    return {"ok": False, "code": "record_referenced", "error": "该记录仍被引用，请先解除关联。",
                            "referenced_by": sorted(references)}
                del records[identifier]
                _write(path, records)
                return {"ok": True, "deleted": True, "id": identifier}
            if set(req) - {"cmd", "id", "format", "request_id"}:
                raise NotebookError("导出仅允许指定 id 与 format，文件固定保存到 data/exports。")
            fmt = _enum(req.get("format", "markdown"), ["markdown", "json"], "format")
            target = _export(record, fmt)
            return {"ok": True, "path": str(target), "format": fmt, "record_id": identifier,
                    "version": record["version"]}
    except NotebookError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except OSError:
        return {"ok": False, "error": "科研记录读写失败，请检查数据目录权限或可用空间。", "code": "storage_error"}
