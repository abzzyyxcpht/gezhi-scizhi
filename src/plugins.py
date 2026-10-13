"""插件系统：plugins/ 目录自动发现，一个 Python 文件即一个插件。

插件契约——模块级 PLUGIN 字典：
    PLUGIN = {
        "name": "bibtex",            # 全局唯一（小写）
        "version": "1.0",
        "description": "…",
        "commands": {                # → REPL 斜杠命令（引擎侧执行）
            "bib": {"help": "…", "run": fn},   # fn(args: str, ctx|None) -> str
        },
        "actions": {                 # → 工作流动作（workflows/*.yaml 直接引用）
            "export_bibtex": fn,     # fn(ctx, params) -> dict（引擎动作签名）
        },
    }
发现顺序：plugins/*.py 与 plugins/*/plugin.py（下划线开头跳过）；
导入失败的插件记录错误并跳过，绝不炸主程序。重名先到先得。
动作名约定自带命名空间（如 export_bibtex / plug_xxx），避免与内置冲突。
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any
import threading

from . import config
from .orchestrator.engine import ACTIONS

PLUGINS_DIR = config.ROOT_DIR / "plugins"
PLUGINS: dict[str, dict[str, Any]] = {}
_ERRORS: list[str] = []
_SIGNATURE = None
_LOCK = threading.RLock()
_OWNED_ACTIONS: dict[str, Any] = {}


def _static_metadata(path: Path) -> dict[str, Any]:
    """Read identity without importing code; dynamic names require plugin.json."""
    manifest = path.parent / "plugin.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("plugin.json 必须为对象")
    else:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        values = []
        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == "PLUGIN" for target in statement.targets):
                    values.append(statement.value)
            elif isinstance(statement, ast.AnnAssign):
                if isinstance(statement.target, ast.Name) and statement.target.id == "PLUGIN":
                    values.append(statement.value)
        if len(values) != 1 or not isinstance(values[0], ast.Dict):
            raise ValueError("需静态元数据：声明 PLUGIN 字典或提供 plugin.json")
        fields = {}
        for key, value in zip(values[0].keys, values[0].values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise ValueError("需静态元数据：PLUGIN 不支持动态键或字典展开")
            if key.value in fields:
                raise ValueError("需静态元数据：PLUGIN 存在重复键")
            fields[key.value] = value
        data = {}
        for key in ("name", "version", "description"):
            value = fields.get(key)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                data[key] = value.value
        commands = fields.get("commands")
        data["commands"] = {}
        if isinstance(commands, ast.Dict):
            for key, value in zip(commands.keys, commands.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    help_text = ""
                    if isinstance(value, ast.Dict):
                        for field, text in zip(value.keys, value.values):
                            if (isinstance(field, ast.Constant) and field.value == "help"
                                    and isinstance(text, ast.Constant) and isinstance(text.value, str)):
                                help_text = text.value
                    data["commands"][key.value] = {"help": help_text}
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("需静态元数据：插件 name 必须为非空字符串")
    commands = data.get("commands") or {}
    if isinstance(commands, list):
        commands = {name: {} for name in commands if isinstance(name, str)}
    if not isinstance(commands, dict):
        raise ValueError("静态 commands 必须为对象或命令名数组")
    return {"name": name.strip().lower(),
            "version": data.get("version", "") if isinstance(data.get("version", ""), str) else "",
            "description": data.get("description", "") if isinstance(data.get("description", ""), str) else "",
            "commands": {name: meta if isinstance(meta, dict) else {}
                         for name, meta in commands.items() if isinstance(name, str)},
            "actions": {}, "enabled": False}


def _load_one(path: Path, preferences: dict[str, Any]) -> None:
    try:
        static = _static_metadata(path)
    except Exception as exc:  # noqa: BLE001 —— unsafe identity must never trigger an import
        error = f"{path.name}: {type(exc).__name__}: {exc}"
        _ERRORS.append(error)
        name = path.parent.name if path.stem == "plugin" else path.stem
        PLUGINS.setdefault(name, {"name": name, "enabled": False, "commands": {},
                                  "actions": {}, "error": error, "metadata_required": True})
        return
    name = static["name"]
    if name in PLUGINS:
        _ERRORS.append(f"{path.name}: 插件名 {name} 重复，先到先得")
        return
    PLUGINS[name] = static
    disabled = {item.strip().lower() for item in preferences.get("plugins_disabled", [])
                if isinstance(item, str)}
    if not preferences.get("plugins_enabled", True) or name in disabled:
        return
    before = dict(ACTIONS)
    try:
        spec = importlib.util.spec_from_file_location(
            f"gezhi_plugin_{path.stem}_{abs(hash(str(path))) % 9999}", path)
        if not spec or not spec.loader:
            raise ValueError("无法创建插件模块")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        meta = getattr(mod, "PLUGIN", None)
        if not isinstance(meta, dict) or meta.get("name", "").strip().lower() != name:
            raise ValueError("运行时插件名称与静态元数据不一致")
        commands, actions = meta.get("commands") or {}, meta.get("actions") or {}
        if not isinstance(commands, dict) or not isinstance(actions, dict):
            raise ValueError("commands/actions 必须为对象")
        if any(not isinstance(key, str) or not isinstance(command, dict)
               or not callable(command.get("run")) for key, command in commands.items()):
            raise ValueError("插件命令必须包含可调用 run")
        if any(not isinstance(key, str) or not callable(fn) for key, fn in actions.items()):
            raise ValueError("插件动作必须为命名函数")
    except Exception as e:  # noqa: BLE001 —— 插件故障不炸主程序
        error = f"{path.name}: {type(e).__name__}: {e}"
        static["error"] = error
        _ERRORS.append(error)
        return
    finally:
        # Registration is declarative: failed imports and decorators cannot replace builtins.
        ACTIONS.clear()
        ACTIONS.update(before)
    meta = {**meta, "name": name, "enabled": True}
    PLUGINS[name] = meta
    for aname, fn in actions.items():
        if aname in ACTIONS:
            _ERRORS.append(f"{name}: 动作 {aname} 已注册，保留原动作")
            continue
        ACTIONS[aname] = fn
        _OWNED_ACTIONS[aname] = fn


def load(force: bool = False) -> None:
    """扫描 plugins/ 目录（幂等；force=True 卸载旧动作后重扫）。"""
    with _LOCK:
        _load(force)


def _load(force: bool = False) -> None:
    global PLUGINS, _ERRORS, _SIGNATURE
    from . import settings
    preferences = settings.load()
    signature = (bool(preferences.get("plugins_enabled", True)),
                 tuple(sorted(preferences.get("plugins_disabled", []))), str(PLUGINS_DIR))
    force = force or signature != _SIGNATURE
    if _SIGNATURE == signature and not force:
        return
    if force:
        for aname, fn in _OWNED_ACTIONS.items():
            if ACTIONS.get(aname) is fn:
                ACTIONS.pop(aname, None)
        _OWNED_ACTIONS.clear()
        PLUGINS, _ERRORS = {}, []
    _SIGNATURE = signature
    if not PLUGINS_DIR.exists():
        return
    for p in sorted(PLUGINS_DIR.glob("*.py")):
        if not p.name.startswith("_"):
            _load_one(p, preferences)
    for p in sorted(PLUGINS_DIR.glob("*/plugin.py")):
        if not p.parent.name.startswith("_"):
            _load_one(p, preferences)


def commands() -> list[dict[str, str]]:
    """全部插件命令（供 REPL 动态并入 /help 与分发）。"""
    load()
    out: list[dict[str, str]] = []
    for meta in PLUGINS.values():
        if not meta.get("enabled", True):
            continue
        for cname, c in (meta.get("commands") or {}).items():
            out.append({"name": cname, "plugin": meta["name"],
                        "help": c.get("help", "")})
    return out


def run_command(name: str, args: str = "", task_id: str | None = None) -> str:
    """执行插件命令，返回展示文本；找不到/失败都不抛错（桥层永不炸）。"""
    return run_command_result(name, args, task_id)["output"]


def run_command_result(name: str, args: str = "", task_id: str | None = None) -> dict[str, Any]:
    """结构化命令结果，异常与未知命令都保持 ok=False。"""
    load()
    for meta in PLUGINS.values():
        if not meta.get("enabled", True):
            continue
        c = (meta.get("commands") or {}).get(name)
        if c:
            try:
                ctx = None
                if task_id:
                    from . import service
                    ctx = service.get(task_id)
                output = c["run"](args, ctx)
                if isinstance(output, dict) and output.get("ok") is False:
                    error = str(output.get("error") or output.get("output") or "插件命令执行失败")
                    return {"ok": False, "output": str(output.get("output") or error), "error": error}
                return {"ok": True, "output": str(output if output is not None else "（无输出）")}
            except Exception as e:  # noqa: BLE001
                error = f"插件命令 /{name} 执行失败：{type(e).__name__}: {e}"
                return {"ok": False, "output": error, "error": error}
    error = f"未知插件命令：/{name}（/plugins 查看已装插件）"
    return {"ok": False, "output": error, "error": error}


def summary() -> dict[str, Any]:
    load()
    return {
        "count": len(PLUGINS),
        "names": [{"name": m["name"], "version": m.get("version", ""),
                   "description": m.get("description", ""),
                   "enabled": m.get("enabled", True),
                   "metadata_required": m.get("metadata_required", False),
                   "error": m.get("error", ""),
                   "commands": list((m.get("commands") or {}).keys())}
                  for m in PLUGINS.values()],
        "errors": list(_ERRORS),
    }


def set_enabled(name: str, enabled: bool) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ValueError("插件开关必须为布尔值")
    with _LOCK:
        load()
        if name not in PLUGINS:
            raise ValueError("插件不存在")
        from . import settings
        disabled = set(settings.load().get("plugins_disabled", []))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        settings.save({"plugins_disabled": sorted(disabled)})
        load(force=True)
        return summary()
