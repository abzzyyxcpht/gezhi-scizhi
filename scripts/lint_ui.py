"""UI 静态校验：把"点开才炸"的错误前移。

五类检查（都是本项目真实踩过的坑）：
1. 自定义辅助函数的关键字不匹配（如 `dark_card(..., width=...)` 但函数没这个形参）；
2. flet 控件的关键字不存在（如 `ft.ProgressRing(stroke=...)`，0.86 里叫 `stroke_width`）；
3. flet 图标/颜色常量不存在（如 `ft.Icons.PLAN`）；
4. flet 的异步方法（FilePicker 的 `pick_files` / `get_directory_path` 等）没有交给
   `page.run_task(...)` 或 `await`——直接调用只会得到 "coroutine never awaited"，
   按钮看起来"点了没反应"；
5. `ft.FilePicker()` 未注册为 page service（`page.services.append(picker)`）——
   调度对了但没注册，对话框同样不弹（2026-09 复发过一次）；
6. `ft.Text(...)` 承载长内容（>60 字面量 / json.dumps 等调用）却没有 `width` 或
   `max_lines`——未设宽的长文本会把整串宽度计进父容器固有尺寸撑破页面，
   无高度边界则无限折行增高（第 28 轮"根治"过的问题在新模块复发）。

用法：python scripts/lint_ui.py        # 有问题时退出码 1
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_FILES = sorted((ROOT / "src" / "ui").glob("*.py"))


def _cfg():
    try:
        import flet
    except ImportError:  # 没装 flet 就只做通用检查
        return None
    return flet


def check_custom_kwargs(tree: ast.AST, rel: str) -> list[str]:
    """调用本文件内自定义函数时，是否传了签名里没有的关键字。"""
    funcs: dict[str, tuple[set[str], bool]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
            funcs[node.name] = (names, a.kwarg is not None)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
            and fn.value.id in {"self", "cls"} else None)
        if name not in funcs:
            continue
        allowed, has_kwargs = funcs[name]
        if has_kwargs:
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg not in allowed:
                out.append(f"{rel}:{node.lineno} 调用 {name}(..., {kw.arg}=...) "
                           f"但该函数不接受此参数（可用：{sorted(allowed)}）")
    return out


def check_flet_kwargs(tree: ast.AST, rel: str, ft) -> list[str]:
    """ft.X(...) 的关键字必须是该类真实支持的字段。"""
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ft"):
            continue
        cls = getattr(ft, node.func.attr, None)
        if not inspect.isclass(cls):
            continue
        try:
            params = inspect.signature(cls).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue
        allowed = set(params)
        for kw in node.keywords:
            if kw.arg and kw.arg not in allowed:
                out.append(f"{rel}:{node.lineno} ft.{node.func.attr}(..., {kw.arg}=...) "
                           f"不是 flet 支持的参数")
    return out


def check_flet_constants(tree: ast.AST, rel: str, ft) -> list[str]:
    """ft.Icons.X / ft.Colors.X 必须真实存在（写错名字只在运行到那一行才炸）。"""
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "ft"):
            continue
        group, member = node.value.attr, node.attr
        if group in ("Icons", "Colors"):
            holder = getattr(ft, group, None)
            if holder is not None and not hasattr(holder, member):
                out.append(f"{rel}:{node.lineno} ft.{group}.{member} 不存在")
    return out



ASYNC_FLET_METHODS = {"pick_files", "get_directory_path", "save_file",
                      "pick_files_and_upload"}


def _is_filepicker_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "FilePicker"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "ft")


def check_filepicker_service(tree: ast.AST, rel: str) -> list[str]:
    """ft.FilePicker() 必须赋给变量并 page.services.append(该变量)。

    2026-09-24 复发记录：调度（await/run_task）做对了但 picker 从未注册，
    "添加文件 / 打开文件夹 / 新建项目"三个按钮的对话框不弹。
    """
    assigned: dict[str, int] = {}
    assigned_lines: set[int] = set()
    call_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_filepicker_call(node.value):
            assigned_lines.add(node.value.lineno)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                assigned[node.targets[0].id] = node.value.lineno
        elif _is_filepicker_call(node):
            call_lines.append(node.lineno)
    registered: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            registered.update(a.id for a in node.args if isinstance(a, ast.Name))
    out: list[str] = []
    for lineno in call_lines:
        if lineno not in assigned_lines:
            out.append(f"{rel}:{lineno} 内联 ft.FilePicker() 无法注册为 service："
                       f"请赋给变量并 page.services.append(...)")
    for name, lineno in assigned.items():
        if name not in registered:
            out.append(f"{rel}:{lineno} ft.FilePicker() 赋给 {name} 但未 "
                       f"page.services.append({name})：对话框不会弹出，按钮假死")
    return out


def check_text_bounds(tree: ast.AST, rel: str) -> list[str]:
    """ft.Text 承载长内容必须设 width 或 max_lines。

    第 28 轮真因：未设 width 的 Text 把整串文字宽度计进固有尺寸，逐级撑破
    父容器；无 max_lines 则在可滚动列里无限增高。字面量短文案与 f-string
    不拦（噪声大），只拦 >60 字面量与调用（json.dumps/str 转换等）形态。
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Text"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ft"):
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        if kwargs & {"width", "max_lines", "expand"}:
            continue
        value = node.args[0] if node.args else None
        risky = (isinstance(value, ast.Constant) and isinstance(value.value, str)
                 and len(value.value) > 60) or isinstance(value, ast.Call)
        if risky:
            out.append(f"{rel}:{node.lineno} ft.Text(长内容) 未设 width/max_lines："
                       f"可能撑破页面或无限增高")
    return out


def check_flet_async_calls(tree: ast.AST, rel: str) -> list[str]:
    """flet 的 FilePicker 等异步方法必须被 run_task 调度或 await，否则按钮是死的。"""
    scheduled: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if name in ("run_task",):          # page.run_task(fn, ...)
                for a in node.args:
                    scheduled.add(id(a))
        elif isinstance(node, ast.Await):
            scheduled.add(id(node.value))

    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in ASYNC_FLET_METHODS:
            if id(node) not in scheduled:
                out.append(f"{rel}:{node.lineno} {fn.attr}(...) 是协程却未被调度："
                           f"请用 page.run_task(...) 或 await")
    return out

def main() -> int:
    ft = _cfg()
    problems: list[str] = []
    checked = 0
    for path in UI_FILES:
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{rel}: 语法错误 {exc}")
            continue
        checked += 1
        problems += check_custom_kwargs(tree, rel)
        if ft is not None:
            problems += check_flet_kwargs(tree, rel, ft)
            problems += check_flet_constants(tree, rel, ft)
            problems += check_flet_async_calls(tree, rel)
        problems += check_filepicker_service(tree, rel)
        problems += check_text_bounds(tree, rel)

    if problems:
        print(f"UI 静态校验发现问题（扫描 {checked} 个文件）：")
        for p in problems:
            print(f"  ✘ {p}")
        return 1
    print(f"UI 静态校验通过：{checked} 个文件，0 处问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
