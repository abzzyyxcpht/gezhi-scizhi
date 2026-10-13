"""端到端演示脚本：跑通整条科研流水线并导出可复现产物。"""
from __future__ import annotations

import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.agents import extraction, literature  # noqa: F401,E402 注册动作
from src.citation import gbt7714  # noqa: F401,E402 注册动作
from src.orchestrator.engine import TaskContext, intervene, run
from src.research_analysis import analyze_artifacts, render_report  # noqa: E402


def run_demo(topic: str = "large language model agents",
             out_dir: Path | None = None,
             keywords: str = "", offline: bool = True) -> dict[str, object]:
    """运行离线演示并返回非敏感运行摘要。"""
    ctx = TaskContext(uuid.uuid4().hex[:12], "research_pipeline",
                      {"topic": topic, "keywords": keywords,
                       "cite_style": "gbt7714",
                       "offline": offline,
                       "search_query": "large language model agents" if offline else topic})

    print(f"=== 任务 {ctx.task_id}：{topic} ===\n")
    _run_demo_steps(ctx)

    print(f"\n--- 最终状态：{ctx.status} ---")
    digest = ctx.artifacts.get("extract", {}).get("digest_md", "")
    outline = ctx.artifacts.get("review_frame", {}).get("outline_md", "")
    cites = ctx.artifacts.get("citation", {}).get("entries", [])
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    print(f"摘要稿长度：{len(digest)} 字符；综述框架长度：{len(outline)} 字符；引用条目：{len(cites)} 条")
    if cites:
        print("\n引用样例：")
        for c in cites[:3]:
            print(" ", c["text"])

    out = Path(out_dir) if out_dir else Path("data/artifacts") / f"demo_{ctx.task_id}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest.md").write_text(digest, encoding="utf-8")
    (out / "outline.md").write_text(outline, encoding="utf-8")
    (out / "references_gbt7714.txt").write_text(
        "\n".join(c["text"] for c in cites), encoding="utf-8")
    with (out / "literature_pool.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "title", "year", "venue", "doi", "score", "reason", "source_type", "demo"])
        for item in pool:
            writer.writerow([item.get("id", ""), item.get("title", ""),
                             item.get("year", ""), item.get("venue", ""),
                             item.get("doi", ""), item.get("score", ""),
                             item.get("reason", ""), item.get("source_type", "unknown"), bool(item.get("demo"))])

    audit = analyze_artifacts(ctx.artifacts, topic)
    (out / "research_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "research_audit.md").write_text(render_report(audit), encoding="utf-8")

    result: dict[str, object] = {
        "task_id": ctx.task_id,
        "status": ctx.status,
        "artifact_dir": str(out),
        "pool_count": len(pool),
        "citation_count": len(cites),
        "offline": offline,
        "source_types": sorted({str(w.get("source_type") or "unknown") for w in pool}),
        "verification_scope": "traceability_only",
        "demo_papers": audit["coverage"]["demo_papers"],
        "files": sorted(p.name for p in out.iterdir() if p.is_file()),
    }
    (out / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"\n产物已保存至 {out}")
    return result


def _run_demo_steps(ctx: TaskContext) -> None:
    run(ctx)
    print(f"\n--- 首次执行结束，状态：{ctx.status} ---")
    pool = ctx.artifacts.get("lit_filter", {}).get("pool", [])
    print(f"文献池：{len(pool)} 篇；轮次：{ctx.artifacts.get('lit_filter', {}).get('rounds')}")
    if pool:
        worst = pool[-1]
        print(f"\n--- 人工干预：剔除《{(worst.get('title') or '')[:50]}》 ---")
        intervene(ctx, "lit_filter", {"pool": pool[:-1]})
    print("\n--- 人工确认，断点续跑 ---")
    from src.orchestrator.engine import load_workflow
    wf = load_workflow(ctx.workflow)
    nodes = {n["id"]: n for n in wf["nodes"]}
    if ctx.pointer in nodes:
        ctx.pointer = nodes[ctx.pointer].get("next")
    run(ctx)


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "large language model agents"
    keywords = sys.argv[2] if len(sys.argv) > 2 else ""
    run_demo(topic, keywords=keywords)


if __name__ == "__main__":
    main()
