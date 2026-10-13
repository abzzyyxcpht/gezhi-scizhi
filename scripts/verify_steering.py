"""Real HTTP and shared bridge/driver steering; fixture model, no provider calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    scratch = root / '_testtmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='steering-wire-', dir=scratch) as directory:
        os.environ['GEZHI_DATA_DIR'] = directory
        for name in list(os.environ):
            if name.startswith('GEZHI_LLM_'):
                os.environ.pop(name)
        from src import bridge, llm, remote_control, settings
        settings.save({'experimental_inflight_steering': True, 'agent_stream_enabled': False,
                       'llm_enabled': False, 'mcp_enabled': False, 'plugins_enabled': False,
                       'hooks_enabled': False, 'git_archive': False})
        info = remote_control.start_server(bridge.handle, '127.0.0.1', 0)
        endpoint = f'http://127.0.0.1:{info["port"]}/api/command'
        def call(command, **values):
            request = Request(endpoint, data=json.dumps({'cmd': command, **values}).encode(),
                              headers={'Authorization': 'Bearer ' + info['token'], 'Content-Type': 'application/json'})
            with urlopen(request, timeout=5) as reply:
                result = json.load(reply)
            assert result['ok'], result
            return result
        entered, release = threading.Event(), threading.Event()
        messages = []
        def model(history, **kwargs):
            messages.append(copy.deepcopy(history))
            if len(messages) == 1:
                entered.set()
                assert release.wait(10), 'fixture model was never released'
            return {'content': '合成模型回答', 'tool_calls': [],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        try:
            sid = bridge.handle({'cmd': 'chat_new', 'title': '运行中干预桥接验收'})['session_id']
            with patch.object(llm, 'available', return_value=True), patch.object(llm, 'chat', side_effect=model):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    running = pool.submit(bridge.handle, {'cmd': 'chat_turn', 'session_id': sid,
                        'request': '原始研究', 'online': True})
                    try:
                        assert entered.wait(5)
                        state = call('steer_status', session_id=sid)['steering']
                        fields = {'session_id': sid, 'expected_turn_id': state['turn_id'],
                                  'steer_request_id': 'http-correction', 'request': '加入对反例与局限的检查'}
                        first = call('steer_add', **fields)['ticket']
                        assert call('steer_add', **fields)['ticket']['id'] == first['id']
                    finally:
                        release.set()
                    result = running.result(timeout=5)
            assert result['ok'] and result['result']['status'] == 'done'
            final = call('steer_status', session_id=sid)['steering']
            assert len(messages) == 2 and messages[-1][-1]['content'] == fields['request']
            assert final['items'][0]['state'] == 'applied' and final['state'] == 'closed'
            store = bridge._runtime(offline=False).sessions
            events = store.read(sid)
            assert sum(e['type'] == 'user_message' for e in events) == 1
            assert sum(e['type'] == 'steer_applied' for e in events) == 1
            assert result['result']['tokens'] == 30 and result['result']['steps'] == 2
            report = {'ok': True, 'transport': 'real loopback HTTP -> bridge -> adaptive driver',
                      'real_provider_calls': 0, 'fixture_model_calls': len(messages),
                      'checks': ['HTTP remains responsive during active model call', 'expected active turn identity',
                                 'idempotent accepted receipt', 'same-turn model context receives correction',
                                 'one top-level user turn', 'original cumulative token and step accounting',
                                 'applied state and terminal inbox closure']}
        finally:
            release.set()
            remote_control.stop_server()
    output = root / 'docs/steering-wire-verification-2026-09-28.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
