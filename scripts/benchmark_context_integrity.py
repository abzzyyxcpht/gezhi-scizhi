"""Measure context assembly and saved-state inspection with isolated synthetic data.

Cache-miss timings clear only the derived status cache. They do not flush OS disk
caches or measure process startup, provider latency, or frontend rendering.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def measure(function, samples, prepare=None):
    durations = []
    for _ in range(samples):
        if prepare:
            prepare()
        started = time.perf_counter()
        function()
        durations.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations)
    return {"samples": samples, "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(samples * .95) - 1], 3),
            "max_ms": round(max(ordered), 3)}


def run():
    # Imported configuration and all state live under a newly created directory.
    # No existing user settings, projects, credentials, or sessions are read.
    with tempfile.TemporaryDirectory(prefix="scizhi-context-benchmark-") as directory:
        scratch = Path(directory).resolve()
        os.environ["GEZHI_DATA_DIR"] = str(scratch / "data")
        for key in list(os.environ):
            if key.startswith(("GEZHI_LLM_", "GEZHI_EMB_")):
                del os.environ[key]
        from src import config, prompts, session_controls, settings, skills
        from src.agent_runtime import SessionStore
        from src.context_manifest import public_manifest
        from src.state_store import write_json

        project = scratch / "project"
        project.mkdir()
        skill_root = scratch / "skills"
        for index in range(40):
            folder = skill_root / f"fixture-{index:02}"
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: fixture-{index:02}\ndescription: 合成技能，用于离线测量索引装配开销\n"
                "stages: planning\n---\n" + "合成方法与核验步骤。" * 300, encoding="utf-8")
        for path in (config.DATA_DIR / "GEZHI.md", project / "GEZHI.md", project / "GEZHI.local.md"):
            path.write_text("# 合成规则\n" + "保留来源及适用范围。" * 20, encoding="utf-8")
        records = [{"id": f"fixture_{i:03}", "title": f"合成偏好{i:03}",
                    "content": "合成内容，不代表真实科研事实。" * 6,
                    "scope": "global" if i % 2 else "project", "project_id": "fixture",
                    "approved": True, "enabled": True, "source": "synthetic benchmark"}
                   for i in range(80)]
        write_json(config.DATA_DIR / "research_memory.json", {"version": 1, "items": records})
        settings.save({"skills_active": [], "memory_context_chars": 2000, "llm_enabled": False})

        def assemble():
            return prompts.build_with_manifest("合成测试任务", stage="planning", project_root=project,
                                               project_id="fixture", agent_role="research")

        with mock.patch.object(skills, "SKILLS_DIR", skill_root):
            messages, manifest = assemble()
            assembly = measure(assemble, 60)
        for index in range(80):
            messages.extend([
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"call-{index}", "type": "function", "function": {
                        "name": "fixture.read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"call-{index}",
                 "content": "SYNTHETIC_PRIVATE_BODY_" + "合成历史工具观察。" * 200},
            ])
        store = SessionStore(scratch / "sessions")
        sid = "context-benchmark"
        store.append(sid, "session_created", {"project_id": "fixture", "workspace_mode": "research"})
        store.append(sid, "user_message", {"text": "合成测试任务", "mode": "agent", "agent": "research"})
        store.append(sid, "driver_started", {"driver": "adaptive"})
        store.append(sid, "completed", {"status": "done"})
        state = {"schema_version": 1, "session_id": sid, "status": "done", "messages": messages,
                 "workspace_mode": "research", "context_manifest": public_manifest(manifest),
                 "model_identity": {"model": "synthetic-offline", "protocol": "responses"},
                 "tool_schemas": [], "model_calls": 0, "steps": 0, "tokens": 0,
                 "token_budget": 100000, "max_steps": 16, "usage_complete": True,
                 "context_limit_tokens": 128000}
        path = store.root / f"{sid}.driver.json"
        write_json(path, state)
        research_path = store.root / f"{sid}.research-contexts.json"
        write_json(research_path, {"schema_version": 1, "omitted_entries": 0, "entries": [
            {"request_id": f"request-{i}", "node": "extract", "prompt_key": "extract_one.v3",
             "outcome": "returned", "observed_at": "2026-10-06T00:00:00Z",
             "context_manifest": state["context_manifest"]} for i in range(16)]})

        def inspect():
            return session_controls.session_status(sid, store=store)

        def clear_cache():
            with session_controls._CACHE_LOCK:
                session_controls._CACHE.clear()
                session_controls._RESEARCH_CACHE.clear()

        with mock.patch.object(prompts, "build_with_manifest", side_effect=AssertionError("status reassembled context")):
            misses = measure(inspect, 30, clear_cache)
            hits = measure(inspect, 200)
            with mock.patch.object(session_controls, "_load_state", side_effect=AssertionError("unchanged state reread")), \
                    mock.patch.object(session_controls, "_load_research_contexts", side_effect=AssertionError("unchanged observations reread")):
                observed = inspect()
        payload = json.dumps(observed, ensure_ascii=False).encode("utf-8")
        assert b"SYNTHETIC_PRIVATE_BODY_" not in payload
        assert observed["context_manifest"] == state["context_manifest"]
        assert len(observed['research_contexts']['entries']) == 4
        assert observed['research_contexts']['omitted_entries'] == 12
        return {
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "platform": platform.system(),
            "method": "Synthetic isolated files; real prompt assembly and SessionStore/session_status. Cache miss clears only derived metrics, not OS caches. No provider or UI rendering timings; no competitor comparison.",
            "fixtures": {"skills": 40, "memory_records": 80, "instruction_files": 3,
                         "messages": len(messages), "manifest_sources": len(manifest["sources"]),
                         "research_requests": 16, "public_research_requests": 4,
                         "driver_utf8_bytes": path.stat().st_size,
                         "research_contexts_utf8_bytes": research_path.stat().st_size,
                         "status_utf8_bytes": len(payload)},
            "context_assembly": assembly, "status_derived_cache_miss": misses,
            "status_cache_hit": hits, "status_no_source_rescan_verified": True,
            "status_no_driver_reread_on_hit_verified": True,
            "status_no_research_contexts_reread_on_hit_verified": True, "model_calls": 0,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    report = run()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
