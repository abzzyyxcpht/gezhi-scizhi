"""Exercise the real phone API, workflow, approval, Loop and export together.

Uses loopback, an isolated temporary data directory and offline fixtures only.
No real model calls, firewall changes or native terminal launches.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("design-previews/2026-09-17-harness-workbench/integration.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="scizhi-workbench-e2e-") as temporary:
        os.environ["GEZHI_DATA_DIR"] = temporary
        for name in list(os.environ):
            if name.startswith("GEZHI_LLM_"):
                os.environ.pop(name)
        (Path(temporary) / "settings.json").write_text(json.dumps({
            "mcp_enabled": False, "plugins_enabled": False, "llm_enabled": False,
            "git_archive": False, "default_offline": True}), encoding="utf-8")
        from src import bridge, remote_control, service, workbench, session_queue, evidence_reviews
        service.init()
        remote = remote_control.start_server(bridge.handle, "127.0.0.1", 0)
        endpoint = f"http://127.0.0.1:{remote['port']}/api/command"
        report = {"ok": False, "transport": "real loopback HTTP", "offline": True, "checks": []}

        def call(command: str, **fields):
            request = Request(endpoint, data=json.dumps({"cmd": command, **fields}).encode(),
                              headers={"Authorization": "Bearer " + remote["token"],
                                       "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise AssertionError(result)
            return result

        def until(get, target, timeout=25):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = get()
                if target(value):
                    return value
                time.sleep(.05)
            raise AssertionError(f"State did not reach target: {value}")

        try:
            initial_settings = (Path(temporary) / 'settings.json').read_bytes()
            readiness = call('product_readiness')['readiness']
            assert readiness['model']['connection_verified'] is False
            assert readiness['research']['offline_available']
            sample = call('product_demo')
            assert sample['offline'] is True and sample['workflow_id'] == 'literature_scan'
            sample_state = until(lambda: call('poll', task_id=sample['task_id']), lambda row: row['status'] == 'done')
            assert sample_state['pool'] and sample_state['citations']
            assert service.get(sample['task_id']).params['offline'] is True
            assert (Path(temporary) / 'settings.json').read_bytes() == initial_settings
            report['checks'].append('read-only readiness and explicit offline onboarding demo complete without changing settings')
            assert len(call("workflows")["workflows"]) == 3
            task = call("workflow_run", workflow_id="evidence_review", topic="large language model agents", offline=True)
            tid = task["task_id"]
            until(lambda: call("poll", task_id=tid), lambda r: r["status"] == "waiting_human")
            assert service.get(tid).status == "waiting_human"
            report["checks"].append("phone and host share the same pending task")
            call("resume", task_id=tid)
            finished = until(lambda: call("poll", task_id=tid), lambda r: r["status"] == "done")
            assert finished["done_nodes"] == finished["total_nodes"]
            assert finished["pool"] and finished["citations"]
            report["checks"].append("explicit human approval completes evidence workflow")

            session = bridge.handle({"cmd": "agent_run", "mode": "plan", "request": "研究证据。api_key=secret-demo-123"})["result"]
            exported = call("session_export", session_id=session["session_id"], format="markdown")
            assert "secret-demo-123" not in exported["content"]
            assert "[REDACTED]" in exported["content"]
            report["checks"].append("real session exported through HTTP with credentials removed")

            sid = session['session_id']
            cursor, observed = None, []
            for _ in range(50):
                page = call('session_events', session_id=sid, cursor=cursor, limit=2)
                observed.extend(page['events'])
                cursor = page['cursor']
                if not page['has_more']:
                    break
            else:
                raise AssertionError('Session event pagination did not finish')
            assert len(observed) == len(bridge._runtime().sessions.read(sid))
            assert 'secret-demo-123' not in json.dumps(observed)
            assert '[REDACTED]' in json.dumps(observed)
            assert call('session_events', session_id=sid, cursor=cursor)['events'] == []
            report['checks'].append('remote event pages are ordered, redacted and empty when idle')

            store = bridge._runtime().sessions
            store.append(sid, 'assistant_delta', {'step': 1, 'delta': '正在核对证据'})
            page = call('session_events', session_id=sid, cursor=cursor, tail=True)
            assert len(page['events']) == 1 and page['events'][0]['payload']['delta'] == '正在核对证据'
            cursor = page['cursor']
            partial = json.dumps({'type': 'assistant_message', 'session_id': sid,
                'payload': {'step': 1, 'content': '验证完成。'}}, ensure_ascii=False).encode('utf-8')
            path = store._path(sid)
            with path.open('ab') as file:
                file.write(partial[:-2])
            assert call('session_events', session_id=sid, cursor=cursor, tail=True)['events'] == []
            with path.open('ab') as file:
                file.write(partial[-2:] + b'\n')
            page = call('session_events', session_id=sid, cursor=cursor, tail=True)
            assert len(page['events']) == 1 and page['events'][0]['type'] == 'assistant_message'
            cursor = page['cursor']
            report['checks'].append('remote deltas arrive before final text; partial JSON is delivered only after completion')

            history = call('session_history', session_id=sid, page_size=1)['history']
            assert history['messages'] and 'secret-demo-123' not in json.dumps(history)
            warm = call('session_history', session_id=sid, page_size=1, snapshot=history['snapshot'])['history']
            assert warm['decoded_events'] == 0
            message = history['messages'][0]
            detail = call('session_history_message', session_id=sid, message_id=message['id'],
                          generation=history['generation'], offset=0, limit=20000)['message']
            assert detail['content'] == message['content']
            report['checks'].append('HTTP indexed history pages reuse cached journal and preserve redaction; long-message endpoint reads same generation')

            replacement = path.with_suffix('.replacement')
            replacement.write_text(json.dumps({'type': 'user_message', 'session_id': sid,
                'payload': {'text': '重置后的讨论'}}, ensure_ascii=False) + '\n', encoding='utf-8')
            os.replace(replacement, path)
            page = call('session_events', session_id=sid, cursor=cursor, tail=True)
            assert page['reset'] and len(page['events']) == 1
            assert page['events'][0]['payload']['text'] == '重置后的讨论'
            reset_history = call('session_history', session_id=sid, snapshot=history['snapshot'])['history']
            assert reset_history['reset'] and reset_history['messages'][0]['content'] == '重置后的讨论'
            report['checks'].append('atomic journal replacement resets the remote cursor to the new tail')

            from src.state_store import write_json
            control_sid = 'controls-e2e'
            store.append(control_sid, 'user_message', {'text': '隔离上下文验收', 'mode': 'agent'})
            store.append(control_sid, 'driver_started', {'driver': 'adaptive'})
            store.append(control_sid, 'completed', {'status': 'done'})
            messages = [{'role': 'system', 'content': '科研资料需要核验。'}, {'role': 'user', 'content': '原始目标'}]
            for index in range(10):
                messages += [{'role': 'user', 'content': str(index) + '待核验资料 ' * 150},
                             {'role': 'assistant', 'content': '已记录观察 ' * 150}]
            write_json(store.root / f'{control_sid}.driver.json', {
                'schema_version': 1, 'session_id': control_sid, 'status': 'done', 'messages': messages,
                'model_identity': {'model': 'fixture-only', 'protocol': 'responses'},
                'tokens': 25, 'model_calls': 1, 'max_steps': 16, 'token_budget': 1000,
                'usage_complete': True, 'context_limit_tokens': 32000, 'compressions': 0})
            before = call('session_status', session_id=control_sid)['status']
            assert before['model']['model'] == 'fixture-only' and before['tokens'] == 25
            compacted = call('session_compact', session_id=control_sid)
            assert compacted['result']['changed'] and compacted['result']['model_calls'] == 0
            assert compacted['result']['after_tokens'] < compacted['result']['before_tokens']
            assert compacted['status']['tokens'] == 25 and compacted['status']['status'] == 'done'
            assert not call('session_compact', session_id=control_sid)['result']['changed']
            report['checks'].append('remote session status and offline compaction preserve reported usage and complete history')

            assert bridge.handle({'cmd': 'settings_save', 'settings': {'experimental_message_queue': True}})['ok']
            queued_sid = bridge.handle({'cmd': 'chat_new', 'title': '隔离队列验收'})['session_id']
            assert bridge.handle({'cmd': 'chat_turn', 'session_id': queued_sid,
                                  'request': '规划后续证据核对', 'mode': 'plan', 'online': False})['ok']
            call('queue_pause', session_id=queued_sid)
            first = call('queue_add', session_id=queued_sid, request='先核对支持材料', queue_request_id='http-first')['ticket']
            retry = call('queue_add', session_id=queued_sid, request='先核对支持材料', queue_request_id='http-first')['ticket']
            assert retry['id'] == first['id']
            last = call('queue_add', session_id=queued_sid, request='检索反驳材料的计划', queue_request_id='http-last')['ticket']
            call('queue_reorder', session_id=queued_sid, ordered_ids=[last['id'], first['id']])
            call('queue_remove', session_id=queued_sid, ticket_id=first['id'])
            call('queue_resume', session_id=queued_sid)
            until(lambda: call('queue_list', session_id=queued_sid)['queue'],
                  lambda row: row['counts']['delivered'] == 1 and row['worker_state'] == 'idle')
            accepted = [event['payload'] for event in bridge._runtime().sessions.read(queued_sid)
                        if event['type'] == 'user_message' and event['payload'].get('queue_ticket')]
            assert len(accepted) == 1 and accepted[0]['queue_ticket'] == last['id']
            assert accepted[0]['offline'] is True
            report['checks'].append('real HTTP queue pause/add/retry/reorder/remove/resume delivers exactly one offline plan turn')

            note = bridge.handle({'cmd': 'notebook_save', 'record': {
                'kind': 'hypothesis', 'task_id': tid, 'title': '反例检索的演示假设',
                'question': '是否补充局限条件？', 'prediction': '需要实际对照验证',
                'evidence': [{'relation': 'challenges', 'quote': '合成反例，不是真实文献',
                              'scope': 'abstract', 'source': {'id': 'http-demo', 'synthetic': True}}]}})
            assert note['ok'], note
            brief = call('evidence_brief', task_id=tid)['brief']
            assert len(brief['records']) == 1 and brief['scope']['task_id'] == tid
            assert brief['records'][0]['counts']['challenges'] == 1 and brief['records'][0]['counts']['synthetic'] == 1
            assert brief['records'][0]['provenance']['scientific_verification'] == 'not_performed'
            report['checks'].append('real HTTP evidence brief preserves task scope, counterevidence and synthetic provenance')

            assert bridge.handle({'cmd': 'settings_save', 'settings': {'experimental_evidence_reviews': True}})['ok']
            record_id = note['record']['id']
            first = call('review_start', record_id=record_id, expected_version=1, mode='local', review_request_id='http-review')['job']
            retry = call('review_start', record_id=record_id, expected_version=1, mode='local', review_request_id='http-review')['job']
            assert first['id'] == retry['id']
            until(lambda: call('review_get', job_id=first['id'])['job'], lambda row: row['status'] == 'ready')
            assert bridge.handle({'cmd': 'notebook_get', 'id': record_id})['record']['version'] == 1
            assert call('review_accept', job_id=first['id'])['job']['accepted_version'] == 2
            assert call('review_accept', job_id=first['id'])['job']['accepted_version'] == 2
            saved = bridge.handle({'cmd': 'notebook_get', 'id': record_id})['record']
            assert len(saved['observations']) == 1 and saved['evidence'] == note['record']['evidence']
            report['checks'].append('real HTTP background review is read-only until explicit version-checked acceptance; retries append one note')
            old = call('review_start', record_id=record_id, expected_version=2, mode='local', review_request_id='http-stale-review')['job']
            until(lambda: call('review_get', job_id=old['id'])['job'], lambda row: row['status'] == 'ready')
            assert bridge.handle({'cmd': 'notebook_save', 'expected_version': 2,
                                  'record': {'id': record_id, 'limitations': '新增适用条件'}})['ok']
            assert call('review_get', job_id=old['id'])['job']['stale']
            rejected = False
            try:
                call('review_accept', job_id=old['id'])
            except AssertionError as error:
                rejected = '已变更' in str(error)
            assert rejected
            report['checks'].append('real HTTP rejects acceptance after source record version changes')

            loop = call("loop_start", workflow_id="literature_scan", topic="large language model agents",
                        interval_seconds=1, max_iterations=2, offline=True)["loop"]
            completed = until(lambda: next(r for r in call("loop_list")["loops"] if r["loop_id"] == loop["loop_id"]),
                              lambda r: r["status"] == "completed")
            assert completed["iteration"] == 2
            report["checks"].append("two real offline Loop iterations complete without overlap")
            report["ok"] = True
        finally:
            evidence_reviews.close()
            session_queue.close()
            remote_control.stop_server()
            if workbench._instance:
                workbench._instance.close()
        assert not remote_control.server_status()["running"]
        report["checks"].append("remote listener stopped and temporary data isolated")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
