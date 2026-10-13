"""Research notebook tools for the existing AgentRuntime permission boundary.

Register with ``register_tools(runtime)``. The runtime owns authorization and
audit logging; these adapters validate research fields and never grant permission.
No experiment execution or automatic scientific verification is performed.
"""
from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

from . import research_notebook

if TYPE_CHECKING:
    from .agent_runtime import AgentRuntime


_DESCRIPTIONS = {
    "id": "Existing notebook record ID for partial updates; omit to generate a new record ID.",
    "kind": "hypothesis for a falsifiable idea, experiment for its method and observations.",
    "title": "Short descriptive title; required for a new record.",
    "question": "Research question; required for a new record.",
    "prediction": "Testable prediction, not an observed result or a verified conclusion.",
    "method": "Experimental protocol, controls, datasets, seeds, and relevant conditions.",
    "metrics": "Measures, units, baselines and decision criteria as text.",
    "result": "Actual supplied or observed output. Never substitute plans or predictions; required for completed.",
    "limitations": "Known weaknesses, failure modes, scope limits and unresolved uncertainty.",
    "next_action": "Concrete next research action without claiming it has run.",
    "task_id": "Actual user/workspace task ID; never invent one. When id is omitted, existing session task context may supply this field.",
    "status": "planned means not run; running means in progress; completed requires actual result; failed preserves failure.",
    "synthetic": "True for simulated or demo results; never hide synthetic provenance.",
    "observations": "Observed events and failed attempts as strings; replaces the array when provided.",
    "evidence": "Explicit supports/challenges/context assertions with source identity, verbatim quote and applicable scope.",
    "related_ids": "Existing notebook IDs linked to this record; cannot include itself.",
}


def _object(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def _schemas() -> dict[str, dict]:
    stored = research_notebook.schema()["schema"]
    fields = {key: copy.deepcopy(rule) for key, rule in stored["properties"].items()
              if not rule.get("readOnly")}
    for key, rule in fields.items():
        rule["description"] = _DESCRIPTIONS[key]
    record = _object(fields)
    # New records need title/question; updates may carry only id + changed fields.
    # The persistence service checks completion after merging existing content.
    record["anyOf"] = [{"required": ["id"]}, {"required": ["title", "question"]}]
    return {
        "list": _object({key: copy.deepcopy(fields[key]) for key in ("kind", "status", "task_id")}),
        "get": _object({"id": copy.deepcopy(fields["id"])}, ["id"]),
        "save": _object({"record": record,
                         "expected_version": {"type": "integer", "minimum": 0,
                             "description": "Optional last observed version; reject stale writes. Use 0 for a new ID."}},
                        ["record"]),
    }


def _strip_internal(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_internal(item) for key, item in value.items()
                if not isinstance(key, str) or not key.startswith("__")}
    if isinstance(value, list):
        return [_strip_internal(item) for item in value]
    return copy.deepcopy(value)


def _shape_errors(value: Any, rule: dict, path: str = "arguments") -> list[str]:
    """Validate nested tool shapes; runtime's own validator covers top-level args.

    This implements only the schema keywords used by this adapter. The notebook
    service remains the authority for references, DOI/URL rules and merged state.
    """
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    want = rule.get("type")
    if want in types and (not isinstance(value, types[want]) or (want == "integer" and isinstance(value, bool))):
        return [f"{path} 必须为 {want}。"]
    errors = []
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path} 取值不在允许范围内。")
    if isinstance(value, str):
        if len(value.strip()) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", len(value)):
            errors.append(f"{path} 长度不符合要求。")
        if "pattern" in rule and not re.search(rule["pattern"], value):
            errors.append(f"{path} 格式不符合要求。")
    if type(value) is int and value < rule.get("minimum", value):
        errors.append(f"{path} 小于允许最小值。")
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        errors.extend(f"{path} 缺少 {key}。" for key in rule.get("required", []) if key not in value)
        if rule.get("additionalProperties") is False:
            errors.extend(f"{path} 不接受字段 {key}。" for key in value if key not in properties)
        for key, child in value.items():
            if key in properties:
                errors.extend(_shape_errors(child, properties[key], f"{path}.{key}"))
    if isinstance(value, list):
        if len(value) > rule.get("maxItems", len(value)):
            errors.append(f"{path} 项目过多。")
        if "items" in rule:
            for index, child in enumerate(value):
                errors.extend(_shape_errors(child, rule["items"], f"{path}[{index}]"))
    if rule.get("anyOf") and not any(not _shape_errors(value, branch, path) for branch in rule["anyOf"]):
        errors.append(f"{path} 缺少必要身份字段，或新记录缺少 title/question。")
    return errors


def register_tools(runtime: AgentRuntime) -> None:
    """Register list/get as read and save as write; never alter runtime policy."""
    schemas = _schemas()

    def handler(command: str):
        def run(arguments: dict[str, Any]) -> dict[str, Any]:
            clean = _strip_internal(arguments)
            problems = _shape_errors(clean, schemas[command])
            if problems:
                return {"ok": False, "error": "invalid_arguments", "problems": problems,
                        "executed": False}
            # _execute_call overwrites this field with its real session ID.
            session_id = arguments.get("__session_id", "")
            if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
                session_id = ""
            if command == "save":
                record = clean["record"]
                if not record.get("id") and "task_id" not in record and session_id:
                    task_id = runtime._workspaces.get(session_id, {}).get("task_id")
                    if task_id:
                        # Use only an existing session context value; never use a
                        # session ID or generated stand-in as a workflow task ID.
                        problems = _shape_errors(task_id, schemas["list"]["properties"]["task_id"], "workspace.task_id")
                        if problems:
                            return {"ok": False, "error": "invalid_task_context", "problems": problems,
                                    "executed": False}
                        record["task_id"] = task_id
            result = research_notebook.handle({"cmd": "notebook_" + command, **clean}, actor="agent")
            return {**result, "execution": {"actor": "agent_tool", "session_id": session_id,
                                            "review_status": "not_reviewed"}}
        return run

    runtime._register("notebook.list", "read",
        "List persistent hypotheses and experiments, optionally filtered by kind/status/task_id; no verification implied.",
        handler("list"), schemas["list"])
    runtime._register("notebook.get", "read",
        "Read a notebook record with evidence sources, scope, synthetic flags, limitations and provenance.",
        handler("get"), schemas["get"])
    runtime._register("notebook.save", "write",
        "Save or partially update a hypothesis/experiment through write approval. planned is a proposal; completed "
        "requires an actual supplied or observed result. Never invent results, source IDs, quotes or novelty. "
        "Records are agent_recorded, not human-reviewed; scientific verification is not_performed. "
        "Do not send provenance, timestamps, version or review flags. Arrays replace their previous value.",
        handler("save"), schemas["save"])
