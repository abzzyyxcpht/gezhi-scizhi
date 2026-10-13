"""RAG 三级漏斗检索评测：lexical-only 与 lexical+embedding 两种模式对比。

离线可跑：候选来自 fixtures.demo_works（不触网）；lexical 模式通过打桩
embedding.get_provider 为 None 获得与改造前逐比特一致的基线分数。
已配置 embedding 时另跑 lexical+embedding 模式，如实记录两种模式的
池规模 / 阈值 / 耗时 / 漏斗，写入 data/exports/eval_report.json。

--live 模式另起一节「真实源指标」：小样本真实 OpenAlex 查询（课题来自
examples/eval_topics.yaml，每课题封顶 20 篇），结果缓存到
data/cache/eval_live/（TTL 7 天）；无网络或查询失败时该课题记 skipped，
整体仍以退出码 0 结束。真实源指标与合成样例标定指标口径不同，报告中
分节存放、分别标注，禁止混用对比。

用法：python scripts/eval_retrieval.py [--topic X] [--live]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config, embedding  # noqa: E402
from src.agents import literature  # noqa: E402
from src.agents.fixtures import demo_works  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVE_TOPICS_YAML = ROOT / "examples" / "eval_topics.yaml"
LIVE_CACHE_DIR = config.DATA_DIR / "cache" / "eval_live"
LIVE_TTL_SECONDS = 7 * 24 * 3600  # 真实源结果缓存 7 天
LIVE_PER_TOPIC_CAP = 20  # 每课题真实文献封顶
CALIBRATION_NOTICE = ("真实源指标（OpenAlex 真实文献）与合成样例标定指标"
                      "（fixtures 离线演示样例）口径不同，不可混用对比")


def _mode_report(query: str, works: list[dict], tau: float) -> dict:
    """显式评测可使用已配置的向量服务，但禁用聊天终审；elapsed 只计评分。"""
    t0 = time.perf_counter()
    with mock.patch.object(literature, '_llm_refine', return_value=({}, set())):
        result = literature.score_pool(query, works, tau, offline=False)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "pool_size": len(result["pool"]),
        "dropped": len(result["dropped"]),
        "threshold": result["threshold"],
        "elapsed_ms": elapsed_ms,
        "funnel": result.get("funnel", {}),
        "scores_top": [w.get("score") for w in result["pool"][:5]],
    }


def run_eval(topic: str, out_path: Path | None = None) -> dict:
    """对比两种评分模式并生成报告；返回报告字典（同时落盘）。"""
    works = demo_works(topic)
    query = topic
    tau = float(config.LIT_SCORE_THRESHOLD)
    modes: dict[str, dict] = {}

    # 基线：强制 provider 为 None（与未引入向量级前的评分逐比特一致）
    with mock.patch.object(embedding, "get_provider", return_value=None):
        modes["lexical"] = _mode_report(query, works, tau)

    provider = embedding.get_provider()
    if provider is None:
        modes["lexical+embedding"] = {
            "embedding": "unavailable",
            "reason": "未配置 embedding_base_url / embedding_model（或环境变量 GEZHI_EMB_*），"
                      "已跳过向量级模式",
        }
    else:
        modes["lexical+embedding"] = _mode_report(query, works, tau)
        modes["lexical+embedding"]["embedding"] = "available"
        modes["lexical+embedding"]["embedding_model"] = str(getattr(provider, "model", "") or "")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "candidates": len(works),
        "tau": tau,
        "modes": modes,
        "calibration_notice": CALIBRATION_NOTICE,
    }
    out_path = out_path or (config.DATA_DIR / "exports" / "eval_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# ---- 真实源对照评测（--live）：与合成节分节存放，禁止混用 ----

def _live_topics(limit: int = 0) -> list[dict[str, str]]:
    """课题清单来自离线评测集（仅取 id/topic 两个字段做真实检索）。"""
    import yaml
    data = yaml.safe_load(LIVE_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    topics = [{"id": str(t.get("id") or i), "topic": str(t.get("topic") or "")}
              for i, t in enumerate(data.get("topics") or []) if str(t.get("topic") or "").strip()]
    return topics[:limit] if limit > 0 else topics


def _live_cache_get(topic_id: str) -> list | None:
    f = LIVE_CACHE_DIR / f"{topic_id}.json"
    if not f.exists() or time.time() - f.stat().st_mtime >= LIVE_TTL_SECONDS:
        return None
    try:
        works = json.loads(f.read_text(encoding="utf-8")).get("works")
    except (json.JSONDecodeError, AttributeError):
        return None
    return works if isinstance(works, list) else None


def _live_cache_put(topic_id: str, works: list) -> None:
    LIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (LIVE_CACHE_DIR / f"{topic_id}.json").write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).isoformat(), "works": works},
        ensure_ascii=False), encoding="utf-8")


def fetch_live_works(topic_id: str, query: str,
                     cap: int = LIVE_PER_TOPIC_CAP) -> tuple[list, str]:
    """真实 OpenAlex 查询（每课题封顶 cap 篇）；命中 7 天缓存则零网络。

    网络失败向上抛异常，由调用方如实记 skipped。
    """
    cached = _live_cache_get(topic_id)
    if cached is not None:
        return cached, "hit"
    from src.connectors import openalex
    works = openalex.search(query, per_page=cap, node="eval_live")[:cap]
    _live_cache_put(topic_id, works)
    return works, "miss"


def run_live_eval(topics: list[dict[str, str]] | None = None,
                  cap: int = LIVE_PER_TOPIC_CAP) -> dict:
    """逐课题真实检索 + 漏斗评分；单课题失败记 skipped 不中断整体。"""
    results: list[dict] = []
    for item in topics if topics is not None else _live_topics():
        entry: dict = {"topic_id": item["id"], "topic": item["topic"],
                       "source": "openalex", "cap": cap}
        try:
            works, cache_state = fetch_live_works(item["id"], item["topic"], cap)
        except Exception as exc:  # noqa: BLE001 —— 无网/限流/解析失败都如实记 skipped
            entry.update(status="skipped",
                         reason=f"{type(exc).__name__}: {exc}")
            results.append(entry)
            continue
        tau = float(config.LIT_SCORE_THRESHOLD)
        with mock.patch.object(embedding, "get_provider", return_value=None):
            lexical = _mode_report(item["topic"], works, tau)
        modes: dict[str, dict] = {"lexical": lexical}
        provider = embedding.get_provider()
        if provider is None:
            modes["lexical+embedding"] = {
                "embedding": "unavailable",
                "reason": "未配置 embedding_base_url / embedding_model（或环境变量 GEZHI_EMB_*），"
                          "已跳过向量级模式",
            }
        else:
            hybrid = _mode_report(item["topic"], works, tau)
            hybrid["embedding"] = "available"
            hybrid["embedding_model"] = str(getattr(provider, "model", "") or "")
            modes["lexical+embedding"] = hybrid
        entry.update(status="ok", cache=cache_state, candidates=len(works),
                     tau=tau, modes=modes)
        results.append(entry)
    done = [r for r in results if r["status"] == "ok"]
    return {
        "notice": CALIBRATION_NOTICE,
        "source": "openalex",
        "cache_dir": str(LIVE_CACHE_DIR),
        "cache_ttl_days": LIVE_TTL_SECONDS // 86400,
        "per_topic_cap": cap,
        "topics_total": len(results),
        "topics_ok": len(done),
        "topics_skipped": len(results) - len(done),
        "topics": results,
    }


def _print_synthetic_section(report: dict, out: Path) -> None:
    print(f"主题：{report['topic']}（候选 {report['candidates']} 篇，阈值 {report['tau']}）")
    for name, mode in report["modes"].items():
        if mode.get("embedding") == "unavailable":
            print(f"[{name}] 不可用：{mode['reason']}")
            continue
        funnel = mode["funnel"]
        print(f"[{name}] 池 {mode['pool_size']}/{report['candidates']}，阈值 {mode['threshold']}，"
              f"耗时 {mode['elapsed_ms']}ms，漏斗 tier={funnel.get('tier')}"
              + (f"，embedding 模型 {mode['embedding_model']}" if mode.get("embedding_model") else ""))
    print(f"报告已写入：{out}")


def _print_live_section(live: dict) -> None:
    print(f"课题 {live['topics_total']} 个（成功 {live['topics_ok']}，跳过 {live['topics_skipped']}），"
          f"每课题封顶 {live['per_topic_cap']} 篇，缓存 {live['cache_dir']}（TTL {live['cache_ttl_days']} 天）")
    for entry in live["topics"]:
        if entry["status"] == "skipped":
            print(f"[live:{entry['topic_id']}] skipped：{entry['reason']}")
            continue
        line = (f"[live:{entry['topic_id']}] 候选 {entry['candidates']} 篇"
                f"（缓存 {'命中' if entry['cache'] == 'hit' else '未命中'}）")
        for name, mode in entry["modes"].items():
            if mode.get("embedding") == "unavailable":
                line += f"｜{name} 不可用"
                continue
            line += f"｜{name} 池 {mode['pool_size']}/{entry['candidates']} 阈值 {mode['threshold']}"
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="检索漏斗评测：lexical vs lexical+embedding")
    parser.add_argument("--topic", default="large language model agents", help="检索主题")
    parser.add_argument("--live", action="store_true",
                        help="追加真实源对照节：真实 OpenAlex 小样本查询（需网络）")
    parser.add_argument("--live-limit", type=int, default=0,
                        help="只跑前 N 个真实课题（0 = 全部，来自 examples/eval_topics.yaml）")
    args = parser.parse_args()
    report = run_eval(args.topic)
    out = config.DATA_DIR / "exports" / "eval_report.json"
    print("== 合成样例标定指标（fixtures 离线演示样例，非真实文献） ==")
    _print_synthetic_section(report, out)
    if args.live:
        print("== 真实源指标（OpenAlex 真实文献；与上一节口径不同，不可混用对比） ==")
        live = run_live_eval(_live_topics(args.live_limit) if args.live_limit else None)
        _print_live_section(live)
        report["live"] = live
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"真实源节已合并写入：{out}")


if __name__ == "__main__":
    main()
