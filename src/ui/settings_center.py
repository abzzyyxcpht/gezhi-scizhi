"""Categorized native settings; every editor uses the shared command bridge."""
from __future__ import annotations

import json
import flet as ft

from .. import settings
from ..bridge import handle
from . import theme as ui_theme
from .theme import palette, style_inputs

CATEGORIES = [
    ("general", "通用", "研究默认行为与运行预算"),
    ("appearance", "外观", "阅读密度、字号与动效"),
    ("models", "模型", "连接、推理强度与阶段路由"),
    ("notebook", "科研记录", "假设、证据、实验与反例"),
    ("browser", "浏览器控制", "通过已配置的 MCP 服务操作浏览器"),
    ("computer", "电脑控制", "绑定电脑控制工具，沿用权限审批"),
    ("remote", "远程控制", "手机与局域网设备"),
    ("shortcuts", "快捷键", "桌面工作台的键盘入口"),
    ("memory", "记忆", "可检查、可撤回的长期研究偏好"),
    ("subagents", "Subagent", "职责分工与并行上限"),
    ("plugins", "插件", "本地扩展的启停与重载"),
    ("mcp", "MCP 服务器", "配置、启停与连接诊断"),
    ("skills", "技能", "按阶段加载科研方法"),
    ("commands", "命令", "可复用的研究提示词"),
    ("hooks", "钩子", "记录或阻止匹配的工具事件"),
    ("usage", "使用统计", "模型用量、缓存与统计覆盖率"),
    ("automations", "自动化", "定期研究与有限轮次 Loop"),
]

# The groups are presentation metadata only.  Keeping the canonical category
# list above means callers that provide custom section builders keep working.
CATEGORY_GROUPS = (
    ("工作台", ("general", "appearance", "shortcuts")),
    ("能力", ("notebook", "browser", "computer", "remote", "memory", "subagents")),
    ("集成", ("models", "plugins", "mcp", "skills", "commands", "hooks", "usage", "automations")),
)
_CATEGORY_BY_KEY = {key: (key, label, hint) for key, label, hint in CATEGORIES}


def memory_manager(scope: str = "global", project_id: str = "", role: str = "",
                   projects: list[dict] | None = None) -> dict:
    """Build the scoped memory payload used by the editor and bridge.

    Project scoped memories must use a registered project; role memories may
    optionally be pinned to one. The returned payload omits irrelevant fields
    so a scope change cannot leave stale project or role selectors behind.
    """
    scope = str(scope or "global")
    if scope not in {"global", "project", "role"}:
        raise ValueError("记忆作用域只能为 global、project 或 role")
    rows = [p for p in (projects or []) if isinstance(p, dict) and p.get("id")]
    known = {str(p["id"]): p for p in rows}
    project_id = str(project_id or "")
    role = str(role or "").strip()
    if scope == "project":
        if project_id not in known:
            raise ValueError("项目作用域必须选择已注册项目")
        return {"scope": scope, "project_id": project_id}
    if scope == "role":
        if not role:
            raise ValueError("角色作用域需要填写角色")
        if project_id and project_id not in known:
            raise ValueError("角色记忆的项目必须来自已注册项目")
        return {"scope": scope, "role": role, "project_id": project_id or None}
    return {"scope": "global"}


