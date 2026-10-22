"""格致 SciZhi 桌面版（Flet / Flutter 渲染，原生 Windows 窗口）。

设计语言：中性工作面、清晰的阅读层级与克制的蓝色操作色。
启动：python -m src.ui.desktop
"""
from __future__ import annotations

import asyncio
import difflib
import itertools
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import flet as ft

from .. import evidence, llm, service, settings as app_settings
from ..agents import stats as stats_agent
from ..agents.specs import ROSTER
from ..workspace_modes import MODES, mode_for_agent
from ..config import ARTIFACT_DIR, ROOT_DIR
from ..version import APP_VERSION
from ..ui_preferences import normalize as normalize_ui, resolve_dark, SCHEMA as UI_SCHEMA
from ..citation.gbt7714 import format_entry
from .code_preview import CodePreview
from .markdown_view import MarkdownView
from .outcome_panel import OutcomeCheckPanel
from .theme import build_theme, configure_typography, palette, repaint, style_inputs, ui_size, radius, code_font_family, motion_duration

# 终态提示：主文案必须是可读中文；内部 reason、枚举与异常文本只作次要信息。
# 后端实际提供的字段（不凭空扩展）：AdaptiveDriver._finish 的 reason/error，
# 其中 message 仅 plan_incomplete 携带；AgentRuntime 确定性路径的 reason（可能
# 已是中文说明）与 error。原始内容一律保留在会话记录/日志里。
_TERMINAL_REASON_TEXT = {
    "permission_denied": "工具调用被权限策略或你的拒绝阻止，可在会话记录查看被拦截的调用后重试。",
    "plan_incomplete": "执行计划仍有未闭合步骤，已完成部分与依据已记录，可在会话记录查看缺项。",
    "model_error": "模型请求失败，请检查模型配置与网络后重试。",
    "model_changed": "会话执行中模型配置已改变，请新建或分叉会话后继续。",
    "step_budget": "已达到本回合的模型步骤上限，可提高预算或拆分任务后重试。",
    "token_budget": "已达到本回合的 token 预算，可提高预算或拆分任务后重试。",
    "context_budget": "上下文超过容量检查，请缩小研究范围或新建会话。",
    "tool_budget": "单步工具请求超过上限，本次执行已停止。",
    "unresolved_tool_errors": "仍有工具调用未成功，自动修复次数已用尽，请核查失败的调用后重试。",
    "interrupted_tool": "中断前可能有工具已经执行，已停止自动重放，请先核查产物后继续。",
    "unsafe_tool_retry": "此前的失败操作可能已有副作用，不能自动重复执行，请核查后重试。",
    "invalid_tool_call": "模型返回的工具调用格式无效，本次未执行任何工具。",
    "invalid_tool_result": "工具返回的执行状态无法核验，已停止执行。",
    "empty_model_result": "模型没有返回内容或工具调用，本次执行已停止。",
    "duplicate_tool_call": "模型重复使用了工具调用标识，本次执行已停止。",
    "plan_state_error": "执行计划状态读取失败，已停止执行，原始日志保留在会话记录。",
    "task_failed": "关联的科研任务尚未完成，未继续后续步骤。",
    "tool_failed": "工具执行失败，本次执行已停止，可在会话记录查看失败的调用。",
    "doom_loop": "单会话步骤超过上限，已停止以避免循环。",
}
_TERMINAL_FALLBACK_TEXT = {
    "blocked": "执行被策略阻止，可在会话记录查看原因。",
    "error": "执行失败，可在会话记录查看原因。",
}
_TERMINAL_STATUS_LABEL = {"blocked": "已阻止", "error": "执行失败"}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _internal_like(token: str) -> bool:
    """ASCII 片段是否像内部标识（snake_case 码、点号工具名、异常类名、档位枚举、会话 ID）。"""
    if len(token) < 3:
        return False
    if "_" in token or "." in token:
        return True
    if token.endswith(("Error", "Exception")):
        return True
    if len(token) >= 8 and token.isalnum() and any(char.isdigit() for char in token):
        return True
    return token.islower() and "-" in token


def _has_internal_token(text: str) -> bool:
    token = ""
    for char in text + " ":
        if char.isascii() and (char.isalnum() or char in "_.-"):
            token += char
            continue
        if _internal_like(token):
            return True
        token = ""
    return False


def _readable_reason(text: str) -> bool:
    """只接受短的中文说明；英文异常与内部枚举交给映射表与兜底文案。"""
    text = text.strip()
    return bool(text) and 2 <= len(text) <= 200 and _has_cjk(text) and not _has_internal_token(text)


def _terminal_notice(payload: dict[str, Any], status: str) -> tuple[str, str]:
    """返回 (用户可读主文案, 次要详情行)；内部 reason 只出现在详情行。"""
    reason = str(payload.get("reason") or "").strip()
    message = str(payload.get("message") or "").strip()
    error = str(payload.get("error") or "").strip()
    primary = next((text for text in (message, error) if _readable_reason(text)), "")
    if not primary:
        primary = _TERMINAL_REASON_TEXT.get(reason, "")
    if not primary:
        primary = _TERMINAL_FALLBACK_TEXT.get(status, "执行未完成，可在会话记录查看原因。")
    details: list[str] = []
    if reason:
        details.append(f"原因代码 {reason}" if reason in _TERMINAL_REASON_TEXT else reason)
    for text in (message, error):
        if text and text != primary and text not in details:
            details.append(text)
    joined = "；".join(details)
    detail_line = (f"详情：{joined}（可在会话记录查看）" if joined
                   else "详情可在会话记录查看")
    return primary, detail_line


