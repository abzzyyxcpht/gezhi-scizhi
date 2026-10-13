"""技能包系统（对标 Claude Code Skills / Anthropic Agent Skills）：skills/<名>/SKILL.md。

渐进式披露三级结构：
- L1 元数据（name + description）：系统提示词只放索引表（prompts.build 合流），
  每个已启用技能一行，正文不进系统提示；
- L2 正文：技能被触发时由模型调用 skills.load 工具按需加载（load_skill_body）；
- L3 附带资源（scripts/references）：正文被加载后按其内部指引再读取。

启用状态持久化在 data/settings.json 的 skills_active 键；/skills 启停语义不变。
无 LLM 时技能静默待命——规则路径不受影响。
"""
from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any

from . import config

SKILLS_DIR = config.ROOT_DIR / "skills"

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _parse_skill_md(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _FRONT.match(text)
    if not m:
        return None
    # Comments, quoted scalars and folded descriptions are real YAML. Splitting
    # lines at ':' lets `context: fork # note` bypass the unsupported-mode guard.
    # Keep YAML lazy so bridge startup does not import it until skills are read.
    import yaml
    try:
        meta = yaml.load(m.group(1), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    for key in ("name", "description", "argument-hint", "context"):
        if key in meta and not isinstance(meta[key], str):
            return None
    stages = meta.get("stages", "")
    if isinstance(stages, list) and all(isinstance(stage, str) for stage in stages):
        stages = ", ".join(stages)
    if not isinstance(stages, str):
        return None

    def _flag(value: Any) -> bool:
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1", "on"}:
                return True
            if normalized in {"false", "no", "0", "off"}:
                return False
        raise ValueError("技能调用标志必须是布尔值")

    try:
        disable_model = _flag(meta.get("disable-model-invocation", False))
        user_invocable = _flag(meta.get("user-invocable", True))
    except ValueError:
        return None
    return {"name": meta.get("name", "").strip() or path.parent.name,
            "description": meta.get("description", ""),
            "stages": stages,
            "disable_model_invocation": disable_model,
            "user_invocable": user_invocable,
            "argument_hint": meta.get("argument-hint", ""),
            "context": meta.get("context", "").strip(),
            "source": str(path.resolve()),
            "body": m.group(2).strip()}


def list_skills() -> list[dict[str, Any]]:
    """全部技能包（含 active 标记）。"""
    from . import settings
    preferences = settings.load()
    names = set(preferences.get('skills_active') or [])
    disabled = set(preferences.get('skills_disabled') or [])
    out: list[dict[str, Any]] = []
    if SKILLS_DIR.exists():
        for p in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            s = _parse_skill_md(p)
            if s:
                s["active"] = s["name"] in names
                s['mode'] = 'off' if s['name'] in disabled else 'on' if s['active'] else 'auto'
                s['invocation_status'] = ('disabled' if s['mode'] == 'off' else
                    'manual_only' if s.get('disable_model_invocation') else
                    'unsupported' if s.get('context') == 'fork' else 'available')
                out.append(s)
    return out


def active_names() -> list[str]:
    from . import settings
    return [n for n in (settings.load().get("skills_active") or [])
            if isinstance(n, str)]


def set_active(name: str, on: bool) -> list[str]:
    """启停技能并持久化，返回最新的启用清单。"""
    from . import settings
    s = settings.load()
    active = [n for n in (s.get("skills_active") or []) if n != name]
    if on:
        active.append(name)
    disabled = {n for n in s.get("skills_disabled", []) if isinstance(n, str)}
    disabled.discard(name) if on else disabled.add(name)
    settings.save({"skills_active": active, "skills_disabled": sorted(disabled)})
    return active


def active_bodies() -> list[tuple[str, str]]:
    """启用中的技能 → [(技能名, 正文)]（供 prompts.build 合流）。"""
    names = set(active_names())
    return [(s["name"], s["body"]) for s in list_skills() if s["name"] in names]


DEFAULT_CONTEXT_BUDGET = 6000
# 单技能正文（L2）最大字符数；skills.load 与 load_skill_body 超出即截断并标注。
SKILL_BODY_MAX_CHARS = 6000
# 技能索引行（L1）中单行描述的最大字符数，避免超长 description 撑爆系统提示。
INDEX_DESC_MAX_CHARS = 160


def index_line(skill: dict[str, Any]) -> str:
    """L1 索引行：`- name：描述（何时用）`，仅进系统提示词索引表。"""
    desc = str(skill.get("description") or "").strip().replace("\n", " ")
    if len(desc) > INDEX_DESC_MAX_CHARS:
        desc = desc[:INDEX_DESC_MAX_CHARS] + "…"
    return f"- {skill['name']}：{desc}"


def load_skill_body(name: str) -> str:
    """读取技能正文（剥离 frontmatter），超出 SKILL_BODY_MAX_CHARS 截断并标注。

    渐进式披露的 L2 入口：仅加载被触发技能的 SKILL.md 正文，不进系统提示。
    技能不存在时抛 ValueError（由工具层转成工具错误回喂模型）。
    """
    key = str(name or "").strip()
    for skill in list_skills():
        if skill["name"] == key:
            body = str(skill.get("body") or "")
            if len(body) > SKILL_BODY_MAX_CHARS:
                body = body[:SKILL_BODY_MAX_CHARS] + "\n\n[正文超出上限，已截断]"
            return body
    raise ValueError(f"技能不存在：{key}")


def load_skill_for_tool(name: str, *, cursor: int = 0,
                        max_chars: int = SKILL_BODY_MAX_CHARS, sha256: str | None = None) -> dict[str, Any]:
    """Load a skill body only when the current settings permit model loading.

    ``cursor`` is a character offset, so callers can continue a bounded body
    without silently losing its tail.  A skill that is explicitly disabled or
    marked ``disable-model-invocation`` is visible in the inspector but cannot
    be read through the model tool.
    """
    key = str(name or "").strip()
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        return {"ok": False, "error": "cursor 无效", "status": "invalid"}
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        return {"ok": False, "error": "max_chars 无效", "status": "invalid"}
    skill = next((s for s in list_skills() if s["name"] == key), None)
    if skill is None:
        return {"ok": False, "error": f"技能不存在：{key}", "status": "missing"}
    from . import settings
    disabled = set(settings.load().get("skills_disabled") or [])
    if key in disabled:
        return {"ok": False, "error": f"技能已停用：{key}", "status": "disabled",
                "name": key, "source": skill.get("source", "")}
    if skill.get("disable_model_invocation"):
        return {"ok": False, "error": f"技能禁止模型自动调用：{key}", "status": "manual_only",
                "name": key, "source": skill.get("source", "")}
    if skill.get('context') == 'fork':
        return {"ok": False, "error": "该技能要求独立上下文执行，当前加载器尚不支持", "status": "unsupported", "name": key}
    body = str(skill.get("body") or "")
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    if sha256 is not None and sha256 != digest:
        return {'ok': False, 'error': '技能正文已改变，请从 cursor=0 重新读取', 'status': 'changed', 'name': key}
    if cursor and sha256 is None:
        return {'ok': False, 'error': '续读须提供上页 sha256，避免混用不同版本', 'status': 'invalid', 'name': key}
    if cursor > len(body):
        return {"ok": False, "error": "cursor 超出技能正文", "status": "invalid",
                "name": key, "source": skill.get("source", "")}
    chunk = body[cursor:cursor + min(max_chars, SKILL_BODY_MAX_CHARS)]
    end = cursor + len(chunk)
    return {"ok": True, "name": key, "body": chunk,
            "source": skill.get("source", ""), "status": "included", "sha256": digest,
            "cursor": cursor, "next_cursor": end if end < len(body) else None,
            "truncated": end < len(body), "total_chars": len(body)}


def register_tools(runtime: Any) -> None:
    """注册 skills.load 工具（read 能力）：按需加载技能正文，实现 L2 披露。"""

    def _skills_load(arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        result = load_skill_for_tool(name, cursor=arguments.get("cursor", 0),
                                     max_chars=arguments.get("max_chars", SKILL_BODY_MAX_CHARS), sha256=arguments.get('sha256'))
        if result.get("ok"):
            result["note"] = (f"已加载技能 {name} 的部分正文；使用 next_cursor 与 sha256 续读后再执行。"
                              if result['truncated'] else f"已加载技能 {name} 的正文；相关资源再按需读取。")
        return result

    runtime._register(
        "skills.load", "read",
        "按需加载已启用技能的 SKILL.md 正文（渐进式披露）：当任务匹配系统提示中"
        "技能索引的某行描述时，先用本工具取回正文，再按其指引执行。",
        _skills_load,
         {"type": "object", "properties": {"name": {"type": "string"},
                                               "cursor": {"type": "integer", "minimum": 0},
                                               "max_chars": {"type": "integer", "minimum": 1},
                                               "sha256": {"type": "string"}},
         "required": ["name"], "additionalProperties": False})


def set_mode(name: str, mode: str) -> dict[str, Any]:
    """Choose explicit on/off or restore stage-based automatic selection."""
    if mode not in {"auto", "on", "off"}:
        raise ValueError("技能模式须为 auto/on/off")
    if name not in {row["name"] for row in list_skills()}:
        raise ValueError("技能不存在")
    from . import settings
    with settings._LOCK:
        values = settings.load()
        active, disabled = set(values.get("skills_active", [])), set(values.get("skills_disabled", []))
        active.discard(name)
        disabled.discard(name)
        if mode == "on":
            active.add(name)
        elif mode == "off":
            disabled.add(name)
        settings.save({"skills_active": sorted(active), "skills_disabled": sorted(disabled)})
    return {"name": name, "mode": mode}

_STAGE_ALIASES = {"plan": "planning", "research": "planning", "pipeline": "planning",
                  "lit_review": "literature", "extract": "extraction", "review": "audit",
                  "critic": "audit", "synthesis": "audit", "stats": "experiment"}


def select_for_stage(stage: str | None = None,
                     context_budget: int = DEFAULT_CONTEXT_BUDGET) -> dict[str, Any]:
    """Bounded, stable selection; explicit enablement is never mutated by selection.

    The budget is characters for the L1 index lines (name + one-line description),
    not model tokens and not full skill bodies. Deferred explicit selections are
    reported, not silently disabled.
    No stage means legacy behavior: select only explicitly enabled skills.
    """
    from . import settings
    if isinstance(context_budget, bool) or not isinstance(context_budget, int) or context_budget < 0:
        raise ValueError("context_budget 必须是非负整数字符预算")
    stage = _STAGE_ALIASES.get(stage or "", stage or "")
    available = {s["name"]: s for s in list_skills()}
    explicit = set(active_names())
    disabled = set(settings.load().get("skills_disabled") or [])
    candidates, excluded = [], []
    for name, skill in available.items():
        if skill.get('invocation_status') != 'available':
            excluded.append({**skill, 'status': skill['invocation_status']})
            continue
        stages = {s.strip() for s in skill.get("stages", "").strip("[]").split(",") if s.strip()}
        if name in explicit:
            candidates.append({**skill, "reason": "explicit"})
        elif stage and stage in stages and name not in disabled:
            candidates.append({**skill, "reason": "stage"})
    candidates.sort(key=lambda s: (s["reason"] != "explicit", s["name"]))
    selected, deferred = [], []
    used = 0
    for skill in candidates:
        cost = len(index_line(skill)) + 1
        item = {**skill, "cost_chars": cost}
        if used + cost <= context_budget:
            selected.append(item)
            used += cost
        else:
            deferred.append({"name": skill["name"], "reason": skill["reason"],
                             "status": "budget_deferred", "source": skill.get('source', ''), "cost_chars": cost})
    return {"stage": stage, "selected": selected, "deferred": deferred,
            "missing": sorted(explicit - available.keys()),
            "disabled": sorted(disabled), "excluded": excluded,
            "budget": {"unit": "characters", "limit_chars": context_budget, "used_chars": used}}
