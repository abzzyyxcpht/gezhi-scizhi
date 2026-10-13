# 便携包隔离环境验证：干净 PATH 下解压即用（不依赖构建机的 Anaconda / Node）
# 运行: python scripts/verify_portable.py
"""Verify the portable distribution in a clean PATH.

Steps: unzip → run CLI --version with a minimal PATH → boot the bundled Python
bridge and round-trip commands over the JSON line protocol → exercise the
research data chain (notebook / world facts / peer review / workflows).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION  # noqa: E402

ZIP = ROOT / "dist" / f"格致SciZhi-便携版-{APP_VERSION}-win64.zip"
# 干净 PATH：仅系统目录（验证不依赖构建机 Anaconda / Node）
CLEAN_PATH = r"C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"


def run(cmd: list[str], env: dict, **kw) -> subprocess.CompletedProcess:
    print("[verify]", " ".join(str(c) for c in cmd)[:150])
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120, **kw)


def bridge_roundtrip(py: str, cwd: str, env: dict, requests: list[dict]) -> list[dict]:
    """向长驻 bridge 进程逐行发送请求，读到响应后即终止（不等待其退出）。"""
    child_env = dict(env, PYTHONIOENCODING="utf-8")  # 与 tui/src/bridge.js 的 spawn 一致
    proc = subprocess.Popen([py, "-m", "src.bridge"], cwd=cwd, env=child_env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding="utf-8", errors="replace", text=True)
    payloads: list[dict] = []
    try:
        # bridge 启动先发一行 {"ok": true, "ready": true} 握手；跳过它
        proc.stdout.readline()  # 握手行读取后即弃：只为越过它
        for req in requests:
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line.strip():
                continue
            try:
                payloads.append(json.loads(line))
            except ValueError:
                pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return payloads


def main() -> int:
    if not ZIP.is_file():
        raise SystemExit(f"缺少便携包：{ZIP}；先运行 python scripts/build_portable.py")
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[verify] {'OK  ' if ok else 'FAIL'} {name}" + (f" -- {detail[:200]}" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="scizhi-portable-verify-") as tmp:
        app = Path(tmp) / "格致SciZhi-便携版"
        with zipfile.ZipFile(ZIP) as zf:
            zf.extractall(app)
        inner = next(app.glob("格致SciZhi-便携版-*"))
        env = dict(os.environ)
        env["PATH"] = CLEAN_PATH
        env["GEZHI_DATA_DIR"] = str(inner / "data")

        # 1) 便携 Node + CLI --version
        r = run([str(inner / "node" / "node.exe"), str(inner / "tui" / "src" / "cli.js"), "--version"], env)
        check("便携 node + cli --version", r.returncode == 0 and APP_VERSION in r.stdout, r.stderr or r.stdout)

        py = str(inner / "python" / "python.exe")

        # 2) 便携 Python + bridge 单命令回环（JSON 行协议）
        payloads = bridge_roundtrip(py, str(inner), env, [{"cmd": "workbench_mode"}])
        ok = bool(payloads) and payloads[0].get("ok") and payloads[0].get("mode") in {"general", "research"}
        check("便携 python bridge 回环（workbench_mode）", ok, json.dumps(payloads, ensure_ascii=False)[:200])

        # 3) 科研数据链（notebook / world / peer-review / workflows 均不调 LLM）
        chain = [
            {"cmd": "notebook_save", "record": {"id": "pv_1", "kind": "hypothesis",
                                                "title": "便携验证假设", "question": "温度是否提升产率？",
                                                "status": "completed", "result": "提升 4.2%",
                                                "evidence": [{"relation": "supports", "source": {"id": "s1"},
                                                              "quote": "q", "scope": "sc"}]}},
            {"cmd": "notebook_list"},
            {"cmd": "world_fact_save", "fact": {"claim": "便携验证事实", "topic": "T",
                                                "source": {"id": "u1", "source_type": "user_provided"}}},
            {"cmd": "world_fact_list"},
            {"cmd": "peer_review", "id": "pv_1"},
            {"cmd": "workflows"},
        ]
        oks = bridge_roundtrip(py, str(inner), env, chain)
        check("科研数据链 6 命令回环", len(oks) >= 6 and all(o.get("ok") for o in oks[:6]),
              json.dumps(oks[:6], ensure_ascii=False)[:300])
        review = next((o for o in oks if "dimensions" in o), None)
        check("peer_review 五维评分（便携）", bool(review and review.get("overall") is not None))
        wf = next((o for o in oks if "workflows" in o), None)
        check("reproduce 工作流在列（便携）", bool(wf and any(w["id"] == "reproduce" for w in wf["workflows"])))

        # 4) headless 单轮（LLM 未配置时应友好失败而非模块缺失——链路即通）
        r = run([str(inner / "node" / "node.exe"), str(inner / "tui" / "src" / "cli.js"), "--print", "ping"], env)
        check("headless --print 链路（允许 LLM 未配置的失败）",
              r.returncode in (0, 1) and "Cannot find module" not in (r.stderr or ""), (r.stderr or "")[:200])

    if failures:
        print(f"[verify] FAILED: {failures}")
        return 1
    print("[verify] ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
