"""Exercise the real phone UI in a disposable browser against a local HTTP fixture.

Requires Playwright and its Chromium browser. No user browser profile, model,
MCP service or research data is accessed. Optional --out writes a screenshot.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--browser-channel', default=None, help='Optional installed browser, e.g. msedge')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix='scizhi-phone-ui-') as folder:
        os.environ['GEZHI_DATA_DIR'] = folder
        from src import remote_control
        from playwright.sync_api import sync_playwright, expect
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []
        session = {'session_id': 'phone-fixture', 'driver': 'adaptive', 'status': 'waiting_task',
                   'summary': '研究证据复核 · 界面验收样例', 'updated_at': '2026-09-19T10:00:00Z',
                   'request': '复核科研智能体的证据来源 · 界面验收样例', 'task_id': 'research-fixture',
                   'content': '记录包含支持与反驳材料；结论仍需研究者核验。'}
        task = {'task_id': 'research-fixture', 'topic': '可追溯的研究记录 · 界面验收样例',
                'status': 'paused', 'pointer': 'human_review', 'offline': True}

        def dispatch(req):
            calls.append(req['cmd'])
            if req['cmd'] == 'list':
                return {'ok': True, 'tasks': [task.copy()]}
            if req['cmd'] == 'workflows':
                return {'ok': True, 'workflows': []}
            if req['cmd'] == 'sessions':
                return {'ok': True, 'sessions': [session.copy()]}
            if req['cmd'] == 'session_snapshot':
                return {'ok': True, 'snapshot': session.copy()}
            if req['cmd'] == 'agent_continue':
                session['status'] = 'running'
                entered.set()
                if not release.wait(12):
                    raise RuntimeError('Cancel did not reach the running continuation')
                return {'ok': True, 'status': session['status']}
            if req['cmd'] == 'agent_cancel':
                session['status'] = 'cancelled'
                release.set()
                return {'ok': True, 'status': 'cancelled'}
            if req['cmd'] == 'poll':
                return {'ok': True, **task, 'events': [{'type': 'fixture', 'message': '示例任务等待复核'}],
                        'total_events': 1, 'art_rev': 'fixture'}
            if req['cmd'] == 'session_export':
                export = Path(req['dir']) / 'session.md'
                export.write_text('# 离线会话\n验收示例', encoding='utf-8')
                return {'ok': True, 'path': str(export)}
            raise AssertionError(f'Unexpected UI command: {req["cmd"]}')

        info = remote_control.start_server(dispatch)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel=args.browser_channel)
                context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1,
                                              accept_downloads=True)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{info["port"]}/', wait_until='networkidle')
                page.get_by_label('访问令牌', exact=True).fill(info['token'])
                page.get_by_role('button', name='连接工作台', exact=True).click()
                expect(page.locator('#workbench')).to_be_visible()
                page.get_by_role('tab', name='会话记录').click()
                page.get_by_role('button', name='查看详情', exact=True).click()
                expect(page.locator('#session-detail')).to_contain_text('等待研究任务')
                page.get_by_role('button', name='继续会话', exact=True).click()
                assert entered.wait(3), 'Continuation never arrived'
                page.get_by_role('button', name='停止会话', exact=True).click()
                expect(page.locator('#session-detail .meta')).to_have_text('已取消')
                expect(page.locator('#session-list .badge')).to_have_text('已取消')
                expect(page.locator('#session-detail')).to_contain_text(session['content'])
                assert release.is_set(), 'Cancel was blocked behind continuation'
                with page.expect_download() as download:
                    page.get_by_role('button', name='导出 Markdown', exact=True).click()
                assert download.value.suggested_filename == 'session.md'
                downloaded = Path(folder) / 'downloaded-session.md'
                download.value.save_as(downloaded)
                assert '验收示例' in downloaded.read_text(encoding='utf-8')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Phone layout overflows'
                if args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.out), full_page=True)
                page.get_by_role('button', name='查看关联研究', exact=True).click()
                expect(page.get_by_role('tab', name='任务进程')).to_have_attribute('aria-selected', 'true')
                expect(page.locator('#task-detail')).to_contain_text(task['topic'])
                page.get_by_role('button', name='断开', exact=True).click()
                expect(page.locator('#login-form')).to_be_visible()
                assert not errors, errors
                context.close()
                browser.close()
        finally:
            release.set()
            remote_control.stop_server()
        print(json.dumps({'ok': True, 'viewport': '390x844', 'calls': calls, 'page_errors': errors,
                          'fixture': True, 'screenshot': str(args.out) if args.out else None}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
