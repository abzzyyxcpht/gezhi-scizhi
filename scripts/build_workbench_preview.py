"""Build a local review gallery from already captured native/phone/Ink evidence."""
from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "design-previews/2026-09-17-harness-workbench"


def main():
    screenshots = [
        ("1440x900-workflows-panel.png", "桌面 · 科研工作流", True),
        ("1080x760-loop-panel.png", "桌面 · Loop 与窄窗口", False),
        ("1440x900-tasks-panel.png", "桌面 · 任务进程", False),
        ("1440x900-remote-panel.png", "桌面 · 手机远控入口", False),
        ("1080x760-commands-panel.png", "桌面 · 命令入口", False),
        ("1080x760-settings-features.png", "设置 · 功能开关与插件", False),
    ]
    sections = []
    for name, label, expanded in screenshots:
        files = list(OUT.glob("*/" + name))
        if not files:
            raise FileNotFoundError(name)
        relative = max(files, key=lambda p: p.stat().st_mtime).relative_to(OUT).as_posix()
        sections.append(f'<details {"open" if expanded else ""}><summary>{label}</summary><a href="{relative}"><img src="{relative}" alt="{label}" loading="lazy"></a></details>')
    sections.append('<details><summary>手机 · 登录、任务与 Loop</summary><p>390px 浏览器实际交互截图，测试数据。实体手机网络尚未实测。</p><div class="phones">' + ''.join(
        f'<a href="{name}"><img src="{name}" alt="手机控制台" loading="lazy"></a>'
        for name in ("phone-control-login.png", "phone-control-task.png", "phone-control-loop.png")) + '</div></details>')
    for path in sorted(OUT.glob("tui-0*.txt")):
        sections.append(f'<details><summary>TUI · {escape(path.stem)}</summary><pre>{escape(path.read_text(encoding="utf-8"))}</pre></details>')
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>格致 · 科研专属工作台</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f6;color:#242b30;font:14px/1.75 'Microsoft YaHei UI',sans-serif}
main{max-width:1320px;margin:auto;padding:40px 24px}h1{font-size:28px;letter-spacing:-.6px;margin:0 0 8px}header{margin-bottom:26px}p{color:#56636b}a{color:#286557}
details{border-top:1px solid #dae1e5;padding:18px 0}summary{font-size:16px;font-weight:600;cursor:pointer}img{max-width:100%;height:auto;border:1px solid #dae1e5;border-radius:8px;display:block;margin-top:16px}
pre{overflow:auto;padding:24px;background:#16221e;color:#dce7e0;font:13px/1.5 Consolas,monospace;border-radius:8px}.phones{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}.phones a{max-width:360px;flex:1 1 280px}
summary:focus-visible,a:focus-visible{outline:2px solid #286557;outline-offset:4px}footer{margin:24px 0;color:#687780}</style>
<main><header><h1>格致 · 科研专属工作台</h1><p>桌面、终端与手机共用科研引擎。以下为实际渲染记录，非界面样机；科研数据使用隔离的离线验收样例，TUI 帧明确标注 mock。</p>
<a href="../../README.md">运行与命令说明</a> · <a href="../../docs/research/performance-2026-09-17-cache-comparison.md">性能实测与适用范围</a></header>'''
    document += '\n'.join(sections)
    document += '<footer>主机需保持运行才能持续调度 Loop 和远程控制。此次更新为源码交付，未重新生成安装包。</footer></main></html>'
    (OUT / "index.html").write_text(document, encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    main()
