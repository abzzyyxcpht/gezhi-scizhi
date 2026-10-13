"""格致 SciZhi 统一启动入口。

用法：
    python scripts/gezhi.py doctor
    python scripts/gezhi.py demo "large language model agents" --out data/exports/demo
    python scripts/gezhi.py agents
    python scripts/gezhi.py agent "调研大模型智能体" --agent pipeline
    python scripts/gezhi.py desktop
    python scripts/gezhi.py tui
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gezhi", description="格致 SciZhi 启动入口")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="运行系统自检")
    demo = sub.add_parser("demo", help="运行离线端到端演示")
    demo.add_argument("topic", nargs="?", default="large language model agents")
    demo.add_argument("keywords", nargs="?", default="")
    demo.add_argument("--out", default=None, help="产物输出目录")
    demo.add_argument("--online", action="store_true",
                      help="使用实时 OpenAlex（默认使用确定性离线样例）")
    agent = sub.add_parser("agent", help="运行科研 Agent Harness")
    agent.add_argument("request", help="自然语言科研任务")
    agent.add_argument("--mode", choices=["plan", "agent", "review"],
                       default="agent", help="Agent 模式")
    agent.add_argument("--online", action="store_true",
                       help="允许调用在线科研工具")
    agent.add_argument("--agent", default="research",
                       help="专项智能体名（research/pipeline/literature/…）")
    sub.add_parser("agents", help="列出专项智能体编制表")
    approve = sub.add_parser("approve", help="批准 Agent 工具调用")
    approve.add_argument("session_id")
    approve.add_argument("tool_call_id")
    approve.add_argument("--session", action="store_true", dest="session_scope")
    deny = sub.add_parser("deny", help="拒绝 Agent 工具调用")
    deny.add_argument("session_id")
    deny.add_argument("tool_call_id")
    sub.add_parser("sessions", help="列出可恢复 Agent 会话")
    sub.add_parser("workflows", help="列出科研工作流模板")
    sub.add_parser('tower', help='任务、审批、用量与团队控制塔')
    swarm = sub.add_parser('swarm', help='并行科研团队')
    swarm.add_argument('action', choices=['plan', 'start', 'get', 'list', 'cancel'])
    swarm.add_argument('value', nargs='?', default='')
    swarm.add_argument('--parallel', type=int, default=None)
    export_session = sub.add_parser("session-export", help="导出脱敏后的智能体会话")
    export_session.add_argument("session_id")
    export_session.add_argument("--format", choices=["markdown", "json"], default="markdown")
    export_session.add_argument("--out", default=None)
    serve = sub.add_parser("serve", help="保持科研引擎运行，供手机或远程 TUI 控制")
    serve.add_argument("--host", default="127.0.0.1", help="局域网手机访问时设置 0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("desktop", help="启动桌面版")
    sub.add_parser("tui", help="启动终端版")
    return parser.parse_args(argv)


def _python_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if args.command == "doctor":
        from src.doctor import format_doctor_text
        print(format_doctor_text())
        return 0

    if args.command == "demo":
        try:
            from scripts.demo_run import run_demo
        except ImportError as exc:
            print(f"无法加载演示依赖：{exc}\n请先运行：pip install -r requirements.txt",
                  file=sys.stderr)
            return 1
        result = run_demo(args.topic, Path(args.out) if args.out else None,
                          args.keywords, offline=not args.online)
        print(f"演示完成：{result['status']}；产物目录：{result['artifact_dir']}")
        return 0 if result["status"] == "done" else 1

    if args.command == "agents":
        from src.agents.specs import list_agents
        print(json.dumps(list_agents(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "agent":
        from src.agent_runtime import AgentRuntime
        runtime = AgentRuntime(offline=not args.online, llm_enabled=args.online)
        result = runtime.start(args.request, mode=args.mode, agent=args.agent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"error", "blocked"} else 1

    if args.command == "sessions":
        from src.agent_runtime import SessionStore
        print(json.dumps(SessionStore().list(), ensure_ascii=False, indent=2))
        return 0

    if args.command in {'tower', 'swarm'}:
        from src import service
        service.restore_tasks()
        if args.command == 'tower':
            from src.control_tower import format_snapshot
            print(format_snapshot(service.get_control_tower()))
            return 0
        try:
            if args.action == 'plan':
                result = service.plan_swarm(args.value, max_parallel=args.parallel)
            elif args.action == 'start':
                # A one-shot CLI must keep its process alive until all workers pause/finish.
                result = service._swarm_coordinator(args.value).start(args.value)
            elif args.action == 'list':
                result = service.list_swarms()
            elif args.action == 'cancel':
                result = service.cancel_swarm(args.value)
            else:
                result = service.get_swarm(args.value)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if isinstance(result, dict) and result.get('status') in {'error', 'partial', 'blocked'} else 0
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if args.command in {"workflows", "session-export"}:
        from src.bridge import handle
        req = {"cmd": args.command.replace("-", "_")}
        if args.command == "session-export":
            req.update(session_id=args.session_id, format=args.format)
            if args.out:
                req["dir"] = args.out
        result = handle(req)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "serve":
        import threading
        from src import service, remote_control
        from src.bridge import handle
        service.init()
        try:
            result = remote_control.start_server(handle, args.host, args.port)
            print("科研工作台已启动；Ctrl+C 停止共享。", flush=True)
            for url in result["urls"]:
                print(url, flush=True)
            print("访问口令：" + result["token"], flush=True)
            threading.Event().wait()
        except KeyboardInterrupt:
            print("\n远程共享已停止。")
        except OSError as exc:
            print(f"无法启动远程服务：{exc}", file=sys.stderr)
            return 1
        finally:
            remote_control.stop_server()
        return 0

    if args.command in {"approve", "deny"}:
        from src.agent_runtime import AgentRuntime
        runtime = AgentRuntime()
        decision = "deny" if args.command == "deny" else ("allow_session" if args.session_scope else "allow")
        result = runtime.approve(args.session_id, args.tool_call_id, decision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"blocked", "error"} else 1

    if args.command == "desktop":
        try:
            from src.ui.desktop import main as desktop_main
            import flet as ft
        except ImportError as exc:
            print(f"桌面版依赖未安装：{exc}\n请先运行：pip install -r requirements.txt",
                  file=sys.stderr)
            return 1
        ft.app(target=desktop_main)
        return 0

    if args.command == "tui":
        package = ROOT / "tui" / "package.json"
        if not package.exists():
            print(f"TUI 配置不存在：{package}", file=sys.stderr)
            return 1
        npm = "npm.cmd" if os.name == "nt" else "npm"
        try:
            return subprocess.call([npm, "start"], cwd=ROOT / "tui",
                                   env=_python_env())
        except FileNotFoundError:
            print("未找到 npm，请安装 Node.js 20+ 后重试", file=sys.stderr)
            return 1

    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
