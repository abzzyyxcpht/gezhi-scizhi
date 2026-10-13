"""Structured world model: cross-task research facts with explicit provenance.

Kosmos-style shared facts: every claim carries a source, a confidence label and a
lifecycle status. This service records submitted assertions only. It never verifies
scientific support, source authenticity, novelty, or reproducibility.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .research_analysis import normalize_doi
from .session_lock import session_lock
from .state_store import write_json

SCHEMA_VERSION = "1.0"
STATUSES = ["asserted", "challenged", "verified", "synthetic"]
CONFIDENCES = ["high", "medium", "low"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_SOURCE_FIELDS = {"id", "title", "doi", "url", "source_type", "synthetic"}
_READ_ONLY = {"created_at", "updated_at", "version", "schema_version", "provenance"}
_EDITABLE = {"id", "claim", "topic", "task_id", "source", "confidence", "status",
             "supports", "challenges", "notes", "synthetic"}
_BOUNDARY = ("世界模型事实来自录入内容，未验证来源真实性、科学支持或创新性。"
             "verified 仅表示录入者自评，不构成独立核查。")

_COMMANDS = {"world_fact_list", "world_fact_save", "world_fact_get", "world_fact_delete",
             "world_fact_challenge", "world_fact_support", "world_fact_schema"}


class WorldFactError(ValueError):
    def __init__(self, message: str, code: str = "invalid_data"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: Any, name: str, *, required: bool = False, maximum: int = 100_000) -> str:
    if not isinstance(value, str):
        raise WorldFactError(f"{name} 必须为字符串。")
    value = value.strip()
    if (required and not value) or len(value) > maximum or "\x00" in value:
        raise WorldFactError(f"{name} 不能为空或超出长度限制。")
    return value


def _identifier(value: Any, name: str = "id", *, optional: bool = False) -> str:
    value = _text(value, name, required=not optional, maximum=80)
    if value and not _ID.fullmatch(value):
        raise WorldFactError(f"{name} 只能包含字母、数字、下划线和连字符，且以字母或数字开头。")
    return value


def _enum(value: Any, values: list[str], name: str) -> str:
    if not isinstance(value, str) or value not in values:
        raise WorldFactError(f"{name} 必须为 {' / '.join(values)}。")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise WorldFactError(f"{name} 必须为布尔值。")
    return value


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0 or value > 100_000:
        raise WorldFactError(f"{name} 必须为 0–100000 的整数。")
    return value


def _source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _SOURCE_FIELDS:
        raise WorldFactError("source 必须为来源对象，且只能包含公开来源字段。")
    source = {name: _text(value.get(name, ""), f"source.{name}", maximum=2000)
              for name in ("id", "title", "doi", "url", "source_type")}
    source["source_type"] = source["source_type"] or "user_provided"
    if source["doi"]:
        doi = normalize_doi(source["doi"])
        if not doi:
            raise WorldFactError("source.doi 格式无效；格式检查不验证文献真实性。")
        source["doi"] = doi
    if source["url"]:
        from urllib.parse import parse_qsl, urlsplit
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
            raise WorldFactError("source.url 必须为不含凭据的公开 HTTP(S) 地址。") from None
    if not any(source[name] for name in ("id", "doi", "url")):
        raise WorldFactError("来源至少需要 id、doi 或 url；题名不能单独作为来源身份。")
    source["synthetic"] = (_bool(value.get("synthetic", False), "source.synthetic") or
                           source["source_type"].casefold() == "synthetic_demo")
    return source


def _validate(fact: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
    if set(fact) - (_EDITABLE | _READ_ONLY):
        raise WorldFactError("fact 包含不支持的字段；请使用 world_fact_schema 中的字段。")
    output = {"id": _identifier(fact["id"]),
              "claim": _text(fact.get("claim", ""), "claim", required=True, maximum=20_000),
              "topic": _text(fact.get("topic", ""), "topic", maximum=2000),
              "task_id": _identifier(fact.get("task_id", ""), "task_id", optional=True),
              "source": _source(fact.get("source")),
              "confidence": _enum(fact.get("confidence", "medium"), CONFIDENCES, "confidence"),
              "status": _enum(fact.get("status", "asserted"), STATUSES, "status"),
              "supports": _count(fact.get("supports", 0), "supports"),
              "challenges": _count(fact.get("challenges", 0), "challenges"),
              "notes": _text(fact.get("notes", ""), "notes", maximum=20_000),
              "synthetic": _bool(fact.get("synthetic", False), "synthetic")}
    if output["status"] == "synthetic" and not output["synthetic"]:
        raise WorldFactError("synthetic 状态必须同时标记 synthetic=true。")
    output.update({name: fact[name] for name in _READ_ONLY - {"provenance"}})
    if origin is None:
        provenance = fact.get("provenance", {})
        if not isinstance(provenance, dict):
            raise WorldFactError("事实来源字段损坏。")
        origin = provenance.get("origin", "user_recorded")
    origin = _enum(origin, ["user_recorded", "agent_recorded"], "provenance.origin")
    output["provenance"] = {
        "origin": origin,
        "scientific_verification": "not_performed",
        "source_identity_verification": "not_performed",
        "contains_synthetic_evidence": output["synthetic"] or output["source"]["synthetic"],
    }
    return output


def _path() -> Path:
    path = Path(config.DATA_DIR) / "world_facts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError
        raw = payload.get("facts")
        if not isinstance(raw, dict):
            raise ValueError
        facts = {}
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
            facts[key] = _validate(value)
        return facts
    except (ValueError, TypeError, KeyError, AttributeError):
        raise WorldFactError("世界模型文件损坏或版本不兼容，已保留原文件；请恢复备份后重试。",
                             "corrupt_store") from None


def _write(path: Path, facts: dict[str, dict[str, Any]]) -> None:
    write_json(path, {"schema_version": SCHEMA_VERSION, "facts": facts})


def schema() -> dict[str, Any]:
    """Return machine-readable fields and the recording boundary."""
    return {"ok": True, "schema_version": SCHEMA_VERSION,
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["claim", "source"],
                       "properties": {
                           "id": {"type": "string", "pattern": _ID.pattern},
                           "claim": {"type": "string", "minLength": 1, "maxLength": 20_000},
                           "topic": {"type": "string", "maxLength": 2000},
                           "task_id": {"type": "string", "maxLength": 80,
                                       "pattern": r"^(?:[A-Za-z0-9][A-Za-z0-9_-]{0,79})?$"},
                           "source": {"type": "object", "required": ["id", "doi", "url"],
                                      "anyOf": [{"required": ["id"]}, {"required": ["doi"]},
                                                {"required": ["url"]}]},
                           "confidence": {"type": "string", "enum": CONFIDENCES, "default": "medium"},
                           "status": {"type": "string", "enum": STATUSES, "default": "asserted"},
                           "supports": {"type": "integer", "minimum": 0, "maximum": 100_000},
                           "challenges": {"type": "integer", "minimum": 0, "maximum": 100_000},
                           "notes": {"type": "string", "maxLength": 20_000},
                           "synthetic": {"type": "boolean", "default": False},
                           "version": {"type": "integer", "minimum": 1, "readOnly": True},
                           "provenance": {"type": "object", "readOnly": True},
                           **{field: {"type": "string", "format": "date-time", "readOnly": True}
                              for field in ("created_at", "updated_at")},
                       }},
            "rules": ["claim 与 source 必填；来源需 id/doi/url 之一。",
                      "synthetic 状态必须同时标记 synthetic=true。",
                      "world_fact_challenge / world_fact_support 只增减计数并改状态。",
                      "verified 仅表示录入者自评，不构成独立核查。"],
            "boundary": _BOUNDARY}


def handle(req: dict, *, actor: str = "user") -> dict:
    """Handle world-model fact CRUD, schema and support/challenge voting.

    Errors are returned as ``{ok: False, error: str, code: str}``; no operation
    contacts external services, executes experiments, or verifies claims.
    """
    try:
        _enum(actor, ["user", "agent"], "actor")
        if not isinstance(req, dict):
            raise WorldFactError("请求必须为对象。")
        cmd = req.get("cmd")
        if not isinstance(cmd, str):
            raise WorldFactError("cmd 必须为字符串。")
        if cmd == "world_fact_schema":
            return schema()
        if cmd not in _COMMANDS:
            raise WorldFactError("未知世界模型命令。", "unknown_command")
        path = _path()
        with session_lock(path):
            facts = _read(path)
            if cmd == "world_fact_list":
                found = list(facts.values())
                if req.get("topic") is not None:
                    topic = _text(req["topic"], "topic", required=True, maximum=2000)
                    found = [f for f in found if f["topic"] == topic]
                if req.get("status") is not None:
                    found = [f for f in found if f["status"] == _enum(req["status"], STATUSES, "status")]
                if req.get("task_id") is not None:
                    found = [f for f in found
                             if f["task_id"] == _identifier(req["task_id"], "task_id", optional=True)]
                found.sort(key=lambda f: (f["updated_at"], f["id"]), reverse=True)
                return {"ok": True, "facts": found, "count": len(found),
                        "schema_version": SCHEMA_VERSION}
            if cmd == "world_fact_save":
                incoming = req.get("fact")
                if not isinstance(incoming, dict):
                    raise WorldFactError("fact 必须为对象。")
                identifier = (_identifier(incoming["id"]) if incoming.get("id") is not None
                              else "wf_" + uuid.uuid4().hex[:12])
                existing = facts.get(identifier)
                expected = req.get("expected_version")
                if expected is not None and (type(expected) is not int or
                                             expected != (existing or {}).get("version", 0)):
                    raise WorldFactError("事实已变更，请重新读取后提交。", "version_conflict")
                stamp = _now()
                fact = copy.deepcopy(existing) if existing else {"source": {}}
                fact.update({k: copy.deepcopy(v) for k, v in incoming.items() if k not in _READ_ONLY})
                fact.update(id=identifier, schema_version=SCHEMA_VERSION, updated_at=stamp,
                            created_at=existing["created_at"] if existing else stamp,
                            version=existing["version"] + 1 if existing else 1)
                fact = _validate(fact, origin="agent_recorded" if actor == "agent" else "user_recorded")
                facts[identifier] = fact
                _write(path, facts)
                return {"ok": True, "fact": fact, "created": existing is None}
            identifier = _identifier(req.get("id"))
            if identifier not in facts:
                raise WorldFactError("未找到世界模型事实。", "not_found")
            fact = facts[identifier]
            if cmd == "world_fact_get":
                return {"ok": True, "fact": fact}
            if cmd == "world_fact_delete":
                del facts[identifier]
                _write(path, facts)
                return {"ok": True, "deleted": True, "id": identifier}
            # 投票：supports/challenges 增减，状态随票数迁移
            if cmd in {"world_fact_support", "world_fact_challenge"}:
                field = "supports" if cmd == "world_fact_support" else "challenges"
                delta = 1 if req.get("delta", 1) in (1, "1") else -1
                current = fact[field] + delta
                if current < 0:
                    raise WorldFactError(f"{field} 计数不能为负。", "invalid_data")
                fact[field] = current
                if fact["status"] != "synthetic":
                    fact["status"] = ("verified" if fact["supports"] > fact["challenges"] and fact["supports"] >= 2
                                      else "challenged" if fact["challenges"] > fact["supports"] else "asserted")
                fact["updated_at"] = _now()
                fact["version"] += 1
                fact = _validate(fact)
                facts[identifier] = fact
                _write(path, facts)
                return {"ok": True, "fact": fact}
            raise WorldFactError("未知世界模型命令。", "unknown_command")
    except WorldFactError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    except OSError:
        return {"ok": False, "error": "世界模型读写失败，请检查数据目录权限或可用空间。", "code": "storage_error"}
