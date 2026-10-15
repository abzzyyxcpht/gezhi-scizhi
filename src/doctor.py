"""系统自检：检查运行环境、依赖与服务配置。

一键体检：数据目录可写、核心依赖、文献源连通性、LLM 端点连通性、缓存占用。
桌面版"设置"页与 TUI 菜单均可触发；桥接层暴露 doctor 命令。
所有检查都不抛异常——自检本身必须永远"能跑完"。
"""
from __future__ import annotations

import time
from typing import Any

from . import config, llm


def _dir_check() -> tuple[bool, str]:
    try:
        probe = config.DATA_DIR / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(config.DATA_DIR)
    except OSError as e:
        return False, f"数据目录不可写：{e}"


def _deps_check() -> tuple[bool, str]:
    missing = []
    for mod in ("httpx", "yaml", "pandas", "numpy", "sklearn", "matplotlib"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return False, f"缺少依赖：{'、'.join(missing)}（pip install -r requirements.txt）"
    return True, "核心依赖齐全（pandas / sklearn / matplotlib / httpx / yaml）"


def _openalex_check() -> tuple[bool, str]:
    try:
        import httpx
        t0 = time.perf_counter()
        r = httpx.get("https://api.openalex.org/works",
                      params={"search": "test", "per-page": 1},
                      timeout=8.0)
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code == 200:
            return True, f"OpenAlex 文献源连通（{ms}ms）"
        return False, f"OpenAlex 返回 HTTP {r.status_code}（离线演示不受影响）"
    except Exception as e:  # noqa: BLE001
        return False, f"OpenAlex 不可达：{type(e).__name__}（离线演示不受影响）"


def _llm_check() -> tuple[bool, str]:
    if not llm.available():
        return True, "未配置 LLM —— 当前为规则引擎模式，离线可完整运行"
    try:
        import httpx
        models = llm.list_models()
        if not models:
            # 清单为空：区分"不可达"与"端点未实现 /models"，给出可操作原因
            base = config.LLM_BASE_URL.rstrip("/")
            try:
                r = httpx.get(f"{base}/models", headers=llm._headers(), timeout=8.0)
                return False, f"LLM 端点返回 HTTP {r.status_code} 或模型清单为空，请检查设置页配置"
            except Exception as e:  # noqa: BLE001
                return False, f"LLM 端点不可达：{type(e).__name__}，将自动降级为规则引擎"
        detail = f"端点连通，探测到 {len(models)} 个模型；默认 {config.LLM_MODEL}"
        fast, strong = llm.model_for("fast"), llm.model_for("strong")
        if fast != config.LLM_MODEL:
            detail += f"；快轨 {fast}"
        if strong != config.LLM_MODEL:
            detail += f"；强轨 {strong}"
        if config.LLM_MODEL not in models:
            return False, detail + "（默认模型不在端点清单中，请核对模型名）"
        return True, detail
    except Exception as e:  # noqa: BLE001
        return False, f"LLM 端点检查异常：{type(e).__name__}，将自动降级为规则引擎"


def _embedding_check() -> tuple[bool, str]:
    """Embedding 向量检索：未配置视为正常（lexical 兜底）；已配置则探测一次。"""
    from . import embedding
    try:
        provider = embedding.get_provider()
        if provider is None:
            return True, "未配置 Embedding 向量检索，lexical 评分兜底（可在设置页配置）"
        vec = provider.embed(["ping"])
        if vec and vec[0]:
            model = str(getattr(provider, "model", "") or "")
            return True, f"Embedding 端点连通（模型 {model}），向量级评分已启用"
        return False, "Embedding 端点探测失败，已自动降级为 lexical 评分（不影响离线演示）"
    except Exception as e:  # noqa: BLE001
        return False, f"Embedding 自检异常：{type(e).__name__}，将使用 lexical 评分兜底"


def _prompts_check() -> tuple[bool, str]:
    """提示词工程自检：模板可构造、版本就绪、JSON 容错解析可用。"""
    from . import prompts
    try:
        key, msgs = prompts.lit_review("自检探针", [])
        if not (key and msgs and msgs[0]["role"] == "system"):
            return False, "提示词模板结构异常"
        sample = prompts.extract_json('```json\n{"reviews": []}\n```')
        if sample != {"reviews": []}:
            return False, "extract_json 容错解析异常（围栏剥离失败）"
        return True, f"提示词模板 v{prompts.PROMPT_VERSION} 就绪（{len(prompts.SYSTEM_PROMPT)} 字系统提示词）"
    except Exception as e:  # noqa: BLE001
        return False, f"提示词自检失败：{type(e).__name__}"


def _cache_check() -> tuple[bool, str]:
    cache = config.DATA_DIR / "cache"
    n = 0
    size = 0
    if cache.exists():
        for f in cache.glob("*.json"):
            n += 1
            size += f.stat().st_size
    mb = size / 1024 / 1024
    return True, f"检索缓存 {n} 条 / {mb:.1f} MB（24h 自动过期，可手动清空）"


def _artifacts_check() -> tuple[bool, str]:
    n_tasks = len(list(config.CHECKPOINT_DIR.glob("*.json")))
    return True, f"断点存档 {n_tasks} 份，产物目录 {config.ARTIFACT_DIR}"


def _mcp_check() -> tuple[bool, str]:
    """MCP 生态：未配置视为正常；已配置则逐台探测。"""
    from . import mcp
    servers = mcp.load_servers()
    if not servers:
        return True, "未配置 MCP 服务器（data/mcp_servers.json，可选工具生态）"
    st = mcp.status_all()
    ok_n = sum(1 for s in st if s["ok"])
    tools = sum(len(s["tools"]) for s in st if s["ok"])
    bad = "；".join(f"{s['name']}：{s['error']}" for s in st if not s["ok"])
    detail = f"MCP {ok_n}/{len(servers)} 台在线，共 {tools} 个工具"
    return ok_n > 0, detail + (("；离线——" + bad) if bad else "")


def _agents_check() -> tuple[bool, str]:
    """专项智能体编制表必须可加载且覆盖赛题功能位。"""
    try:
        from .agents.specs import ROSTER
        need = {"research", "pipeline", "literature", "extraction",
                "synthesis", "citation", "stats", "critic", "explore"}
        missing = sorted(need - set(ROSTER))
        if missing:
            return False, f"编制表缺智能体：{'、'.join(missing)}"
        names = "、".join(f"{s.title}({s.name})" for s in ROSTER.values())
        return True, f"{len(ROSTER)} 个专项智能体就绪：{names}"
    except Exception as e:  # noqa: BLE001
        return False, f"编制表加载失败：{type(e).__name__}: {e}"


def _git_check() -> tuple[bool, str]:
    """研究档案库：git 可用性（缺失只降级档案功能，不影响导出）。"""
    from . import research_repo
    if not research_repo.git_available():
        return False, "未检测到 git——研究档案库不可用（产物导出不受影响）"
    return True, f"git 可用，研究档案库 {research_repo.REPO_DIR}"


CHECKS: list[tuple[str, Any]] = [
    ("数据目录", _dir_check),
    ("核心依赖", _deps_check),
    ("文献源 OpenAlex", _openalex_check),
    ("大模型端点", _llm_check),
    ("Embedding 向量检索", _embedding_check),
    ("提示词工程", _prompts_check),
    ("专项智能体编制表", _agents_check),
    ("MCP 工具生态", _mcp_check),
    ("研究档案库", _git_check),
    ("检索缓存", _cache_check),
    ("断点存档", _artifacts_check),
]


def run_doctor() -> dict[str, Any]:
    """执行全部自检项，返回 {ok, items:[{name, ok, detail}], summary}。"""
    items: list[dict[str, Any]] = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 —— 自检绝不中断
            ok, detail = False, f"检查异常：{e}"
        items.append({"name": name, "ok": bool(ok), "detail": detail})
    hard_fail = any(not it["ok"] for it in items[:2])  # 目录/依赖属硬性项
    n_pass = sum(1 for it in items if it["ok"])
    summary = f"{n_pass}/{len(items)} 项通过"
    return {"ok": not hard_fail, "items": items, "summary": summary}


def format_doctor_text() -> str:
    """渲染为多行纯文本（TUI / 命令行直接打印）。"""
    r = run_doctor()
    marks = {True: "✔", False: "✘"}
    lines = [f"系统自检：{r['summary']}", ""]
    for it in r["items"]:
        lines.append(f"  {marks[it['ok']]} {it['name']}　{it['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Windows 控制台默认 GBK，✔/✘ 会炸编码；强制 UTF-8 输出
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(format_doctor_text())