def main(page: ft.Page) -> None:
    service.init()
    drafts = None
    terminal = None
    async def stop_queue_on_disconnect(e):
        from ..session_queue import close as close_queue
        from ..evidence_reviews import close as close_reviews
        state['poll'] = False
        agent_state['view_epoch'] += 1
        from ..shell_execution import cancel_all
        if terminal is not None:
            terminal.close()
        await asyncio.to_thread(cancel_all)
        try:
            if drafts is not None:
                await asyncio.to_thread(drafts.close)
        finally:
            try:
                await asyncio.to_thread(close_reviews)
            finally:
                await asyncio.to_thread(close_queue)

    page.on_disconnect = stop_queue_on_disconnect
    # Native Flet close does not reliably emit the web disconnect callback.
    # Intercept this window's close request before its client process exits.
    native_closing = False

    async def finish_native_close(force=False):
        nonlocal native_closing
        if native_closing:
            return
        native_closing = True
        saved = drafts is None or await asyncio.to_thread(drafts.close, force=force)
        if not saved and not force:
            native_closing = False
            def stay(e):
                page.pop_dialog()
            async def discard(e):
                page.pop_dialog()
                await finish_native_close(force=True)
            page.show_dialog(ft.AlertDialog(title=ft.Text('仍有草稿尚未保存'),
                content=ft.Text('其他窗口的版本冲突或磁盘错误使部分草稿未能保存。可以返回检查草稿，或放弃未保存内容后关闭。',
                    max_lines=5, overflow=ft.TextOverflow.ELLIPSIS),
                actions=[ft.TextButton('返回处理', on_click=stay),
                         ft.TextButton('放弃未保存内容并关闭', on_click=discard)]))
            return
        try:
            await stop_queue_on_disconnect(None)
        finally:
            await page.window.destroy()

    async def native_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            await finish_native_close()

    page.window.prevent_close = True
    page.window.on_event = native_window_event
    page.title = "格致 SciZhi · 通用与科研智能体"
    page.window.icon = str(ROOT_DIR/'assets'/'gezhi.ico')
    # 外观：主题偏好由 settings.theme_mode 持久化；palette 是运行时
    # 配色注册表，构建期经它取色的控件在深色启动时直接拿到暗色值——
    # 桌面控件树已全量 palette 化，无需启动期重映射（theme.repaint 只在
    # 运行期切换主题时按值补救动态构建的子树）。
    appearance = normalize_ui(app_settings.load())
    appearance_state = {'values': appearance}
    configure_typography(appearance)
    palette.set_dark(resolve_dark(appearance, system_dark=page.platform_brightness != ft.Brightness.LIGHT))
    page.bgcolor = palette.BG
    # 窗口尺寸按逻辑像素解释：150% 缩放的 2560×1600 屏只有约 1138 逻辑宽，
    # 固定 1320×840 会被系统夹进屏幕、布局却仍按 1320 排——右缘（页面留白与
    # 最右卡片）整条被切到屏幕外（首屏截图实测发现）。最大化最稳：任何
    # DPI/缩放下都铺满工作区，布局按真实可用宽度排布。
    page.window.maximized = True
    page.window.min_width, page.window.min_height = 1080, 700
    page.padding = 0
    # 页面内容列定宽：flet 0.86 里带 padding 的页面容器，其内容会向右溢出约一个
    # padding 宽（右缘圆角/留白被窗口切掉，实测 page.width=1265 逻辑而内容画到
    # 1273+；Column/STRETCH/Row 包裹/ResponsiveRow/alignment 五种重构都无效）。
    # 给内容列一个显式宽度即可从构造上杜绝溢出——定宽 < 最小窗宽(1080)，任何
    # 缩放下都放得下；不依赖 flet 的弹性宽度计算。
    # 导航与检查器各自占位，主列使用剩余宽度；窄窗口按需打开浮层导航。
    SIDE_W = 216
    RAIL_W = 340
    layout = {"page_w": 920, "rail": False, "sidebar_w": SIDE_W,
              "panel_manual": None, "panel_dialog": False, "navigation_open": False}
    PAGE_W = layout["page_w"]  # 构建期初值；resize 后改 layout 并回写控件 width
    busy_indicators: dict[str, tuple[ft.ProgressRing, ft.Icon]] = {}

    def busy_indicator(key: str, size: int) -> ft.Stack:
        animated = reasoning_control.motion_enabled() and bool(motion_duration('panel'))
        ring = ft.ProgressRing(width=size, height=size, stroke_width=2.5, visible=animated)
        idle = ft.Icon(ft.Icons.HOURGLASS_EMPTY, size=size, color=palette.TEXT_2, visible=not animated)
        busy_indicators[key] = (ring, idle)
        return ft.Stack([ring, idle], width=size, height=size)

    def refresh_busy_indicators() -> None:
        animated = reasoning_control.motion_enabled() and bool(motion_duration('panel'))
        for ring, idle in busy_indicators.values():
            ring.visible, idle.visible = animated, not animated
        if not animated:
            for stroke in thinking_strokes:
                stroke.animate_opacity = None
                stroke.opacity = .65
    page.theme_mode = ft.ThemeMode.DARK if palette.dark else ft.ThemeMode.LIGHT
    if appearance['theme_mode'] == 'system':
        page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = build_theme(dark=palette.dark)

    state: dict[str, Any] = {"task_id": None, "seen_events": 0, "poll": True,
                             "results_key": None, "project_id": ""}
    from ..bridge import handle as workspace_handle
    state['project_id'] = workspace_handle({'cmd': 'project_list'}).get('active_project_id') or ''

    def apply_theme(dark: bool) -> None:
        """深浅主题唯一入口：设置页「外观」保存后由 workspace_settings_saved 调用。

        配色注册表换值 → 页面主题/底色更换 → 已构建控件树按旧值重映射。
        sidebar/stage/inspector/pages 均为 main 的闭包局部量，调用期才解析。
        """
        if palette.dark == bool(dark):
            return
        palette.set_dark(dark)
        page.theme_mode = ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT
        page.theme = build_theme(dark=dark)
        page.bgcolor = palette.BG
        repaint(sidebar, stage, inspector, page.drawer, agent_approval_card, *pages)
        page.update()

    # ============ 通用小件 ============
    def seal(size: int = 46) -> ft.Image:
        """与启动入口共用的小尺寸品牌标识。"""
        return ft.Image(src=str(ROOT_DIR/'assets'/'gezhi.png'), width=size, height=size,
                        semantics_label='格致 SciZhi', fit=ft.BoxFit.CONTAIN)

    def chip(text: str, color: str) -> ft.Container:
        """低饱和状态标签，文字与颜色同时表达状态。"""
        fills = {palette.SUCCESS: palette.SUCCESS_BG, palette.INFO: palette.INFO_BG,
                 palette.WARN: palette.WARN_BG, palette.DANGER: palette.DANGER_BG,
                 palette.ACCENT: palette.ACCENT_BG}
        # 着色标签前景用文字级 accent：填充级 ACCENT 铺在 ACCENT_BG 上对比度
        # 不足 4.5:1（浅 4.24 / 深 3.98，实测），文字级 ACCENT_TEXT 两板均过。
        fg = palette.ACCENT_TEXT if color == palette.ACCENT else color
        return ft.Container(
            bgcolor=fills.get(color, palette.SURFACE_3), border_radius=radius('small'),
            padding=ft.Padding(10, 4, 10, 4),
            content=ft.Text(text, size=11, color=fg, weight=ft.FontWeight.W_500))

    def soft_card(content, padding=16, expand=None, width=None) -> ft.Container:
        """用浅灰底和细边界标识内容分组。

        width 建议显式给（默认 None = 按内容收缩）：flet 无宽度控件按固有宽度参与
        布局，卡片里的长文本/进度条会把整列撑破，导致整页空白。
        """
        return ft.Container(
            bgcolor=palette.CARD, border_radius=radius('panel'), padding=padding,
            expand=expand, width=width,
            border=ft.Border.all(1, palette.BORDER),
            content=content)

    def dark_card(content, padding=16, expand=None, width=None) -> ft.Container:
        """承载执行记录与证据摘要。"""
        return ft.Container(
            bgcolor=palette.SURFACE_2, border_radius=radius('panel'), padding=padding, expand=expand,
            width=width,
            border=ft.Border.all(1, palette.BORDER),
            content=content)

    STATUS_COLOR = {"running": palette.INFO, "done": palette.SUCCESS,
                    "waiting_human": palette.ACCENT, "paused": palette.ACCENT_TEXT,
                    "failed": palette.DANGER, "pending": palette.TEXT_2,
                    "cancelled": palette.TEXT_2}

    # ============ 页面 0：新建任务 ============
    tf_topic = ft.TextField(label="研究方向", hint_text="描述研究问题，或输入一个课题…",
                            multiline=True, min_lines=3, max_lines=5,
                            text_size=16, border_radius=radius(), filled=True,
                            bgcolor=palette.SURFACE_2, color=palette.TEXT,
                            focused_border_color=palette.ACCENT_TEXT,
                            border_color=palette.BORDER_2, col=12,
                            shift_enter=True)
    tf_kw = ft.TextField(label="关键词（空格分隔，可空）", border_radius=radius(),
                         filled=True, bgcolor=palette.SURFACE_3,
                         border_color=palette.BORDER_2, col=12)
    tf_y1 = ft.TextField(label="起始年", col=3, border_radius=radius(),
                         filled=True, bgcolor=palette.SURFACE_3,
                         border_color=palette.BORDER_2,
                         input_filter=ft.NumbersOnlyInputFilter())
    tf_y2 = ft.TextField(label="截止年", col=3, border_radius=radius(),
                         filled=True, bgcolor=palette.SURFACE_3,
                         border_color=palette.BORDER_2,
                         input_filter=ft.NumbersOnlyInputFilter())
    dd_style = ft.Dropdown(
        label="参考文献格式", value="gbt7714", border_radius=radius(), filled=True,
        col=6, bgcolor=palette.SURFACE_3, border_color=palette.BORDER_2,
        options=[ft.dropdown.Option("gbt7714", "GB/T 7714—2015"),
                                  ft.dropdown.Option("apa", "APA 7")])
    dd_workflow = ft.Dropdown(label="科研工作流", value="research_pipeline", options=[
        ft.dropdown.Option("research_pipeline", "完整综述研究"),
        ft.dropdown.Option("literature_scan", "文献快筛与引用"),
        ft.dropdown.Option("evidence_review", "证据抽取与人工复核")])
    home_offline = ft.Switch(label="离线示例", value=app_settings.load().get("default_offline", True))
    home_offline_note = ft.Text('', size=11, color=palette.TEXT_3, max_lines=3,
                                overflow=ft.TextOverflow.ELLIPSIS)
    def refresh_home_offline_note(update=False):
        home_offline_note.value = ('使用内置合成文献，不联网、不调用模型；不可作为真实科研证据。'
            if home_offline.value else '在线研究将访问文献来源；模型调用取决于当前设置。')
        if update:
            page.update()
    home_offline.on_change = lambda e: refresh_home_offline_note(True)
    refresh_home_offline_note()

    def open_model_settings():
        settings_center.select('models', update=False)
        nav_to(5)

    history_col = ft.Column(spacing=0)
    history_count = ft.Text("", size=12, color=palette.TEXT_2)
    side_recent = ft.Column(spacing=3)

    def delete_task_confirm(tid: str, topic: str) -> None:
        def do_delete(e) -> None:
            page.pop_dialog()
            try:
                deleted = service.delete_task(tid)
            except Exception as ex:  # noqa: BLE001
                page.show_dialog(ft.SnackBar(ft.Text(f"删除失败：{ex}", max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS), bgcolor=palette.DANGER))
                return
            if state["task_id"] == tid:
                state["task_id"] = None
                state["_sig"] = None
                state["results_key"] = None
                state["seen_events"] = 0
                log_col.controls.clear()
                result_meta.controls.clear()
                banner.visible = False
                refresh_inspector()
                nav_to(0)
            refresh_history()
            page.show_dialog(ft.SnackBar(ft.Text(f"已删除任务《{topic[:20]}》" if deleted else "该任务已不存在，列表已更新"),
                                         bgcolor=palette.INFO))

        def cancel_delete(e) -> None:
            page.pop_dialog()

        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("删除任务"),
            content=ft.Text(f"确定删除《{topic[:30]}》？\n断点存档将被移除，已导出的产物不受影响。"),
            actions=[
                ft.TextButton("取消", on_click=cancel_delete),
                ft.FilledButton("删除", on_click=do_delete,
                                style=ft.ButtonStyle(bgcolor=palette.DANGER,
                                                     color=palette.BG)),
            ]))

    def refresh_history() -> None:
        history_col.controls.clear()
        side_recent.controls.clear()
        tasks = service.list_tasks()
        history_count.value = f"{len(tasks)} 项研究" if tasks else ""
        if not tasks:
            history_col.controls.append(
                ft.Text("还没有研究记录。输入课题，开始检索文献。",
                        size=12, color=palette.TEXT_2))
            side_recent.controls.append(
                ft.Text("暂无会话", size=11, color=palette.TEXT_3))
        for t in tasks[:8]:
            color = STATUS_COLOR.get(t["status"], palette.TEXT_2)
            pct = (f"{t['done_nodes']}/{t['total_nodes']} 节点"
                   if t["total_nodes"] else "")
            meta = " · ".join(x for x in (t.get("created_at", ""), pct) if x)
            history_col.controls.append(ft.Container(
                border_radius=radius('small'), padding=ft.Padding(2, 13, 2, 13),
                border=ft.Border.only(bottom=ft.BorderSide(1, palette.BORDER)),
                content=ft.Row([
                    ft.Container(width=8, height=8, border_radius=4, bgcolor=color),
                    ft.Column([
                        ft.Text(t["topic"], size=14, width=max(160, min(layout["page_w"], 880) - 110),
                                color=palette.TEXT,
                                weight=ft.FontWeight.W_500,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"{t['status_text']} · {meta}", size=11,
                                width=max(160, min(layout["page_w"], 880) - 110),
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color=palette.TEXT_2),
                    ], spacing=5),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                  icon_color=palette.TEXT_2,
                                  tooltip="删除任务",
                                  on_click=lambda e, tid=t["task_id"],
                                  tp=t["topic"]: delete_task_confirm(tid, tp)),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=palette.TEXT_2, size=18),
                ]),
                on_click=lambda e, tid=t["task_id"]: open_task(tid),
                ink=True))
        for t in tasks[:5]:
            color = STATUS_COLOR.get(t["status"], palette.TEXT_2)
            on = t["task_id"] == state["task_id"]
            side_recent.controls.append(ft.Container(
                padding=ft.Padding(8, 6, 8, 6), border_radius=radius('small'),
                bgcolor=palette.SURFACE_3 if on else "transparent",
                content=ft.Row([
                    ft.Container(width=6, height=6, border_radius=3, bgcolor=color),
                    ft.Text(t["topic"], size=12, width=layout["sidebar_w"] - 56,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                            color=palette.TEXT if on else palette.TEXT_2),
                ], spacing=8),
                on_click=lambda e, tid=t["task_id"]: open_task(tid),
                ink=True))
        page.update()

    def start_task(e) -> None:
        if not tf_topic.value or not tf_topic.value.strip():
            page.show_dialog(ft.SnackBar(ft.Text("请先填写研究方向"), bgcolor=palette.TEXT))
            return
        # 首次建任务会同步导入检索引擎（约 2s）：放后台线程并给忙碌反馈，
        # 避免 UI 事件循环被占、界面像卡死
        topic, keywords = tf_topic.value, tf_kw.value or ""
        year_from = int(tf_y1.value) if tf_y1.value else None
        year_to = int(tf_y2.value) if tf_y2.value else None
        style, workflow = dd_style.value, dd_workflow.value
        offline = bool(home_offline.value)
        home_busy.value = "正在启动（首次加载检索引擎约需数秒）…"
        home_start_btn.disabled = True
        page.update()

        def work():
            try:
                tid = service.create_task(topic, keywords, year_from, year_to,
                                          style, workflow=workflow, offline=offline)
            except Exception as ex:  # noqa: BLE001
                home_busy.value = ""
                home_start_btn.disabled = False
                page.show_dialog(ft.SnackBar(ft.Text(f"启动失败：{ex}"), bgcolor=palette.DANGER))
                page.update()
                return
            home_busy.value = ""
            home_start_btn.disabled = False
            open_task(tid)

        page.run_thread(work)

    tf_topic.on_submit = start_task

    search_options = ft.Container(visible=False, content=ft.ResponsiveRow(
        [dd_workflow, tf_kw, tf_y1, tf_y2, dd_style], spacing=12, run_spacing=12))

    def toggle_search_options(e) -> None:
        search_options.visible = not search_options.visible
        page.update()

    def use_topic(topic: str) -> None:
        tf_topic.value = topic
        page.update()

    research_art = ft.Image(src=str(ROOT_DIR / "assets" / "research-study.svg"),
                            width=176, height=104, fit=ft.BoxFit.CONTAIN,
                            exclude_from_semantics=True)
    home_busy = ft.Text("", size=12, color=palette.TEXT_2)
    home_start_btn = ft.FilledButton("开始研究", icon=ft.Icons.ARROW_FORWARD,
                    on_click=start_task,
                    style=ft.ButtonStyle(bgcolor=palette.ACCENT, color=palette.ON_ACCENT))
    home_demo_button = ft.TextButton('体验离线研究', disabled=True,
        on_click=lambda e: onboarding.start_demo(), style=ft.ButtonStyle(color=palette.ACCENT_TEXT))
    form_card = ft.Container(width=PAGE_W, content=ft.Column([
        ft.Container(height=8),
        ft.Row([
            ft.Column([
                ft.Text("开始一项研究", size=ui_size("xl"), weight=ft.FontWeight.W_600,
                        color=palette.TEXT),
                ft.Text("检索文献，核对证据，整理摘要与参考文献。",
                        size=13, color=palette.TEXT_2),
            ], spacing=12, expand=True),
            research_art,
        ], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        tf_topic,
        search_options,
        ft.Row([
            ft.Row([ft.TextButton("检索条件", icon=ft.Icons.TUNE,
                          on_click=toggle_search_options,
                          style=ft.ButtonStyle(color=palette.TEXT_2)), home_offline], spacing=12),
            ft.Row([home_busy, home_start_btn], spacing=10),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        home_offline_note,
        ft.Row([
            ft.Text("试试", size=11, color=palette.TEXT_3),
            ft.TextButton("大模型工具调用", on_click=lambda e: use_topic("大语言模型智能体的工具调用与可靠性"),
                          style=ft.ButtonStyle(color=palette.TEXT_2)),
            ft.TextButton("检索增强生成", on_click=lambda e: use_topic("检索增强生成的证据质量与评估方法"),
                          style=ft.ButtonStyle(color=palette.TEXT_2)),
            home_demo_button,
            ft.TextButton('配置模型', on_click=lambda e: open_model_settings(),
                          style=ft.ButtonStyle(color=palette.TEXT_2)),
        ], spacing=4, wrap=True),
    ], spacing=12))
    hist_card = ft.Container(width=PAGE_W, padding=ft.Padding(0, 18, 0, 0),
        content=ft.Column([
            ft.Row([ft.Text("最近研究", size=14, weight=ft.FontWeight.W_600,
                            color=palette.TEXT), history_count],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            history_col,
        ], spacing=8))
    home_column = ft.Column([form_card, hist_card], spacing=20, width=PAGE_W,
                           scroll=ft.ScrollMode.AUTO, expand=True)
    page_new = ft.Container(expand=True, alignment=ft.Alignment(0, -1),
                            content=home_column)

    # ============ 页面 1：任务监控 ============
    # wrap=True：节点链在窄内容列里换行，而不是把整列撑破（flet 会整页不渲染）
    steps_row = ft.Row(spacing=8, wrap=True)
    log_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
    btn_pause = ft.FilledButton("暂停", icon=ft.Icons.PAUSE,
                                style=ft.ButtonStyle(bgcolor=palette.SURFACE_2, color=palette.TEXT))
    btn_resume = ft.FilledButton("人工确认 / 续跑", icon=ft.Icons.PLAY_ARROW,
                                 style=ft.ButtonStyle(bgcolor=palette.SUCCESS,
                                                      color=palette.BG))
    btn_cancel = ft.TextButton("取消任务", icon=ft.Icons.CANCEL_OUTLINED,
                               icon_color=palette.DANGER,
                               style=ft.ButtonStyle(color=palette.DANGER))
    btn_restart = ft.TextButton("整体重跑", icon=ft.Icons.REFRESH,
                                icon_color=palette.INFO,
                                style=ft.ButtonStyle(color=palette.INFO))
    monitor_title = ft.Text("尚未选择任务", size=16, weight=ft.FontWeight.BOLD,
                            color=palette.TEXT)
    status_chip_box = ft.Row()
    progress_bar = ft.ProgressBar(value=0, expand=True, color=palette.INFO,
                                  bgcolor=palette.BORDER,
                                  bar_height=6, border_radius=3)
    progress_text = ft.Text("", size=11, color=palette.TEXT_2)

    def render_steps(art: dict[str, Any], durations: dict[str, float] | None = None) -> None:
        ctx = service.get(state["task_id"]) if state["task_id"] else None
        seq = service.node_sequence(ctx.workflow if ctx else "research_pipeline")
        pointer = art.get("pointer")
        done_count = service.task_progress(ctx)[0] if ctx else 0
        done_nodes = {n["id"] for n in seq[:done_count]}
        status = art.get("status")
        steps_row.controls.clear()
        for i, n in enumerate(seq):
            nid = n["id"]
            if nid in done_nodes:
                color, icon = palette.SUCCESS, ft.Icons.CHECK_CIRCLE
            elif nid == pointer and status == "running":
                color, icon = palette.INFO, ft.Icons.SYNC
            elif nid == pointer and status == "waiting_human":
                color, icon = palette.ACCENT, ft.Icons.BACK_HAND
            else:
                color, icon = palette.BORDER, ft.Icons.CIRCLE_OUTLINED
            label = n["name"]
            dur = (durations or {}).get(nid)
            if dur is not None and nid in done_nodes:      # 与 TUI 一致：标出"哪一步慢"
                label = f"{label} {dur:g}s"
            steps_row.controls.append(ft.Column([
                ft.Icon(icon, color=color, size=26),
                # 限宽：节点名 Text 无宽度时会把整条链的固有宽度算大，撑破内容列
                ft.Text(label, size=11, color=palette.TEXT, width=112,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.CENTER),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2))
            if i < len(seq) - 1:
                steps_row.controls.append(
                    ft.Container(width=34, height=2, bgcolor=palette.BORDER,
                                 margin=ft.Margin(0, 0, 0, 22)))

    LOG_COLOR = {"error": palette.DANGER, "human": palette.WARN,
                 "workflow": palette.INFO, "node": palette.SUCCESS}

    def render_events(evts: list[dict[str, Any]]) -> None:
        if not evts:
            if not log_col.controls:
                log_col.controls.append(ft.Text("暂无执行记录。", size=12, color=palette.TEXT_3))
            return
        if state["seen_events"] == 0:
            log_col.controls.clear()
        for e in evts[state["seen_events"]:]:
            log_col.controls.append(ft.Text(
                f"[{e.get('ts','')}] {e.get('node') or e.get('kind')} — {e.get('message','')}",
                size=12, font_family=code_font_family(), width=layout["page_w"] - 96,
                color=LOG_COLOR.get(e.get("kind"), palette.TEXT_2)))
        state["seen_events"] = len(evts)
        if len(log_col.controls) > 800:   # 长任务：保留最近 800 条，防控件无限增长
            log_col.controls = log_col.controls[-800:]

    def do_cancel(e) -> None:
        if state["task_id"]:
            service.cancel(state["task_id"])
            page.show_dialog(ft.SnackBar(ft.Text("已请求取消（将在当前节点结束）"),
                                         bgcolor=palette.INFO))

    def do_resume(e) -> None:
        if not state["task_id"]:
            return
        try:
            service.resume(state["task_id"])
        except Exception as ex:  # noqa: BLE001
            page.show_dialog(ft.SnackBar(ft.Text(str(ex), max_lines=3,
                                                 overflow=ft.TextOverflow.ELLIPSIS), bgcolor=palette.DANGER))

    def do_restart(e) -> None:
        if not state["task_id"]:
            return
        try:
            service.restart(state["task_id"])
        except Exception as ex:  # noqa: BLE001
            page.show_dialog(ft.SnackBar(ft.Text(str(ex), max_lines=3,
                                                 overflow=ft.TextOverflow.ELLIPSIS), bgcolor=palette.DANGER))
            return
        state["seen_events"] = 0
        log_col.controls.clear()
        page.show_dialog(ft.SnackBar(ft.Text("已从头重跑任务"), bgcolor=palette.INFO))

    btn_pause.on_click = lambda e: (service.pause(state["task_id"]) if state["task_id"] else None)
    btn_resume.on_click = do_resume
    btn_cancel.on_click = do_cancel
    btn_restart.on_click = do_restart

    monitor_steps_card = soft_card(ft.Column([
        steps_row,
        ft.Container(height=6),
        ft.Row([progress_bar, progress_text], spacing=12,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ], spacing=4), padding=16, width=PAGE_W)
    monitor_log = ft.Container(
        expand=True, width=PAGE_W, bgcolor=palette.SURFACE_2,
        border_radius=radius('panel'), border=ft.Border.all(1, palette.BORDER),
        padding=14, content=ft.Column([
            ft.Text("执行记录", size=12, weight=ft.FontWeight.W_500, color=palette.TEXT_2),
            log_col,
        ], spacing=12, expand=True))
    monitor_col = ft.Column(width=PAGE_W, expand=True, spacing=12, controls=[
        ft.Row([monitor_title, status_chip_box], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        monitor_steps_card,
        ft.Row([btn_pause, btn_resume, btn_cancel, btn_restart],
               spacing=8, wrap=True),
        monitor_log,
    ])
    monitor_empty = ft.Container(
        visible=True, padding=ft.Padding(0, 36, 0, 0),
        content=ft.Column([
            ft.Text("还没有打开的研究", size=16, weight=ft.FontWeight.W_600,
                    color=palette.TEXT),
            ft.Text("从「新任务」输入课题，或点左侧最近会话继续。",
                    size=13, color=palette.TEXT_2),
            ft.FilledButton("开始研究", on_click=lambda e: nav_to(0),
                            style=ft.ButtonStyle(bgcolor=palette.ACCENT,
                                                 color=palette.ON_ACCENT)),
        ], spacing=10))
    hitl_bar = ft.Container(
        visible=False, bgcolor=palette.SURFACE_2, border_radius=radius('panel'),
        border=ft.Border.all(1, palette.ACCENT), padding=12, width=PAGE_W,
        content=ft.Row([
            ft.Text("文献池待确认", size=13, weight=ft.FontWeight.W_600,
                    color=palette.ACCENT_TEXT),
            ft.Text("核对去留后再放行。", size=12, color=palette.TEXT_2, expand=True),
            ft.TextButton("看文献池",
                          on_click=lambda e: _open_results(0),
                          style=ft.ButtonStyle(color=palette.TEXT_2)),
            ft.FilledButton(
                "确认放行", icon=ft.Icons.PLAY_ARROW,
                on_click=do_resume,
                style=ft.ButtonStyle(bgcolor=palette.SUCCESS, color=palette.BG)),
        ], spacing=10, wrap=True))
    # ---------- 看板页签：任务卡片网格 + 时间账横向条形图 ----------
    # 数据复用 src/dashboard.py 的纯计算（board/ledger/saved_minutes 本就从
    # ASCII 渲染中拆出，返回结构化字典）；这里只负责图形化，不动 ASCII 输出。
    board_summary = ft.Row(spacing=8, wrap=True)
    board_grid = ft.Row(spacing=10, run_spacing=10, wrap=True)
    board_bars = ft.Column(spacing=8)

    def _board_chip(text: str, color: str = palette.TEXT) -> ft.Container:
        return ft.Container(bgcolor=palette.SURFACE_3, border_radius=radius('small'),
                            padding=ft.Padding(10, 3, 10, 3),
                            content=ft.Text(text, size=11, color=color))

    def _board_card(t: dict[str, Any], works: int, rounds: int) -> ft.Container:
        """单任务卡片：题名 / 状态色 / 节点进度 / 文献·轮次·更新时间。"""
        color = STATUS_COLOR.get(t["status"], palette.TEXT_2)
        total = t.get("total_nodes") or 0
        done = t.get("done_nodes") or 0
        return ft.Container(
            width=280, bgcolor=palette.SURFACE_2, border_radius=radius('panel'), padding=12,
            border=ft.Border.all(1, palette.BORDER), ink=True,
            on_click=lambda e, tid=t["task_id"]: open_task(tid),
            content=ft.Column([
                ft.Row([
                    ft.Container(width=8, height=8, border_radius=4, bgcolor=color),
                    ft.Text(t.get("topic", ""), size=13, weight=ft.FontWeight.W_500,
                            color=palette.TEXT, expand=True, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=6),
                ft.Row([
                    ft.Text(t.get("status_text") or t.get("status", ""), size=11,
                            color=color),
                    ft.Text(f"{done}/{total} 节点", size=11, color=palette.TEXT_2),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.ProgressBar(value=done / total if total else 0, color=color,
                               bgcolor=palette.BORDER, bar_height=5, border_radius=3),
                ft.Text(f"文献 {works} 篇 · 递归 {rounds} 轮 · "
                        f"{str(t.get('created_at', ''))[:10] or '—'}",
                        size=10, color=palette.TEXT_3),
            ], spacing=8))

    def refresh_board() -> None:
        """任务卡片 + 时间账：复用 dashboard.ledger 聚合与 saved_minutes 口径。"""
        from .. import dashboard
        tasks = service.list_tasks()
        pools: dict[str, int] = {}
        round_counts: dict[str, int] = {}
        for t in tasks:
            ctx = service.get(t["task_id"])
            lit = (ctx.artifacts.get("lit_filter", {}) if ctx else {}) or {}
            pools[t["task_id"]] = len(lit.get("pool", []))
            round_counts[t["task_id"]] = len(lit.get("rounds", []))
        led = dashboard.ledger(tasks, pools, round_counts)
        board_summary.controls = [
            _board_chip(f"任务 {led['tasks']} 项"),
            _board_chip(f"已完成 {led['done']}"),
            _board_chip(f"文献 {led['works']} 篇"),
            _board_chip(f"省下 {led['saved_text']}", palette.ACCENT_TEXT),
        ]
        board_grid.controls = [
            _board_card(t, pools.get(t["task_id"], 0),
                        round_counts.get(t["task_id"], 0)) for t in tasks]
        if not tasks:
            board_grid.controls.append(ft.Text(
                "暂无任务档案——从「新任务」输入研究方向即开工。",
                size=12, color=palette.TEXT_2))
        per_task = sorted(
            ((t, dashboard.saved_minutes(pools.get(t["task_id"], 0),
                                         round_counts.get(t["task_id"], 0)))
             for t in tasks),
            key=lambda item: -item[1])[:8]
        top = max((minutes for _, minutes in per_task), default=0)
        board_bars.controls = []
        for t, minutes in per_task:
            bar_w = max(2, int(320 * minutes / top)) if top else 2
            board_bars.controls.append(ft.Row([
                ft.Text(t.get("topic", "")[:14], size=11, color=palette.TEXT_2,
                        width=150, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Container(width=bar_w, height=12, border_radius=6,
                             bgcolor=palette.ACCENT),
                ft.Text(f"{minutes} 分钟", size=11, color=palette.TEXT_2),
            ], spacing=8))

    board_view = ft.Container(
        expand=True, padding=ft.Padding(0, 8, 0, 0),
        content=ft.Column([
            ft.Row([
                ft.Text("科研看板", size=16, weight=ft.FontWeight.W_600,
                        color=palette.TEXT),
                ft.Row([board_summary,
                        ft.TextButton("刷新", icon=ft.Icons.REFRESH,
                                      on_click=lambda e: (refresh_board(), page.update()),
                                      style=ft.ButtonStyle(color=palette.TEXT_2))],
                       spacing=8, wrap=True),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Text("任务卡片", size=13, weight=ft.FontWeight.W_500, color=palette.TEXT_2),
            board_grid,
            ft.Text("研究时间账（跨任务累计 · 口径同 TUI 时间账）", size=13,
                    weight=ft.FontWeight.W_500, color=palette.TEXT_2),
            board_bars,
        ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True))

    monitor_tabs = ft.Tabs(
        selected_index=0, expand=True, length=2,
        content=ft.Column([
            ft.TabBar(tabs=[ft.Tab(label="执行监控"), ft.Tab(label="看板")]),
            ft.TabBarView(expand=True, controls=[monitor_col, board_view]),
        ], expand=True))
    page_monitor = ft.Container(
        expand=True,
        content=ft.Column([monitor_empty, hitl_bar, monitor_tabs],
                          spacing=12, expand=True))

    # ============ 页面 2：科研成果 ============
    pool_table = ft.DataTable(
        border_radius=radius('panel'), heading_row_color=palette.SURFACE_2,
        column_spacing=20, horizontal_margin=12,
        columns=[ft.DataColumn(ft.Text("题名", color=palette.TEXT_2)),
                 ft.DataColumn(ft.Text("作者 / 年 / 来源", color=palette.TEXT_2)),
                 ft.DataColumn(ft.Text("相关度", color=palette.TEXT_2)),
                 ft.DataColumn(ft.Text("去留理由", color=palette.TEXT_2)),
                 ft.DataColumn(ft.Text("", ))],
        rows=[])
    digest_md = MarkdownView("", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
    outline_md = MarkdownView("", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
    audit_md = MarkdownView("打开研究后可查看证据审计。", selectable=True,
                           extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
    cite_col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    cite_style_dd = ft.Dropdown(
        value="gbt7714", width=220, dense=True,
        options=[ft.dropdown.Option("gbt7714", "GB/T 7714—2015"),
                 ft.dropdown.Option("apa", "APA 7")])
    banner = ft.Container(visible=False, width=PAGE_W, bgcolor=palette.SURFACE_2,
                          border_radius=radius('panel'),
                          padding=12, content=ft.Text("", size=13, color=palette.WARN))
    result_meta = ft.Row(spacing=8)
    dir_picker = ft.FilePicker()
    page.services.append(dir_picker)

    def meta_chip(text: str) -> ft.Container:
        return ft.Container(bgcolor=palette.SURFACE_3, border_radius=radius('small'),
                            padding=ft.Padding(10, 3, 10, 3),
                            content=ft.Text(text, size=11, color=palette.INFO))

    def render_results(art: dict[str, Any], force: bool = False) -> None:
        pool = art.get("pool", [])
        # 变更检测：数据没变就不重建表格，避免轮询期间整表闪烁
        key = (state["task_id"], json.dumps(pool, ensure_ascii=False, sort_keys=True),
               art.get("digest_md"), art.get("outline_md"), art.get("status"))
        if key == state["results_key"] and not force:
            return
        state["results_key"] = key

        result_meta.controls = [
            meta_chip(f"文献 {len(pool)} 篇"),
            meta_chip(f"递归 {len(art.get('rounds', []))} 轮"),
            meta_chip(f"引用 {len(pool)} 条"),
        ]
        pool_table.rows = []
        for w in pool:
            score = w.get("score") or 0
            pool_table.rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(w.get("title", ""), size=12,
                                    width=max(170, int(layout["page_w"] * .27)),
                                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                ft.DataCell(ft.Text(
                    f"{', '.join((w.get('authors') or [])[:2])} / {w.get('year') or '-'} / {w.get('venue') or '-'}",
                    size=11, width=max(130, int(layout["page_w"] * .20)),
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                ft.DataCell(ft.Row([
                    ft.ProgressBar(value=min(score / 0.6, 1.0), width=60,
                                   color=palette.INFO, bgcolor=palette.BORDER),
                    ft.Text(f"{score:.2f}", size=10)])),
                ft.DataCell(ft.Text(w.get("reason", ""), size=11,
                                    width=max(130, int(layout["page_w"] * .19)),
                                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                ft.DataCell(ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE,
                                          icon_color=palette.DANGER, icon_size=18,
                                          tooltip="人工剔除",
                                          on_click=lambda e, wid=w.get("id"): drop_work(wid))),
            ]))
        digest_md.value = art.get("digest_md") or "摘要尚未生成。文献确认完成后会继续整理。"
        outline_md.value = art.get("outline_md") or "综述框架尚未生成。可在研究进度中查看当前步骤。"
        from ..research_analysis import analyze_artifacts, render_report
        audit_md.value = render_report(analyze_artifacts(art, art.get("topic", "")))
        render_citations(pool)
        render_graph_view(art)
        refresh_net_graph(art)
        banner.visible = False

    def render_citations(pool: list[dict[str, Any]]) -> None:
        style = cite_style_dd.value
        cite_col.controls.clear()
        for i, w in enumerate(pool, 1):
            try:
                text = format_entry(w, style)
            except Exception:  # noqa: BLE001
                continue
            prefix = f"[{i}] " if style == "gbt7714" else ""
            cite_col.controls.append(ft.Text(prefix + text, size=12, selectable=True))

    cite_style_dd.on_select = lambda e: render_results(
        service.artifacts_of(state["task_id"]), force=True) if state["task_id"] else None

    def copy_citations(e) -> None:
        if not state["task_id"]:
            return
        pool = service.artifacts_of(state["task_id"]).get("pool", [])
        lines = []
        for i, w in enumerate(pool, 1):
            t = format_entry(w, cite_style_dd.value)
            lines.append(f"[{i}] {t}" if cite_style_dd.value == "gbt7714" else t)
        page.run_task(page.clipboard.set, "\n".join(lines))
        page.show_dialog(ft.SnackBar(ft.Text("参考文献已复制到剪贴板"), bgcolor=palette.SUCCESS))

    async def choose_export_directory(task_id: str) -> None:
        try:
            directory = await dir_picker.get_directory_path(dialog_title="选择产物导出目录")
            if not directory:
                return
            files = await asyncio.to_thread(service.export_artifacts, task_id, directory)
            archive_note = ""
            if app_settings.load().get("git_archive", True):
                try:
                    from .. import research_repo
                    ctx = service.get(task_id)
                    await asyncio.to_thread(research_repo.commit_task, task_id,
                                            ctx.params.get("topic", "") if ctx else "")
                except Exception:
                    archive_note = "；Git 归档未完成，导出文件已保留"
            page.show_dialog(ft.SnackBar(
                ft.Text(f"已导出 {len(files)} 个文件到 {directory}{archive_note}"), bgcolor=palette.SUCCESS))
        except Exception as exc:
            page.show_dialog(ft.SnackBar(ft.Text(f"导出失败：{exc}"), bgcolor=palette.DANGER))

    def export_all(e) -> None:
        if state["task_id"]:
            page.run_task(choose_export_directory, state["task_id"])

    def drop_work(wid: str) -> None:
        if state["task_id"]:
            service.drop_works(state["task_id"], [wid])
            page.show_dialog(ft.SnackBar(ft.Text("已剔除（版本化留痕）"), bgcolor=palette.INFO))
            render_results(service.artifacts_of(state["task_id"]), force=True)
            page.update()

    pool_view = ft.Column([pool_table], scroll=ft.ScrollMode.AUTO, expand=True)
    # ---------- 证据图谱视图（成果页第 5 个页签） ----------
    graph_metrics = ft.Row(spacing=10, wrap=True)
    graph_tree = ft.Column(spacing=8)
    net_image = ft.Image(src="", visible=False, width=520, border_radius=radius('panel'))
    net_meta = ft.Text("", size=11, color=palette.TEXT_2)
    net_card = soft_card(ft.Column([
        ft.Row([ft.Text("池内引用网络", size=13, weight=ft.FontWeight.W_500,
                        color=palette.TEXT),
                net_meta]),
        net_image,
    ], spacing=6), padding=12)
    net_card.visible = False
    state["net_key"] = None

    def refresh_net_graph(art: dict[str, Any]) -> None:
        """引用网络 PNG（与锚定账本互补）：池不变时复用缓存，变化才重渲染。"""
        tid = state["task_id"]
        pool_n = len(art.get("pool", []))
        graph_key = (tid, service.evidence_graph_signature(art.get("pool") or [], art.get("topic", "")))
        if not tid or not pool_n:
            state["net_key"] = None
            net_card.visible = False
            return
        if (state["net_key"] == graph_key and net_card.visible
                and (not net_image.visible or (net_image.src and Path(net_image.src).is_file()))):
            return
        # 渲染约 0.7s（matplotlib 冷启动 + 绘制）：必须放到后台线程，
        # 否则会卡住 UI 事件循环（本函数由轮询回调触达）
        state["net_key"] = graph_key
        net_card.visible = True
        net_image.visible = False
        net_meta.value = "正在渲染引用网络…"

        def work() -> None:
            try:
                g = service.evidence_graph(tid)
            except Exception:  # noqa: BLE001 —— 图谱失败不影响成果页
                if state["task_id"] != tid or state["net_key"] != graph_key:
                    return
                net_card.visible = False
                page.update()
                return
            if state["task_id"] != tid or state["net_key"] != graph_key:
                return
            if g.get("file") and Path(g["file"]).exists():
                net_image.src = g["file"]
                net_image.visible = True
                total = g.get("pool_total") or pool_n
                net_meta.value = (f"文献池 {total} 篇"
                                  + (f"，图示相关度 Top {g['nodes']}" if total > g["nodes"] else "")
                                  + f" · 池内引用边 {g['edges']} 条 · 孤立 {g['isolated']} 篇"
                                  f"（节点编号即相关度排名，见右侧题名卡）")
            else:
                net_card.visible = False
            page.update()

        page.run_thread(work)

    def g_chip(text: str, color: str) -> ft.Container:
        return ft.Container(bgcolor=palette.SURFACE_2, border_radius=radius('panel'),
                            padding=ft.Padding(12, 5, 12, 5),
                            content=ft.Text(text, size=12, color=color))

    def render_graph_view(art: dict[str, Any]) -> None:
        """锚定率 + 章节→支撑文献 + 孤儿文献：把"哪块料没备齐"画出来。"""
        g = evidence.build_graph_from_art(art)
        rate = g["anchor_rate"]
        rate_color = (palette.SUCCESS if rate >= 85
                      else palette.WARN if rate >= 60 else palette.ACCENT)
        graph_metrics.controls = [
            g_chip(f"文献池 {g['pool']} 篇", palette.TEXT),
            g_chip(f"已锚定 {g['anchored']} 篇", palette.SUCCESS),
            g_chip(f"孤儿 {len(g['orphans'])} 篇",
                   palette.WARN if g["orphans"] else palette.TEXT_2),
            g_chip(f"缺证据章节 {len(g['missing_sections'])} 节",
                   palette.ACCENT if g["missing_sections"] else palette.TEXT_2),
            g_chip(f"防幻觉砍引 {g['dropped_refs']} 条", palette.INFO),
        ]
        graph_tree.controls = [
            ft.Row([ft.Text("锚定率", size=13, weight=ft.FontWeight.W_500,
                            color=palette.TEXT),
                    ft.ProgressBar(value=rate / 100, width=200, color=rate_color,
                                   bgcolor=palette.BORDER),
                    ft.Text(f"{rate}%（{g['anchored']}/{g['pool']} 篇被框架引用）",
                            size=12, color=rate_color)], spacing=10),
        ]
        if not g["sections"]:
            graph_tree.controls.append(ft.Text(
                "尚无综述框架：跑完「综述搭子·框架」节点后，这里会显示 章节 → 支撑文献 的树。",
                size=12, color=palette.TEXT_2))
        for sec in g["sections"]:
            missing = sec["missing"]
            color = palette.ACCENT if missing else palette.SUCCESS
            graph_tree.controls.append(soft_card(ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.CIRCLE, size=10, color=color),
                    ft.Text(sec["title"], size=13, weight=ft.FontWeight.W_500,
                            color=palette.TEXT),
                    ft.Text(f"{len(sec['refs'])} 篇支撑"
                            + ("　缺证据：写到这里会无话可说" if missing else ""),
                            size=11, color=color),
                ], spacing=8),
                ft.Column([ft.Text(f"· {t}", size=11, width=layout["page_w"] - 200,
                               color=palette.TEXT_3)
                           for t in sec["refs"][:6]], spacing=2),
            ], spacing=4), padding=12))
        if g["orphans"]:
            graph_tree.controls.append(ft.Text(
                f"孤儿文献（{len(g['orphans'])} 篇：进了池却没进任何章节，建议补进框架或判定剔除）",
                size=13, weight=ft.FontWeight.W_500, color=palette.WARN))
            for w in g["orphans"][:8]:
                score = w.get("score")
                head = f"[{score:.2f}] " if isinstance(score, (int, float)) else ""
                graph_tree.controls.append(ft.Text(f"· {head}{w.get('title', '')}",
                                                   width=layout["page_w"] - 80,
                                                   size=11, color=palette.TEXT_3))
            if len(g["orphans"]) > 8:
                graph_tree.controls.append(ft.Text(
                    f"· 另有 {len(g['orphans']) - 8} 篇，见「文献池」页签",
                    size=11, color=palette.TEXT_2))

    graph_view = ft.Container(
        ft.Column([graph_metrics, net_card, graph_tree], spacing=12,
                  scroll=ft.ScrollMode.AUTO, expand=True), padding=8)

    # 成果页第 7 个页签：确定性验收清单。只读（经桥接 outcome_checks 读取），
    # 人工复核入口只写独立的复核记录，不改写任务产物。
    outcome_panel = OutcomeCheckPanel(page, api=workspace_handle)

    def _result_tab_changed() -> None:
        # 验收清单在切到该页签时才读取；无任务时由面板给出明确空状态。
        if result_tabs.selected_index == 6:
            outcome_panel.refresh(state.get("task_id"))

    result_tabs = ft.Tabs(
        selected_index=0, expand=True, length=7,
        on_change=lambda e: _result_tab_changed(),
        content=ft.Column([
            ft.TabBar(tabs=[
                ft.Tab(label="文献池"), ft.Tab(label="摘要整理稿"),
                ft.Tab(label="综述框架"), ft.Tab(label="参考文献"),
                ft.Tab(label="证据图谱"), ft.Tab(label="证据审计"),
                ft.Tab(label="验收清单"),
            ]),
            ft.TabBarView(expand=True, controls=[
                ft.Container(pool_view, padding=8),
                ft.Container(ft.Column([digest_md], scroll=ft.ScrollMode.AUTO), padding=8),
                ft.Container(ft.Column([outline_md], scroll=ft.ScrollMode.AUTO), padding=8),
                ft.Container(
                    ft.Column([ft.Row([cite_style_dd,
                                       ft.FilledButton("一键复制", icon=ft.Icons.COPY,
                                                       on_click=copy_citations)]),
                               cite_col], spacing=8), padding=8),
                graph_view,
                ft.Container(ft.Column([audit_md], scroll=ft.ScrollMode.AUTO), padding=8),
                outcome_panel.content,
            ]),
        ], expand=True))

    def export_package(e) -> None:
        if not state["task_id"]:
            return
        try:
            result = workspace_handle({'cmd': 'research_package',
                                       'task_id': state["task_id"]})
            if not result.get('ok'):
                raise RuntimeError(result.get('error') or '导出失败')
            page.show_dialog(ft.SnackBar(ft.Text(
                f"研究包已导出：{Path(result['zip_path']).name}"), bgcolor=palette.SUCCESS))
        except Exception as exc:  # noqa: BLE001
            page.show_dialog(ft.SnackBar(ft.Text(f"研究包导出失败：{exc}"),
                                         bgcolor=palette.DANGER))

    result_col = ft.Column(width=PAGE_W, expand=True, spacing=12, controls=[
        ft.Row([ft.Text("科研成果", size=ui_size("xl"), weight=ft.FontWeight.W_600,
                        color=palette.TEXT),
                ft.Row([
                    ft.OutlinedButton("可复现研究包", icon=ft.Icons.INVENTORY_2_OUTLINED,
                                      on_click=export_package,
                                      style=ft.ButtonStyle(color=palette.TEXT)),
                    ft.FilledButton("导出全部产物", icon=ft.Icons.DOWNLOAD,
                                    on_click=export_all,
                                    style=ft.ButtonStyle(bgcolor=palette.SURFACE_2,
                                                         color=palette.TEXT)),
                ], spacing=8)],
               alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        result_meta, banner, result_tabs,
    ])
    page_result = ft.Container(expand=True, content=result_col)

    # ============ 页面 3：数据速览 ============
    file_picker = ft.FilePicker()
    page.services.append(file_picker)
    stats_area = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    async def choose_data_file() -> None:
        try:
            files = await file_picker.pick_files(
                dialog_title="选择 CSV / Excel / TXT", allow_multiple=False,
                allowed_extensions=["csv", "xlsx", "xls", "txt"])
            if files and files[0].path:
                load_data_file(Path(files[0].path))
        except Exception as exc:
            page.show_dialog(ft.SnackBar(ft.Text(f"读取文件失败：{exc}"), bgcolor=palette.DANGER))

    def load_data_file(src: Path) -> None:
        state["stats_epoch"] = state.get("stats_epoch", 0) + 1
        stats_epoch = state["stats_epoch"]
        # 后台线程解析，大文件不卡界面
        stats_area.controls.clear()
        stats_area.controls.append(ft.Row([
            busy_indicator('stats', 22),
            ft.Text(f"正在分析 {src.name} …", size=13, color=palette.TEXT_2)],
            spacing=10))
        page.update()

        def work() -> None:
            try:
                r = stats_agent.analyze(src)
            except Exception as ex:  # noqa: BLE001
                if state["stats_epoch"] != stats_epoch:
                    return
                stats_area.controls.clear()
                stats_area.controls.append(ft.Text(f"解析失败：{ex}",
                                                   size=13, color=palette.DANGER))
                page.update()
                return
            if state["stats_epoch"] == stats_epoch:
                render_stats(src.name, r)

        page.run_thread(work)

    def describe_table(r: dict[str, Any]) -> ft.Control | None:
        """描述统计表：行=统计量，列=数值变量（最多前 4 列防溢出）。"""
        desc = r.get("describe") or {}
        if not desc:
            return None
        cols = list(desc.keys())[:4]
        rows_spec = [("样本数", "count"), ("均值", "mean"), ("标准差", "std"),
                     ("最小", "min"), ("25%", "25%"), ("中位数", "50%"),
                     ("75%", "75%"), ("最大", "max")]
        table = ft.DataTable(
            data_row_min_height=36, data_row_max_height=42, heading_row_height=40,
            columns=[ft.DataColumn(ft.Text("统计量", size=12,
                                           weight=ft.FontWeight.BOLD))] +
                    [ft.DataColumn(ft.Text(c, size=12,
                                           weight=ft.FontWeight.BOLD), numeric=True) for c in cols],
            rows=[])
        for label, k in rows_spec:
            cells = [ft.DataCell(ft.Text(label, size=11, color=palette.TEXT_2))]
            for c in cols:
                v = desc.get(c, {}).get(k)
                cells.append(ft.DataCell(ft.Text(
                    f"{v:,.4g}" if isinstance(v, float) else str(v if v is not None else "-"),
                    size=11)))
            table.rows.append(ft.DataRow(cells=cells))
        return table

    def render_stats(name: str, r: dict[str, Any]) -> None:
        stats_area.controls.clear()
        stats_area.controls.append(ft.Text(
            f"数据 {name}：{r['shape'][0]} 行 × {r['shape'][1]} 列",
            size=14, weight=ft.FontWeight.BOLD, color=palette.TEXT))
        if r["missing"]:
            stats_area.controls.append(ft.Text(f"缺失值：{r['missing']}", size=12))
        if r.get("non_finite"):
            excluded = "、".join(f"{column} 列 {count} 个" for column, count in r["non_finite"].items())
            stats_area.controls.append(ft.Text(f"已排除无穷值：{excluded}；统计与图表仅使用有效样本。",
                                               size=12, max_lines=3, tooltip=excluded))
        dt = describe_table(r)
        if dt:
            stats_area.controls.append(ft.Text("描述统计", size=13,
                                               weight=ft.FontWeight.BOLD,
                                               color=palette.INFO))
            stats_area.controls.append(soft_card(dt, padding=8, width=layout["page_w"]))
        if r["outliers"]:
            for c, o in r["outliers"].items():
                stats_area.controls.append(ft.Container(
                    bgcolor=palette.SURFACE_2, border_radius=radius('panel'), padding=8,
                    content=ft.Text(f"⚠ {c} 列发现 {len(o['rows'])} 个异常值"
                                    f"（行 {o['rows'][:8]}…，规则 {o['rule']}，"
                                    f"正常区间 {o['bounds'][0]}~{o['bounds'][1]}）",
                                    size=12, color=palette.ACCENT)))
        for p in r["correlations"]:
            strength = "强" if abs(p["r"]) >= 0.8 else "中度"
            sample = f"，成对有效样本 n={p['n']}" if p.get("n") is not None else ""
            stats_area.controls.append(ft.Text(
                f"相关系数：{p['pair'][0]} 与 {p['pair'][1]} {strength}相关（r={p['r']}{sample}）",
                size=12))
        if r["chart_advice"]:
            stats_area.controls.append(ft.Text("图表建议", size=13,
                                               weight=ft.FontWeight.BOLD,
                                               color=palette.INFO))
            for a in r["chart_advice"]:
                stats_area.controls.append(ft.Text(f"· {a}", size=12))
        imgs = [ft.Image(src=str(ARTIFACT_DIR / c), width=420,
                         border_radius=radius('panel')) for c in r["charts"]]
        stats_area.controls.append(ft.Row(imgs, wrap=True, spacing=10))
        page.update()

    stats_col = ft.Column(width=PAGE_W, expand=True, spacing=12, controls=[
        ft.Row([ft.Text("数据速览", size=ui_size("xl"), weight=ft.FontWeight.W_600,
                        color=palette.TEXT),
                ft.FilledButton("上传实验数据", icon=ft.Icons.UPLOAD_FILE,
                                on_click=lambda e: page.run_task(choose_data_file),
                                style=ft.ButtonStyle(bgcolor=palette.ACCENT,
                                                     color=palette.ON_ACCENT)),
                ft.TextButton("试试内置示例数据",
                              on_click=lambda e: render_stats(
                                  "experiment_demo.csv",
                                  stats_agent.analyze(ROOT_DIR / "examples" / "experiment_demo.csv")))],
               alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        stats_area,
    ])
    page_stats = ft.Container(expand=True, content=stats_col)

    # ============ 页面 4：智能体工作台（权限面板） ============
    from ..agent_runtime import AgentRuntime

    def get_runtime(session_id: str | None = None) -> AgentRuntime:
        """Existing sessions retain their saved offline/online execution mode."""
        from ..bridge import _runtime, _session_runtime
        if session_id:
            saved = _session_runtime(session_id)
            if saved.sessions.snapshot(session_id).get('status') != 'draft':
                return saved
            # A newly created chat has no saved user turn/route yet. Treat its
            # first request exactly like executing before selecting a chat.
        on = llm.available("planning")
        return _runtime(offline=not on)

    CAP_STYLE = {"read": ("只读", palette.SUCCESS), "write": ("写入", palette.ACCENT),
                 "network": ("网络", palette.INFO), "external": ("外部", palette.ACCENT)}

    agent_request = ft.TextField(
        label="交给智能体的任务",
        hint_text="描述需要完成的研究工作…",
        multiline=True, min_lines=2, max_lines=5, border_radius=radius(),
        text_size=15, filled=True, bgcolor=palette.SURFACE_2,
        border=ft.InputBorder.NONE, content_padding=ft.Padding(2, 8, 2, 4),
        border_color=palette.BORDER_2, focused_border_color=palette.ACCENT_TEXT,
        shift_enter=True, tooltip="Enter 发送 · Shift+Enter 换行",
        on_submit=lambda e: agent_go('run') if not agent_run_btn.disabled else None)
    workspace_mode = {'value': app_settings.load().get('workspace_mode', 'research')}
    workspace_session_note = ft.Text('', size=11, color=palette.TEXT_3, max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS)
    workspace_mode_control = None
    workspace_title = ft.Text('科研智能体', size=ui_size("xl"), weight=ft.FontWeight.W_600,
                              color=palette.TEXT, expand=True)
    workspace_description = ft.Text('', size=12, color=palette.TEXT_2)
    workspace_examples = ft.Row(spacing=4, wrap=True)
    workspace_readiness = {'model': llm.available('planning')}
    workspace_chrome_ready = {'value': False}

    def refresh_workspace_context():
        mode = workspace_mode['value']
        sid = agent_state.get('session_id')
        saved_agent = agent_state.get('workspace_agent')
        existing = bool(sid)
        effective = (agent_state.get('workspace_mode') or mode_for_agent(saved_agent or agent_pick.value)) if existing else mode
        record = MODES[effective]
        workspace_title.value = f"{record['label']}智能体"
        workspace_description.value = record['description']
        agent_request.hint_text = record['description'] + ' · ' + record['placeholder']
        if workspace_chrome_ready['value']:
            agent_toolbox.title.value = '会话与工作区工具' if effective == 'general' else '研究与会话工具'
            quick_model_controls.button.content = '通用模型' if effective == 'general' else '科研模型'
            quick_model_controls.stage.label = '模型用途' if effective == 'general' else '科研阶段'
            quick_model_controls.dialog.title.value = f"{record['label']}模型 · 选择与思考"
            evidence_button.visible = effective == 'research'
        if onboarding is not None:
            onboarding.content.visible = effective == 'research'
        if effective == 'general':
            agent_mode_text.value = ('通用任务使用当前模型配置；写入和终端操作按权限确认。'
                if workspace_readiness['model'] else
                '配置模型后可执行通用任务；离线时仅提供工作区信息，不执行代码修改。')
        if existing:
            workspace_session_note.value = (f"当前会话：{record['label']} · 沿用原角色；"
                f"新建会话使用{MODES[mode]['label']}模式。")
        else:
            workspace_session_note.value = f"新会话：{record['label']}模式 · 写入与外部操作仍需按权限确认。"
        workspace_examples.visible = not existing or agent_empty.visible
        if workspace_examples.data == effective:
            return
        workspace_examples.data = effective
        workspace_examples.controls = []
        samples = (['阅读当前项目，说明结构和启动方式', '定位并修复一个问题，说明验证结果', '整理目录里的材料，先给出计划']
            if effective == 'general' else
            ['检索研究方向，列出可核验的文献', '对照支持与反驳证据', '分析实验数据并说明局限'])
        for sample in samples:
            def choose(e, text=sample):
                if (agent_request.value or '').strip():
                    page.show_dialog(ft.SnackBar(ft.Text('输入区已有草稿；请先提交或清空后选择示例。')))
                    return
                agent_request.value = text
                draft_changed(e)
                page.update()
            workspace_examples.controls.append(ft.TextButton(sample, on_click=choose,
                style=ft.ButtonStyle(color=palette.ACCENT_TEXT, icon_size=14)))

    def workspace_mode_changed(mode, navigate=True, *, set_draft_agent=True, update=True):
        workspace_mode['value'] = mode
        if set_draft_agent and not agent_state.get('session_id'):
            agent_pick.value = MODES[mode]['agent']
            if drafts is not None:
                drafts.capture(touched=True)
        refresh_workspace_context()
        for idx in (0, 1, 2, 3, 6):
            nav_buttons[idx].visible = mode == 'research'
        sidebar_subtitle.value = MODES[mode]['title']
        side_recent_heading.value = '最近研究' if mode == 'research' else '常用操作'
        side_recent.visible = mode == 'research'
        general_shortcuts.visible = mode == 'general'
        if navigate:
            # A mode switch is persisted by WorkspaceModeControl on a worker
            # thread.  Let it request the final single repaint after this
            # callback has changed the page chrome; nav_to can therefore defer
            # its own update during that path.
            nav_to(4 if mode == 'general' else 0, update=update)
        elif update:
            page.update()
        return bool(update)
    agent_pick = ft.Dropdown(
        label="专项智能体", value="research", width=200, border_radius=radius(), filled=True,
        bgcolor=palette.SURFACE_2,
        options=[ft.dropdown.Option(spec.name, spec.title)
                 for spec in ROSTER.values()])
    agent_mode = ft.Dropdown(
        label="执行模式", value="agent", width=180, border_radius=radius(), filled=True,
        bgcolor=palette.SURFACE_2,
        options=[ft.dropdown.Option("agent", "执行任务"),
                 ft.dropdown.Option("plan", "只读规划"),
                 ft.dropdown.Option("review", "证据审查")])
    agent_run_btn = ft.FilledButton(
        "执行任务", icon=ft.Icons.ARROW_FORWARD, on_click=lambda e: agent_go("run"),
        style=ft.ButtonStyle(bgcolor=palette.ACCENT, color=palette.ON_ACCENT))
    agent_plan_btn = ft.OutlinedButton(
        "预览计划", icon=ft.Icons.SCHEMA,
        on_click=lambda e: agent_go("plan"))
    agent_continue_btn = ft.OutlinedButton("继续会话", icon=ft.Icons.PLAY_ARROW, visible=False,
                                           on_click=lambda e: control_agent("agent_continue"))
    agent_cancel_btn = ft.TextButton("停止会话", icon=ft.Icons.STOP_CIRCLE_OUTLINED, disabled=True,
                                     on_click=lambda e: control_agent("agent_cancel"),
                                     style=ft.ButtonStyle(color={ft.ControlState.DEFAULT: palette.DANGER,
                                         ft.ControlState.DISABLED: palette.TEXT_3}))
    agent_answer = MarkdownView("", selectable=True, visible=False,
                               extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
    agent_mode_text = ft.Text(size=12, color=palette.TEXT_2)
    agent_mode_help = ft.Text('', size=11, color=palette.TEXT_3, max_lines=2,
                              overflow=ft.TextOverflow.ELLIPSIS)

    def refresh_agent_actions(e=None):
        mode = agent_mode.value or 'agent'
        agent_run_btn.content = {'plan': '生成只读计划', 'review': '执行证据审查'}.get(mode, '执行任务')
        agent_mode_help.value = {'plan': '只生成并保存计划，不执行工具。',
            'review': '复核材料与结论，工具操作仍受权限控制。'}.get(mode, '执行任务请求，写入与外部操作按权限策略确认。')
        if e is not None:
            page.update()

    agent_mode.on_select = refresh_agent_actions
    refresh_agent_actions()
    agent_plan_col = ft.Column(spacing=6)
    agent_plan_card = soft_card(ft.Column([
        agent_plan_col], spacing=6), padding=12, width=PAGE_W - 360)
    agent_plan_card.visible = False
    agent_approval_card = ft.Container(visible=False)
    agent_log_col = ft.Column(spacing=0, visible=False)
    agent_session_label = ft.Text("", size=11, color=palette.TEXT_2)
    agent_sessions_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    thinking_strokes = [ft.Container(width=3, height=8 + i * 3, border_radius=2,
                                    bgcolor=palette.ACCENT, opacity=.3) for i in range(3)]
    agent_busy = ft.Row(visible=False, spacing=8, controls=[
        ft.Row(thinking_strokes, spacing=3, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Text("正在执行…", size=12,
                color=palette.TEXT_2)])

    onboarding = None

    def refresh_agent_mode_text() -> None:
        refresh_home_offline_note()
        if onboarding is not None:
            onboarding.refresh()
            return
        on = llm.available("planning")
        agent_mode_text.value = (
            "新会话使用模型配置，尚未验证连接；已有会话沿用原执行方式。"
            if on else "新会话使用规则模式与内置离线示例；可先体验流程，再配置模型。")

    refresh_agent_mode_text()

    def cap_chip(cap: str) -> ft.Container:
        label, color = CAP_STYLE.get(cap, (cap, palette.TEXT_2))
        return chip(f"{label}", color)

    def render_plan(plan: dict[str, Any]) -> None:
        agent_empty.visible = False
        agent_plan_card.visible = True
        agent_plan_col.controls = [
            ft.Text(f"执行计划：{plan.get('summary', '')}", size=12,
                    weight=ft.FontWeight.BOLD, color=palette.TEXT)]
        for i, s in enumerate(plan.get("steps", []), 1):
            agent_plan_col.controls.append(ft.Row([
                ft.Text(f"{i}.", size=12, color=palette.TEXT_2),
                ft.Text(s.get("tool", ""), size=12, font_family=code_font_family(),
                        color=palette.INFO, weight=ft.FontWeight.W_600,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                cap_chip(s.get("capability", "")
                         if "capability" in s else _cap_of(s.get("tool", ""))),
                ft.Text(s.get("reason", ""), size=11, color=palette.TEXT_2,
                        expand=True),
            ], spacing=6))

    def _cap_of(tool_name: str) -> str:
        rt = get_runtime()
        return rt.tools[tool_name].capability if tool_name in rt.tools else "read"

    def _summarize_arg(value: Any) -> str:
        """审批参数摘要：超长值截断（file.write 的正文由 diff 展示，不必全量）。"""
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 160 else text[:160] + "…"

    def _approval_diff_block(session_id: str, tool: str,
                             arguments: dict[str, Any]) -> ft.Control | None:
        """只读比较文件现状与待执行编辑，沿用代码显示偏好并标明截断。"""
        from ..tools_workspace import _read_text, _resolve_in_root, _resolve_root
        try:
            root = _resolve_root(get_runtime(session_id), session_id)
            path = _resolve_in_root(root, arguments.get("path"))
            old_text = _read_text(path) if path.is_file() else ""
            if tool == "file.write":
                new_text = str(arguments.get("content") or "")
            elif tool == "file.edit":
                lines = old_text.splitlines()
                start = int(arguments.get("start_line") or 0)
                end = int(arguments.get("end_line") or 0)
                if not path.is_file() or not 1 <= start <= end <= len(lines):
                    return ft.Text("行区间越界，无法预览这次修改。", size=11, color=palette.WARN)
                new_text = "\n".join(lines[:start - 1]
                                     + str(arguments.get("new_text") or "").splitlines()
                                     + lines[end:]) + "\n"
            elif tool == "file.str_replace":
                old = str(arguments.get("old_str") or "")
                if not old:
                    return ft.Text("替换目标不能为空，无法预览这次修改。", size=11, color=palette.WARN)
                count = old_text.count(old)
                if count != 1:
                    return ft.Text(f"替换目标匹配 {count} 次，需要唯一匹配才能修改。", size=11, color=palette.WARN)
                new_text = old_text.replace(old, str(arguments.get("new_str") or ""), 1)
            else:
                return None
            if len(old_text.splitlines()) + len(new_text.splitlines()) > 4000:
                return ft.Text("文件过大，diff 预览已省略（可在终端自行核对）。",
                               size=11, color=palette.TEXT_3)
        except Exception:  # noqa: BLE001 —— 读取失败不阻断审批
            return None
        diff_iter = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(),
                                         fromfile=f"a/{path.name}", tofile=f"b/{path.name}",
                                         lineterm="")
        lines = list(itertools.islice(diff_iter, 261))
        if not lines:
            return ft.Text("变更后文件内容与现状一致（无差异）。", size=11, color=palette.TEXT_3)
        rows: list[ft.Control] = [CodePreview("\n".join(lines[:260]), language='diff')]
        if len(lines) > 260:
            rows.append(ft.Text("仅展示前 260 行差异，后续修改已省略；请核对完整文件后审批。",
                                size=11, color=palette.WARN, selectable=True))
        return ft.Container(bgcolor=palette.BG, border_radius=radius('panel'),
            border=ft.Border.all(1, palette.BORDER), padding=8,
            height=min(300, 64 + len(lines) * 24),
            content=ft.Column(rows, scroll=ft.ScrollMode.AUTO, spacing=1))

    def close_approval_dialog() -> None:
        """关闭全局审批弹窗（若打开）；程序触发的关闭不算用户主动忽略。"""
        if agent_state.pop("approval_dialog_id", None) is None:
            return
        agent_state["approval_closing"] = True
        try:
            page.pop_dialog()
        except Exception:  # noqa: BLE001
            pass
        agent_state["approval_closing"] = False

    def _approval_dismissed(e) -> None:
        """用户主动关闭弹窗：记录该 pending，轮询不再反复弹出。"""
        if agent_state.get("approval_closing"):
            return
        pid = agent_state.get("approval_open_id")
        if pid:
            agent_state["approval_dismissed"] = pid
        agent_state["approval_dialog_id"] = None

    def render_approval(pending: dict[str, Any], session_id: str | None = None) -> None:
        """全局审批弹窗：任意会话的 waiting_approval 都会弹出，与所在页无关。"""
        approval_session = session_id or agent_state["session_id"]
        label, color = CAP_STYLE.get(pending.get("capability", ""),
                                     ("工具", palette.TEXT_2))
        arguments = pending.get("arguments") or {}
        args_text = "\n".join(f"  {k}: {_summarize_arg(v)}" for k, v in
                              arguments.items()) or "  （无参数）"
        tool_name = str(pending.get("tool", ""))

        extras: list[ft.Control] = []
        # 调用前展示目标、影响、风险分类与可逆性。
        action = pending.get("action") or {}
        if action:
            extras.append(ft.Column([
                ft.Text(f"风险类：{action.get('risk', '')} · 所需档位：{action.get('tier', '')}",
                        size=11, color=palette.TEXT_2),
                ft.Text(f"目标：{action.get('target', '')}", size=11, color=palette.TEXT_2),
                ft.Text(f"影响：{action.get('impact', '')}", size=11, color=palette.TEXT_2),
                ft.Text("可逆：是（可按留痕恢复）" if action.get("reversible")
                        else "可逆：否（副作用不可自动撤销，请谨慎批准）",
                        size=11, color=palette.TEXT_3),
            ], spacing=2))
        if tool_name in ("file.write", "file.edit", "file.str_replace"):
            diff_block = _approval_diff_block(approval_session, tool_name, arguments)
            if diff_block is not None:
                extras += [ft.Text("变更预览（统一 diff · 绿=新增 红=删除）：",
                                   size=11, color=palette.TEXT_2), diff_block]
        elif tool_name == "shell.run":
            command = str(arguments.get("command") or "")

            async def copy_command(e) -> None:
                try:
                    await page.clipboard.set(command)
                except Exception:
                    page.show_dialog(ft.SnackBar(
                        ft.Text("复制失败，请重试或选中命令复制"), bgcolor=palette.DANGER))
                    return
                page.show_dialog(ft.SnackBar(
                    ft.Text("命令已复制到剪贴板"), bgcolor=palette.SUCCESS))

            extras.append(ft.Row([
                ft.Text("命令：", size=12, color=palette.TEXT_2),
                ft.Text(command, size=12, font_family=code_font_family(),
                        color=palette.TEXT, selectable=True, expand=True),
                ft.IconButton(ft.Icons.COPY_OUTLINED, icon_size=16,
                              icon_color=palette.TEXT_2, tooltip="复制命令",
                              on_click=copy_command),
            ], spacing=8))
            if arguments.get("cwd"):
                extras.append(ft.Text(f"工作目录：{arguments['cwd']}",
                                      size=11, color=palette.TEXT_3))

        def decide(decision: str) -> None:
            def _do(e) -> None:
                # 以弹窗当前展示的 pending 为准，防止跨会话串批
                if agent_state.get("approval_open_id") != pending.get("id"):
                    return
                view_epoch = agent_state["view_epoch"]
                agent_state["approval_open_id"] = None
                close_approval_dialog()
                agent_busy.visible = True
                agent_approval_card.disabled = True
                page.update()

                def work() -> None:
                    try:
                        rt = get_runtime(approval_session)
                        r = rt.approve(approval_session,
                                       pending["id"], decision)
                    except Exception as ex:  # noqa: BLE001
                        page.show_dialog(ft.SnackBar(
                            ft.Text(f"审批失败：{ex}"), bgcolor=palette.DANGER))
                        agent_busy.visible = False
                        agent_approval_card.disabled = False
                        page.update()
                        return
                    if (view_epoch == agent_state["view_epoch"]
                            and approval_session == agent_state["session_id"]):
                        render_agent_result(r)
                    agent_approval_card.disabled = False
                    agent_busy.visible = False
                    page.update()

                page.run_thread(work)

            return _do

        agent_approval_card.visible = True
        agent_approval_card.disabled = False
        agent_approval_card.bgcolor = palette.SURFACE_2
        agent_approval_card.border_radius=radius('panel')
        agent_approval_card.border = ft.Border.all(1, palette.BORDER_2)
        agent_approval_card.padding = 18
        agent_approval_card.content = ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.GAVEL, color=color, size=20),
                ft.Text("需要你确认", size=14, weight=ft.FontWeight.W_600,
                        color=color),
                cap_chip(pending.get("capability", "")),
                ft.Text(f"会话 {str(approval_session or '')[:8]}…", size=11,
                        color=palette.TEXT_3),
            ], spacing=8),
            ft.Text(f"工具 {tool_name} 请求执行：", size=12),
            ft.Text(args_text, size=11, font_family=code_font_family(),
                    color=palette.TEXT),
            *extras,
            ft.Row([
                ft.FilledButton("批准执行", on_click=decide("allow"),
                                style=ft.ButtonStyle(bgcolor=palette.SUCCESS,
                                                     color=palette.BG)),
                ft.OutlinedButton("批准并本会话记住", on_click=decide("allow_session")),
                ft.OutlinedButton("拒绝", on_click=decide("deny"),
                                  style=ft.ButtonStyle(color=palette.DANGER)),
            ], spacing=8, wrap=True),
        ], spacing=6)
        style_inputs(agent_approval_card)
        # 提升为全局弹窗：记录当前 pending，去重后弹出
        agent_state["approval_open_id"] = pending.get("id")
        agent_state["approval_session"] = approval_session
        if agent_state.get("approval_dialog_id") != pending.get("id"):
            agent_state["approval_dialog_id"] = pending.get("id")
            page.show_dialog(ft.AlertDialog(
                modal=False, on_dismiss=_approval_dismissed,
                content=ft.Container(width=560, content=agent_approval_card)))

    approval_scan_at = [0.0]

    def check_global_approvals() -> None:
        """Bounded approval discovery, including separately journaled children.

        Top-level status projections prefilter snapshots. Child metadata paths
        only locate journals: stale terminal metadata must never hide a pending
        approval. Each scan visits at most 100 child paths and keeps its iterator
        for the next batch; snapshots reuse SessionStore's incremental cache.
        """
        import re

        if agent_state.get("approval_dialog_id"):
            return  # 已有弹窗展示
        now = time.monotonic()
        if now - approval_scan_at[0] < 1.5:
            return
        approval_scan_at[0] = now
        rt = get_runtime()
        try:
            rows = rt.sessions.list()
        except Exception:  # noqa: BLE001
            return
        approval_session = agent_state.get("approval_session")
        open_key = (approval_session, agent_state.get("approval_open_id"))
        dismissed = agent_state.get("approval_dismissed")
        dismissed_keys = agent_state.setdefault("approval_dismissed_keys", set())
        if dismissed and approval_session:
            dismissed_keys.add((approval_session, dismissed))
        if rows and all("status" in row for row in rows):
            candidates = [row for row in rows
                          if str(row.get("status") or "") == "waiting_approval"]
        else:
            candidates = rows   # Old projections still use bounded snapshots.

        def inspect(sid: str) -> bool:
            try:
                snap = rt.sessions.snapshot(sid)
            except Exception:  # noqa: BLE001
                return False
            pending = snap.get("pending")
            if snap.get("status") == "waiting_approval" and pending:
                pid = pending.get("id")
                key = (sid, pid)
                if pid and key not in dismissed_keys and key != open_key:
                    # Reset the legacy scalar signal before showing another
                    # session, so equal call IDs cannot impersonate its dismissal.
                    agent_state["approval_dismissed"] = None
                    render_approval(pending, sid)
                    return True
            return False

        offset = agent_state.get("approval_scan_offset", 0) % max(1, len(candidates))
        agent_state["approval_scan_offset"] = offset + 20
        for index in range(min(20, len(candidates))):
            row = candidates[(offset + index) % len(candidates)]
            if inspect(row["session_id"]):
                return

        root = rt.sessions.root
        scan = agent_state.get("approval_child_scan")
        if scan is None or scan[0] != str(root):
            scan = (str(root), iter(root.glob("*/children/*.meta.json")))
            agent_state["approval_child_scan"] = scan
        seen, restarted = set(), False
        for _ in range(100):
            try:
                path = next(scan[1])
            except StopIteration:
                if restarted:
                    agent_state["approval_child_scan"] = None
                    return
                # Pick up metadata added behind the previous iterator without
                # waiting for a second polling interval. Restart at most once.
                scan = (str(root), iter(root.glob("*/children/*.meta.json")))
                agent_state["approval_child_scan"] = scan
                restarted = True
                continue
            except OSError:
                agent_state["approval_child_scan"] = None
                return
            sid = path.name.removesuffix(".meta.json")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid) or sid in seen:
                continue
            seen.add(sid)
            if inspect(sid):
                return
        warning = "本轮最多检查100条子会话；其余将继续分批扫描。完整待确认列表可在控制塔查看。"
        if agent_state.get("approval_scan_warning") != warning:
            agent_state["approval_scan_warning"] = warning
            page.show_dialog(ft.SnackBar(ft.Text(warning, max_lines=3,
                                                 overflow=ft.TextOverflow.ELLIPSIS)))

    def render_events_log(events: list[dict[str, Any]]) -> None:
        events = events[-200:]
        signature = (agent_state.get('session_id'), events)
        if signature == agent_state.get('log_view'):
            return
        agent_state['log_view'] = signature
        agent_log_col.visible = True
        agent_log_wrap.visible = bool(events)
        meaningful = {'user_message', 'assistant_message', 'assistant_delta', 'plan_created',
                      'tool_call', 'tool_result', 'permission_requested', 'error'}
        agent_empty.visible = not agent_busy.visible and not any(
            event.get('type') in meaningful for event in events)
        workspace_examples.visible = agent_empty.visible
        agent_log_col.controls = []
        event_names = {"user_message": "研究请求", "agent_selected": "分派智能体",
                       "plan_created": "执行计划", "tool_call": "调用工具",
                       "tool_result": "工具结果", "permission_requested": "等待确认",
                       "permission_granted": "已批准", "permission_denied": "已拒绝",
                       "completed": "执行结束", "error": "执行失败", "assistant_message": "模型响应",
                       "driver_started": "模型工具循环", "driver_paused": "等待科研任务", "cancel_requested": "请求停止"}
        for e in events:
            typ = e.get("type", "")
            p = e.get("payload") or {}
            detail = ""
            if typ == "tool_call":
                detail = f"{p.get('name', '')} {p.get('arguments', {})}"
            elif typ == "tool_result":
                detail = str(p.get("result", ""))[:140]
            elif typ == "plan_created":
                detail = (p.get("plan") or {}).get("summary", "")
            elif typ in ("permission_requested", "permission_granted",
                         "permission_denied"):
                detail = f"{p.get('tool', '')} ({p.get('capability', '')})"
            elif typ == "user_message":
                detail = str(p.get("text", ""))[:120]
            elif typ == "agent_selected":
                spec = ROSTER.get(p.get("agent", ""))
                detail = spec.title if spec else str(p.get("agent", ""))
            elif typ == "error":
                detail = str(p.get("message", ""))
            elif typ == "completed" and p.get("status") in {"blocked", "error"}:
                # 受阻回合以 completed 事件落盘：把原始 message/reason 留在日志，
                # 用户才能在「会话记录」里核验弹窗提到的原因（内部码只在此出现）。
                detail = " · ".join(str(part) for part in
                                    (p.get("message") or "", p.get("reason") or "") if part)
            elif typ == "assistant_message":
                detail = str(p.get("content", ""))[:300]
                if p.get("tool_calls"):
                    detail = "、".join(c.get("name", "") for c in p["tool_calls"])
            elif typ == "driver_paused":
                detail = f"任务 {p.get('task_id', '')} 尚未结束，处理任务后继续会话。"
            color = {"error": palette.DANGER, "permission_requested": palette.WARN,
                     "permission_denied": palette.DANGER,
                     "permission_granted": palette.SUCCESS}.get(typ, palette.TEXT_2)
            agent_log_col.controls.append(ft.Container(
                padding=ft.Padding(0, 10, 0, 10),
                border=ft.Border.only(bottom=ft.BorderSide(1, palette.BORDER)),
                content=ft.Row([
                    ft.Text(e.get("timestamp", "")[11:19], size=10,
                            font_family=code_font_family(), color=palette.TEXT_3, width=62),
                    ft.Column([
                        ft.Text(event_names.get(typ, typ), size=12,
                                color=color, weight=ft.FontWeight.W_500,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(detail, size=12, color=palette.TEXT, selectable=True,
                                visible=bool(detail)),
                    ], spacing=4, expand=True),
                ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)))

    def render_agent_result(r: dict[str, Any]) -> None:
        status = r.get("status", "")
        agent_state["session_id"] = r.get("session_id", agent_state["session_id"])
        if r.get('workspace_mode') in MODES:
            agent_state['workspace_mode'] = r['workspace_mode']
        if r.get('agent'):
            agent_state['workspace_agent'] = r['agent']
        status_labels = {"draft": "新会话", "waiting_approval": "等待确认", "done": "已完成",
                         "blocked": "已阻止", "error": "执行失败", "planned": "计划已就绪",
                         "waiting_task": "等待科研任务", "cancelled": "已停止", "cancelling": "正在停止"}
        agent_state["status"] = status
        agent_continue_btn.visible = status in {"waiting_task", "waiting_children", "paused"}
        agent_cancel_btn.disabled = status in {"draft", "done", "blocked", "error", "cancelled", "planned"}
        agent_answer.value = r.get("content") or ""
        agent_answer.visible = bool(agent_answer.value)
        agent_session_label.value = f"{status_labels.get(status, status)} · {agent_state['session_id'] or ''}"
        if r.get("budget"):
            budget = r["budget"]
            agent_session_label.value += f" · 模型调用 {budget.get('model_calls', 0)}/{budget.get('max_steps', 0)}"
            if not r.get("usage_complete", True):
                agent_session_label.value += " · 部分用量未报送"
        if r.get("context", {}).get("compressions"):
            agent_session_label.value += f" · 已压缩 {r['context']['compressions']} 次"
        if r.get("plan"):
            render_plan(r["plan"])
        if status == "waiting_approval":
            if r.get("pending"):
                render_approval(r["pending"])
            agent_approval_card.visible = True
        else:
            agent_approval_card.visible = False
            if agent_state.get("approval_session") in (None, agent_state["session_id"]):
                agent_state["approval_open_id"] = None
                close_approval_dialog()
        if status in _TERMINAL_STATUS_LABEL:
            primary, detail_line = _terminal_notice(r, status)
            page.show_dialog(ft.SnackBar(ft.Column([
                ft.Text(f"{_TERMINAL_STATUS_LABEL[status]}：{primary}", size=13,
                        max_lines=3, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(detail_line, size=11, max_lines=3,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=2), bgcolor=palette.DANGER))
        agent_empty.visible = False
        refresh_sessions()
        composer.refresh()
        refresh_workspace_context()
        session_status.refresh(force=True)
        activity_panel.refresh(force=True)
        session_queue.refresh(force=True)
        if composer.transcript.controls:
            agent_answer.visible = False

    agent_state: dict[str, Any] = {"session_id": None, "view_epoch": 0}

    def control_agent(command: str) -> None:
        sid, epoch = agent_state["session_id"], agent_state["view_epoch"]
        if not sid or (command == "agent_continue" and agent_busy.visible):
            return
        if command == "agent_continue":
            agent_busy.visible = True
            page.update()
        def work():
            from ..bridge import handle
            response = handle({"cmd": command, "session_id": sid})
            if epoch == agent_state["view_epoch"]:
                if response.get("ok"):
                    render_agent_result(response["result"])
                else:
                    page.show_dialog(ft.SnackBar(ft.Text(response.get("error", "操作失败"),
                                                         max_lines=3, overflow=ft.TextOverflow.ELLIPSIS),
                                                 bgcolor=palette.DANGER))
            if command == "agent_continue":
                agent_busy.visible = False
            session_status.refresh(force=True)
            page.update()
        page.run_thread(work)

    def agent_go(kind: str) -> None:
        if agent_busy.visible:
            return
        submitted_text = agent_request.value or ""
        req = submitted_text.strip()
        if not req:
            page.show_dialog(ft.SnackBar(ft.Text("请先填写任务请求"),
                                         bgcolor=palette.TEXT))
            return
        submitted_session_id = agent_state.get("session_id")
        submitted_project_id = state.get('project_id') or ''
        submitted_workspace_mode = workspace_mode['value']
        rt = get_runtime(submitted_session_id)
        mode = agent_mode.value or "agent"
        picked = agent_pick.value or "research"
        draft_ticket = drafts.submitted() if kind != 'plan' else None
        agent_state["view_epoch"] += 1
        view_epoch = agent_state["view_epoch"]
        agent_busy.visible = True
        agent_run_btn.disabled = True
        agent_plan_btn.disabled = True
        agent_approval_card.visible = False
        agent_plan_card.visible = False
        agent_log_wrap.visible = False
        agent_empty.visible = False
        agent_session_label.value = "正在规划…" if kind == "plan" else "正在执行…"
        agent_continue_btn.visible = False
        agent_answer.visible = False
        page.update()

        def work() -> None:
            accepted = False
            try:
                if kind == "plan":
                    plan = rt.plan(req, "plan", picked, workspace_mode=submitted_workspace_mode)
                    if view_epoch == agent_state["view_epoch"]:
                        render_plan(plan)
                        agent_session_label.value = "计划预览 · 尚未执行"
                else:
                    def started(sid):
                        if view_epoch == agent_state["view_epoch"]:
                            if not submitted_session_id:
                                drafts.adopt_session(sid, draft_ticket)
                            agent_state["session_id"] = sid
                            agent_state['status'] = 'running'
                            agent_state['workspace_agent'] = picked
                            if not submitted_session_id:
                                agent_state['workspace_mode'] = submitted_workspace_mode
                            refresh_workspace_context()
                            onboarding.update_context()
                            agent_cancel_btn.disabled = False
                            agent_session_label.value = f"执行中 · {sid}"
                            page.update()
                    sid = submitted_session_id
                    if not sid:
                        created = workspace_handle({'cmd':'chat_new', 'project_id':submitted_project_id, 'title':req[:70],
                            'workspace_mode': submitted_workspace_mode, 'agent': picked})
                        if not created.get('ok'):
                            raise ValueError(created.get('error', '无法新建会话'))
                        sid = created['session_id']
                    started(sid)
                    response = workspace_handle({'cmd':'chat_turn', 'session_id':sid, 'request':req,
                        'mode':mode, 'agent':picked, 'online':not rt.offline})
                    if not response.get('ok'):
                        raise ValueError(response.get('error', '会话执行失败'))
                    accepted = True
                    r = response['result']
                    if view_epoch == agent_state["view_epoch"]:
                        render_agent_result(r)
                        composer.refresh()
                        agent_answer.visible = bool(agent_answer.value) and not composer.transcript.controls
            except Exception as ex:  # noqa: BLE001
                page.show_dialog(ft.SnackBar(ft.Text(f"智能体失败：{ex}"),
                                             bgcolor=palette.DANGER))
            finally:
                if draft_ticket is not None:
                    drafts.finish(draft_ticket, accepted=accepted)
            agent_busy.visible = False
            agent_run_btn.disabled = False
            agent_plan_btn.disabled = False
            session_status.refresh(force=True)
            page.update()

        page.run_thread(work)

    def select_agent_session(sid: str) -> None:
        store = get_runtime(sid).sessions
        snapshot = store.snapshot(sid)
        agent_state["view_epoch"] += 1
        agent_state["session_id"] = sid
        agent_state['status'] = snapshot.get('status', 'unknown')
        agent_state['workspace_agent'] = snapshot.get('agent') or (
            MODES[workspace_mode['value']]['agent'] if snapshot.get('status') == 'draft' else 'research')
        agent_state['workspace_mode'] = snapshot.get('workspace_mode') or mode_for_agent(agent_state['workspace_agent'])
        onboarding.update_context()
        agent_approval_card.visible = False
        agent_plan_card.visible = False
        drafts.select(sid, defaults={'agent': snapshot.get('agent', 'research'),
            'mode': snapshot.get('mode') if snapshot.get('mode') in {'agent', 'plan', 'review'} else 'agent'})
        refresh_agent_actions()
        refresh_workspace_context()
        agent_continue_btn.visible = (snapshot.get("status") == "waiting_children" or
            snapshot.get("status") in {"waiting_task", "paused", "running"} and snapshot.get("driver") == "adaptive")
        agent_cancel_btn.disabled = snapshot.get("status") in {"draft", "done", "blocked", "error", "cancelled", "planned"}
        agent_answer.value = snapshot.get("content") or ""
        agent_answer.visible = bool(agent_answer.value)
        if snapshot.get("plan"):
            render_plan(snapshot["plan"])
        if snapshot.get("pending"):
            render_approval(snapshot["pending"], sid)
        else:
            # 打开的会话没有待审批：关闭可能属于其他会话的全局弹窗
            agent_state["approval_open_id"] = None
            agent_state["approval_session"] = None
            close_approval_dialog()
        labels = {"draft": "新会话", "done": "已完成", "planned": "已规划", "blocked": "已阻止",
                  "error": "执行失败", "waiting_approval": "等待确认",
                  "running": "上次执行尚未结束", "unknown": "历史会话"}
        agent_session_label.value = f"{labels.get(snapshot['status'], snapshot['status'])} · {sid}"
        composer.refresh()
        session_status.refresh(force=True)
        activity_panel.refresh(force=True)
        session_queue.refresh(force=True)
        agent_log_wrap.visible = snapshot['status'] != 'draft' and bool(composer.recent_events)
        agent_answer.visible = bool(agent_answer.value) and not composer.transcript.controls
        if agent_state.pop("sessions_dialog", False):
            page.pop_dialog()
        page.update()

    def refresh_sessions() -> None:
        rt = get_runtime()
        rows = rt.sessions.list()[:8]
        agent_sessions_col.controls = []
        if not rows:
            agent_sessions_col.controls.append(
                ft.Text("暂无会话（运行一次智能体后可审计）", size=11,
                        color=palette.TEXT_2))
        for srow in rows:

            def open_session(e, sid=srow["session_id"]) -> None:
                select_agent_session(sid)

            agent_sessions_col.controls.append(ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text(srow.get("summary") or "未命名会话", size=13,
                                color=palette.TEXT, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(srow["session_id"], size=10,
                                font_family=code_font_family(), color=palette.TEXT_3),
                    ], spacing=4, expand=True),
                    ft.Text(f"{srow['events']} 条记录", size=11, color=palette.TEXT_2),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=14,
                            color=palette.TEXT_2),
                ], spacing=6),
                ink=True, on_click=open_session, border_radius=radius('control'),
                padding=ft.Padding(6, 4, 6, 4)))

    refresh_sessions()

    # ---- 工具权限策略编辑器（持久化到 settings.tool_permissions） ----
    policy_dropdowns: dict[str, ft.Dropdown] = {}
    policy_tier_dropdown: list[ft.Dropdown] = []

    def _default_decision(capability: str) -> str:
        return "allow" if capability == "read" else "ask"

    def build_policy_editor() -> ft.Control:
        rt = get_runtime()
        current = app_settings.load().get("tool_permissions") or {}
        from .. import research_permissions
        tier_now = research_permissions.normalize_tier(
            app_settings.load().get("agent_permission_tier"))
        tier_dd = ft.Dropdown(
            width=220, dense=True, border_radius=radius('control'), value=tier_now,
            options=[ft.dropdown.Option(t, research_permissions.TIER_LABELS[t])
                     for t in research_permissions.TIERS],
            tooltip="档位只决定哪些动作进入审批流；工具级允许/询问/拒绝仍按下方策略")
        policy_tier_dropdown.append(tier_dd)
        matrix_rows = [
            ft.Row([
                ft.Text(row["label"], size=12, color=palette.TEXT, width=88),
                *[ft.Text(f"{research_permissions.RISK_LABELS[risk]}："
                          f"{'放行' if ok else '拦截'}",
                          size=12, width=110,
                          color=palette.TEXT if ok else palette.DANGER)
                  for risk, ok in row["risks"].items()],
            ], spacing=6)
            for row in research_permissions.matrix()]
        rows: list[ft.Control] = [
            ft.Text("科研权限三档门", size=13, weight=ft.FontWeight.W_600, color=palette.TEXT),
            ft.Text("档位是门槛：不覆盖的动作直接拒绝并说明所需档位，调整后可重试；"
                    "覆盖内的写入仍按下方工具策略逐次询问。", size=11, color=palette.TEXT_3),
            tier_dd,
            ft.Column(matrix_rows, spacing=4),
            ft.Divider(color=palette.BORDER, height=16),
        ]
        for name, spec in rt.tools.items():
            dd = ft.Dropdown(
                width=156, dense=True, border_radius=radius('control'),
                value=current.get(name, _default_decision(spec.capability)),
                options=[ft.dropdown.Option("allow", "允许"),
                         ft.dropdown.Option("ask", "每次询问"),
                         ft.dropdown.Option("deny", "拒绝")])
            policy_dropdowns[name] = dd
            rows.append(ft.Row([
                ft.Text(name, size=10, font_family=code_font_family(), expand=True),
                cap_chip(spec.capability),
                dd,
            ], spacing=4))
        return ft.Column(rows, spacing=10, expand=True, scroll=ft.ScrollMode.AUTO)

    policy_col = build_policy_editor()

    def save_policy(e) -> None:
        current = app_settings.load().get("tool_permissions") or {}
        rt = get_runtime()
        merged = dict(current)
        for name, dd in policy_dropdowns.items():
            spec = rt.tools.get(name)
            want = dd.value
            if spec and want and want != _default_decision(spec.capability):
                merged[name] = want
            else:
                merged.pop(name, None)  # 与默认一致则清除覆盖
        payload: dict[str, Any] = {"tool_permissions": merged}
        if policy_tier_dropdown:
            payload["agent_permission_tier"] = policy_tier_dropdown[0].value
        app_settings.save(payload)
        get_runtime()  # 策略签名变化 → 自动重建运行时
        page.show_dialog(ft.SnackBar(ft.Text("权限策略已保存并即时生效"),
                                     bgcolor=palette.SUCCESS))

    def reset_policy(e) -> None:
        # 按各工具能力回默认：只读放行，写入/网络回到审批
        rt = get_runtime()
        for name, dd in policy_dropdowns.items():
            spec = rt.tools.get(name)
            if spec:
                dd.value = _default_decision(spec.capability)
        app_settings.save({"tool_permissions": {}})
        get_runtime()
        page.show_dialog(ft.SnackBar(ft.Text("权限策略已重置为默认（只读放行，其余审批）"),
                                     bgcolor=palette.INFO))

    def show_agent_sessions(e) -> None:
        refresh_sessions()
        agent_state["sessions_dialog"] = True
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("智能体会话"),
            content=ft.Container(width=620, height=400, content=agent_sessions_col),
            actions=[ft.TextButton("关闭", on_click=lambda e: page.pop_dialog())]))

    def show_agent_policy(e) -> None:
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("工具权限"),
            content=ft.Container(width=620, height=430,
                content=ft.Column([
                    ft.Text("为工具选择询问、允许或拒绝。保存后对新执行生效。",
                            size=12, color=palette.TEXT_2),
                    policy_col,
                ], spacing=16, expand=True)),
            actions=[
                ft.TextButton("重置", on_click=reset_policy),
                ft.TextButton("关闭", on_click=lambda e: page.pop_dialog()),
                ft.FilledButton("保存权限策略", on_click=save_policy,
                                style=ft.ButtonStyle(bgcolor=palette.ACCENT, color=palette.ON_ACCENT)),
            ]))

    agent_log_wrap = ft.Container(width=PAGE_W, visible=False,
        content=ft.ExpansionTile(title=ft.Text('最近运行记录 · 最多 200 条', size=12, color=palette.TEXT_2),
            controls=[agent_log_col], dense=True, tile_padding=0, maintain_state=True,
            shape=ft.RoundedRectangleBorder(radius=0), collapsed_shape=ft.RoundedRectangleBorder(radius=0)))
    agent_empty = ft.Container(padding=ft.Padding(12, 28, 12, 20),
        content=ft.Column([
            ft.Container(content=ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED,
                size=26, color=palette.ACCENT_TEXT), width=52, height=52,
                alignment=ft.Alignment.CENTER, border_radius=radius('dialog'),
                bgcolor=palette.ACCENT_BG),
            ft.Text("从一个问题开始", size=24, weight=ft.FontWeight.W_500,
                    color=palette.TEXT, text_align=ft.TextAlign.CENTER),
            ft.Text("描述目标，或添加材料。计划、工具结果和确认操作会留在同一会话中。",
                    size=14, color=palette.TEXT_2, text_align=ft.TextAlign.CENTER),
            workspace_examples,
        ], spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER))
    from .session_composer import SessionComposer
    def draft_view_changed():
        composer.update_preview()
        refresh_agent_actions()
        session_queue.draft_changed()
        session_steering.draft_changed()
        refresh_workspace_context()

    def draft_changed(e=None):
        if drafts is not None:
            drafts.capture(touched=e is not None)
        draft_view_changed()

    def open_new_draft_session(sid, preserve_input):
        if preserve_input:
            ticket = drafts.submitted()
            try:
                drafts.adopt_session(sid, ticket)
            finally:
                drafts.finish(ticket, accepted=False)
        select_agent_session(sid)

    composer = SessionComposer(page, agent_request, lambda: agent_state['session_id'],
        lambda: state['project_id'], select_agent_session, lambda: on_resized(),
        event_page_provider=lambda sid, **options: get_runtime(sid).sessions.event_page(sid, **options),
        events_changed=render_events_log, on_draft_changed=draft_changed,
        open_new_session=open_new_draft_session)
    from .session_queue import SessionQueuePanel
    session_queue = SessionQueuePanel(page, agent_request, lambda: agent_state['session_id'],
        epoch=lambda: agent_state['view_epoch'], on_draft_changed=draft_changed,
        draft_revision=lambda: drafts.revision() if drafts is not None else None)
    from .session_steering import SessionSteeringPanel
    session_steering = SessionSteeringPanel(page, agent_request, lambda: agent_state['session_id'],
        epoch=lambda: agent_state['view_epoch'], on_draft_changed=draft_changed,
        on_continue=lambda: control_agent('agent_continue'),
        draft_revision=lambda: drafts.revision() if drafts is not None else None)

    from .composer_drafts import ComposerDrafts
    drafts = ComposerDrafts(page, agent_request, agent_pick, agent_mode, changed=draft_view_changed)
    drafts.select(None, state.get('project_id') or '', defaults={'agent': MODES[workspace_mode['value']]['agent']})
    agent_request.on_change = draft_changed
    agent_pick.on_select = draft_changed
    agent_mode.on_select = draft_changed
    from .evidence_brief import EvidenceBriefPanel
    from .evidence_reviews import EvidenceReviewInbox
    review_inbox = EvidenceReviewInbox(page)
    evidence_panel = EvidenceBriefPanel(page, agent_request, lambda: state.get('task_id'),
        on_draft_changed=draft_changed, open_records=lambda: nav_to(6), open_reviews=review_inbox.open)
    evidence_button = ft.TextButton('证据对照', icon=ft.Icons.FACT_CHECK_OUTLINED,
        on_click=lambda e: evidence_panel.open())
    from .session_status import SessionStatusPanel
    from .control_tower import STATUS as SESSION_STATUS

    def observe_session_status(row):
        if row.get('session_id') != agent_state.get('session_id') or agent_busy.visible:
            return False
        status = row.get('status') or 'unknown'
        label = f"{SESSION_STATUS.get(status, status)} · {row['session_id']}"
        changed = agent_session_label.value != label
        agent_state['status'] = status
        agent_session_label.value = label
        idle = status in {'unknown', 'draft', 'done', 'planned'}
        cannot_cancel = status in {'unknown', 'draft', 'done', 'blocked', 'error', 'cancelled', 'planned'}
        changed = changed or agent_run_btn.disabled == idle or agent_cancel_btn.disabled != cannot_cancel
        agent_run_btn.disabled = not idle
        agent_cancel_btn.disabled = cannot_cancel
        return changed

    session_status = SessionStatusPanel(page, lambda: agent_state['session_id'],
        epoch=lambda: agent_state['view_epoch'], busy=lambda: agent_busy.visible,
        on_status=observe_session_status)
    from .onboarding import OnboardingPanel

    def readiness_changed(row):
        if row is None:
            agent_mode_text.value = '新会话配置暂不可读，请打开模型设置检查。'
            home_demo_button.disabled = True
        else:
            workspace_readiness['model'] = row.get('agent_mode') == 'model'
            agent_mode_text.value = ('新会话使用模型配置，尚未验证连接；已有会话沿用原执行方式。'
                if row.get('agent_mode') == 'model' else
                '新会话使用规则模式与内置离线示例；已有会话沿用原执行方式。')
            home_demo_button.disabled = onboarding.demo_button.disabled
        refresh_workspace_context()

    def demo_state_changed(pending):
        home_demo_button.disabled = onboarding.demo_button.disabled
        if pending:
            home_busy.value = '正在启动离线示例…'
        elif home_busy.value == '正在启动离线示例…':
            home_busy.value = ''

    onboarding = OnboardingPanel(page, agent_request, open_models=open_model_settings,
        open_demo=lambda tid: open_task(tid), api=workspace_handle,
        on_ready=readiness_changed, on_example=draft_changed, on_demo_state=demo_state_changed,
        selected=lambda: agent_state.get('session_id') if agent_state.get('status') not in {'draft', 'unknown'} else None)
    from .control_tower import ControlTower, ReasoningControl
    control_tower = ControlTower(page, open_task=lambda tid: open_panel_task(tid),
        open_session=lambda sid: open_panel_session(sid), request=lambda: agent_request.value)
    reasoning_motion = {"enabled": bool(app_settings.load().get("ui_animations", True))}

    def reasoning_changed(value):
        set_reasoning.value = value

    reasoning_control = ReasoningControl(page,
        value=app_settings.load().get("llm_reasoning_effort", "auto"),
        save=lambda patch: workspace_handle({"cmd": "settings_save", "settings": patch}),
        motion=lambda: reasoning_motion["enabled"], changed=reasoning_changed)

    trail_seen = {"signature": None}

    def refresh_effort_trail():
        """Show what the last request carried; never infer from the saved dial."""
        from .. import logging_util
        path = getattr(logging_util, "_MODEL_LOG", None)
        try:
            stat = path.stat() if path is not None else None
        except OSError:
            stat = None
        signature = (str(path), stat.st_mtime_ns, stat.st_size) if stat else (str(path), None, None)
        if signature == trail_seen["signature"]:
            return  # unchanged log: keep the rendered line, skip the re-read
        trail_seen["signature"] = signature
        try:
            receipts = logging_util.usage_summary().get("reasoning", {}).get("receipts") or []
        except (OSError, ValueError, TypeError):
            receipts = []
        reasoning_control.show_trail(receipts[-1] if receipts else None)

    refresh_effort_trail()
    from .model_controls import ModelControlPanel
    def quick_model_saved():
        reasoning_control.refresh(app_settings.load().get("llm_reasoning_effort", "auto"))
        set_reasoning.value = reasoning_control.value
        refresh_mode_text()
        refresh_mode_badge()
    quick_model_controls = ModelControlPanel(page, api=workspace_handle, on_saved=quick_model_saved)
    from .session_activity import SessionActivityPanel
    activity_panel = SessionActivityPanel(page, lambda: agent_state['session_id'],
        epoch=lambda: agent_state['view_epoch'], open_session=lambda sid: open_panel_session(sid))
    from .terminal_panel import TerminalPanel
    # 内建终端：UI 输入视为已授权，复用 tools_workspace 的 shell 实现
    terminal = TerminalPanel(page, get_runtime, composer.ensure_session)
    agent_options = ft.ExpansionTile(title=ft.Text('执行选项', size=12, color=palette.TEXT_2),
        controls=[workspace_description, workspace_session_note, agent_mode_text,
                  ft.Row([agent_pick, agent_mode], spacing=12, wrap=True), agent_mode_help,
                  reasoning_control.content,
                  session_queue.toggle, session_steering.toggle], dense=True,
        min_tile_height=34, tile_padding=0, controls_padding=ft.Padding(0,0,0,10),
        collapsed_shape=ft.RoundedRectangleBorder(radius=0),
        shape=ft.RoundedRectangleBorder(radius=0), maintain_state=True)
    agent_toolbox = ft.ExpansionTile(title=ft.Text('研究与会话工具', size=12, color=palette.TEXT_2),
        controls=[ft.Row([
            ft.TextButton('控制塔', icon=ft.Icons.DASHBOARD_OUTLINED, on_click=lambda e: control_tower.open('tower')),
            ft.TextButton('Swarm 协作', icon=ft.Icons.HUB_OUTLINED, on_click=lambda e: control_tower.open('swarm')),
            evidence_button,
            ft.TextButton('复核收件箱', icon=ft.Icons.INBOX_OUTLINED, on_click=lambda e: review_inbox.open()),
        ], spacing=8, wrap=True), composer.toolbar], dense=True, min_tile_height=30, tile_padding=0,
        controls_padding=ft.Padding(0, 0, 0, 8), maintain_state=True,
        shape=ft.RoundedRectangleBorder(radius=0), collapsed_shape=ft.RoundedRectangleBorder(radius=0))
    session_details = ft.ExpansionTile(
        title=ft.Text('会话状态与上下文', size=12, color=palette.TEXT_2),
        controls=[agent_session_label, session_status.content, activity_panel.content, composer.info],
        dense=True, min_tile_height=30, tile_padding=0,
        controls_padding=ft.Padding(0, 0, 0, 8), maintain_state=True,
        shape=ft.RoundedRectangleBorder(radius=0), collapsed_shape=ft.RoundedRectangleBorder(radius=0))
    # Long histories and expanded tools scroll independently of the request
    # and execution controls, preserving a reachable stop action.
    # Keep current execution progress visible above the transcript, while its
    # expanded details share the history scroller and leave the composer reachable.
    session_status.content.controls.remove(session_status.execution_plan.content)
    agent_activity = ft.Column([
        session_status.execution_plan.content,
        composer.preview, agent_empty, composer.transcript, agent_plan_card, agent_answer,
        session_queue.content, session_steering.content,
        agent_options, agent_toolbox, onboarding.content,
        session_details, agent_log_wrap, terminal.body,
    ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
    # Reparent the original controls as one docked composer; their callbacks,
    # draft generations and session identities stay attached to the same objects.
    agent_composer = ft.Container(width=PAGE_W, padding=12, border_radius=radius('panel'),
        bgcolor=palette.SURFACE_2, border=ft.Border.all(1, palette.BORDER_2),
        content=ft.Column([
            agent_request, composer.attachments,
            ft.Row([quick_model_controls.button, agent_plan_btn, agent_run_btn,
                    agent_busy], spacing=8, wrap=True),
            ft.Row([*composer.primary_actions.controls,
                    agent_continue_btn, agent_cancel_btn,
                    session_queue.add_button, session_steering.add_button],
                   spacing=4, wrap=True),
            drafts.content,
        ], spacing=4))
    agent_column = ft.Column([
        ft.Row([
            workspace_title,
            ft.IconButton(ft.Icons.HISTORY, tooltip="最近会话", icon_size=18,
                          icon_color=palette.TEXT_2, on_click=show_agent_sessions),
            ft.IconButton(ft.Icons.TUNE, tooltip="工具权限", icon_size=18,
                          icon_color=palette.TEXT_2, on_click=show_agent_policy),
            ft.IconButton(ft.Icons.TERMINAL, tooltip="终端", icon_size=18,
                          icon_color=palette.TEXT_2, on_click=lambda e: terminal.toggle()),
        ], spacing=8),
        agent_activity,
        agent_composer,
    ], width=PAGE_W, spacing=8)
    page_agent = ft.Container(expand=True, content=ft.Row([agent_column, composer.side], spacing=14,
        expand=True, alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH))

    # ============ 页面 5：设置 ============
    s = app_settings.load()
    set_url = ft.TextField(label="服务地址", hint_text="https://…/v1",
                           value=s["llm_base_url"], border_radius=8, filled=True,
                           bgcolor=palette.SURFACE_2)
    set_key = ft.TextField(label="API 密钥", hint_text="无需鉴权的本地服务可留空",
                           value=s["llm_api_key"], password=True,
                           can_reveal_password=True, border_radius=8, filled=True,
                           bgcolor=palette.SURFACE_2)
    set_model = ft.TextField(label="默认模型名", value=s["llm_model"],
                             border_radius=8, filled=True, bgcolor=palette.SURFACE_2)
    set_model_fast = ft.TextField(
        label="文献筛选模型（可选）",
        value=s.get("llm_model_fast", ""), border_radius=8, filled=True,
        bgcolor=palette.SURFACE_2)
    set_model_strong = ft.TextField(
        label="综述生成模型（可选）",
        value=s.get("llm_model_strong", ""), border_radius=8, filled=True,
        bgcolor=palette.SURFACE_2)
    set_emb_url = ft.TextField(
        label="向量服务地址（可选）", hint_text="https://…/v1（OpenAI 兼容 /embeddings）",
        value=s.get("embedding_base_url", ""), border_radius=8, filled=True,
        bgcolor=palette.SURFACE_2)
    set_emb_key = ft.TextField(
        label="向量 API 密钥（可选）", hint_text="无需鉴权的本地服务可留空",
        value=s.get("embedding_api_key", ""), password=True,
        can_reveal_password=True, border_radius=8, filled=True,
        bgcolor=palette.SURFACE_2)
    set_emb_model = ft.TextField(
        label="向量模型名（可选）",
        value=s.get("embedding_model", ""), border_radius=8, filled=True,
        bgcolor=palette.SURFACE_2)
    from ..model_registry import PROTOCOLS, THINKING_STYLES, EFFORTS
    protocol_labels = {"chat_completions": "Chat Completions · 兼容协议", "responses": "OpenAI Responses",
                       "anthropic_messages": "Anthropic Messages", "gemini": "Gemini generateContent", "ollama": "Ollama 原生"}
    set_protocol = ft.Dropdown(label="默认连接协议", value=s.get("llm_protocol", "chat_completions"),
        options=[ft.dropdown.Option(p, protocol_labels[p]) for p in PROTOCOLS], col=12)
    effort_labels = {"auto": "供应商默认", "off": "关闭思考", "minimal": "最低", "low": "低", "medium": "中", "high": "高", "xhigh": "很高", "max": "最高"}
    set_reasoning = ft.Dropdown(label="思考强度", value=s.get("llm_reasoning_effort", "auto"),
        options=[ft.dropdown.Option(p, effort_labels[p]) for p in EFFORTS], col=6)
    set_thinking = ft.Dropdown(label="思考参数方式", value=s.get("llm_thinking_style", "auto"),
        options=[ft.dropdown.Option(p, p) for p in THINKING_STYLES], col=6)
    set_budget = ft.TextField(label="思考预算 / tokens", value=str(s.get("llm_thinking_budget", 4096)),
                              input_filter=ft.NumbersOnlyInputFilter(), col=6)
    set_output = ft.TextField(label="总输出上限 / tokens", value=str(s.get("llm_max_output_tokens", 8192)),
                              input_filter=ft.NumbersOnlyInputFilter(), col=6)
    set_cache = ft.Switch(label="发送显式缓存提示（自动缓存仍由平台决定）", value=s.get("llm_cache_enabled", True))
    set_driver = ft.Dropdown(label="Agent 执行方式", value=s.get("agent_execution", "adaptive"),
        options=[ft.dropdown.Option("adaptive", "模型工具循环"), ft.dropdown.Option("planned", "固定计划")], col=12)
    set_steps = ft.TextField(label="最多模型步骤", value=str(s.get("agent_max_steps", 16)),
                             input_filter=ft.NumbersOnlyInputFilter(), col=6)
    set_token_budget = ft.TextField(label="累计用量停止阈值", value=str(s.get("agent_token_budget", 100000)),
                                    input_filter=ft.NumbersOnlyInputFilter(), col=6)
    model_options = ft.ResponsiveRow([set_protocol, set_reasoning, set_thinking, set_budget, set_output],
                                     spacing=12, run_spacing=14, key=ft.ScrollKey("model_options"))
    driver_options = ft.ResponsiveRow([set_driver, set_steps, set_token_budget], spacing=12, run_spacing=14)
    set_tau = ft.Slider(label="相关性阈值 {value}", value=s["lit_threshold"],
                        min=0.05, max=0.5, divisions=9)
    set_round = ft.Slider(label="递归最大轮次 {value}", value=s["lit_max_round"],
                          min=1, max=5, divisions=4)
    set_crossref = ft.Switch(label="CrossRef 双源并行检索（首轮）",
                             value=bool(s.get("enable_crossref", True)))
    feature_labels = {
        "llm_enabled": "使用模型增强", "default_offline": "新任务默认使用离线示例",
        "git_archive": "导出时保存 Git 研究档案", "mcp_enabled": "启用 MCP 外部工具",
        "plugins_enabled": "启用插件系统", "desktop_inspector": "宽屏默认显示研究详情",
        "ui_animations": "播放思考与启动动效",
    }
    feature_switches = {key: ft.Switch(label=label, value=bool(s.get(key, True)))
                        for key, label in feature_labels.items()}
    model_status = ft.Text("", size=12, color=palette.TEXT_2, selectable=True)
    model_choices = ft.Dropdown(label="服务端可用模型", visible=False,
                                on_select=lambda e: (setattr(set_model, "value", model_choices.value), page.update()))
    plugin_rows = ft.Column(spacing=8)

    def refresh_plugins() -> None:
        from .. import plugins
        summary = plugins.summary()
        plugin_rows.controls = []
        for item in summary["names"]:
            def toggle_plugin(e, name=item["name"]):
                try:
                    plugins.set_enabled(name, bool(e.control.value))
                    refresh_plugins()
                    page.update()
                except Exception as exc:
                    page.show_dialog(ft.SnackBar(ft.Text(str(exc), max_lines=3,
                                                         overflow=ft.TextOverflow.ELLIPSIS), bgcolor=palette.DANGER))
            plugin_rows.controls.append(ft.Container(
                padding=ft.Padding(0, 8, 0, 8), border=ft.Border.only(bottom=ft.BorderSide(1, palette.BORDER)),
                content=ft.Column([
                    ft.Row([ft.Text(f"{item['name']}  {item.get('version', '')}", size=13,
                                    color=palette.TEXT, expand=True),
                            ft.Switch(value=item.get("enabled", True), label="启用",
                                      disabled=not app_settings.load().get("plugins_enabled", True),
                                      on_change=toggle_plugin)]),
                    ft.Text(item.get("description", ""), size=12, color=palette.TEXT_2, width=640),
                    ft.Text(" ".join('/' + c for c in item.get('commands', [])),
                            size=11, font_family=code_font_family(), color=palette.ACCENT,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=4)))
        for error in summary.get("errors", []):
            plugin_rows.controls.append(ft.Text(error, size=12, color=palette.DANGER, width=640))
        if not summary["names"]:
            plugin_rows.controls.append(ft.Text("暂无插件。可将本地插件放入项目 plugins 目录后重载。", size=12))

    def reload_plugins(e) -> None:
        from .. import plugins
        plugins.load(force=True)
        refresh_plugins()
        page.update()

    def show_skills(e) -> None:
        from .. import skills
        rows = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        stage = ft.Dropdown(label="查看阶段", value="planning", options=[
            ft.dropdown.Option("planning", "研究规划"), ft.dropdown.Option("literature", "文献终审"),
            ft.dropdown.Option("extraction", "证据抽取"), ft.dropdown.Option("audit", "证据复核"),
            ft.dropdown.Option("experiment", "实验审查")])
        info = ft.Text("", size=12, color=palette.TEXT_2, width=600)
        def refresh(e=None):
            preferences = app_settings.load()
            active, disabled = set(preferences.get("skills_active", [])), set(preferences.get("skills_disabled", []))
            selection = skills.select_for_stage(stage.value)
            selected_names = {s["name"] for s in selection["selected"]}
            info.value = (f"当前配置 · 本阶段可选 {len(selected_names)} 个技能索引，正文按需读取。"
                          "实际加载请查看会话的执行上下文来源；扩展预算按字符计。")
            if selection["deferred"]:
                info.value += " 延期：" + "、".join(s["name"] for s in selection["deferred"])
            rows.controls = []
            for skill in skills.list_skills():
                name = skill["name"]
                def change(event, key=name):
                    skills.set_mode(key, event.control.value)
                    refresh()
                mode = "off" if name in disabled else "on" if name in active else "auto"
                rows.controls.append(ft.Column([
                    ft.Row([ft.Text(name + (" · 本阶段可选" if name in selected_names else ""),
                                    size=13, color=palette.TEXT, width=350),
                            ft.Dropdown(value=mode, width=170, dense=True,
                                options=[ft.dropdown.Option("auto", "按阶段选择"), ft.dropdown.Option("on", "明确启用"), ft.dropdown.Option("off", "停用")],
                                on_select=change)], spacing=12),
                    ft.Text(skill.get("description", ""), size=12, color=palette.TEXT_2, width=590),
                    ft.Text({'manual_only': '禁止模型调用；手动调用入口待支持',
                             'unsupported': '要求独立上下文执行；当前加载器尚不支持',
                             'disabled': '已停用，模型工具不能读取',
                             'available': '可按需加载正文'}.get(skill.get('invocation_status'), '状态未记录'),
                            size=11, color=palette.TEXT_2, width=590),
                    ft.Text(skill.get('source', ''), size=10, color=palette.TEXT_3, width=590,
                            selectable=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Divider(color=palette.BORDER, height=1),
                ], spacing=5))
            style_inputs(rows)
            page.update()
        stage.on_select = refresh
        refresh()
        content = ft.Column([stage, info, rows], spacing=16, expand=True)
        style_inputs(content)
        page.show_dialog(ft.AlertDialog(title=ft.Text("科研 Skill"), content=ft.Container(width=640, height=480, content=content),
                                         actions=[ft.TextButton("关闭", on_click=lambda e: page.pop_dialog())]))

    def probe_models(e) -> None:
        model_status.value = "正在检测已保存的模型连接…"
        page.update()
        def work():
            models = llm.list_models()
            model_choices.options = [ft.dropdown.Option(name) for name in models]
            model_choices.visible = bool(models)
            model_status.value = (f"连接成功，可用模型 {len(models)} 个。" if models
                                  else "未取得模型列表。请保存服务地址后重试，并检查地址与密钥。")
            page.update()
        page.run_thread(work)
    tau_value = ft.Text(f"{float(set_tau.value):.2f}", size=12,
                        font_family=code_font_family(), color=palette.TEXT_2)
    round_value = ft.Text(f"{int(set_round.value)} 轮", size=12,
                          font_family=code_font_family(), color=palette.TEXT_2)

    def update_search_settings(e) -> None:
        tau_value.value = f"{float(set_tau.value):.2f}"
        round_value.value = f"{int(set_round.value)} 轮"
        page.update()

    set_tau.on_change = update_search_settings
    set_round.on_change = update_search_settings
    mode_text = ft.Text(size=12, color=palette.TEXT_2)
    doctor_col = ft.Column(spacing=4)

    def refresh_mode_text() -> None:
        mode_text.value = ("规划模型已配置，各科研阶段可使用独立连接。" if llm.available("planning")
                           else "当前使用规则规划；可配置默认连接或科研阶段专用模型。")

    def save_settings(e) -> None:
        try:
            new_budgets = {"llm_thinking_budget": int(set_budget.value), "llm_max_output_tokens": int(set_output.value),
                           "agent_max_steps": int(set_steps.value), "agent_token_budget": int(set_token_budget.value)}
        except (ValueError, TypeError):
            page.show_dialog(ft.SnackBar(ft.Text("思考预算、输出上限和执行预算须为整数。"), bgcolor=palette.DANGER))
            return
        try:
            app_settings.save({"llm_base_url": set_url.value or "",
                           "llm_api_key": set_key.value or "",
                           "llm_model": set_model.value or "",
                           "llm_model_fast": set_model_fast.value or "",
                           "llm_model_strong": set_model_strong.value or "",
                           "embedding_base_url": set_emb_url.value or "",
                           "embedding_api_key": set_emb_key.value or "",
                           "embedding_model": set_emb_model.value or "",
                           "llm_protocol": set_protocol.value, "llm_reasoning_effort": set_reasoning.value,
                           "llm_thinking_style": set_thinking.value, "llm_cache_enabled": bool(set_cache.value),
                           "agent_execution": set_driver.value, **new_budgets,
                           "lit_threshold": set_tau.value,
                           "lit_max_round": int(set_round.value),
                           "enable_crossref": bool(set_crossref.value),
                           **{key: bool(control.value) for key, control in feature_switches.items()}})
        except ValueError as exc:
            page.show_dialog(ft.SnackBar(ft.Text(str(exc), max_lines=3,
                                                 overflow=ft.TextOverflow.ELLIPSIS), bgcolor=palette.DANGER))
            return
        from .. import plugins, mcp
        plugins.load()
        if not feature_switches["mcp_enabled"].value:
            mcp.register_actions()
        else:
            page.run_thread(mcp.register_actions)
        refresh_plugins()
        home_offline.value = bool(feature_switches["default_offline"].value)
        reasoning_motion["enabled"] = bool(feature_switches["ui_animations"].value)
        reasoning_control.refresh(set_reasoning.value)
        refresh_mode_text()
        refresh_mode_badge()
        refresh_agent_mode_text()
        on_resized()
        doctor_col.controls.clear()
        page.show_dialog(ft.SnackBar(ft.Text("设置已保存并即时生效"), bgcolor=palette.SUCCESS))
        page.update()

    usage_col = ft.Column(spacing=3)

    def save_general_settings(e) -> None:
        try:
            app_settings.save({"agent_execution": set_driver.value,
                "agent_max_steps": int(set_steps.value), "agent_token_budget": int(set_token_budget.value),
                "lit_threshold": set_tau.value, "lit_max_round": int(set_round.value),
                "enable_crossref": bool(set_crossref.value),
                **{key: bool(control.value) for key, control in feature_switches.items() if key != "llm_enabled"}})
        except (ValueError, TypeError) as exc:
            page.show_dialog(ft.SnackBar(ft.Text(f"设置未保存：{exc}"), bgcolor=palette.DANGER))
            return
        from .. import plugins, mcp
        plugins.load()
        if not feature_switches["mcp_enabled"].value:
            mcp.register_actions()
        else:
            page.run_thread(mcp.register_actions)
        refresh_plugins()
        home_offline.value = bool(feature_switches["default_offline"].value)
        workspace_settings_saved()
        page.show_dialog(ft.SnackBar(ft.Text("通用设置已保存"), bgcolor=palette.SUCCESS))
        page.update()

    def refresh_usage(e=None) -> None:
        from .. import logging_util
        u = logging_util.usage_summary()
        m, t = u["model"], u["tool"]
        usage_col.controls = [
            ft.Text(f"模型调用 {m['calls']} 次 · tokens {m['total_tokens']:,}"
                    f"（输入 {m['prompt_tokens']:,} / 输出 {m['completion_tokens']:,}）"
                    f" · 均延迟 {m['avg_latency_ms']}ms · 失败 {m['failures']}",
                    size=12, color=palette.TEXT),
            ft.Text(f"工具调用 {t['calls']} 次 · 均延迟 {t['avg_latency_ms']}ms"
                    f" · 失败 {t['failures']}", size=12, color=palette.TEXT)]
        if m["by_node"]:
            detail = "  ".join(f"{n}: {v['calls']}次/{v['tokens']:,}tok"
                               for n, v in sorted(m["by_node"].items(),
                                                  key=lambda kv: -kv[1]["tokens"])[:6])
            usage_col.controls.append(ft.Text(f"按节点：{detail}", size=11,
                                              color=palette.TEXT_2))
        if not m["calls"] and not t["calls"]:
            usage_col.controls.append(
                ft.Text("暂无调用记录。运行任务后显示模型与工具用量。",
                        size=11, color=palette.TEXT_2))
        if "cache_hit_rate" in m:
            rate = f"{m['cache_hit_rate']:.1%}" if m["cache_hit_rate"] is not None else "未报告"
            usage_col.controls.append(ft.Text(
                f"模型缓存命中率 {rate} · 报送 {m['cache_reported_calls']}/{m['calls']} 次 · 未知 {m['cache_unknown_calls']} 次",
                size=12, color=palette.ACCENT, width=min(layout["page_w"], 700)))
            usage_col.controls.append(ft.Text("供应商缓存、会话列表缓存和本地检索缓存分别统计。", size=11, color=palette.TEXT_3))
            for group in sorted(m.get("by_model", {}).values(), key=lambda g: -g["calls"])[:12]:
                rate = f"{group['cache_hit_rate']:.1%}" if group["cache_hit_rate"] is not None else "未报告"
                usage_col.controls.append(ft.Text(
                    f"{group['connection_id'] or '旧记录'} / {group['protocol'] or '未知协议'} / {group['model'] or group['route']}\n"
                    f"{group['calls']} 次 · 输入 {group['prompt_tokens']:,} · 输出 {group['completion_tokens']:,} · 缓存 {rate} · 报送 {group['cache_reported_calls']}/{group['calls']}",
                    size=12, color=palette.TEXT_2, width=min(layout["page_w"], 700), selectable=True))
        page.update()

    def run_doctor_ui(e) -> None:
        doctor_col.controls = [ft.Row([
            busy_indicator('doctor', 18),
            ft.Text("自检中…（含网络探测，约数秒）", size=12, color=palette.TEXT_2)],
            spacing=8)]
        page.update()

        def work() -> None:
            from .. import doctor   # 按需：自检只在点按钮时才需要
            r = doctor.run_doctor()
            marks = {True: "✔", False: "✘"}
            colors = {True: palette.SUCCESS, False: palette.DANGER}
            doctor_col.controls = [
                ft.Text(f"自检完成：{r['summary']}", size=13,
                        weight=ft.FontWeight.BOLD,
                        color=palette.SUCCESS if r["ok"] else palette.DANGER)]
            for it in r["items"]:
                doctor_col.controls.append(ft.Row([
                    ft.Text(marks[it["ok"]], size=13, color=colors[it["ok"]],
                            weight=ft.FontWeight.BOLD),
                    ft.Text(f"{it['name']}：{it['detail']}", size=12, expand=True),
                ], spacing=6))
            page.update()

        page.run_thread(work)

    refresh_mode_text()

    routing_options = ft.Container(visible=False, content=ft.Column([
        ft.Text("按研究阶段使用不同模型；留空时使用默认模型。", size=12, color=palette.TEXT_2),
        set_model_fast, set_model_strong,
    ], spacing=12))

    def toggle_routing(e) -> None:
        routing_options.visible = not routing_options.visible
        page.update()

    from .model_settings import ModelSettingsPanel
    def advanced_model_saved() -> None:
        refresh_mode_text()
        refresh_mode_badge()
        refresh_agent_mode_text()
        on_resized()
    model_settings_panel = ModelSettingsPanel(page, on_saved=advanced_model_saved)
    advanced_models = ft.Container(visible=False, content=model_settings_panel.content, key=ft.ScrollKey("advanced_models"))
    def toggle_advanced_models(e) -> None:
        advanced_models.visible = not advanced_models.visible
        if advanced_models.visible:
            model_settings_panel.refresh()
        page.update()

    # ---- 设置分区注册表：section_id → 构建函数 ----
    # 旧实现把全部控件塞进一个 Column，再按索引切片 + 标题文本匹配回填
    # SettingsCenter——标题一改、顺序一动就错位。注册表让分类与构建一一对应，
    # 视觉与分类保持不变；settings_column 由同一批控件原序组装，仅作整体宽度载体。
    def _settings_section_models() -> list[ft.Control]:
        return [
            ft.Text("默认模型连接", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
            ft.Text("支持 OpenAI 兼容服务，包括元景万悟与本地模型。", size=12, color=palette.TEXT_2),
            feature_switches["llm_enabled"], set_url, set_key, set_model, model_options,
            ft.Text("思考参数按协议及模型校验，不支持时会明确报错。预算模式与强度等级并不等价。",
                    size=12, color=palette.TEXT_2, width=680), set_cache,
            ft.Text("向量检索 Embedding（可选）", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
            ft.Text("配置后文献评分启用语义向量级（余弦 × RRF 融合）；留空则 lexical 评分兜底，离线行为不变。",
                    size=12, color=palette.TEXT_2, width=680),
            set_emb_url, set_emb_key, set_emb_model,
            ft.Row([ft.TextButton("检测已保存的连接", icon=ft.Icons.NETWORK_CHECK, on_click=probe_models),
                    ft.TextButton("保存模型设置", icon=ft.Icons.SAVE, on_click=save_settings)], wrap=True),
            model_status, model_choices,
            ft.TextButton("按阶段选择模型", icon=ft.Icons.TUNE, on_click=toggle_routing),
            routing_options,
            ft.TextButton("多连接与模型路由", icon=ft.Icons.ACCOUNT_TREE_OUTLINED, on_click=toggle_advanced_models),
            advanced_models,
        ]

    def _settings_section_general() -> list[ft.Control]:
        return [
            ft.Text("执行控制", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
            driver_options,
            ft.Text("达到报送用量阈值后停止后续请求。未报送用量时仍受步骤上限约束。", size=12, color=palette.TEXT_2, width=680),
            ft.Divider(height=24, color=palette.BORDER),
            ft.Text("文献检索", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
            ft.Row([ft.Text("相关性阈值", size=12, color=palette.TEXT_2), tau_value],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            set_tau,
            ft.Row([ft.Text("最大检索轮次", size=12, color=palette.TEXT_2), round_value],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            set_round, set_crossref,
            ft.Divider(height=24, color=palette.BORDER),
            ft.Text("工作台功能", key="feature_settings", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
            *[control for key, control in feature_switches.items() if key != "llm_enabled"],
        ]

    def _settings_section_plugins() -> list[ft.Control]:
        return [
            ft.Row([ft.Text("插件系统", size=14, weight=ft.FontWeight.W_600, color=palette.TEXT),
                    ft.TextButton("重载插件", icon=ft.Icons.REFRESH, on_click=reload_plugins)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            plugin_rows,
            ft.TextButton("科研 Skill 与阶段加载", icon=ft.Icons.AUTO_STORIES_OUTLINED, on_click=show_skills),
            ft.Row([
                ft.FilledButton("保存设置", icon=ft.Icons.SAVE, on_click=save_settings,
                                style=ft.ButtonStyle(bgcolor=palette.ACCENT, color=palette.ON_ACCENT)),
                ft.OutlinedButton("系统自检", icon=ft.Icons.HEALTH_AND_SAFETY,
                                  on_click=run_doctor_ui),
            ], spacing=10),
            doctor_col,
        ]

    def _settings_section_usage() -> list[ft.Control]:
        return [ft.Row([ft.Text("用量统计", size=14, weight=ft.FontWeight.W_600,
                                color=palette.TEXT),
                        ft.TextButton("刷新", icon=ft.Icons.REFRESH,
                                      on_click=refresh_usage)], key=ft.ScrollKey("model_usage")),
                usage_col]

    settings_section_builders: "OrderedDict[str, Callable[[], list[ft.Control]]]" = OrderedDict(
        models=_settings_section_models,
        general=_settings_section_general,
        plugins=_settings_section_plugins,
        usage=_settings_section_usage)
    settings_sections = {key: build() for key, build in settings_section_builders.items()}
    settings_column = ft.Column([
        ft.Text("设置", size=ui_size("xl"), weight=ft.FontWeight.W_600, color=palette.TEXT),
        mode_text,
        ft.Container(height=6),
        *settings_sections["models"],
        ft.Divider(height=24, color=palette.BORDER),
        *settings_sections["general"],
        ft.Divider(height=24, color=palette.BORDER),
        *settings_sections["plugins"],
        ft.Divider(height=24, color=palette.BORDER),
        *settings_sections["usage"],
    ], width=720, spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
    from .settings_center import SettingsCenter, apply_appearance
    style_inputs(*settings_column.controls)

    def workspace_settings_saved():
        saved = normalize_ui(app_settings.load())
        appearance_state['values'] = saved
        settings_center.sync_appearance_preferences(saved)
        notebook_center.sync_appearance_preferences(saved)
        for key, control in feature_switches.items():
            control.value = saved[key]
        reasoning_motion["enabled"] = bool(saved.get("ui_animations", True))
        configure_typography(saved)
        reasoning_control.refresh(saved.get("llm_reasoning_effort", "auto"))
        set_reasoning.value = saved.get("llm_reasoning_effort", "auto")
        refresh_busy_indicators()
        apply_theme(resolve_dark(saved, system_dark=page.platform_brightness != ft.Brightness.LIGHT))
        page.theme_mode = (ft.ThemeMode.SYSTEM if saved['theme_mode'] == 'system' else
                           ft.ThemeMode.DARK if palette.dark else ft.ThemeMode.LIGHT)
        apply_appearance(sidebar, stage, inspector, page.drawer, agent_approval_card, *pages)
        workspace_mode_control._paint()
        page.theme = build_theme(dark=palette.dark)
        refresh_shortcut_hints()
        refresh_mode_text()
        refresh_agent_mode_text()
        on_resized()

    def refresh_appearance_preferences():
        saved = normalize_ui(app_settings.load())
        keys = (*UI_SCHEMA, 'appearance_font_family', 'appearance_code_font')
        if all(saved.get(key) == appearance_state['values'].get(key) for key in keys):
            return False
        workspace_settings_saved()
        return True

    settings_center = SettingsCenter(page, sections={
        "models": [mode_text, *settings_sections["models"]],
        "general": [*settings_sections["general"],
                    ft.FilledButton("保存设置", icon=ft.Icons.SAVE, on_click=save_general_settings)],
        "plugins": settings_sections["plugins"],
        "usage": settings_sections["usage"],
    }, on_saved=workspace_settings_saved)
    # The retained plugin footer saves feature preferences only, never stale model keys.
    for control in settings_center.sections["plugins"]:
        if isinstance(control, ft.Row):
            for child in control.controls:
                if isinstance(child, ft.FilledButton) and child.content == "保存设置":
                    child.on_click = save_general_settings
    page_settings = ft.Container(expand=True, content=settings_center.content)
    notebook_center = SettingsCenter(page, on_saved=workspace_settings_saved)
    notebook_center.select("notebook", update=False)
    page_notebook = ft.Container(expand=True, content=notebook_center.main)
    from .project_studio import ProjectStudio

    def open_project_draft():
        # Use a project-only destination if its chat cannot be loaded. An older
        # project's chat must never become the new project's implicit target.
        agent_state['view_epoch'] += 1
        agent_state['session_id'], agent_state['status'] = None, 'draft'
        agent_state.pop('workspace_mode', None)
        agent_state.pop('workspace_agent', None)
        agent_state['approval_open_id'] = agent_state['approval_session'] = None
        close_approval_dialog()
        agent_approval_card.visible = agent_plan_card.visible = False
        agent_answer.value, agent_answer.visible = '', False
        agent_log_wrap.visible = False
        agent_cancel_btn.disabled, agent_continue_btn.visible = True, False
        agent_session_label.value = '当前项目 · 尚未创建会话'
        drafts.select(None, state['project_id'], defaults={'agent': MODES[workspace_mode['value']]['agent']})
        composer.refresh()
        session_status.refresh(force=True)
        session_queue.refresh(force=True)
        onboarding.update_context()

        refresh_workspace_context()

    def project_changed(project):
        previous_project = state['project_id']
        state['project_id'] = project.get('id', '')
        if state['project_id'] != previous_project:
            agent_state['view_epoch'] += 1
        if agent_state['session_id']:
            try:
                current = workspace_handle({'cmd':'chat_get', 'session_id':agent_state['session_id']}).get('session', {})
                if current.get('project_id') != state['project_id']:
                    matches = workspace_handle({'cmd':'chat_sessions', 'project_id':state['project_id']}).get('sessions', [])
                    result = matches[0] if matches else workspace_handle({'cmd':'chat_new', 'project_id':state['project_id']})
                    if not result.get('session_id'):
                        raise ValueError(result.get('error', '未能打开项目会话'))
                    select_agent_session(result['session_id'])
            except Exception as exc:  # Optional chat loading must preserve project identity.
                open_project_draft()
                page.show_dialog(ft.SnackBar(ft.Text(f'已切换项目，会话暂不可用：{exc}',
                    max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)))
        elif state['project_id'] != previous_project:
            drafts.select(None, state['project_id'], defaults={'agent': MODES[workspace_mode['value']]['agent']})
        composer.info.value = '当前项目 · '+project.get('name', '')+'；新建会话将绑定此项目。'
        if project.get('view') == 'chat':
            nav_to(4)
    def attach_project_file(path):
        nav_to(4)
        if agent_state['session_id']:
            row = workspace_handle({'cmd':'chat_get', 'session_id':agent_state['session_id']}).get('session', {})
            if row.get('project_id') != state['project_id']:
                composer.new()
        composer.attach(path)
    project_studio = ProjectStudio(page, on_project_changed=project_changed,
        on_session_open=lambda sid: (nav_to(4), select_agent_session(sid)), on_attach=attach_project_file)
    page_project = ft.Container(expand=True, content=project_studio.content)
    refresh_plugins()

    refresh_usage()

    # ============ 导航与轮询 ============
    mode_badge = ft.Container(
        border_radius=radius('small'), padding=ft.Padding(8, 4, 8, 4), bgcolor=palette.SURFACE_3,
        content=ft.Text("", size=9, color=palette.TEXT_2, text_align=ft.TextAlign.CENTER))

    def refresh_mode_badge() -> None:
        on = llm.available("planning")
        mode_badge.bgcolor = palette.SURFACE_3 if on else palette.SURFACE_3
        mode_badge.content.value = "模型规划" if on else "规则规划"

    refresh_mode_badge()

    pages = [page_new, page_monitor, page_result, page_stats, page_agent,
             page_settings, page_notebook, page_project]
    body = ft.Container(expand=True, content=pages[0])
    nav_index = {"i": 0}
    nav_buttons: list[ft.Container] = []

    NAV_ITEMS = (
        (0, "新任务", ft.Icons.ADD),
        (1, "研究进度", ft.Icons.PLAY_CIRCLE_OUTLINE),
        (2, "文献与成果", ft.Icons.ARTICLE_OUTLINED),
        (3, "数据分析", ft.Icons.INSERT_CHART_OUTLINED),
        (4, "智能体", ft.Icons.ACCOUNT_TREE_OUTLINED),
        (5, "设置", ft.Icons.SETTINGS_OUTLINED),
        (6, "研究记录", ft.Icons.SCIENCE_OUTLINED),
        (7, "项目", ft.Icons.FOLDER_OUTLINED),
    )
    breadcrumb = ft.Text("研究工作台", size=12, color=palette.TEXT_2)
    async def open_navigation(e=None):
        layout['navigation_open'] = True
        page.drawer.selected_index = nav_index['i']
        await page.show_drawer()

    async def drawer_select(e):
        index = int(e.control.selected_index)
        layout['navigation_open'] = False
        await page.close_drawer()
        nav_to(index)

    def drawer_dismiss(e=None):
        layout['navigation_open'] = False

    page.drawer = ft.NavigationDrawer(
        controls=[ft.NavigationDrawerDestination(label=label, icon=icon) for _, label, icon in NAV_ITEMS],
        bgcolor=palette.SURFACE, indicator_color=palette.ACTIVE, width=248,
        on_change=drawer_select, on_dismiss=drawer_dismiss)
    navigation_toggle = ft.IconButton(ft.Icons.MENU, tooltip='导航',
                                      on_click=lambda e: page.run_task(open_navigation), visible=False)
    workspace_status = ft.Text("", size=11, color=palette.TEXT_2,
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                               expand=True)

    def _paint_nav() -> None:
        for i, button in enumerate(nav_buttons):
            on = i == nav_index["i"]
            button.style.bgcolor = palette.ACTIVE if on else 'transparent'
            label = button.content.controls[1]
            icon = button.content.controls[0]
            label.color = palette.ACCENT_TEXT if on else palette.TEXT_2
            label.weight = ft.FontWeight.W_600 if on else ft.FontWeight.W_500
            icon.color = palette.ACCENT_TEXT if on else palette.TEXT_2

    def _sync_monitor_chrome() -> None:
        ctx = service.get(state["task_id"]) if state["task_id"] else None
        has = ctx is not None
        monitor_empty.visible = not has
        monitor_col.visible = has
        hitl_bar.visible = bool(has and service.artifacts_of(state["task_id"]).get("status") == "waiting_human")

    def nav_to(index: int, *, update: bool = True) -> None:
        nav_index["i"] = index
        body.content = pages[index]
        _paint_nav()
        breadcrumb.value = f"工作台  /  {NAV_ITEMS[index][1]}"
        _sync_monitor_chrome()
        on_resized(update=update)
        if index == 1:
            refresh_board()
        if index in (1, 2) and state["task_id"] and service.get(state["task_id"]):
            refresh_task()
            if index == 2:
                render_results(service.artifacts_of(state["task_id"]), force=True)
                if result_tabs.selected_index == 6:
                    outcome_panel.refresh(state["task_id"])
        elif index == 2:
            render_results({}, force=True)
            outcome_panel.refresh(None)
        if index == 0:
            refresh_history()
        if index in (0, 4):
            onboarding.refresh()
        if index == 4:
            session_queue.refresh(force=True)
            session_steering.refresh(force=True)
        if index == 6:
            notebook_center.select("notebook", update=False)
        if index == 7:
            project_studio.refresh()
        if update:
            page.update()

    def _open_results(tab: int = 0) -> None:
        result_tabs.selected_index = tab
        nav_to(2)

    for idx, label, icon in NAV_ITEMS:
        button = ft.TextButton(
            content=ft.Row([
                ft.Icon(icon, size=18, color=palette.TEXT),
                ft.Text(label, size=14.5, color=palette.TEXT, weight=ft.FontWeight.W_500),
            ], spacing=10),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding(10, 9, 10, 9),
                bgcolor='transparent',
                overlay_color=palette.SURFACE_3,
                elevation=0,
            ),
            width=SIDE_W - 24,
            on_click=lambda e, i=idx: nav_to(i),
        )
        nav_buttons.append(button)

    inspect_topic = ft.Text("未打开任务", size=13, weight=ft.FontWeight.W_600,
                            color=palette.TEXT, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS)
    inspect_status = ft.Text("空闲", size=12, color=palette.TEXT_2)
    inspect_counts = ft.Column(spacing=6)
    inspect_hitl = ft.Text("无待审批动作", size=12, color=palette.TEXT_3)
    inspect_hitl_btn = ft.FilledButton(
        "去确认", visible=False, on_click=lambda e: nav_to(1),
        style=ft.ButtonStyle(bgcolor=palette.ACCENT, color=palette.ON_ACCENT))
    inspect_agents = ft.Column(spacing=2)

    def _pick_agent(name: str) -> None:
        agent_pick.value = name
        nav_to(4)

    for spec in ROSTER.values():
        if spec.mode == "primary":
            continue
        inspect_agents.controls.append(ft.Container(
            padding=ft.Padding(6, 4, 6, 4), border_radius=radius('small'),
            content=ft.Text(spec.title, size=11, color=palette.TEXT_2),
            on_click=lambda e, n=spec.name: _pick_agent(n),
            ink=True))

    def refresh_inspector(art: dict[str, Any] | None = None,
                          ctx: Any = None) -> None:
        """刷新当前任务、证据计数、权限与执行状态。"""
        if ctx is None and state["task_id"]:
            ctx = service.get(state["task_id"])
        if art is None and state["task_id"]:
            art = service.artifacts_of(state["task_id"])
        if not ctx or not art:
            inspect_topic.value = "未打开任务"
            inspect_status.value = "从左侧开新任务，或点最近任务继续"
            inspect_counts.controls = [
                ft.Text("文献 0　抽取 0　引用 0", size=12, color=palette.TEXT_3)]
            inspect_hitl.value = "无待审批动作"
            inspect_hitl.color = palette.TEXT_3
            inspect_hitl_btn.visible = False
            return
        inspect_topic.value = str(ctx.params.get("topic") or "")
        inspect_status.value = art.get("status_text") or art.get("status") or ""
        pool_n = len(art.get("pool") or [])
        digest = bool(art.get("digest_md"))
        outline = bool(art.get("outline_md"))
        cite_n = len(art.get("citations") or [])
        def _ev_row(label: str, value: str, tab: int) -> ft.Container:
            return ft.Container(
                padding=ft.Padding(0, 4, 0, 4), border_radius=radius('small'),
                content=ft.Row([
                    ft.Text(label, size=12, color=palette.TEXT_2, expand=True),
                    ft.Text(value, size=12, color=palette.TEXT),
                ]),
                on_click=lambda e, t=tab: _open_results(t), ink=True)

        inspect_counts.controls = [
            _ev_row("文献池", f"{pool_n} 篇", 0),
            _ev_row("摘要稿", "已出" if digest else "未出", 1),
            _ev_row("综述", "已出" if outline else "未出", 2),
            _ev_row("引用", f"{cite_n} 条", 3),
        ]
        waiting = art.get("status") == "waiting_human"
        inspect_hitl.value = "待人工确认" if waiting else "无待审批动作"
        inspect_hitl.color = palette.ACCENT_TEXT if waiting else palette.TEXT_3
        inspect_hitl_btn.visible = waiting

    refresh_inspector()

    from .workspace_mode import WorkspaceModeControl
    workspace_mode_control = WorkspaceModeControl(page, mode=workspace_mode['value'],
        # workspace_mode_changed mutates the full desktop chrome.  Defer its
        # repaint so WorkspaceModeControl can paint the final button state and
        # commit both changes in one page.update() after the worker returns.
        on_changed=lambda mode: workspace_mode_changed(mode, update=False),
        api=workspace_handle)
    workspace_chrome_ready['value'] = True
    refresh_workspace_context()
    sidebar_subtitle = ft.Text(MODES[workspace_mode['value']]['title'], color=palette.TEXT_2, size=12, weight=ft.FontWeight.W_500)
    side_recent_heading = ft.Text('最近研究', size=13.5, color=palette.TEXT, weight=ft.FontWeight.W_600)
    general_shortcuts = ft.Column([
        ft.TextButton('命令面板 · Ctrl+K', icon=ft.Icons.SEARCH, on_click=lambda e: show_panel('commands')),
        ft.TextButton('最近会话', icon=ft.Icons.HISTORY, on_click=show_agent_sessions),
        ft.TextButton('模型与连接', icon=ft.Icons.TUNE, on_click=lambda e: open_model_settings()),
        ft.TextButton('工具权限', icon=ft.Icons.VERIFIED_USER_OUTLINED, on_click=show_agent_policy),
    ], spacing=2, visible=workspace_mode['value'] == 'general')
    # 左栏：品牌、模式、导航、最近任务与设置。
    sidebar = ft.Container(
        width=SIDE_W, bgcolor=palette.BG,
        padding=ft.Padding(12, 14, 12, 12),
        content=ft.Column([
            ft.Row([seal(36), ft.Column([
                ft.Text("格致 SciZhi", color=palette.TEXT, size=16,
                        weight=ft.FontWeight.W_700),
                sidebar_subtitle,
            ], spacing=1)], spacing=9),
            workspace_mode_control.content,
            ft.Column([nav_buttons[7], *nav_buttons[:5], nav_buttons[6]], spacing=3),
            ft.Divider(color=palette.BORDER, height=12),
            ft.Column([side_recent_heading, side_recent, general_shortcuts],
                      spacing=8, scroll=ft.ScrollMode.AUTO, expand=True),
            ft.Divider(color=palette.BORDER, height=8),
            nav_buttons[5],
            ft.Row([mode_badge, ft.Text(f"v{APP_VERSION}", color=palette.TEXT_3, size=11.5)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ], spacing=10, expand=True, width=SIDE_W - 24))

    # 详情面板在宽窗口停靠，窄窗口以弹层显示。
    def close_panel() -> None:
        tools_panel.epoch += 1
        if layout["panel_dialog"]:
            page.pop_dialog()
            layout["panel_dialog"] = False
        layout["panel_manual"] = False
        inspector.content = tools_panel.content
        on_resized()

    def show_panel(section: str = "overview") -> None:
        raw_width = getattr(page, "width", 1280)
        win = int(raw_width) if isinstance(raw_width, (int, float)) else 1280
        layout["panel_manual"] = True
        if win < 1380:
            if layout["panel_dialog"]:
                page.pop_dialog()
            inspector.content = None
            layout["panel_dialog"] = True
            raw_height = getattr(page, "height", 800)
            height = int(raw_height) if isinstance(raw_height, (int, float)) else 800
            tools_panel.resize(min(560, win - 100))
            page.show_dialog(ft.AlertDialog(
                modal=True, content=ft.Container(width=min(560, win - 100), height=max(360, height - 170),
                                                 content=tools_panel.content),
                content_padding=16))
        else:
            if layout["panel_dialog"]:
                page.pop_dialog()
                layout["panel_dialog"] = False
            inspector.content = tools_panel.content
        on_resized()
        tools_panel.select(section)

    def open_panel_session(sid: str) -> None:
        if layout["panel_dialog"]:
            close_panel()
        nav_to(4)
        select_agent_session(sid)

    def open_panel_task(tid: str) -> None:
        if layout["panel_dialog"]:
            close_panel()
        open_task(tid)

    def open_terminal(e) -> None:
        def work():
            from ..bridge import handle
            result = handle({"cmd": "terminal_open"})
            if not result.get("ok"):
                page.show_dialog(ft.SnackBar(ft.Text(result.get("error", "无法打开终端"),
                                                     max_lines=3, overflow=ft.TextOverflow.ELLIPSIS),
                                             bgcolor=palette.DANGER))
        page.run_thread(work)

    toolbar = ft.Row([
        ft.IconButton(ft.Icons.DASHBOARD_OUTLINED, tooltip="Tower 控制塔", icon_size=18,
                      on_click=lambda e: control_tower.open("tower")),
        ft.IconButton(ft.Icons.HUB_OUTLINED, tooltip="Swarm 协作", icon_size=18,
                      on_click=lambda e: control_tower.open("swarm")),
        ft.IconButton(ft.Icons.CODE, tooltip="命令 · Ctrl+K", icon_size=18, on_click=lambda e: show_panel("commands")),
        ft.IconButton(ft.Icons.ACCOUNT_TREE_OUTLINED, tooltip="科研工作流", icon_size=18, on_click=lambda e: show_panel("workflows")),
        ft.IconButton(ft.Icons.LIST_ALT, tooltip="任务进程", icon_size=18, on_click=lambda e: show_panel("tasks")),
        ft.IconButton(ft.Icons.REPEAT, tooltip="Loop", icon_size=18, on_click=lambda e: show_panel("loops")),
        ft.IconButton(ft.Icons.HISTORY, tooltip="会话与导出", icon_size=18, on_click=lambda e: show_panel("sessions")),
        ft.IconButton(ft.Icons.DEVICES, tooltip="手机远控", icon_size=18, on_click=lambda e: show_panel("remote")),
        ft.IconButton(ft.Icons.TERMINAL, tooltip="打开本机终端", icon_size=18, on_click=open_terminal),
    ], spacing=0)

    def refresh_shortcut_hints() -> None:
        from ..workspace_features import DEFAULT_SHORTCUTS
        bindings = {**DEFAULT_SHORTCUTS, **app_settings.load().get('keyboard_shortcuts', {})}
        general_shortcuts.controls[0].content = f"命令面板 · {bindings['commands']}"
        toolbar.controls[2].tooltip = f"命令 · {bindings['commands']}"
        nav_buttons[5].tooltip = f"设置 · {bindings['settings']}"
        workspace_mode_control.refresh_shortcuts()

    # ---- 通知中心：铃铛（含未读角标）+ 顶部下拉面板 ----
    # 数据层复用 research_planning：notifications_list / notification_read；
    # SnackBar 仍负责新通知瞬显（research_activity 轮询里保留）。
    notif_col = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    notif_badge = ft.Container(
        width=15, height=15, border_radius=8, bgcolor=palette.DANGER, visible=False,
        alignment=ft.Alignment(0, 0),
        content=ft.Text("0", size=8, color=palette.BG, text_align=ft.TextAlign.CENTER))

    def _notif_row(n: dict[str, Any]) -> ft.Container:
        """单条通知：未读加粗标题 + 时间 + 摘要；点击标记已读并写回数据层。"""
        read = bool(n.get("read"))

        def open_and_mark(e) -> None:
            if not read:
                try:
                    workspace_handle({'cmd': 'notification_read', 'id': n['id']})
                except Exception:  # noqa: BLE001 —— 标记失败不阻断列表展示
                    pass
            notif_refresh()

        return ft.Container(
            padding=ft.Padding(4, 8, 4, 8),
            border=ft.Border.only(bottom=ft.BorderSide(1, palette.BORDER)),
            content=ft.Column([
                ft.Row([
                    ft.Text(n.get("title", ""), size=12, expand=True,
                            weight=ft.FontWeight.W_400 if read else ft.FontWeight.W_600,
                            color=palette.TEXT_2 if read else palette.TEXT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(str(n.get("created_at", ""))[:16], size=10,
                            color=palette.TEXT_3, font_family=code_font_family()),
                ], spacing=8),
                ft.Text(str(n.get("body", ""))[:80], size=11, color=palette.TEXT_2,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                        visible=bool(n.get("body"))),
            ], spacing=3),
            on_click=open_and_mark, ink=True)

    def notif_refresh() -> None:
        """读数据层重建列表，并同步铃铛角标（打开面板/标记已读后调用）。"""
        try:
            result = workspace_handle({'cmd': 'notifications_list'})
        except Exception:  # noqa: BLE001 —— 通知中心失败不影响主流程
            return
        rows = (result.get("notifications") or [])[:30]
        unread = int(result.get("unread_count") or 0)
        state["notif_unread"] = unread
        notif_badge.visible = unread > 0
        notif_badge.content.value = str(unread) if unread < 100 else "99+"
        notif_col.controls = []
        if not rows:
            notif_col.controls.append(
                ft.Text("暂无通知", size=12, color=palette.TEXT_2))
        for n in rows:
            notif_col.controls.append(_notif_row(n))

    def notif_toggle(e) -> None:
        # 只切换外层包裹容器：Flet Stack 中无尺寸约束的子容器会铺满整个
        # 台面并拦截其下全部点击，因此隐藏时必须整层 Offstage，且给足显式尺寸。
        notif_overlay.visible = not notif_overlay.visible
        if notif_overlay.visible:
            notif_refresh()
        page.update()

    def notif_read_all(e) -> None:
        try:
            workspace_handle({'cmd': 'notification_read', 'all': True})
        except Exception:  # noqa: BLE001
            pass
        notif_refresh()
        page.update()

    notif_panel = ft.Container(
        width=360, bgcolor=palette.POPOVER, border_radius=radius('panel'),
        border=ft.Border.all(1, palette.BORDER),
        padding=ft.Padding(12, 10, 12, 10),
        content=ft.Column([
            ft.Row([
                ft.Text("通知中心", size=13, weight=ft.FontWeight.W_600, color=palette.TEXT),
                ft.Row([
                    ft.TextButton("全部已读", on_click=notif_read_all,
                                  style=ft.ButtonStyle(color=palette.TEXT_2)),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=16, icon_color=palette.TEXT_3,
                                  tooltip="关闭", on_click=notif_toggle),
                ], spacing=0),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(color=palette.BORDER, height=8),
            ft.Container(height=320, content=notif_col),
        ], spacing=4))
    # 显式尺寸 + 随面板一起 Offstage：避免无约束容器铺满 Stack 拦截整页点击。
    notif_overlay = ft.Container(
        visible=False, width=380, height=452,
        alignment=ft.Alignment(1, -1), padding=ft.Padding(0, 40, 4, 0),
        content=notif_panel)
    bell = ft.Stack(width=40, height=40, controls=[
        ft.IconButton(ft.Icons.NOTIFICATIONS_OUTLINED, tooltip="通知中心",
                      icon_size=18, icon_color=palette.TEXT_2, on_click=notif_toggle),
        ft.Container(content=notif_badge, alignment=ft.Alignment(1, -1),
                     padding=ft.Padding(0, 4, 4, 0)),
    ])

    stage = ft.Container(
        expand=True, bgcolor=palette.BG, padding=8,
        content=ft.Container(
            expand=True, bgcolor=palette.SURFACE, border_radius=radius('panel'),
            border=ft.Border.all(1, palette.BORDER),
            padding=ft.Padding(24, 16, 24, 12),
            content=ft.Stack([
                ft.Column([
                    ft.Row([ft.Row([navigation_toggle, breadcrumb], spacing=8), ft.Row([toolbar, bell], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(color=palette.BORDER, height=16),
                    body,
                    ft.Divider(color=palette.BORDER, height=8),
                    ft.Row([ft.Container(width=5, height=5, border_radius=3,
                                         bgcolor=palette.SUCCESS),
                            workspace_status,
                            ft.Text("格致 · 本地工作区", size=10, color=palette.TEXT_3)],
                           spacing=8),
                ], spacing=4, expand=True),
                # 下拉面板叠在台面右上：不占布局、不推移工具栏；
                # 包裹层显式定尺寸且随开合 Offstage，绝不拦截整页点击。
                notif_overlay,
            ], expand=True)))

    # 右栏显示当前任务与证据计数。
    inspector = ft.Container(
        width=RAIL_W, bgcolor=palette.BG,
        padding=ft.Padding(12, 14, 14, 14),
        border=ft.Border.only(left=ft.BorderSide(1, palette.BORDER)),
        content=ft.Column([
            ft.Text("研究详情", size=12, color=palette.TEXT_3),
            inspect_topic,
            inspect_status,
            ft.Container(height=8),
            ft.Text("证据", size=12, color=palette.TEXT_3),
            inspect_counts,
            ft.Container(height=8),
            ft.Text("权限", size=12, color=palette.TEXT_3),
            inspect_hitl,
            inspect_hitl_btn,
            ft.Container(height=8),
            ft.TextButton("查看文献与成果", icon=ft.Icons.ARROW_FORWARD,
                          on_click=lambda e: nav_to(2),
                          style=ft.ButtonStyle(color=palette.TEXT_2)),
        ], spacing=6, scroll=ft.ScrollMode.AUTO))

    from .workbench_panel import WorkbenchPanel
    tools_panel = WorkbenchPanel(page, inspector.content, open_panel_task, open_panel_session,
                                 lambda: state["task_id"], lambda: tf_topic.value, close_panel,
                                 command_context=lambda: {'busy': agent_busy.visible},
                                 on_mode_changed=lambda mode: (
                                     workspace_mode_control.sync(app_settings.load().get('workspace_mode', 'research')),
                                     workspace_mode_changed(workspace_mode_control.mode, navigate=False)))
    inspector.content = tools_panel.content

    def keyboard_shortcut(e) -> None:
        if layout['navigation_open']:
            if str(getattr(e, 'key', '')).casefold() == 'escape':
                layout['navigation_open'] = False
                page.run_task(page.close_drawer)
            return
        from ..workspace_features import shortcut_action
        action = shortcut_action(str(getattr(e, "key", "")), ctrl=bool(getattr(e, "ctrl", False)),
                                 alt=bool(getattr(e, "alt", False)), shift=bool(getattr(e, "shift", False)))
        if action == 'workspace_mode':
            workspace_mode_control.toggle()
        elif action == "commands":
            show_panel("commands")
        elif action == "settings":
            nav_to(5)
        elif action == "new_task":
            if workspace_mode['value'] == 'general':
                nav_to(4)
                composer.new(preserve_input=True)
            else:
                nav_to(0)
        elif action == "results":
            nav_to(2)
        elif action == "processes":
            show_panel("tasks")
    page.on_keyboard_event = keyboard_shortcut

    def apply_layout() -> None:
        w = layout["page_w"]
        home_column.width = min(w, 880)
        form_card.width = min(w, 880)
        hist_card.width = min(w, 880)
        tf_topic.width = min(w, 880)
        search_options.width = min(w, 880)
        history_col.width = min(w, 880)
        for row in history_col.controls:
            if isinstance(row, ft.Container):
                for text in row.content.controls[1].controls:
                    text.width = max(160, min(w, 880) - 110)
        research_art.visible = w >= 740
        monitor_col.width = w
        monitor_steps_card.width = w
        monitor_log.width = w
        hitl_bar.width = w
        banner.width = w
        result_col.width = w
        for row in pool_table.rows:
            for index, minimum, fraction in ((0, 170, .27), (1, 130, .20), (3, 130, .19)):
                row.cells[index].content.width = max(minimum, int(w * fraction))
        stats_col.width = w
        chat_width = min(880, max(300, w - 334) if composer.side.visible else w)
        agent_column.width = chat_width
        agent_composer.width = chat_width
        agent_request.width = max(260, chat_width - 24)
        reasoning_control.content.width = chat_width
        reasoning_control.note.width = chat_width
        agent_plan_card.width = chat_width
        agent_log_wrap.width = chat_width
        terminal.body.width = chat_width
        composer.preview.width = chat_width
        composer.transcript.width = chat_width
        project_studio.resize(w)
        settings_column.width = min(w, 720)
        model_options.width = driver_options.width = min(w, 720)
        model_settings_panel.resize(min(w, 720))
        settings_center.resize(w)
        notebook_center.main.width = min(w, 880)
        notebook_center.resize_fields()
        for field in (set_url, set_key, set_model, set_model_fast, set_model_strong):
            field.width = min(w, 720)
        model_choices.width = min(w, 720)
        for card in plugin_rows.controls:
            if isinstance(card, ft.Container):
                card.content.controls[1].width = min(w, 720)
        sidebar.width = layout["sidebar_w"]
        sidebar.content.width = layout["sidebar_w"] - 24
        if workspace_mode_control is not None:
            workspace_mode_control.resize(layout["sidebar_w"] - 24)
        for button in nav_buttons:
            button.width = layout["sidebar_w"] - 24
        inspector.visible = layout["rail"]
        inspector.width = RAIL_W if layout["rail"] else 0
        if not layout["panel_dialog"]:
            tools_panel.resize(RAIL_W - 26)
        if control_tower.opened:
            control_tower.resize()

    def on_resized(e: Any = None, *, update: bool = True) -> None:
        raw_width = getattr(e, "width", None) or getattr(page, "width", None)
        win = int(raw_width) if isinstance(raw_width, (int, float)) else 1280
        if win < 500:
            win = 1280
        auto_rail = nav_index["i"] in (1, 2) and bool(state["task_id"]) and feature_switches["desktop_inspector"].value
        wanted = auto_rail if layout["panel_manual"] is None else layout["panel_manual"]
        rail = win >= 1380 and bool(wanted) and not layout["panel_dialog"]
        sidebar_mode = appearance_state['values']['sidebar_mode']
        docked = sidebar_mode == 'docked' and win >= 1200
        sidebar.visible = docked
        navigation_toggle.visible = not docked
        side_w = SIDE_W if docked else 0
        chrome = side_w + (RAIL_W if rail else 0) + 68
        pw = max(280, win - chrome)
        layout["page_w"] = pw
        layout["rail"] = rail
        # Retain the side panel's internal geometry while it is offstage.
        layout["sidebar_w"] = SIDE_W
        workspace_status.value = ("规划模型已配置" if llm.available("planning") else
            "通用任务需配置模型 · 离线仅工作区信息" if workspace_mode['value'] == 'general' else
            "规则规划 · 各科研阶段可单独配置模型")
        apply_layout()
        if update:
            page.update()

    page.on_resize = on_resized
    def platform_theme_changed(e=None):
        if appearance_state['values']['theme_mode'] == 'system':
            workspace_settings_saved()
    page.on_platform_brightness_change = platform_theme_changed
    style_inputs(*pages, policy_col)
    apply_appearance(sidebar, stage, inspector, *pages)
    page.theme = build_theme(dark=palette.dark)
    refresh_shortcut_hints()
    page.add(ft.Row([sidebar, stage, inspector], spacing=0, expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH))

    # 初始导航态：默认页是「新建任务」，但 nav_to 只在点击时改样式——
    # 不主动高亮的话，用户无法从导航判断当前所在页（首屏截图实测发现）
    workspace_mode_changed(workspace_mode['value'], set_draft_agent=False)

    def open_task(tid: str) -> None:
        state["task_id"] = tid
        state["seen_events"] = 0
        state["results_key"] = None
        state["net_key"] = None
        state["_sig"] = None
        outcome_panel.reset()
        log_col.controls.clear()
        result_meta.controls = []
        refresh_inspector()
        refresh_history()
        nav_to(1)

    def fmt_elapsed(sec: float) -> str:
        sec = max(int(sec), 0)
        if sec >= 3600:
            return f"{sec // 3600}h{(sec % 3600) // 60:02d}m{sec % 60:02d}s"
        if sec >= 60:
            return f"{sec // 60}m{sec % 60:02d}s"
        return f"{sec}s"

    def refresh_task() -> str | None:
        """Selection and polling share one synchronous projection of the task."""
        tid = state["task_id"]
        ctx = service.get(tid) if tid else None
        if ctx is None:
            monitor_empty.visible = True
            monitor_col.visible = False
            hitl_bar.visible = False
            return None
        art = service.artifacts_of(tid)
        monitor_title.value = str(ctx.params.get("topic", ""))
        status_chip_box.controls = [
            chip(art["status_text"], STATUS_COLOR.get(art["status"], palette.TEXT_2))]
        render_steps(art, ctx.node_durations)
        render_events(art.get("events", []))
        done_n, total_nodes = service.task_progress(ctx)
        progress_bar.value = min(done_n / max(total_nodes, 1), 1.0)
        progress_text.value = f"{done_n}/{total_nodes} 步骤"
        if ctx.started_at:
            elapsed = (ctx.finished_at or time.time()) - ctx.started_at
            progress_text.value += f" · 已用 {fmt_elapsed(elapsed)}"
        st = art["status"]
        btn_pause.disabled = st != "running"
        btn_resume.disabled = st not in ("paused", "waiting_human")
        btn_resume.content = "确认文献并继续" if st == "waiting_human" else "继续研究"
        btn_cancel.disabled = st not in ("running", "paused", "waiting_human")
        btn_restart.disabled = st == "running"
        hitl_bar.visible = st == "waiting_human"
        monitor_empty.visible = False
        monitor_col.visible = True
        sig = (tid, st, done_n, len(art.get("events", [])), art.get("pointer"))
        if st == "running" or sig != state.get("_sig"):
            state["_sig"] = sig
            refresh_inspector(art, ctx)
            if body.content == page_result:
                render_results(art)
            page.update()
        return st

    async def poller() -> None:
        delay = 0.8
        while state['poll']:
            await asyncio.sleep(delay)
            if not state['poll']:
                return
            refresh_appearance_preferences()
            st = refresh_task()
            # The dial state is saved locally; the trail records what was
            # actually sent, so refresh it whenever a turn may have finished.
            if agent_state['session_id'] and not agent_busy.visible:
                refresh_effort_trail()
            if agent_state["session_id"] and (agent_busy.visible or nav_index["i"] == 4):
                # 当前会话即使本机空闲，也可能在 TUI/远端继续或回滚。
                # 空闲沿用低频轮询；未变化的事件不会重绘气泡。
                composer.consume_events()
                session_status.refresh()
                activity_panel.refresh()
                session_queue.refresh()
                session_steering.refresh()
            # 全局审批：任何会话 waiting_approval 都弹窗，与当前所在页无关
            check_global_approvals()
            if (layout["rail"] or layout["panel_dialog"]) and tools_panel.section in {"tasks", "loops"} and not tools_panel.running:
                tools_panel.refresh()
            delay = (0.3 if agent_busy.visible
                     else 0.8 if st == "running"
                     else 5.0 if st in ("done", "failed", "cancelled") else 2.5)
            if nav_index['i'] == 4 and composer.has_more_events:
                delay = 0.15  # bounded, sequential catch-up while the user watches

    page.run_task(poller)
    async def research_activity():
        from .. import research_planning
        shown = set()
        await asyncio.to_thread(research_planning.observe_tasks)
        await asyncio.to_thread(research_planning.observe_sessions)
        while state['poll']:
            await asyncio.sleep(2)
            if not state['poll']:
                return
            await asyncio.to_thread(research_planning.tick)
            await asyncio.to_thread(research_planning.observe_tasks)
            await asyncio.to_thread(research_planning.observe_sessions)
            if not state['poll']:
                return
            result = research_planning.handle({'cmd':'notifications_list', 'unread':True})
            if not result.get('enabled', True):
                continue
            unread = int(result.get('unread_count') or 0)
            if unread != state.get("notif_unread"):
                # 铃铛角标跟随数据层；列表面板打开时顺便重建
                state["notif_unread"] = unread
                notif_badge.visible = unread > 0
                notif_badge.content.value = str(unread) if unread < 100 else "99+"
                if notif_overlay.visible:
                    notif_refresh()
                page.update()
            fresh = [n for n in result.get('notifications', []) if n['id'] not in shown]
            shown.update(n['id'] for n in fresh)
            if fresh:
                page.show_dialog(ft.SnackBar(ft.Text(fresh[0]['title']+' · '+fresh[0].get('body', ''))))
                page.update()
    page.run_task(research_activity)
    async def thinking_motion() -> None:
        frame = 0
        while state['poll']:
            reasoning_motion["enabled"] = bool(feature_switches["ui_animations"].value)
            animate = reasoning_control.motion_enabled() and agent_busy.visible
            reasoning_control.pulse(frame, active=agent_busy.visible)
            for stroke in thinking_strokes:
                stroke.animate_opacity = ft.Animation(motion_duration('panel'), ft.AnimationCurve.EASE_OUT) if animate else None
            if animate:
                for i, stroke in enumerate(thinking_strokes):
                    stroke.opacity = 1 if frame % 3 == i else .3
                frame += 1
                page.update()
            else:
                for stroke in thinking_strokes:
                    stroke.opacity = .65
            await asyncio.sleep(.35 if animate else 1.5)
    page.run_task(thinking_motion)
    refresh_history()


if __name__ == "__main__":
    ft.run(main)
