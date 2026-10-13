"""离线评测集：批量跑脱敏课题、核对期望特征、写 data/exports/eval_report.json。

- 全程不触网：候选全部来自 src.agents.fixtures.demo_works（合成样例）；
  运行期间强制 embedding provider 为空，漏斗固定 lexical 级，结果可复现；
- 沿用 scripts/eval_retrieval.py 的报告结构（generated_at/topic/candidates/tau/modes），
  仅新增 "eval_set" 键，不破坏原结构；
- 期望特征判定是纯函数 check_expectations()，tests/test_eval_set.py 有单测。

用法：
    python scripts/run_eval_set.py
    python scripts/run_eval_set.py --topics examples/eval_topics.yaml --out data/exports/eval_report.json
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402

from scripts.eval_retrieval import run_eval  # noqa: E402
from src import config, embedding  # noqa: E402
from src.agents import extraction, literature  # noqa: F401,E402 注册工作流动作
from src.citation import gbt7714  # noqa: F401,E402 注册工作流动作
from src.evidence import build_graph_from_art  # noqa: E402
from src.orchestrator import engine  # noqa: E402
from src.orchestrator.engine import TaskContext  # noqa: E402

DEFAULT_TOPICS = ROOT / "examples" / "eval_topics.yaml"

# 跑题对照的判定口径：离线 fixtures 全部为合成样例（demo=True /
# source_type=synthetic_demo），因此「池为空或全部带离线样例标记」只验证产物
# 标记正确、未被误标为真实文献；不代表真实检索下漏斗能滤掉不相关文献。
OFF_TOPIC_NOTE = ("离线 fixtures 全部为合成样例；本口径只验证产物带样例标记、"
                  "未被误标为真实文献，不代表真实检索的过滤能力")


def load_topics(path: str | Path) -> dict[str, Any]:
    """读取评测集 YAML，返回 {topics, off_topic_control, ...}。"""
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("topics"), list) or not spec["topics"]:
        raise ValueError("评测集文件必须包含非空 topics 列表")
    for item in spec["topics"]:
        if not isinstance(item, dict) or not item.get("topic"):
            raise ValueError("每个课题必须包含 topic 字段")
    return spec


def run_topic(topic: str, keywords: str = "") -> dict[str, Any]:
    """单课题离线跑通流水线，返回指标（异常时 status=error，不抛出）。"""
    ctx = TaskContext(uuid.uuid4().hex[:12], "research_pipeline",
                      {"topic": topic, "keywords": keywords, "cite_style": "gbt7714",
                       "offline": True, "search_query": topic})
    t0 = time.perf_counter()
    try:
        # 断点写入临时目录、embedding 强制为空：保证不触网、不污染 data/
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(engine, "CHECKPOINT_DIR", Path(td)), \
                mock.patch.object(embedding, "get_provider", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()):
            engine.run(ctx)
            if ctx.status == "waiting_human":
                # HITL 检查点：人工确认（不改动文献池）后断点续跑
                wf = engine.load_workflow(ctx.workflow)
                nodes = {n["id"]: n for n in wf["nodes"]}
                if ctx.pointer in nodes:
                    ctx.pointer = nodes[ctx.pointer].get("next")
                engine.run(ctx)
    except Exception as exc:  # noqa: BLE001 —— 单课题失败不中断整套评测
        return {"status": f"error: {type(exc).__name__}",
                "pool_size": 0, "citation_count": 0, "rounds": 0,
                "anchor_rate": 0, "funnel_tier": "", "elapsed_ms": 0,
                "demo_flagged": False}
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    lit = ctx.artifacts.get("lit_filter", {}) or {}
    pool = lit.get("pool", []) or []
    graph = build_graph_from_art(ctx.artifacts)
    return {
        "status": ctx.status,
        "pool_size": len(pool),
        "citation_count": len((ctx.artifacts.get("citation", {}) or {}).get("entries", []) or []),
        "rounds": len(lit.get("rounds", []) or []),
        "anchor_rate": int(graph.get("anchor_rate", 0)),
        "funnel_tier": str((lit.get("funnel", {}) or {}).get("tier", "")),
        "elapsed_ms": elapsed_ms,
        "demo_flagged": all(bool(w.get("demo")) or w.get("source_type") == "synthetic_demo"
                            for w in pool),
    }


def check_expectations(metrics: dict[str, Any], expect: dict[str, Any]) -> dict[str, Any]:
    """纯函数：逐条核对期望特征，返回 {passed, checks}。

    普通课题核对 pool_min / anchor_min / min_rounds；跑题对照课题按
    「池为空或全部带离线样例标记」口径判定（见 OFF_TOPIC_NOTE）。
    """
    checks: list[dict[str, Any]] = []

    def add(feature: str, passed: bool, expected: str, actual: str, note: str = "") -> None:
        checks.append({"feature": feature, "expected": expected,
                       "actual": actual, "passed": bool(passed), "note": note})

    if expect.get("off_topic"):
        ok = metrics.get("pool_size", 0) == 0 or bool(metrics.get("demo_flagged"))
        flagged = "全部带离线样例标记" if metrics.get("demo_flagged") else "存在未标记文献"
        add("off_topic_control", ok,
            "池为空或全部带离线样例标记",
            f"池 {metrics.get('pool_size', 0)} 篇，{flagged}", OFF_TOPIC_NOTE)
    else:
        pool_min = int(expect.get("pool_min", 0))
        anchor_min = float(expect.get("anchor_min", 0))
        min_rounds = int(expect.get("min_rounds", 0))
        add("pool_size>=pool_min", metrics.get("pool_size", 0) >= pool_min,
            f"池规模 ≥ {pool_min}", f"池 {metrics.get('pool_size', 0)} 篇")
        add("anchor_rate>=anchor_min", metrics.get("anchor_rate", 0) >= anchor_min,
            f"锚定率 ≥ {anchor_min:g}%", f"锚定率 {metrics.get('anchor_rate', 0)}%")
        add("rounds>=min_rounds", metrics.get("rounds", 0) >= min_rounds,
            f"轮次 ≥ {min_rounds}", f"轮次 {metrics.get('rounds', 0)}")
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def run_eval_set(topics_path: str | Path = DEFAULT_TOPICS,
                 out_path: str | Path | None = None) -> dict[str, Any]:
    """跑全部课题（含跑题对照），在 eval_retrieval 报告结构上新增 eval_set 键。"""
    spec = load_topics(topics_path)
    out = Path(out_path) if out_path else config.DATA_DIR / "exports" / "eval_report.json"

    items = [dict(item) for item in spec["topics"]]
    control = spec.get("off_topic_control")
    if isinstance(control, dict) and control.get("topic"):
        items.append({**control, "control": True})

    records: list[dict[str, Any]] = []
    for item in items:
        metrics = run_topic(str(item["topic"]), str(item.get("keywords", "") or ""))
        verdict = check_expectations(metrics, item.get("expect", {}) or {})
        records.append({
            "id": str(item.get("id", "") or ""),
            "topic": str(item["topic"]),
            "control": bool(item.get("control")),
            **metrics,
            "passed": verdict["passed"],
            "checks": verdict["checks"],
        })

    # 基线报告：eval_retrieval 的原结构原样保留，只追加 eval_set 键
    base_topic = str(spec["topics"][0]["topic"])
    with mock.patch.object(embedding, "get_provider", return_value=None):
        report = run_eval(base_topic, out)
    passed_n = sum(1 for r in records if r["passed"])
    report["eval_set"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topics_path": str(Path(topics_path)),
        "offline_scope": str(spec.get("offline_scope", "fixtures_demo_only")) +
                         "（合成样例，不构成真实科研证据）",
        "summary": {"total": len(records), "passed": passed_n,
                    "failed": len(records) - passed_n},
        "records": records,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def print_summary(report: dict[str, Any], output_path: str | Path | None = None) -> None:
    """终端摘要表：课题 / 池 / 轮次 / 引用 / 锚定率 / 耗时 / 漏斗 / 结论。"""
    records = report["eval_set"]["records"]
    header = f"{'课题':<30}{'池':>4}{'轮次':>6}{'引用':>6}{'锚定率':>8}{'耗时ms':>9}  {'漏斗tier':<20}{'结论':<4}"
    print("\n离线评测集摘要（不触网，fixtures 合成样例）")
    print("-" * len(header) * 2)
    print(header)
    print("-" * len(header) * 2)
    for r in records:
        label = _clip((r["id"] + " " if r["id"] else "") + r["topic"], 30)
        mark = "✔" if r["passed"] else "✘"
        print(f"{label:<30}{r['pool_size']:>4}{r['rounds']:>6}{r['citation_count']:>6}"
              f"{r['anchor_rate']:>7}%{r['elapsed_ms']:>9}  {r['funnel_tier']:<20}{mark:<4}")
    s = report["eval_set"]["summary"]
    print("-" * len(header) * 2)
    print(f"结论：{s['passed']}/{s['total']} 通过；报告：{output_path or 'data/exports/eval_report.json'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线评测集：批量课题 + 跑题对照，写 eval_report.json")
    parser.add_argument("--topics", default=str(DEFAULT_TOPICS), help="评测集 YAML 路径")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 data/exports/eval_report.json）")
    args = parser.parse_args(argv)
    report = run_eval_set(args.topics, args.out)
    print_summary(report, args.out)
    return 0 if report["eval_set"]["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
