"""Compare full-log reads, event pages and projections on synthetic journals.

python scripts/benchmark_sessions.py --out docs/research/session-projection-performance-2026-09-27.json

No model, remote server, user session or GUI is used. Both paths share warm OS
file caches; the baseline is the unchanged SessionStore.read(), not a competitor.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize(samples):
    values = sorted(samples)
    return {'samples': len(values), 'median_ms': round(statistics.median(values), 4),
            'p95_ms': round(values[max(0, math.ceil(.95 * len(values)) - 1)], 4)}


def measure(operation, repeats):
    operation()
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        values.append((time.perf_counter() - start) * 1000)
    return summarize(values)


def write_fixture(path, sid, count):
    text = '科研样例：复核实验对照、可重复性和反驳证据。' * 4
    with path.open('w', encoding='utf-8', newline='\n') as file:
        for index in range(count):
            turn, slot = divmod(index, 8)
            kind, payload = [
                ('user_message', {'text': f'{turn}: {text}', 'mode': 'agent'}),
                ('assistant_delta', {'step': 1, 'delta': text[:35]}),
                ('assistant_delta', {'step': 1, 'delta': text[35:]}),
                ('assistant_message', {'step': 1, 'content': text}),
                ('tool_call', {'id': f'call-{turn}', 'name': 'fixture.read', 'arguments': {}}),
                ('tool_result', {'id': f'call-{turn}', 'result': {'ok': True, 'summary': text}}),
                ('subagent_status', {'child': f'child-{turn}', 'agent': 'critic', 'status': 'done'}),
                ('completed', {'status': 'done'}),
            ][slot]
            file.write(json.dumps({'session_id': sid, 'type': kind, 'payload': payload,
                'timestamp': '2026-09-24T12:00:00+0000'}, ensure_ascii=False) + '\n')


def benchmark(count, repeats, limit):
    from src.agent_runtime import SessionStore

    with tempfile.TemporaryDirectory(prefix='scizhi-events-benchmark-') as directory:
        store = SessionStore(Path(directory) / 'sessions')
        sid = 'synthetic'
        path = store.root / f'{sid}.jsonl'
        write_fixture(path, sid, count)
        fixture_bytes = path.stat().st_size
        expected = store.read(sid)[-limit:]
        page = store.event_page(sid, limit=limit, tail=True)
        assert page['events'] == expected, 'Tail page differs from full-log reference'
        assert not page['has_more'], 'Tail page should reach the complete end of this fixture'
        cursor = page['cursor']

        first_full = measure(lambda: store.read(sid)[-limit:], repeats)
        first_tail = measure(lambda: store.event_page(sid, limit=limit, tail=True), repeats)
        idle_full = measure(lambda: store.read(sid), repeats)
        def idle_page():
            result = store.event_page(sid, cursor=cursor, limit=limit, tail=True)
            assert result['events'] == [] and not result['reset']
        idle_incremental = measure(idle_page, repeats)
        cold_projection = measure(lambda: SessionStore(store.root).snapshot(sid), repeats)
        store.snapshot(sid)
        idle_projection = measure(lambda: store.snapshot(sid), repeats)

        appended_full, appended_incremental, snapshot_after_append, full_projection_after_append = [], [], [], []
        for index in range(repeats):
            event = store.append(sid, 'assistant_delta', {'step': 1, 'delta': '新增片段', 'probe': index})
            start = time.perf_counter()
            reference = store.read(sid)
            appended_full.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            result = store.event_page(sid, cursor=cursor, limit=limit, tail=True)
            appended_incremental.append((time.perf_counter() - start) * 1000)
            assert result['events'] == [event] and reference[-1] == event
            assert not result['reset'] and not result['has_more']
            cursor = result['cursor']
            start = time.perf_counter()
            snapshot = store.snapshot(sid)
            snapshot_after_append.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            expected_snapshot = SessionStore._project(sid, store.read(sid))
            full_projection_after_append.append((time.perf_counter() - start) * 1000)
            assert snapshot == expected_snapshot, 'Incremental snapshot differs from full projection'

        external_projection = []
        other_store = SessionStore(store.root)
        for index in range(repeats):
            other_store.append(sid, 'assistant_message', {'content': f'External result {index}'})
            start = time.perf_counter()
            snapshot = store.snapshot(sid)
            external_projection.append((time.perf_counter() - start) * 1000)
            assert snapshot['content'] == f'External result {index}'
        assert store.list()[0]['events'] == count + repeats * 2

        return {'events': count, 'file_bytes': fixture_bytes, 'tail_limit': limit,
                'first_open_full_read_then_tail': first_full, 'first_open_tail_page': first_tail,
                'idle_full_read': idle_full, 'idle_event_page': idle_incremental,
                'append_full_read': summarize(appended_full),
                'append_event_page': summarize(appended_incremental),
                'first_snapshot_cold': cold_projection, 'idle_snapshot': idle_projection,
                'append_snapshot_incremental': summarize(snapshot_after_append),
                'append_snapshot_full_recompute': summarize(full_projection_after_append),
                'external_append_snapshot_incremental': summarize(external_projection),
                'correctness': 'tail matches reference; idle empty; each event delivered once; incremental snapshots equal full projections; external writes and final count agree'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sizes', default='1000,10000,50000')
    parser.add_argument('--repeats', type=int, default=12)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    try:
        sizes = [int(value) for value in args.sizes.split(',')]
    except ValueError:
        parser.error('--sizes must contain comma-separated integers')
    if not sizes or any(not 1 <= size <= 200000 for size in sizes):
        parser.error('each size must be between 1 and 200000')
    if not 3 <= args.repeats <= 100 or not 1 <= args.limit <= 500:
        parser.error('repeats must be 3..100 and limit 1..500')
    report = {
        'schema_version': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
        'environment': {'python': platform.python_version(), 'platform': platform.platform()},
        'method': 'Same process, warm filesystem caches, synthetic JSONL, median and nearest-rank p95.',
        'baseline': 'Unchanged SessionStore.read, plus explicit full-log SessionStore._project for projection comparison.',
        'limits': ['Not end-to-end GUI latency or model generation speed.',
                   'No third-party Harness was benchmarked.',
                   'Cold snapshots still replay the complete log; warm snapshots project committed additions only.',
                   'Temporary fixtures are automatically removed; real research data is not read.'],
        'workloads': [benchmark(size, args.repeats, args.limit) for size in sizes],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