class SettingsCenter:
    def __init__(self, page, sections=None, on_saved=None):
        self.page, self.sections, self.on_saved = page, sections or {}, on_saved
        self.section = "general"
        self.width = 720
        self.forms: dict[str, dict[str, ft.Control]] = {}
        self.message = ft.Text("", size=12, color=palette.TEXT_2, selectable=True, visible=False)
        self.title = ft.Text("通用", size=22, color=palette.TEXT, weight=ft.FontWeight.W_600)
        self.subtitle = ft.Text("研究默认行为与运行预算", size=12, color=palette.TEXT_2)
        self.main = ft.Column(spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
        self.nav_buttons = {}
        rows = []
        for key, label, _ in CATEGORIES:
            button = ft.TextButton(content=ft.Text(label, size=13, color=palette.TEXT_2),
                width=134, tooltip=label,
                style=ft.ButtonStyle(padding=ft.Padding(12, 8, 12, 8),
                    shape=ft.RoundedRectangleBorder(radius=6),
                    alignment=ft.Alignment(-1, 0), bgcolor='transparent',
                    overlay_color={ft.ControlState.FOCUSED: palette.ACTIVE,
                                   ft.ControlState.HOVERED: palette.HOVER},
                    side={ft.ControlState.FOCUSED: ft.BorderSide(1, palette.ACCENT_TEXT),
                          ft.ControlState.DEFAULT: ft.BorderSide(0, 'transparent')}),
                on_click=lambda e, k=key: self.select(k),
                on_hover=lambda e, k=key: self._paint_nav_button(e, k))
            self.nav_buttons[key] = button
            rows.append(button)
        self._category_rows = rows
        self._category_groups = {}
        self._nav_empty = ft.Text("没有匹配的设置", size=12, color=palette.TEXT_2,
                                  visible=False, selectable=True)
        self.search_empty = self._nav_empty
        grouped_rows = []
        for group, keys in CATEGORY_GROUPS:
            heading = ft.Text(group, size=11, color=palette.TEXT_3, weight=ft.FontWeight.W_600)
            self._category_groups[group] = (heading, tuple(keys))
            grouped_rows.append(heading)
            grouped_rows.extend(self.nav_buttons[key] for key in keys)
        self.nav = ft.Container(width=158, padding=ft.Padding(0, 0, 12, 0),
            border=ft.Border.only(right=ft.BorderSide(1, palette.BORDER)),
            content=ft.Column(grouped_rows, spacing=2, scroll=ft.ScrollMode.AUTO, expand=True))
        self.search_input = ft.TextField(
            label="搜索设置", hint_text="输入中文或 English 关键词",
            text_size=13, expand=True, on_change=self._on_search,
            on_submit=self._select_first_filtered)
        self.clear_search = ft.TextButton("清除", on_click=self._clear_search, visible=False)
        self.search_field = self.search_input
        self.clear_search_button = self.clear_search
        self.search_bar = ft.Row([self.search_input, self.clear_search], spacing=8)
        self.selector = ft.Dropdown(label="设置分类", value="general", visible=True,
            options=[ft.dropdown.Option(key, label) for key, label, _ in CATEGORIES],
            on_select=lambda e: self.select(self.selector.value))
        self.body = ft.Container(content=self.main, expand=True)
        self.content = ft.Column([self.search_bar, self.search_empty, self.selector, ft.Row([self.nav, self.body], spacing=22, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START)], spacing=14, expand=True)
        self._filtered_keys = [key for key, _, _ in CATEGORIES]
        self.select("general", update=False)
        self.resize(720)

    def _on_search(self, e=None) -> None:
        """Filter category affordances without rebuilding the active editor."""
        if e is not None and getattr(e, "control", None) is not None:
            self.search_input.value = e.control.value or ""
        self._filter_categories(self.search_input.value)
        self.page.update()

    def _category_matches(self, query: str, key: str, label: str, hint: str, group: str) -> bool:
        haystack = " ".join((key, label, hint, group)).casefold()
        # Search terms are ANDed, which keeps a short Chinese or English query
        # useful while still allowing e.g. ``appearance font``.
        return all(term in haystack for term in str(query or "").casefold().split())

    def _filter_categories(self, query: str = "") -> list[str]:
        query = str(query or "").strip()
        matches: list[str] = []
        for group, keys in CATEGORY_GROUPS:
            for key in keys:
                _, label, hint = _CATEGORY_BY_KEY[key]
                if not query or self._category_matches(query, key, label, hint, group):
                    matches.append(key)
        self._filtered_keys = matches
        self.clear_search.visible = bool(query)
        for group, (heading, keys) in self._category_groups.items():
            visible = any(key in matches for key in keys)
            heading.visible = visible
            for key in keys:
                self.nav_buttons[key].visible = key in matches
        self._nav_empty.visible = not matches
        self.selector.options = [ft.dropdown.Option(key, _CATEGORY_BY_KEY[key][1]) for key in matches]
        self.selector.value = self.section if self.section in matches else None
        self.selector.disabled = not matches
        return matches

    def _clear_search(self, e=None) -> None:
        self.search_input.value = ""
        self._filter_categories("")
        self.page.update()

    def _select_first_filtered(self, e=None) -> None:
        if self._filtered_keys:
            self.select(self._filtered_keys[0])

    def _paint_nav_button(self, e, key: str) -> None:
        """Apply neutral selected and hover states while retaining Text content."""
        button = self.nav_buttons.get(key)
        if button is None:
            return
        hovered = str(getattr(e, "data", "")) == "true"
        if key == self.section:
            button.style.bgcolor = palette.ACTIVE
            button.content.color = palette.ACCENT_TEXT
        elif hovered:
            button.style.bgcolor = palette.SURFACE_3
            button.content.color = palette.TEXT
        else:
            button.style.bgcolor = "transparent"
            button.content.color = palette.TEXT_2
        button.update()

    def text(self, value, max_lines=0):
        # max_lines>0 时限制高度：诊断 JSON 等长内容不能把分区撑爆
        # （容器已有宽度，折行无限增高才是事故形态）
        return ft.Text(value, size=12, color=palette.TEXT_2, selectable=True,
                       max_lines=max_lines or None,
                       overflow=ft.TextOverflow.ELLIPSIS if max_lines else None)

    def field(self, label, value="", multiline=False, secret=False):
        control = ft.TextField(label=label, value=value, text_size=13, password=secret,
            can_reveal_password=secret, multiline=multiline, min_lines=3 if multiline else 1,
            max_lines=7 if multiline else 1)
        control._scizhi_field_width = 680
        return control

    def choice(self, label, items, value):
        control = ft.Dropdown(label=label, value=value, options=[ft.dropdown.Option(k, v) for k, v in items])
        control._scizhi_field_width = 340
        return control

    def button(self, label, action, primary=False):
        def invoke(e):
            try:
                action()
            except (ValueError, TypeError) as exc:
                from ..workbench import redact
                self._notice(redact(str(exc)), True)
        return (ft.FilledButton if primary else ft.OutlinedButton)(label, on_click=invoke)

    def _notice(self, text, error=False):
        self.message.visible = True
        self.message.value, self.message.color = str(text), palette.DANGER if error else palette.ACCENT
        self.page.update()

    def _call(self, req, callback=None, refresh=False):
        try:
            result = handle(req)
            if not result.get("ok", True):
                raise ValueError(result.get("error", "操作失败"))
            if callback:
                callback(result)
            if refresh:
                self.select(self.section)
            self._notice("已保存" if req["cmd"].endswith("save") else "操作完成" + (" · " + result["path"] if result.get("path") else ""))
            if self.on_saved:
                self.on_saved()
            return result
        except Exception as exc:
            from ..workbench import redact
            self._notice(redact(str(exc)), error=True)
            return None

    def _read(self, cmd, key, **kwargs):
        result = handle({"cmd": cmd, **kwargs})
        if not result.get("ok", True):
            raise ValueError(result.get("error", "读取失败"))
        return result.get(key, [])

    def _save_settings(self, patch):
        return self._call({"cmd": "settings_save", "settings": patch})

    def select(self, key, update=True):
        if key not in self._filtered_keys:
            self.search_input.value = ''
            self._filter_categories('')
        self.section = key
        self.selector.value = key
        _, label, hint = next(x for x in CATEGORIES if x[0] == key)
        self.title.value, self.subtitle.value, self.message.value = label, hint, ""
        self.message.visible = False
        for name, button in self.nav_buttons.items():
            button.style.bgcolor = palette.ACTIVE if name == key else "transparent"
            button.content.color = palette.ACCENT_TEXT if name == key else palette.TEXT_2
        try:
            controls = self.sections.get(key)
            if controls is None:
                controls = getattr(self, "build_" + key)()
        except Exception as exc:
            controls = [self.text("读取此功能失败，请重试。"), self.text(str(exc)),
                        self.button("重新读取", lambda: self.select(key))]
        self.main.controls = [self.title, self.subtitle, self.message, ft.Divider(color=palette.BORDER, height=12), *controls]
        style_inputs(self.main, self.selector, self.nav)
        apply_appearance(self.main)
        self.resize(self.width)
        if update:
            self.page.update()

    def resize(self, width):
        self.width = max(320, width)
        # A normal desktop window is roughly 900 px wide; keep the persistent
        # category rail once there is room for its 158 px column and the form.
        wide = width >= 720
        self.nav.visible, self.selector.visible = wide, not wide
        self.content.width = width
        self.main.width = min(760, width - (192 if wide else 0))
        self.resize_fields()

    def resize_fields(self):
        pending = list(self.main.controls)
        while pending:
            node = pending.pop()
            desired = getattr(node, "_scizhi_field_width", None)
            if desired:
                node.width = min(desired, max(260, self.main.width - 12))
            for attr in ("controls", "content"):
                value = getattr(node, attr, None)
                pending.extend(value if isinstance(value, list) else [value] if isinstance(value, ft.Control) else [])

    def build_general(self):
        opts = settings.load()
        controls = []
        switches = {}
        for key, title in [("default_offline", "默认使用离线演示材料"), ("git_archive", "导出时归档研究记录")]:
            switches[key] = ft.Switch(label=title, value=opts[key])
            controls.append(switches[key])
        return controls + [self.button("保存通用设置", lambda: self._save_settings({k: c.value for k, c in switches.items()}), True)]

    def build_appearance(self):
        opts = settings.load()
        font_family = self.choice("正文中文字体", [("system", "系统默认（含中文回退）"), ("yahei", "微软雅黑")],
                                   opts.get("appearance_font_family", "system"))
        code_font = self.choice("代码字体", [("cascadia", "Cascadia Code"), ("consolas", "Consolas")],
                                 opts.get("appearance_code_font", "cascadia"))
        scale = self.choice("阅读字号", [(str(v), label) for v, label in [(0.9, "小"), (1.0, "标准"), (1.1, "较大"), (1.2, "大")]],
                            str(opts.get("appearance_font_scale", 1.0)))
        density = self.choice("界面密度", [("comfortable", "舒适"), ("compact", "紧凑")],
                              opts.get("appearance_density", "comfortable"))
        dark = ft.Switch(label="深色主题", value=bool(opts.get("appearance_dark_mode", False)))
        animation = ft.Switch(label="思考与开场动效", value=opts["ui_animations"])
        inspector = ft.Switch(label="宽屏显示右侧研究详情", value=opts["desktop_inspector"])
        self.forms["appearance"] = {"font_family": font_family, "code_font": code_font, "scale": scale,
                                     "density": density, "dark": dark, "animation": animation,
                                     "inspector": inspector}
        preview_text = ft.Text("Aa 中文 · 研究记录与公式 f(x)", size=15, color=palette.TEXT,
                               font_family=self._body_font(font_family.value))
        preview_code = ft.Text("for result in evidence: verify(result)", size=13, color=palette.TEXT_2,
                               font_family=self._code_font(code_font.value))
        self.appearance_preview = ft.Container(
            content=ft.Column([ft.Text("实时预览", size=12, color=palette.TEXT), preview_text, preview_code], spacing=6),
            padding=12, bgcolor=palette.SURFACE_2, border_radius=6)

        def preview_changed(e=None):
            preview_text.font_family = self._body_font(font_family.value)
            preview_code.font_family = self._code_font(code_font.value)
            preview_text.size = round(15 * float(scale.value or 1), 1)
            preview_code.size = round(13 * float(scale.value or 1), 1)
            self.appearance_preview.update()

        for control in (font_family, code_font, scale, density):
            control.on_select = preview_changed
        for control in (dark, animation, inspector):
            control.on_change = preview_changed
        self.forms["appearance_preview"] = {"body": preview_text, "code": preview_code}
        return [self._appearance_row("字体与阅读", "正文和代码分别使用可读的系统字体或等宽字体。", font_family),
                self._appearance_row("代码显示", "选择代码区域的等宽字形。", code_font),
                self._appearance_row("阅读字号", "同步调整文本、表单标签和 Markdown 标题。", scale),
                self._appearance_row("界面密度", "紧凑模式减少分组之间的间距。", density),
                self._appearance_row("主题", "切换中性灰底色和蓝色操作色。", dark),
                self._appearance_row("动效", "控制思考和开场过渡动画。", animation),
                self._appearance_row("研究详情", "在宽屏工作台中显示右侧研究详情。", inspector),
                self.appearance_preview,
                self.button("应用外观", lambda: self._apply_appearance_settings(font_family, code_font, scale,
                    density, dark, animation, inspector), True)]

    def _appearance_row(self, label, description, control):
        return ft.Container(content=ft.Column([
            ft.Text(label, size=13, color=palette.TEXT, weight=ft.FontWeight.W_600),
            ft.Text(description, size=11, color=palette.TEXT_2), control], spacing=4),
            padding=ft.Padding(0, 3, 0, 3))

    @staticmethod
    def _body_font(value):
        return ui_theme.body_font_family(value)

    @staticmethod
    def _code_font(value):
        return ui_theme.code_font_family(value)

    def _apply_appearance_settings(self, font_family, code_font, scale, density, dark, animation, inspector):
        patch = {
            "appearance_font_family": font_family.value,
            "appearance_code_font": code_font.value,
            "appearance_font_scale": float(scale.value),
            "appearance_density": density.value,
            "appearance_dark_mode": bool(dark.value),
            "ui_animations": bool(animation.value),
            "desktop_inspector": bool(inspector.value),
        }
        result = self._save_settings(patch)
        if result is not None:
            apply_appearance(self.main)

    def build_memory(self):
        rows = self._read("memory_list", "memories")
        selected = {"id": None}
        try:
            project_state = handle({"cmd": "project_list"})
            projects_result = project_state.get("projects", [])
            active_project_id = project_state.get("active_project_id") or ""
        except Exception:
            projects_result, active_project_id = [], ""
        fields = {"title": self.field("标题"), "content": self.field("研究偏好与方法约定", multiline=True),
                  "source": self.field("来源说明", "用户录入"),
                  "approved": ft.Switch(label="我已核对，允许用于后续会话", value=False),
                  "enabled": ft.Switch(label="启用此条记忆", value=True)}
        scope = self.choice("作用域", [("global", "全局（所有项目）"),
                                      ("project", "项目"), ("role", "角色")], "global")
        project_options = [ft.dropdown.Option("", "不绑定项目")]
        for project in projects_result:
            project_options.append(ft.dropdown.Option(str(project["id"]),
                                                      str(project.get("name") or project["id"])))
        project = ft.Dropdown(label="项目（项目作用域必选；角色作用域可选）",
                              value=active_project_id if active_project_id in {p.get("id") for p in projects_result} else "",
                              options=project_options)
        project._scizhi_field_width = 680
        role = self.field("角色（例如 critic、literature；角色作用域必填）")
        def scope_changed(e=None):
            project.visible = scope.value in {"project", "role"}
            role.visible = scope.value == "role"
            self.page.update()
        scope.on_select = scope_changed
        project.visible = role.visible = False
        self.forms["memory"] = {**fields, "scope": scope, "project_id": project, "role": role}
        def edit(row):
            selected["id"] = row["id"]
            for k, f in fields.items():
                f.value = row.get(k, "")
            scope.value = row.get("scope", "global")
            project.value = row.get("project_id", "") or ""
            role.value = row.get("role", "") or ""
            scope_changed()
            self.page.update()
        def save():
            try:
                value = {k: f.value for k, f in fields.items()}
                value.update(memory_manager(scope.value, project.value, role.value, projects_result))
            except (ValueError, TypeError) as exc:
                self._notice(str(exc), error=True)
                return
            if selected["id"]:
                value["id"] = selected["id"]
            self._call({"cmd": "memory_save", "memory": value}, refresh=True)
        opts = settings.load()
        enabled = ft.Switch(label="在新会话中加载已确认的记忆", value=opts["memory_enabled"])
        budget = self.field("记忆长度预算（字符）", str(opts["memory_context_chars"]))
        return [enabled, budget, self.button("保存加载策略", lambda: self._save_settings({"memory_enabled": enabled.value,
                    "memory_context_chars": int(budget.value)})),
                self.text("研究偏好不作为论文证据。修改后请重新核对；已运行会话保留原有上下文。"),
                *self.record_rows(rows, lambda r: r["title"],
                    lambda r: ("全局" if r.get("scope", "global") == "global" else
                               (f"项目 · {r.get('project_id', '')}" if r.get("scope") == "project" else
                                f"角色 · {r.get('role', '')}" + (f" · {r.get('project_id')}" if r.get('project_id') else "")))
                    + (" · 已确认" if r.get("approved") else " · 待确认"), edit),
                scope, project, role, *fields.values(), ft.Row([self.button("保存记忆", save, True), self.button("删除所选记忆",
                    lambda: self._call({"cmd": "memory_delete", "id": selected["id"]}, refresh=True))], wrap=True)]

    def record_rows(self, rows, title, detail, edit):
        if not rows:
            return [ft.Container(content=self.text("尚无记录。填写下方表单后保存。"), padding=14, bgcolor=palette.SURFACE_2, border_radius=6)]
        return [ft.Container(padding=ft.Padding(12, 10, 12, 10), bgcolor=palette.SURFACE_2, border_radius=6,
            content=ft.Row([ft.Column([ft.Text(title(row), size=13, color=palette.TEXT, max_lines=2),
                ft.Text(detail(row), size=11, color=palette.TEXT_2, max_lines=2)], expand=True),
                ft.TextButton("编辑", on_click=lambda e, r=row: edit(r))], spacing=8)) for row in rows]

    def build_shortcuts(self):
        result = handle({"cmd": "shortcuts"})
        fields = {name: self.field(result["labels"][name], value) for name, value in result["shortcuts"].items()}
        self.forms["shortcuts"] = fields
        return [self.text("修改后立即生效。Ctrl、Alt、Shift 可组合；冲突时不会保存。TUI 使用自身的行编辑快捷键。"),
                *fields.values(), self.button("保存快捷键", lambda: self._save_settings({"keyboard_shortcuts": {k: f.value for k, f in fields.items()}}), True)]

    def build_commands(self):
        rows = self._read("custom_commands", "commands")
        fields = {"name": self.field("命令名称", "review-method"), "description": self.field("用途", "检查实验设计"),
            "template": self.field("提示词模板 · 用 {{input}} 插入输入", "检查以下研究的对照设置、数据泄漏和统计假设，列出需要补充的实验：\n{{input}}", multiline=True),
            "mode": self.choice("执行模式", [("plan", "仅规划"), ("review", "只读审查"), ("agent", "工具执行")], "review"),
            "enabled": ft.Switch(label="启用命令", value=True)}
        self.forms["commands"] = fields
        def edit(row):
            for k, f in fields.items():
                f.value = row[k]
            self.page.update()
        return [self.text("保存后在桌面命令面板或 TUI 输入 /run <名称> <材料>。模板不执行 shell，工具调用仍需授权。"),
            *self.record_rows(rows, lambda r: "/run " + r["name"], lambda r: r["description"], edit), *fields.values(),
            ft.Row([self.button("保存命令", lambda: self._call({"cmd": "custom_command_save", "command": {k: f.value for k, f in fields.items()}}, refresh=True), True),
                self.button("删除命令", lambda: self._call({"cmd": "custom_command_delete", "name": fields["name"].value}, refresh=True))], wrap=True)]

    def build_subagents(self):
        opts = settings.load()
        enabled = ft.Switch(label="允许委派子智能体", value=opts["subagents_enabled"])
        count = self.choice("同时运行上限", [(str(n), str(n)) for n in range(1, 5)], str(opts["subagent_max_parallel"]))
        agents = self._read("agents", "agents")
        return [enabled, count, self.text("独立职责与工具白名单由运行时检查；并行预算也作用于 fanout。"),
            self.button("保存委派设置", lambda: self._save_settings({"subagents_enabled": enabled.value, "subagent_max_parallel": int(count.value)}), True),
            *[ft.Container(content=ft.Column([ft.Text(a.get("title", a.get("name", "")), size=14, color=palette.TEXT,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                self.text(a.get("description", "")), self.text("工具 · " + " / ".join(a.get("tools", [])))]),
                padding=14, bgcolor=palette.SURFACE_2, border_radius=6) for a in agents]]

    def build_skills(self):
        rows = self._read("skills_list", "skills")
        controls = [self.text("阶段选择节省上下文。明确启用也受长度预算约束；停用后不会注入提示词。")]
        opts = settings.load()
        for row in rows:
            name = row["name"]
            mode = "off" if name in opts["skills_disabled"] else "on" if name in opts["skills_active"] else "auto"
            choice = self.choice(name, [("auto", "按阶段"), ("on", "明确启用"), ("off", "停用")], mode)
            choice.on_select = lambda e, n=name, c=choice: self._call({"cmd": "skill_mode", "name": n, "mode": c.value})
            controls.extend([choice, self.text(row.get("description", ""))])
        return controls

    def build_mcp(self):
        rows = self._read("mcp_config", "servers")
        fields = {"name": self.field("服务名称"),
            "transport": self.choice("连接方式", [("stdio", "本地程序 · stdio"), ("streamable_http", "HTTP · Streamable HTTP 2025")], "stdio"),
            "command": self.field("可执行程序路径"),
            "args": self.field("参数 · 每行一个", multiline=True),
            "env": self.field("环境变量 JSON · 空白保留已存密钥", secret=True),
            "url": self.field("MCP 服务地址", ""), "headers": self.field("鉴权请求头 JSON · 空白保留", secret=True),
            "enabled": ft.Switch(label="启用服务", value=True), "autoload": ft.Switch(label="启动时发现工具", value=False)}
        self.forms["mcp"] = fields
        diagnostic = self.text("")
        def transport_changed(e=None):
            local = fields["transport"].value == "stdio"
            for key in ("command", "args", "env"):
                fields[key].visible = local
            for key in ("url", "headers"):
                fields[key].visible = not local
            self.page.update()
        fields["transport"].on_select = transport_changed
        transport_changed()
        def edit(row):
            for key in ("name", "command", "url", "enabled", "autoload"):
                fields[key].value = row.get(key, False if key in {"enabled", "autoload"} else "")
            fields["transport"].value = row.get("transport", "stdio")
            fields["args"].value, fields["env"].value, fields["headers"].value = "\n".join(row.get("args", [])), "", ""
            transport_changed()
            self.page.update()
        def save():
            try:
                server = {k: fields[k].value for k in ("name", "transport", "enabled", "autoload")}
                if server["transport"] == "stdio":
                    server["command"] = fields["command"].value
                    server["args"] = [s for s in fields["args"].value.splitlines() if s]
                    if fields["env"].value.strip():
                        server["env"] = json.loads(fields["env"].value)
                else:
                    server["url"] = fields["url"].value
                    if fields["headers"].value.strip():
                        server["headers"] = json.loads(fields["headers"].value)
                self._call({"cmd": "mcp_save", "server": server}, refresh=True)
            except ValueError:
                self._notice("环境变量或请求头 JSON 格式无效；未回显敏感输入", True)
        def probe():
            diagnostic.value = "正在连接已配置服务…"
            self.page.update()
            def work():
                result = handle({"cmd": "mcp_probe", "name": fields["name"].value})
                tools = result.get("tools", [])
                diagnostic.value = ("连接成功 · 协议 " + str(result.get("protocol_version", "")) + " · " + "、".join(t.get("name", "") for t in tools)) if result.get("ok") else result.get("error", "连接失败")
                self.page.update()
            self.page.run_thread(work)
        return [self.text("支持本地 stdio 与 2025 会话式 Streamable HTTP。保存不连接服务，诊断会发起连接。凭据放入环境变量或请求头。"),
            *self.record_rows(rows, lambda r: r["name"], lambda r: ("启用" if r.get("enabled", True) else "停用") + " · " + r.get("transport", "stdio"), edit),
            *fields.values(), ft.Row([self.button("保存服务", save, True), self.button("诊断连接", probe),
                self.button("删除所选服务", lambda: self._call({"cmd": "mcp_delete", "name": fields["name"].value}, refresh=True))], wrap=True), diagnostic]

    def build_browser(self):
        return self._control("browser")

    def build_computer(self):
        return self._control("computer")

    def _control(self, kind):
        status = self._read("control_status", "controls").get(kind, {})
        servers = self._read("mcp_config", "servers")
        server = self.choice("控制服务", [(s["name"], s["name"]) for s in servers], status.get("server") or None)
        enabled = ft.Switch(label="启用此控制连接", value=status.get("enabled", False))
        tool, args = self.field("工具名称"), self.field("工具参数 JSON", "{}", multiline=True)
        result_view = self.text("")
        self.forms[kind] = {"server": server, "enabled": enabled, "tool": tool, "args": args}
        def save():
            result = self._call({"cmd": "control_save", "kind": kind, "binding": {"server": server.value or "", "enabled": enabled.value}})
            if result:
                self._save_settings({kind + "_control_enabled": enabled.value})
                self.select(kind)
        def invoke():
            try:
                arguments = json.loads(args.value)
            except ValueError:
                self._notice("工具参数 JSON 无效", True)
                return
            def work():
                result = handle({"cmd": "control_call", "kind": kind, "tool": tool.value, "arguments": arguments})
                from ..workbench import redact
                result_view.value = json.dumps(redact(result), ensure_ascii=False, indent=2)[:12000]
                self.page.update()
            self.page.run_thread(work)
        hint = ("可绑定 Microsoft Playwright MCP。先在 MCP 服务器中添加本机已安装程序，再诊断工具名称。" if kind == "browser" else
                "绑定你选择的电脑控制 MCP 服务。控制权限以该服务实际提供的截图、输入和窗口工具为准。")
        return [self.text(status.get("reason") or ("已绑定配置；诊断后确认工具可用" if status.get("configured") else "尚未绑定控制服务")), self.text(hint),
                server, enabled, self.button("保存控制连接", save, True), ft.Divider(color=palette.BORDER),
                self.text("手动调用已绑定服务；执行前核对工具名称和参数。Agent 调用另外经过会话工具审批。"),
                tool, args, self.button("调用工具", invoke), result_view]

    def build_hooks(self):
        response = handle({"cmd": "hook_list"})
        rows = response.get("hooks", [])
        fields = {"id": self.field("钩子 ID", "research-trace"), "name": self.field("名称", "研究工具记录"),
            "event": self.choice("触发事件", [("before_tool", "工具执行前"), ("after_tool", "工具执行后"), ("completed", "会话结束")], "after_tool"),
            "action": self.choice("处理方式", [("record", "记录事件"), ("block_tool", "阻止工具（执行前）")], "record"),
            "tool": self.field("匹配工具", "*"), "message": self.field("记录说明", "科研运行追踪"),
            "enabled": ft.Switch(label="启用此钩子", value=True)}
        self.forms["hooks"] = fields
        def edit(row):
            for k, f in fields.items():
                f.value = row.get(k, "")
            self.page.update()
        enabled = ft.Switch(label="启用运行时钩子", value=settings.load()["hooks_enabled"],
            on_change=lambda e: self._save_settings({"hooks_enabled": e.control.value}))
        return [enabled, self.text("钩子可记录事件或收紧工具权限。记录钩子不会递归发起 Agent，也不会运行 shell。"),
            *self.record_rows(rows, lambda r: r.get("name", r["id"]), lambda r: r["event"] + " · " + r["action"], edit),
            *fields.values(), ft.Row([self.button("保存钩子", lambda: self._call({"cmd": "hook_save", "hook": {k: f.value for k, f in fields.items()}}, refresh=True), True),
                self.button("删除钩子", lambda: self._call({"cmd": "hook_delete", "id": fields["id"].value}, refresh=True))], wrap=True),
            self.text("近期触发记录"),
            self.text(json.dumps(response.get("logs", [])[-8:], ensure_ascii=False, indent=2)[:6000],
                      max_lines=24)]

    def build_remote(self):
        current = handle({"cmd": "remote_status"})
        status = self.text("运行中" if current.get("running") else "尚未开启")
        urls = self.text("\n".join(current.get("urls", [])))
        token = self.field("连接口令（仅本机显示）", secret=True)
        port = self.field("端口（0 自动分配）", "8765")
        def start():
            try:
                def done(result):
                    status.value, urls.value = "运行中", "\n".join(result.get("urls", []))
                    token.value = result.get("token", "")
                self._call({"cmd": "remote_start", "host": "0.0.0.0", "port": int(port.value)}, callback=done)
            except ValueError:
                self._notice("端口必须是整数", True)
        return [status, urls, self.text("手机与电脑连接同一局域网，在手机打开地址并输入口令。关闭服务后连接立即失效。"), port, token,
                ft.Row([self.button("开启局域网控制", start, True), self.button("关闭服务", lambda: self._call({"cmd": "remote_stop"}, refresh=True))], wrap=True)]

    def build_automations(self):
        rows = self._read("loop_list", "loops")
        flows = self._read("workflows", "workflows")
        selected = self.choice("科研工作流", [(r["id"], r["title"]) for r in flows], "literature_scan")
        topic, interval, count = self.field("研究问题"), self.field("完成后间隔（秒）", "3600"), self.field("最多执行轮数", "3")
        offline = ft.Switch(label="使用离线演示材料", value=settings.load()["default_offline"])
        def start():
            try:
                self._call({"cmd": "loop_start", "workflow_id": selected.value, "topic": topic.value,
                    "interval_seconds": int(interval.value), "max_iterations": int(count.value), "offline": offline.value}, refresh=True)
            except ValueError:
                self._notice("间隔与轮次必须是有效整数", True)
        controls = [self.text("宿主需保持运行。每轮完成后再计时；等待人工确认时暂停推进，重启后手动恢复。")]
        for r in rows:
            controls.append(ft.Column([self.text(f"{r.get('topic', '')} · {r.get('status')} · {r.get('iteration', 0)}/{r.get('max_iterations', 0)}"),
                ft.Row([self.button("停止", lambda row=r: self._call({"cmd": "loop_stop", "loop_id": row["loop_id"]}, refresh=True)),
                    self.button("恢复", lambda row=r: self._call({"cmd": "loop_resume", "loop_id": row["loop_id"]}, refresh=True))])]))
        return controls + [selected, topic, interval, count, offline, self.button("创建并启动自动化", start, True)]

    def build_notebook(self):
        rows = self._read("notebook_list", "records")
        selected = {"id": None, "evidence": [], "version": None}
        fields = {"kind": self.choice("记录类型", [("hypothesis", "研究假设"), ("experiment", "实验记录")], "hypothesis"),
            "title": self.field("记录标题"), "question": self.field("研究问题", multiline=True),
            "prediction": self.field("可证伪预测", multiline=True), "method": self.field("方法、对照与复现条件", multiline=True),
            "metrics": self.field("观测指标"), "status": self.choice("状态", [("planned", "计划中"), ("running", "进行中"), ("completed", "已完成"), ("failed", "未成功")], "planned"),
            "result": self.field("实际结果 · 完成时必填", multiline=True), "limitations": self.field("局限与反例", multiline=True),
            "next_action": self.field("下一步"), "task_id": self.field("关联研究任务 ID（可选）"),
            "related_ids": self.field("关联假设或实验 ID · 每行一个", multiline=True),
            "observations": self.field("过程观察 · 每行一条", multiline=True),
            "synthetic": ft.Switch(label="这是一条演示或合成记录", value=False)}
        self.forms["notebook"] = fields
        evidence = {"id": self.field("新增证据的 DOI / URL / 来源 ID"), "quote": self.field("原文片段", multiline=True),
            "relation": self.choice("与假设的关系（由你核对）", [("supports", "支持"), ("challenges", "反驳"), ("context", "背景")], "context"),
            "scope": self.choice("阅读范围", [("metadata", "元数据"), ("abstract", "摘要"), ("full_text", "全文"), ("user_observation", "实验观察")], "abstract")}
        evidence_view = self.text("")
        def edit(row):
            selected.update(id=row["id"], evidence=list(row.get("evidence", [])), version=row.get("version"))
            for k, field in fields.items():
                field.value = "\n".join(row.get(k, [])) if k in {"related_ids", "observations"} else row.get(k, "")
            evidence_view.value = "已有证据 " + str(len(selected["evidence"])) + " 条\n" + "\n".join(e.get("quote", "")[:180] for e in selected["evidence"])
            self.page.update()
        def save():
            record = {k: f.value for k, f in fields.items()}
            for key in ("related_ids", "observations"):
                record[key] = [line.strip() for line in record[key].splitlines() if line.strip()]
            if selected["id"]:
                record["id"] = selected["id"]
            record["evidence"] = list(selected["evidence"])
            if evidence["id"].value.strip() or evidence["quote"].value.strip():
                record["evidence"].append({"source": {"id": evidence["id"].value, "synthetic": False},
                    "quote": evidence["quote"].value, "relation": evidence["relation"].value, "scope": evidence["scope"].value})
            req = {"cmd": "notebook_save", "record": record}
            if selected["version"] is not None:
                req["expected_version"] = selected["version"]
            self._call(req, refresh=True)
        plan_fields = ("kind", "title", "question", "prediction", "method", "metrics", "related_ids", "task_id", "synthetic")
        plan_view = ft.Container(content=ft.Column([fields[k] for k in plan_fields], spacing=14))
        results_view = ft.Container(visible=False, content=ft.Column([
            *[fields[k] for k in ("status", "observations", "result", "limitations", "next_action")],
            evidence_view, *evidence.values()], spacing=14))
        view = self.choice("记录内容", [("plan", "问题与方案"), ("results", "结果与证据")], "plan")
        def change_view(e):
            plan_view.visible, results_view.visible = view.value == "plan", view.value == "results"
            self.page.update()
        view.on_select = change_view
        hypotheses = sum(r["kind"] == "hypothesis" for r in rows)
        challenges = sum(e.get("relation") == "challenges" for r in rows for e in r.get("evidence", []))
        def record_detail(row):
            kind = {"hypothesis": "研究假设", "experiment": "实验记录"}[row["kind"]]
            state = {"planned": "计划中", "running": "进行中", "completed": "已完成", "failed": "未成功"}[row["status"]]
            return kind + " · " + state + (" · 演示材料" if row.get("synthetic") else "")
        return [self.text("从问题出发，记录能被证伪的预测。证据关系由录入者核对，保存记录不代表科学结论已得到验证。"),
            self.text(f"假设 {hypotheses} · 实验 {len(rows) - hypotheses} · 反驳证据 {challenges}"),
            *self.record_rows(rows, lambda r: r["title"], record_detail, edit),
            view, plan_view, results_view,
            ft.Row([self.button("保存科研记录", save, True), self.button("导出 Markdown", lambda: self._call({"cmd": "notebook_export", "id": selected["id"], "format": "markdown"})),
                self.button("删除所选记录", lambda: self._call({"cmd": "notebook_delete", "id": selected["id"]}, refresh=True))], wrap=True)]


def apply_appearance(*roots):
    """Apply typography and density to a widget tree idempotently.

    Flet keeps input and Markdown styles as nested objects, so walking only
    ``controls`` misses dropdown labels and code previews.  Every mutable size
    records its source value once; changing settings back to 1.0 therefore
    restores the original value instead of scaling the already scaled value.
    """
    opts = settings.load()
    scale = opts.get("appearance_font_scale", 1.0)
    scale = float(scale) if type(scale) in (int, float) and scale in (.9, 1.0, 1.1, 1.2) else 1.0
    factor = .8 if opts.get("appearance_density") == "compact" else 1
    configured = False
    configure = getattr(ui_theme, "configure_typography", None)
    if callable(configure):
        configure(opts)
        configured = True

    def scale_style(style, seen_styles, code=False):
        if style is None or id(style) in seen_styles:
            return
        seen_styles.add(id(style))
        if isinstance(style, ft.TextStyle):
            if isinstance(getattr(style, "size", None), (int, float)):
                original = getattr(style, "_scizhi_original_size", style.size)
                style._scizhi_original_size = original
                style.size = round(original * scale, 1)
            family = getattr(style, "font_family", None)
            if family is not None:
                style.font_family = (SettingsCenter._code_font(opts.get("appearance_code_font", "cascadia"))
                                     if code else SettingsCenter._body_font(opts.get("appearance_font_family", "system")))

    pending, seen, seen_styles = list(roots), set(), set()
    while pending:
        node = pending.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, ft.Control):
            if isinstance(node, ft.Markdown):
                # theme.markdown_styles uses the configured base scale.  A
                # fresh sheet also ensures a new font reaches existing text.
                styles = getattr(ui_theme, "markdown_styles", None)
                if callable(styles):
                    node.md_style_sheet = styles()
            ui_theme.style_typography(node)
            if isinstance(node, (ft.Column, ft.Row)) and isinstance(node.spacing, (int, float)):
                original = getattr(node, "_scizhi_original_spacing", node.spacing)
                node._scizhi_original_spacing = original
                node.spacing = round(original * factor, 1)
            for attr in ("style", "label_style", "hint_style", "text_style"):
                style = getattr(node, attr, None)
                if isinstance(style, ft.TextStyle):
                    current = str(getattr(style, "font_family", "") or "").casefold()
                    scale_style(style, seen_styles, any(token in current for token in ("consolas", "cascadia", "mono")))
            button_style = getattr(node, "style", None)
            if isinstance(button_style, ft.ButtonStyle):
                style = getattr(button_style, "text_style", None)
                if isinstance(style, ft.TextStyle):
                    scale_style(style, seen_styles)
            for attr in ("controls", "content", "actions", "tabs", "leading", "trailing", "title", "subtitle", "label"):
                child = getattr(node, attr, None)
                if isinstance(child, (list, tuple)):
                    pending.extend(child)
                elif isinstance(child, ft.Control):
                    pending.append(child)
            sheet = getattr(node, "md_style_sheet", None)
            if sheet is not None:
                # MarkdownStyleSheet exposes each text style as *_text_style.
                for name in dir(sheet):
                    if name.endswith("_text_style"):
                        style = getattr(sheet, name, None)
                        if isinstance(style, ft.TextStyle):
                            code_style = "code" in name.casefold()
                            # configured markdown styles already include the
                            # global scale; ordinary older themes do not.
                            if not configured:
                                scale_style(style, seen_styles, code_style)
                            if getattr(style, "font_family", None) is not None:
                                style.font_family = (SettingsCenter._code_font(opts.get("appearance_code_font", "cascadia"))
                                                     if code_style else SettingsCenter._body_font(opts.get("appearance_font_family", "system")))
        elif isinstance(node, ft.TextStyle):
            scale_style(node, seen_styles)
