"""Independent desktop editor for connections, model profiles and stage routes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from .. import model_registry
from ..model_protocols import capabilities
from .theme import palette, style_inputs


_NEW = "@new"
_STAGES = {"default": "默认用途", "fast": "快速处理", "strong": "深入处理", "planning": "研究规划",
           "extraction": "证据抽取", "synthesis": "综述综合", "review": "复核审查"}
_AUTH = {"auto": "按协议选择", "bearer": "Bearer token", "anthropic": "Anthropic API key",
         "gemini": "Gemini API key", "none": "无需认证"}
_NUMBER_LABELS = {"thinking_budget": "思考预算", "max_output_tokens": "总输出上限", "context_window": "上下文上限"}


def _options(values: dict[str, str]) -> list:
    return [ft.dropdown.Option(key, label) for key, label in values.items()]


def _label(row: dict[str, Any]) -> str:
    identifier = str(row.get("id") or "")
    label = str(row.get("label") or identifier)
    label = label if len(label) <= 32 else label[:31] + "…"
    return label if label == identifier else f"{label} · {identifier[:32]}"


def _note(text: str) -> ft.Text:
    # 说明文字同样要有高度边界：resize() 只给少数状态 Text 设宽度，
    # 静态长注释在窄栏会无限折行增高
    return ft.Text(text, size=12, color=palette.TEXT_2, max_lines=4,
                   overflow=ft.TextOverflow.ELLIPSIS)


class ModelSettingsPanel:
    """Mount ``content`` and call ``resize`` with available inner width.

    Construction, refresh and editing are local only. ``on_saved`` receives no
    arguments and runs after a successful persistence operation.
    """

    def __init__(self, page: Any, on_saved: Callable[[], None] | None = None):
        self.page = page
        self.on_saved = on_saved or (lambda: None)
        self._width = 640.0
        self._initializing = True
        self._refreshing = False
        self._snapshot: dict[str, Any] = {"connections": [], "profiles": [], "routes": {}, "resolved": {}}
        catalog = capabilities()
        protocols = {p["id"]: p["label"] for p in catalog["protocols"]}
        styles = {p["id"]: p["label"] for p in catalog["thinking_styles"]}
        efforts = {p["id"]: p["label"] for p in catalog["efforts"]}

        self.view = ft.Dropdown(label="配置内容", value="connections", options=_options({
            "connections": "服务连接", "profiles": "模型配置", "routes": "用途路由"}), on_select=self._select_view)
        self.summary = _note("")
        self.summary.max_lines = 3
        self.summary.overflow = ft.TextOverflow.ELLIPSIS

        self.connection_selector = ft.Dropdown(label="选择连接", value=_NEW, on_select=self._select_connection)
        self.connection_fields = {
            "id": ft.TextField(label="连接标识", hint_text="如 remote 或 local_reader"),
            "label": ft.TextField(label="显示名称", hint_text="便于识别的名称"),
            "protocol": ft.Dropdown(label="接口协议", value="chat_completions", options=_options(protocols)),
            "auth_style": ft.Dropdown(label="认证方式", value="auto", options=_options(_AUTH)),
            "base_url": ft.TextField(label="服务基础地址", hint_text="服务端提供的 HTTP(S) 地址"),
            "api_key": ft.TextField(label="API key", password=True, can_reveal_password=True,
                                    hint_text="编辑时留空，保留原有密钥"),
        }
        self.key_status = _note("尚未保存密钥")
        self.clear_key = ft.Switch(label="清除已保存密钥", value=False, on_change=self._toggle_clear_key)
        self.connection_save = ft.FilledButton("保存连接", on_click=self._save_connection)
        self.connection_delete = ft.OutlinedButton("删除连接", disabled=True, on_click=self._delete_connection)
        self.connection_view = ft.Column([
            self.connection_selector,
            _note("先保存服务连接，再为它添加模型。默认连接在上方基础设置中维护。"),
            ft.Row(list(self.connection_fields.values()), wrap=True, spacing=12, run_spacing=12),
            self.key_status, self.clear_key,
            ft.Row([self.connection_save, self.connection_delete], wrap=True, spacing=8),
        ], spacing=12)

        self.profile_selector = ft.Dropdown(label="选择模型配置", value=_NEW, on_select=self._select_profile)
        self.profile_fields = {
            "id": ft.TextField(label="模型配置标识", hint_text="如 reader 或 critic"),
            "label": ft.TextField(label="显示名称", hint_text="便于识别的名称"),
            "connection_id": ft.Dropdown(label="使用连接", value="legacy"),
            "model": ft.TextField(label="服务端模型名称", hint_text="填写此连接提供的准确模型名称"),
            "thinking_style": ft.Dropdown(label="思考控制方式", value="auto", options=_options(styles)),
            "reasoning_effort": ft.Dropdown(label="思考强度", value="auto", options=_options(efforts)),
        }
        for key, label in _NUMBER_LABELS.items():
            self.profile_fields[key] = ft.TextField(label=label, value=str(model_registry.PROFILE_DEFAULTS[key]),
                                                   keyboard_type=ft.KeyboardType.NUMBER)
        self.prompt_cache = ft.Switch(label="启用显式缓存提示", value=True)
        self.profile_save = ft.FilledButton("保存模型配置", on_click=self._save_profile)
        self.profile_delete = ft.OutlinedButton("删除模型配置", disabled=True, on_click=self._delete_profile)
        self.profile_view = ft.Column([
            self.profile_selector,
            ft.Row(list(self.profile_fields.values()), wrap=True, spacing=12, run_spacing=12),
            _note("模型名称不能证明其思考能力。已知不兼容组合会在请求前提示，其余由服务端确认；保存不会发送模型请求。"),
            _note("auto 保留服务默认；off 请求真正关闭。预算方式下低 / 中 / 高使用配置预算的 1/4、1/2、全部，并受模型最小预算约束。总输出上限包含思考与正文。"),
            self.prompt_cache,
            _note("该开关只控制显式缓存提示；平台自动缓存由服务端决定，命中率以返回用量为准。"),
            ft.Row([self.profile_save, self.profile_delete], wrap=True, spacing=8),
        ], spacing=12, visible=False)

        self.route_stage = ft.Dropdown(label="用途", value="default", options=_options(_STAGES), on_select=self._select_route)
        self.route_profile = ft.Dropdown(label="使用模型配置", value="legacy")
        self.route_status = _note("")
        self.route_save = ft.FilledButton("保存此用途路由", on_click=self._save_route)
        self.route_view = ft.Column([
            _note("为不同研究阶段选择模型。每次只保存当前用途，其他路由保持不变。"),
            ft.Row([self.route_stage, self.route_profile], wrap=True, spacing=12, run_spacing=12),
            self.route_status, self.route_save,
        ], spacing=12, visible=False)
        self.content = ft.Column([
            ft.Text("多连接与模型路由", size=16, color=palette.TEXT, weight=ft.FontWeight.W_600),
            self.summary, self.view, self.connection_view, self.profile_view, self.route_view,
        ], spacing=14)
        self.refresh()
        self._initializing = False

    def _update(self) -> None:
        if not self._initializing and not self._refreshing:
            self.page.update()

    def _error(self, message: str) -> None:
        for key, label in _NUMBER_LABELS.items():
            message = message.replace(key, label)
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("配置未保存", color=palette.TEXT),
            content=ft.Text(message, size=13, color=palette.TEXT, width=min(self._width, 480)),
            actions=[ft.TextButton("关闭", on_click=lambda _e: self.page.pop_dialog())],
        ))

    def _saved(self, message: str) -> None:
        self.refresh()
        self.on_saved()
        self.page.show_dialog(ft.SnackBar(ft.Text(message), bgcolor=palette.TEXT))

    def _select_view(self, _event: Any = None) -> None:
        chosen = self.view.value
        self.connection_view.visible = chosen == "connections"
        self.profile_view.visible = chosen == "profiles"
        self.route_view.visible = chosen == "routes"
        self._update()

    def _select_connection(self, _event: Any = None) -> None:
        row = next((c for c in self._snapshot["connections"] if c["id"] == self.connection_selector.value), {})
        defaults = {"id": "", "label": "", "protocol": "chat_completions", "auth_style": "auto", "base_url": ""}
        for key, field in self.connection_fields.items():
            field.value = "" if key == "api_key" else row.get(key, defaults.get(key, ""))
        self.connection_fields["id"].read_only = bool(row)
        self.connection_fields["api_key"].disabled = False
        self.clear_key.value = False
        self.clear_key.disabled = not bool(row)
        self.connection_delete.disabled = not bool(row)
        has_key = bool(row.get("has_api_key"))
        self.key_status.value = "密钥已保存；输入框留空会保留。" if has_key else "当前未保存密钥；本地无认证服务可留空。"
        self._update()

    def _toggle_clear_key(self, _event: Any = None) -> None:
        self.connection_fields["api_key"].disabled = bool(self.clear_key.value)
        if self.clear_key.value:
            self.connection_fields["api_key"].value = ""
        self._update()

    def _select_profile(self, _event: Any = None) -> None:
        row = next((p for p in self._snapshot["profiles"] if p["id"] == self.profile_selector.value), {})
        defaults = {**model_registry.PROFILE_DEFAULTS, "id": "", "label": "", "model": "", "connection_id": "legacy"}
        for key, field in self.profile_fields.items():
            value = row.get(key, defaults[key])
            field.value = str(value) if key in _NUMBER_LABELS else value
        self.profile_fields["id"].read_only = bool(row)
        self.prompt_cache.value = bool(row.get("prompt_cache", True))
        self.profile_delete.disabled = not bool(row)
        self._update()

    def _select_route(self, _event: Any = None) -> None:
        stage = self.route_stage.value or "default"
        explicit = self._snapshot.get("routes", {}).get(stage)
        resolved = self._snapshot.get("resolved", {}).get(stage) or {}
        self.route_profile.value = explicit or resolved.get("profile_id", "legacy")
        model = str(resolved.get("model") or "尚未填写模型")[:80]
        connection = str(resolved.get("connection_id") or "legacy")[:40]
        prefix = "已单独配置" if explicit else "尚未单独配置，当前沿用"
        self.route_status.value = f"{prefix}：{model} · {connection}。保存后使用所选配置。"
        self._update()

    def refresh(self) -> None:
        """Reload saved records without any model/network request or key echo."""
        try:
            self._snapshot = model_registry.describe()
        except (ValueError, OSError):
            self._error("无法读取模型配置。请检查设置文件中的连接、模型与用途引用是否有效。")
            return
        connections, profiles = self._snapshot["connections"], self._snapshot["profiles"]
        connection_labels = {c["id"]: _label(c) for c in connections}
        profile_labels = {p["id"]: _label(p) for p in profiles}
        self.connection_selector.options = _options({_NEW: "新增服务连接", **connection_labels})
        self.profile_selector.options = _options({_NEW: "新增模型配置", **profile_labels})
        self.profile_fields["connection_id"].options = _options({"legacy": "默认连接 · 基础设置", **connection_labels})
        self.route_profile.options = _options({"legacy": "默认模型 · 基础设置", **profile_labels})
        if self.connection_selector.value not in connection_labels:
            self.connection_selector.value = _NEW
        if self.profile_selector.value not in profile_labels:
            self.profile_selector.value = _NEW
        self._refreshing = True
        try:
            self._select_connection()
            self._select_profile()
            self._select_route()
        finally:
            self._refreshing = False
        resolved = self._snapshot.get("resolved", {}).get("default") or {}
        model = str(resolved.get("model") or "尚未填写")[:80]
        self.summary.value = f"{len(connections)} 个服务连接 · {len(profiles)} 个模型配置；默认用途：{model}。"
        style_inputs(self.content)
        self.resize(self._width)
        self._update()

    def resize(self, width: float) -> None:
        """Fit fields into one or two columns without rebuilding their values."""
        self._width = max(160.0, float(width))
        full = self._width
        half = (full - 12) / 2 if full >= 560 else full
        self.content.width = full
        self.view.width = self.connection_selector.width = self.profile_selector.width = full
        for key, field in self.connection_fields.items():
            field.width = full if key in {"base_url", "api_key"} else half
        for key, field in self.profile_fields.items():
            field.width = full if key == "model" else half
        self.route_stage.width = self.route_profile.width = half
        for control in (self.summary, self.key_status, self.route_status, self.clear_key, self.prompt_cache):
            control.width = full

    def _save_connection(self, _event: Any = None) -> None:
        row = {key: str(field.value or "").strip() for key, field in self.connection_fields.items() if key != "api_key"}
        key_value = str(self.connection_fields["api_key"].value or "").strip()
        if self.clear_key.value:
            row["api_key"] = ""
        elif key_value:
            row["api_key"] = key_value
        try:
            model_registry.save_connection(row)
        except (ValueError, OSError) as exc:
            self._error(str(exc) if isinstance(exc, ValueError) else "无法写入设置文件，请检查目录权限。")
            return
        self.connection_selector.value = row["id"]
        self._saved("服务连接已保存")

    def _save_profile(self, _event: Any = None) -> None:
        try:
            row: dict[str, Any] = {}
            for key, field in self.profile_fields.items():
                value = str(field.value or "").strip()
                if key in _NUMBER_LABELS:
                    try:
                        row[key] = int(value)
                    except ValueError:
                        raise ValueError(f"{_NUMBER_LABELS[key]}请填写整数。") from None
                else:
                    row[key] = value
            row["prompt_cache"] = bool(self.prompt_cache.value)
            model_registry.save_profile(row)
        except (ValueError, OSError) as exc:
            self._error(str(exc) if isinstance(exc, ValueError) else "无法写入设置文件，请检查目录权限。")
            return
        self.profile_selector.value = row["id"]
        self._saved("模型配置已保存")

    def _save_route(self, _event: Any = None) -> None:
        try:
            model_registry.set_route(self.route_stage.value or "default", self.route_profile.value or "legacy")
        except (ValueError, OSError) as exc:
            self._error(str(exc) if isinstance(exc, ValueError) else "无法写入设置文件，请检查目录权限。")
            return
        self._saved("当前用途路由已保存")

    def _delete_connection(self, _event: Any = None) -> None:
        if self.connection_selector.value == _NEW:
            self._error("请先选择已保存的服务连接。")
            return
        try:
            model_registry.delete_connection(self.connection_selector.value)
        except (ValueError, OSError) as exc:
            self._error(str(exc) if isinstance(exc, ValueError) else "无法写入设置文件，请检查目录权限。")
            return
        self._saved("服务连接已删除")

    def _delete_profile(self, _event: Any = None) -> None:
        if self.profile_selector.value == _NEW:
            self._error("请先选择已保存的模型配置。")
            return
        try:
            model_registry.delete_profile(self.profile_selector.value)
        except (ValueError, OSError) as exc:
            self._error(str(exc) if isinstance(exc, ValueError) else "无法写入设置文件，请检查目录权限。")
            return
        self._saved("模型配置已删除")
