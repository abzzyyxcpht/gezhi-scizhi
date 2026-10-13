"""格致 SciZhi TUI 版（Textual 终端界面，水墨暗色主题）。

启动：python -m src.ui.tui
快捷键：n 新建任务 | p 暂停 | r 续跑/确认 | e 导出产物 | q 退出
"""
from __future__ import annotations


from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label,
                             Markdown, RichLog, Select, Static, TabbedContent,
                             TabPane, Tree)

from .. import service
from ..config import ROOT_DIR
from ..citation.gbt7714 import format_entry
from . import brand

STATUS_ICON = {"running": "🔵 运行中", "done": "🟢 已完成",
               "waiting_human": "🟠 待确认", "paused": "🟣 已暂停",
               "failed": "🔴 失败", "pending": "⚪ 排队中"}


class GezhiTUI(App):
    """格致终端应用主类。"""

    TITLE = "格致 SciZhi"
    SUB_TITLE = "格物致知，智启科研"

    CSS = """
    Screen { background: #1A2230; }
    #left { width: 44; border-right: solid #3A4658; padding: 1; }
    #brand { color: #C23531; text-style: bold; }
    #slogan { color: #8A94A6; text-style: italic; }
    #quote { color: #5A6577; text-style: italic; margin-bottom: 1; }
    Input { margin-bottom: 1; }
    #tasks { height: 1fr; border: solid #3A4658; }
    RichLog { background: #0F172A; border: solid #3A4658; }
    DataTable { height: 1fr; }
    TabbedContent { height: 1fr; }
    #steps { height: 5; border: solid #3A4658; padding: 0 1; }
    .step-active { color: #C23531; text-style: bold; }
    .step-done { color: #3A7D5D; }
    Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("n", "focus_new", "新建"),
        Binding("p", "pause", "暂停"),
        Binding("r", "resume", "续跑/确认"),
        Binding("e", "export", "导出"),
        Binding("q", "quit", "退出"),
    ]

    current_task: str | None = None
    _seen: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                yield Static("▮ 格致 SciZhi", id="brand")
                yield Static(brand.SLOGAN, id="slogan")
                yield Static(brand.QUOTE, id="quote")
                yield Input(placeholder="研究方向，如：大模型智能体", id="in_topic")
                yield Input(placeholder="关键词（可空）", id="in_kw")
                yield Select([("GB/T 7714—2015", "gbt7714"), ("APA 7", "apa")],
                             value="gbt7714", id="in_style", allow_blank=False)
                yield Button("⏵ 启动科研流水线", id="btn_start", variant="error")
                yield Label("任务档案", id="lbl_tasks")
                yield Tree("历史任务", id="tasks")
            with Vertical():
                yield Static("尚未选择任务", id="steps")
                with TabbedContent():
                    with TabPane("日志", id="tab_log"):
                        yield RichLog(id="log", markup=True)
                    with TabPane("文献池", id="tab_pool"):
                        yield DataTable(id="pool")
                    with TabPane("摘要稿", id="tab_digest"):
                        yield Markdown("", id="digest")
                    with TabPane("综述框架", id="tab_outline"):
                        yield Markdown("", id="outline")
                    with TabPane("参考文献", id="tab_cite"):
                        yield RichLog(id="cite")
                    with TabPane("数据统计", id="tab_stats"):
                        yield RichLog(id="stats")
        yield Footer()

    def on_mount(self) -> None:
        service.init()
        table = self.query_one("#pool", DataTable)
        table.add_columns("题名", "作者/年", "相关度", "理由")
        tree = self.query_one("#tasks", Tree)
        tree.root.expand()
        self._refresh_tasks()
        self.set_interval(0.8, self._poll)
        self.notify("欢迎回来。按 n 新建任务，按 q 退出。", title="格致")

    # ---- 任务操作 ----
    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "btn_start":
            self.action_focus_new_run()

    def action_focus_new(self) -> None:
        self.query_one("#in_topic", Input).focus()

    def action_focus_new_run(self) -> None:
        topic = self.query_one("#in_topic", Input).value.strip()
        if not topic:
            self.notify("请先填写研究方向", severity="warning")
            return
        kw = self.query_one("#in_kw", Input).value
        style = self.query_one("#in_style", Select).value
        try:
            self.current_task = service.create_task(topic, kw, cite_style=style)
        except Exception as ex:  # noqa: BLE001
            self.notify(f"启动失败：{ex}", severity="error")
            return
        self._seen = 0
        self.query_one("#log", RichLog).clear()
        self._refresh_tasks()
        self.notify(f"任务 {self.current_task} 已启动", title="文献猎手出发")

    def action_pause(self) -> None:
        if self.current_task:
            service.pause(self.current_task)
            self.notify("已请求暂停（轮次间隙生效）")

    def action_resume(self) -> None:
        if self.current_task:
            service.resume(self.current_task)
            self.notify("已续跑")

    def action_export(self) -> None:
        if not self.current_task:
            return
        out = ROOT_DIR / "data" / "exports" / self.current_task
        files = service.export_artifacts(self.current_task, out)
        self.notify(f"已导出 {len(files)} 个文件至 {out}", title="导出完成")

    # ---- 数据刷新 ----
    def _refresh_tasks(self) -> None:
        tree = self.query_one("#tasks", Tree)
        tree.root.remove_children()
        for t in service.list_tasks()[:10]:
            icon = STATUS_ICON.get(t["status"], t["status"])
            tree.root.add_leaf(f"{icon} {t['topic'][:16]}",
                               data=t["task_id"])

    def on_tree_node_selected(self, e: Tree.NodeSelected) -> None:
        if e.node.data:
            self.current_task = e.node.data
            self._seen = 0

    def _poll(self) -> None:
        if not self.current_task:
            return
        art = service.artifacts_of(self.current_task)
        # 步骤条
        seq = service.node_sequence()
        pointer = art.get("pointer")
        done_nodes = set(service.get(self.current_task).artifacts.keys())
        parts = []
        for n in seq:
            if n["id"] in done_nodes:
                parts.append(f"[green]●[/] {n['name']}")
            elif n["id"] == pointer:
                parts.append(f"[bold red]▶ {n['name']}[/]")
            else:
                parts.append(f"[grey50]○ {n['name']}[/]")
        status = STATUS_ICON.get(art["status"], art["status"])
        self.query_one("#steps", Static).update(
            f"{status}   " + "  →  ".join(parts))
        # 日志增量
        log = self.query_one("#log", RichLog)
        evts = art.get("events", [])
        for e in evts[self._seen:]:
            color = {"error": "red", "human": "yellow",
                     "workflow": "cyan"}.get(e.get("kind"), "green")
            log.write(f"[{color}][{e.get('ts','')}] {e.get('node') or e.get('kind')}"
                      f" — {e.get('message','')}[/]")
        self._seen = len(evts)
        # 产物
        self._render_pool(art.get("pool", []))
        self.query_one("#digest", Markdown).update(art.get("digest_md") or "*暂无*")
        self.query_one("#outline", Markdown).update(art.get("outline_md") or "*暂无*")
        cite = self.query_one("#cite", RichLog)
        cite.clear()
        for i, w in enumerate(art.get("pool", []), 1):
            try:
                cite.write(f"[{i}] {format_entry(w, 'gbt7714')}")
            except Exception:  # noqa: BLE001
                pass
        if art["status"] == "done" and art.get("pool"):
            cite.write("")
            cite.write(brand.time_saved_text(len(art["pool"]),
                                             len(art.get("rounds", []))))
        self._refresh_tasks()

    _pool_rendered: int = -1

    def _render_pool(self, pool: list[dict]) -> None:
        if len(pool) == self._pool_rendered:
            return
        self._pool_rendered = len(pool)
        table = self.query_one("#pool", DataTable)
        table.clear()
        for w in pool:
            table.add_row((w.get("title") or "")[:38],
                          f"{', '.join((w.get('authors') or [])[:1])}/{w.get('year') or '-'}",
                          f"{w.get('score', 0):.3f}",
                          (w.get("reason") or "")[:30])


def main() -> None:
    GezhiTUI().run()


if __name__ == "__main__":
    main()
