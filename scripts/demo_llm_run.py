"""离线 LLM 演示：用内置 Mock 端点跑通"LLM 参与"的抽取与综述框架，产出 model_calls.jsonl 样例。

用法：
    python scripts/demo_llm_run.py                 # 使用内置 6 篇样例文献
    python scripts/demo_llm_run.py "研究方向"      # 自定义主题

说明：
- Mock 端点在 scripts/mock_llm_server.py，是 OpenAI 兼容的最小实现，不联网、不产生费用；
- 流程与真实端点完全一致（prompts 模板 → chat_json → 重试 → 日志），
  所以本脚本产出的 data/logs/model_calls.jsonl 与真实接入后的格式、字段一致；
- 真实接入只需设置 GEZHI_LLM_BASE_URL / GEZHI_LLM_API_KEY / GEZHI_LLM_MODEL。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config, llm  # noqa: E402
from src.agents import extraction  # noqa: E402
from src.agents.fixtures import demo_works  # noqa: E402
from src.config import LOG_DIR  # noqa: E402
from src.orchestrator.engine import TaskContext  # noqa: E402

MODEL_LOG = LOG_DIR / "model_calls.jsonl"


def _start_mock() -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "mock_llm_server.py"), "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8")
    line = (proc.stdout.readline() or "").strip()
    if not line.startswith("LISTENING"):
        proc.kill()
        raise RuntimeError(f"Mock 端点启动失败：{line!r}")
    return proc, int(line.split()[1])


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "large language model agents"
    proc, port = _start_mock()
    try:
        # 指向 Mock 端点（与真实端点同协议；快/强双轨都指过去）
        config.LLM_BASE_URL = f"http://127.0.0.1:{port}/v1"
        config.LLM_API_KEY = "mock-key"
        config.LLM_MODEL = "mock-strong"
        config.LLM_MODEL_FAST = "mock-fast"
        config.LLM_MODEL_STRONG = "mock-strong"

        print(f"=== 离线 LLM 演示：{topic} ===")
        print(f"Mock 端点 http://127.0.0.1:{port}/v1 · 可用模型 {llm.list_models()}")
        before = MODEL_LOG.stat().st_size if MODEL_LOG.exists() else 0

        ctx = TaskContext("llmdemo01", "research_pipeline", {"topic": topic})
        ctx.artifacts["lit_filter"] = {"pool": demo_works(topic), "rounds": [{"round": 1}]}

        t0 = time.perf_counter()
        ext = extraction.extract_pool(ctx, {"limit": 6})
        ctx.artifacts["extract"] = ext    # 引擎平时负责的落盘步骤，这里手工补上
        print(f"\n[extract] 抽取 {len(ext['records'])} 篇，耗时 {time.perf_counter() - t0:.2f}s")
        print("  首篇方法：", (ext["records"][0].get("method") or "")[:60])

        t0 = time.perf_counter()
        frame = extraction.build_review_frame(ctx, {})
        ctx.artifacts["review_frame"] = frame
        print(f"\n[review_frame] 生成 {len(frame['sections'])} 节，耗时 {time.perf_counter() - t0:.2f}s")
        for sec in frame["sections"]:
            print(f"  · {sec['title']}｜支撑 {len(sec['refs'])} 篇")

        print("\n事件流：")
        for e in ctx.events:
            print(f"  [{e['ts']}] {e.get('node') or e['kind']} — {e['message']}")

        # 展示本次新增的模型调用日志（真实字段，可直接作参赛材料）
        rows = []
        if MODEL_LOG.exists():
            with MODEL_LOG.open("r", encoding="utf-8") as f:
                f.seek(before)
                rows = [json.loads(x) for x in f.read().splitlines() if x.strip()]
        print(f"\n本次新增模型调用日志 {len(rows)} 条（{MODEL_LOG}）：")
        for r in rows:
            print(f"  · {r['ts']} {r['node']:<12} {r['model_route']:<22} "
                  f"prompt_key={r.get('prompt_key') or '-':<18} "
                  f"{r['prompt_tokens']}+{r['completion_tokens']} tok {r['latency_ms']}ms")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
