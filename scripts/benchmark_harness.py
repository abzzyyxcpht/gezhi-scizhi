"""Reproducible, isolated SciZhi bridge and session-store microbenchmarks.

Run: python scripts/benchmark_harness.py --out benchmark.json
No provider, MCP server, plugin or real research data is used. Process-cold
startup measurements do not imply cold operating-system filesystem caches.

--e2e-llm（默认关闭，显式开启才跑）：用真实已配置的 LLM 端点跑一条完整
agent 任务（模型规划 / adaptive driver，离线 planner 不参与），输出延迟与
token 成本基线到同一 JSON 报告的 metrics.e2e_llm_agent_task。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
TASK_COUNT = 100
SESSION_COUNT = 100
DOCUMENTS_PER_TASK = 17
EVENTS_PER_TASK = 40
EVENTS_PER_SESSION = 24
CONCURRENT_REQUESTS = 20
DISABLED_SETTINGS = {
    "llm_enabled": False, "llm_api_key": "", "llm_base_url": "",
    "mcp_enabled": False, "plugins_enabled": False, "git_archive": False,
    "enable_crossref": False, "default_offline": True,
}


def _prepare_data(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "settings.json").write_text(json.dumps(DISABLED_SETTINGS), encoding="utf-8")
    (path / "mcp_servers.json").write_text('{"servers": []}', encoding="utf-8")


def _install_network_guard(guard_dir: Path) -> Path:
    """sitecustomize guards the real `-m src.bridge` entrypoint, without wrapping it."""
    guard_dir.mkdir(parents=True)
    (guard_dir / "sitecustomize.py").write_text(
        "import os, sys\n"
        "def _deny_network(event, args):\n"
        "    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'socket.bind'}:\n"
        "        with open(os.environ['GEZHI_BENCH_NETWORK_LOG'], 'a', encoding='utf-8') as log:\n"
        "            log.write(event + '\\n')\n"
        "        raise RuntimeError('Network disabled by harness benchmark')\n"
        "sys.addaudithook(_deny_network)\n", encoding="utf-8")
    return guard_dir


def _environment(data_dir: Path, guard_dir: Path, network_log: Path) -> dict[str, str]:
    env = dict(os.environ)
    # A user's provider variables must not override the temporary settings.
    for key in list(env):
        if key.startswith("GEZHI_LLM_"):
            env.pop(key)
    env.update(GEZHI_DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               GEZHI_BENCH_NETWORK_LOG=str(network_log),
               PYTHONPATH=os.pathsep.join((str(guard_dir), str(ROOT))))
    return env


# ---- 真实 LLM 端到端基线（--e2e-llm，独立子进程，不经网络守卫） ----

# 子进程协议：stdout 单行 E2E_RESULT / E2E_ERROR + JSON；凭据只经环境变量传递。
_E2E_CHILD = r'''
import json, sys, time
sys.path.insert(0, {root!r})
from src import config, llm
from src.agent_runtime import AgentRuntime

REQUEST = {request!r}

def emit(kind, payload):
    print(kind + " " + json.dumps(payload, ensure_ascii=False), flush=True)

if not llm.available("planning"):
    emit("E2E_ERROR", {{"error": "LLM 未配置或不可用（检查 GEZHI_LLM_* 环境变量或设置页）",
                        "base_url_set": bool(config.LLM_BASE_URL), "model": config.LLM_MODEL}})
    sys.exit(2)

runtime = AgentRuntime(offline=False, llm_enabled=True, permissions={{"*": "allow"}})
t0 = time.perf_counter()
result = runtime.start(REQUEST, mode="agent", agent="research")
elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)

# planner 口径：adaptive driver 直接由模型驱动（无离线 planner 参与）；
# 非 driver 路径则读 plan_created 事件的 planner 字段核对。
planner = ""
for event in runtime.sessions.read(result["session_id"]):
    if event.get("type") == "plan_created":
        planner = str((event.get("payload") or {{}}).get("plan", {{}}).get("planner") or "")
driver_mode = result.get("driver")
offline_planner = driver_mode != "adaptive" and planner != "model"

# token 成本：以 model_calls.jsonl（供应商报送口径）为准，driver 自报为备
log_rows = []
log_path = config.LOG_DIR / "model_calls.jsonl"
if log_path.exists():
    log_rows = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
usage = {{k: sum((r.get(k) or 0) for r in log_rows)
         for k in ("prompt_tokens", "completion_tokens")}}

emit("E2E_RESULT", {{
    "request": REQUEST, "status": result.get("status"), "elapsed_ms": elapsed_ms,
    "driver": driver_mode, "planner": planner, "offline_planner": offline_planner,
    "steps": result.get("steps"), "model_calls": result.get("model_calls"),
    "tokens_driver": result.get("tokens"), "usage_complete": result.get("usage_complete"),
    "usage_log": usage, "model_calls_logged": len(log_rows),
}})
'''

E2E_DEFAULT_REQUEST = "调研大语言模型智能体在科研文献检索中的应用现状，用中文给出三条结论"
E2E_SETTINGS = {**DISABLED_SETTINGS, "llm_enabled": True, "default_offline": False,
                "agent_execution": "adaptive"}


def _run_e2e_llm(*, scratch: Path, passthrough: dict[str, str], request: str,
                 timeout: float) -> dict[str, Any]:
    """真实 LLM 下跑一条完整 agent 任务，返回延迟/token 成本基线。

    子进程使用独立临时数据目录；GEZHI_LLM_* 凭据仅在显式开启时从宿主
    环境原样带入，其余隔离约定与微基准一致。LLM 未配置或子进程失败时
    如实记 ok=False，不影响微基准部分与整体退出码。
    """
    data_dir = scratch / "e2e_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "settings.json").write_text(json.dumps(E2E_SETTINGS), encoding="utf-8")
    (data_dir / "mcp_servers.json").write_text('{"servers": []}', encoding="utf-8")
    child_path = scratch / "e2e_child.py"
    child_path.write_text(_E2E_CHILD.format(root=str(ROOT), request=request), encoding="utf-8")
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("GEZHI_LLM_"):
            env.pop(key)
    env.update(passthrough)  # 真实端点配置仅在此显式开启时恢复
    env.pop("GEZHI_BENCH_NETWORK_LOG", None)
    env.update(GEZHI_DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONPATH=str(ROOT))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, str(child_path)], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "request": request, "timeout_s": timeout,
                "error": f"e2e 子进程超时（>{timeout}s）"}
    wall_ms = round((time.perf_counter() - t0) * 1000, 3)
    result, error_payload = None, None
    for line in proc.stdout.splitlines():
        if line.startswith("E2E_RESULT "):
            result = json.loads(line[len("E2E_RESULT "):])
        elif line.startswith("E2E_ERROR "):
            error_payload = json.loads(line[len("E2E_ERROR "):])
    if result is None:
        return {"ok": False, "request": request, "returncode": proc.returncode,
                "child_wall_ms": wall_ms,
                "error": (error_payload or {}).get("error")
                         or (proc.stderr.strip()[-2000:] or "子进程未产出 E2E_RESULT")}
    report: dict[str, Any] = {"ok": True, "request": request, "child_wall_ms": wall_ms,
                              "returncode": proc.returncode, **result}
    if result.get("offline_planner"):
        report["degraded"] = "模型规划不可用，任务回退离线 planner；基线不代表真实 LLM 成本"
    return report


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _summary(values: list[float]) -> dict[str, Any]:
    return {"samples": len(values), "unit": "ms",
            "p50_ms": round(_percentile(values, .50), 6),
            "p95_ms": round(_percentile(values, .95), 6),
            "mean_ms": round(statistics.fmean(values), 6),
            "min_ms": round(min(values), 6), "max_ms": round(max(values), 6),
            "samples_ms": [round(value, 6) for value in values]}


def _measure_list(fn: Callable[[], list], *, samples: int,
                  expected_count: int, warmups: int = 5) -> dict[str, Any]:
    for _ in range(warmups):
        if len(fn()) != expected_count:
            raise AssertionError("Fixture count did not match benchmark definition")
    durations = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        rows = fn()
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        if len(rows) != expected_count:
            raise AssertionError("List benchmark returned incomplete data")
    return {**_summary(durations), "items": expected_count,
            "warmup_calls_excluded": warmups, "measurement": "in_process_call_only"}


class BridgeClient:
    """Binary JSONL client with bounded waits and response arrival timestamps."""

    def __init__(self, env: dict[str, str], timeout: float):
        self.timeout = timeout
        self._incoming: queue.Queue = queue.Queue()
        self._write_lock = threading.Lock()
        self.stderr: list[str] = []
        self._started = time.perf_counter_ns()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "src.bridge"], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._errors = threading.Thread(target=self._pump_stderr, daemon=True)
        self._reader.start()
        self._errors.start()
        try:
            ready, _, received = self.receive()
            if ready != {"ok": True, "ready": True}:
                raise AssertionError(f"Unexpected ready handshake: {ready}")
            self.ready_ms = (received - self._started) / 1_000_000
        except BaseException:
            self.close()
            raise

    def _pump_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in iter(self.process.stdout.readline, b""):
            arrived = time.perf_counter_ns()
            try:
                response = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                self._incoming.put((exc, line, arrived))
            else:
                self._incoming.put((response, line, arrived))
        self._incoming.put((EOFError("Bridge stdout closed"), b"", time.perf_counter_ns()))

    def _pump_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in iter(self.process.stderr.readline, b""):
            self.stderr.append(line.decode("utf-8", errors="replace").rstrip())

    def receive(self) -> tuple[dict[str, Any], bytes, int]:
        try:
            response, raw, arrived = self._incoming.get(timeout=self.timeout)
        except queue.Empty as exc:
            raise TimeoutError("Timed out waiting for bridge JSONL response") from exc
        if isinstance(response, Exception):
            raise RuntimeError(f"Bridge protocol failure: {response}; stderr={self.stderr[-5:]}")
        if not isinstance(response, dict):
            raise AssertionError("Bridge response must be a JSON object")
        return response, raw, arrived

    def send(self, request: dict[str, Any]) -> int:
        raw = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            written = time.perf_counter_ns()
            assert self.process.stdin is not None
            self.process.stdin.write(raw)
            self.process.stdin.flush()
            return written

    def request(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes, int]:
        self.send(request)
        response, raw, arrived = self.receive()
        if response.get("ok") is not True or response.get("request_id") != request.get("request_id"):
            raise AssertionError(f"Request failed or response ID mismatched: {response}")
        return response, raw, arrived

    def close(self) -> int:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            raise RuntimeError("Bridge failed to stop after stdin EOF")
        finally:
            self._reader.join(timeout=2)
            self._errors.join(timeout=2)
            if self.process.stdout:
                self.process.stdout.close()
            if self.process.stderr:
                self.process.stderr.close()
        return code


def _task_fixtures(service, TaskContext) -> list[Any]:
    """Synthetic records are identified in every textual research artifact."""
    tasks = []
    statuses = (("done", None), ("waiting_human", "human_review"),
                ("paused", "lit_filter"), ("running", "extract"))
    for index in range(TASK_COUNT):
        task_id = f"bench_task_{index:03d}"
        ctx = TaskContext(task_id, "research_pipeline", {
            "topic": f"[BENCHMARK SYNTHETIC] Topic {index}", "offline": True})
        ctx.status, ctx.pointer = statuses[index % len(statuses)]
        pool = [{"id": f"bench_document_{doc:03d}",
                 "title": f"[BENCHMARK SYNTHETIC] Paper {doc}",
                 "authors": ["Synthetic Fixture"], "year": 2020 + doc % 6,
                 "score": round(1 - doc / 100, 4), "decision": "保留",
                 "reason": "仅用于性能测量的合成记录，不构成科研证据。",
                 "url": f"https://example.invalid/benchmark/{doc}",
                 "abstract": "Synthetic fixture abstract for benchmarking research task payloads. " * 30}
                for doc in range(DOCUMENTS_PER_TASK)]
        ctx.artifacts = {
            "lit_filter": {"pool": pool, "rounds": [{"round": 1, "candidates": 17,
                                                    "new_relevant": 17, "ratio": 1}]},
            "extract": {"records": [{"id": paper["id"], "method": "Synthetic method"} for paper in pool],
                        "digest_md": f"# SYNTHETIC DIGEST {task_id}\n" + "Performance fixture paragraph.\n" * 100},
            "review_frame": {"outline_md": "# SYNTHETIC OUTLINE\n" + "Fixture section.\n" * 50},
            "citation": {"entries": [{"source_id": paper["id"],
                                       "text": paper["title"], "issues": []} for paper in pool]},
        }
        for number in range(EVENTS_PER_TASK):
            ctx.emit("node", node="lit_filter", message=f"[SYNTHETIC] benchmark event {number}")
        ctx.save()
        service._tasks[task_id] = ctx
        tasks.append(ctx)
    return tasks


def _session_fixtures(SessionStore, data_dir: Path):
    store = SessionStore(data_dir / "sessions")
    for index in range(SESSION_COUNT):
        sid = f"bench_session_{index:03d}"
        store.append(sid, "user_message", {"text": f"[BENCHMARK SYNTHETIC] Session {index}",
                                           "agent": "research", "mode": "agent"})
        store.append(sid, "agent_selected", {"agent": "research"})
        store.append(sid, "plan_created", {"plan": {"request": f"synthetic request {index}",
                                                     "steps": [{"tool": "fixture"} for _ in range(10)]}})
        for step in range(10):
            store.append(sid, "tool_call", {"id": f"call_{step}", "name": "fixture"})
            store.append(sid, "tool_result", {"tool_call_id": f"call_{step}",
                                              "result": {"ok": True, "text": "synthetic result " * 20}})
        store.append(sid, "completed", {"status": "done"})
    if len(store.read("bench_session_000")) != EVENTS_PER_SESSION:
        raise AssertionError("Session fixture definition changed")
    return store


def _concurrent_snapshots(client: BridgeClient) -> dict[str, Any]:
    # Warm the actual session runtime so this measures queueing and projection,
    # rather than charging a lazy import to an arbitrary concurrent request.
    client.request({"cmd": "session_snapshot", "session_id": "bench_session_000",
                    "request_id": "warm_session"})
    barrier = threading.Barrier(CONCURRENT_REQUESTS)

    def sender(index):
        request_id = f"concurrent_{index:02d}"
        sid = f"bench_session_{index:03d}"
        barrier.wait(timeout=10)
        sent = client.send({"cmd": "session_snapshot", "session_id": sid, "request_id": request_id})
        return request_id, sid, sent

    wall_start = time.perf_counter_ns()
    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as executor:
        futures = [executor.submit(sender, index) for index in range(CONCURRENT_REQUESTS)]
        expected = {rid: (sid, sent) for rid, sid, sent in (future.result(timeout=15) for future in futures)}
    arrival_order, durations, checks = [], [], []
    total_wire_bytes = 0
    for _ in range(CONCURRENT_REQUESTS):
        response, raw, arrived = client.receive()
        rid = response.get("request_id")
        if rid not in expected or rid in arrival_order:
            raise AssertionError(f"Unknown or duplicate concurrent response ID: {rid}")
        sid, sent = expected[rid]
        snapshot = response.get("snapshot") or {}
        if response.get("ok") is not True or snapshot.get("session_id") != sid or snapshot.get("status") != "done":
            raise AssertionError(f"Response payload did not match request {rid}: {response}")
        arrival_order.append(rid)
        durations.append((arrived - sent) / 1_000_000)
        total_wire_bytes += len(raw)
        checks.append({"request_id": rid, "expected_session_id": sid,
                       "actual_session_id": snapshot["session_id"], "ok": True})
    send_order = sorted(expected, key=lambda rid: expected[rid][1])
    return {**_summary(durations), "concurrent_senders": CONCURRENT_REQUESTS,
            "protocol": "real_stdio_bridge", "command": "session_snapshot",
            "warmup_requests_excluded": 1, "all_matched": True, "mismatches": 0,
            "duplicates": 0, "missing": 0, "send_order": send_order, "arrival_order": arrival_order,
            "out_of_order_observed": send_order != arrival_order, "response_checks": checks,
            "total_response_wire_bytes": total_wire_bytes,
            "wall_ms_including_client_threads": round((time.perf_counter_ns() - wall_start) / 1_000_000, 6)}


def _source_fingerprints() -> dict[str, str]:
    names = ("scripts/benchmark_harness.py", "src/bridge.py", "src/service.py",
             "src/agent_runtime.py", "src/settings.py", "src/orchestrator/engine.py", "src/mcp.py",
             "src/project_workspace.py", "src/session_workspace.py", "src/research_planning.py")
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def run_benchmark(*, cold_runs: int, samples: int, timeout: float,
                  e2e_llm: bool = False, e2e_request: str = E2E_DEFAULT_REQUEST,
                  e2e_timeout: float = 180.0) -> dict[str, Any]:
    if any(name == "src.config" or name.startswith("src.") for name in sys.modules):
        raise RuntimeError("Run this standalone so GEZHI_DATA_DIR is set before importing project modules")
    # 宿主真实端点凭据只在 --e2e-llm 显式开启时经 passthrough 带给 e2e 子进程
    llm_passthrough = {k: v for k, v in os.environ.items() if k.startswith("GEZHI_LLM_")}
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "ok": False,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {"python": sys.version, "python_executable": sys.executable,
                        "python_implementation": platform.python_implementation(),
                        "os": platform.platform(), "machine": platform.machine(),
                        "processor": platform.processor(), "logical_cpus": os.cpu_count(),
                        "gc_enabled": gc.isenabled()},
        "source_sha256": _source_fingerprints(),
        "methodology": {
            "percentiles": "linear interpolation at (n - 1) * percentile",
            "cold_start": "new python -m src.bridge process; empty temporary data; OS file cache not cleared",
            "list_timing": "50 calls by default after 5 warmups; fixture creation/imports excluded",
            "poll_bytes": "actual UTF-8 stdout bytes including platform JSONL line ending; same task and request ID",
            "network": "LLM/MCP/plugins disabled; Python audit hook rejects socket connect/DNS/sendto/bind",
            "fixture_scope": "temporary GEZHI_DATA_DIR; synthetic data only; deleted after measurement",
            "comparison_scope": "This project on the recorded machine only; no upstream harness was benchmarked",
            "e2e_llm": "separate child process; real configured endpoint (--e2e-llm only); "
                      "adaptive driver with model planning (offline planner reported as degraded); "
                      "token cost from model_calls.jsonl provider-reported usage",
        },
        "fixtures": {"tasks": TASK_COUNT, "documents_per_task": DOCUMENTS_PER_TASK,
                     "events_per_task": EVENTS_PER_TASK, "sessions": SESSION_COUNT,
                     "events_per_session": EVENTS_PER_SESSION},
        "metrics": {},
    }
    original_env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="gezhi-harness-bench-") as scratch:
        scratch_path = Path(scratch)
        guard = _install_network_guard(scratch_path / "guard")
        network_log = scratch_path / "blocked-network.log"
        cold_times = []
        for index in range(cold_runs):
            data_dir = scratch_path / f"cold_{index}"
            _prepare_data(data_dir)
            client = BridgeClient(_environment(data_dir, guard, network_log), timeout)
            cold_times.append(client.ready_ms)
            if client.close() != 0:
                raise AssertionError(f"Cold-start bridge exited with an error: {client.stderr}")
        document["metrics"]["bridge_process_cold_ready"] = _summary(cold_times)

        data_dir = scratch_path / "fixtures"
        _prepare_data(data_dir)
        os.environ.clear()
        os.environ.update(_environment(data_dir, guard, network_log))
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        blocked_in_parent = []

        def deny_parent_network(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"}:
                blocked_in_parent.append(event)
                raise RuntimeError("Network disabled by harness benchmark")

        sys.addaudithook(deny_parent_network)
        try:
            from src import config, service
            from src.agent_runtime import SessionStore
            from src.orchestrator.engine import TaskContext
            if config.DATA_DIR.resolve() != data_dir.resolve():
                raise AssertionError("Project data directory escaped the temporary benchmark directory")
            service.init()
            tasks = _task_fixtures(service, TaskContext)
            sessions = _session_fixtures(SessionStore, data_dir)
            document["metrics"]["service_list_tasks_100"] = _measure_list(
                service.list_tasks, samples=samples, expected_count=TASK_COUNT)
            document["metrics"]["session_store_list_100"] = _measure_list(
                sessions.list, samples=samples, expected_count=SESSION_COUNT)
            document["fixtures"]["session_jsonl_total_bytes"] = sum(
                path.stat().st_size for path in (data_dir / "sessions").glob("*.jsonl"))
            client = BridgeClient(_environment(data_dir, guard, network_log), timeout)
            try:
                # Restored checkpoints intentionally omit live events. Report
                # exactly what the real bridge transports from that fixture.
                full, full_raw, _ = client.request({"cmd": "poll", "task_id": tasks[0].task_id,
                                                    "full": True, "request_id": "poll_size"})
                steady, steady_raw, _ = client.request({"cmd": "poll", "task_id": tasks[0].task_id,
                                                        "art_rev": full["art_rev"],
                                                        "since": full["total_events"], "full": False,
                                                        "request_id": "poll_size"})
                if "pool" not in full or "pool" in steady or steady.get("events"):
                    raise AssertionError("Steady poll did not suppress unchanged artifacts/events")
                document["metrics"]["poll_payload_bytes"] = {
                    "protocol": "real_stdio_bridge", "full_bytes": len(full_raw),
                    "steady_bytes": len(steady_raw),
                    "reduction_percent": round((1 - len(steady_raw) / len(full_raw)) * 100, 4),
                    "full_to_steady_ratio": round(len(full_raw) / len(steady_raw), 4),
                    "documents": DOCUMENTS_PER_TASK,
                    "transported_events": full["total_events"],
                    "note": "Checkpoint restore does not restore in-memory events; both responses use that same restored task",
                }
                document["metrics"]["concurrent_request_id_health"] = _concurrent_snapshots(client)
            finally:
                exit_code = client.close()
            if exit_code:
                raise AssertionError(f"Fixture bridge exited with an error: {client.stderr}")
            child_attempts = network_log.read_text(encoding="utf-8").splitlines() if network_log.exists() else []
            document["isolation"] = {"temporary_data_confirmed": True,
                                     "parent_network_attempts": blocked_in_parent,
                                     "child_network_attempts": child_attempts,
                                     "disabled_settings": DISABLED_SETTINGS,
                                     "all_child_processes_exited": True,
                                     "source_unchanged_during_run": _source_fingerprints() == document["source_sha256"]}
            if blocked_in_parent or child_attempts:
                raise AssertionError("Benchmark unexpectedly attempted network access")
            if not document["isolation"]["source_unchanged_during_run"]:
                raise AssertionError("Source files changed during measurement; rerun after edits finish")
            document["ok"] = True
        finally:
            os.environ.clear()
            os.environ.update(original_env)
        if e2e_llm:
            # 微基准的隔离结论已经封存；e2e 在独立子进程里访问真实端点，
            # 失败如实记 ok=False，不影响微基准结果与整体退出码。
            document["metrics"]["e2e_llm_agent_task"] = _run_e2e_llm(
                scratch=scratch_path, passthrough=llm_passthrough,
                request=e2e_request, timeout=e2e_timeout)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output JSON report")
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=30, help="Per-response timeout in seconds")
    parser.add_argument("--e2e-llm", action="store_true",
                        help="真实 LLM 端到端 agent 任务基线（默认关闭；需已配置端点，会产生费用）")
    parser.add_argument("--e2e-request", default=E2E_DEFAULT_REQUEST, help="e2e agent 任务请求文本")
    parser.add_argument("--e2e-timeout", type=float, default=180, help="e2e 子进程超时（秒）")
    args = parser.parse_args(argv)
    if not 1 <= args.cold_runs <= 100 or not 1 <= args.samples <= 10_000 or not 0 < args.timeout <= 60:
        parser.error("cold-runs must be 1..100, samples 1..10000, timeout greater than 0 and at most 60")
    if not 10 <= args.e2e_timeout <= 1800:
        parser.error("e2e-timeout must be 10..1800 seconds")
    output = args.out.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = run_benchmark(cold_runs=args.cold_runs, samples=args.samples, timeout=args.timeout,
                               e2e_llm=args.e2e_llm, e2e_request=args.e2e_request,
                               e2e_timeout=args.e2e_timeout)
    except Exception as exc:
        report = {"schema_version": SCHEMA_VERSION, "ok": False,
                  "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "error": f"{type(exc).__name__}: {exc}"}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "out": str(output), "error": report["error"]}, ensure_ascii=False))
        return 1
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_keys = {"p50_ms", "p95_ms", "full_bytes", "steady_bytes", "all_matched", "samples"}
    print(json.dumps({"ok": True, "out": str(output), "metrics": {
        name: {key: value for key, value in metric.items() if key in summary_keys}
        if isinstance(metric, dict) else metric
        for name, metric in report["metrics"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
