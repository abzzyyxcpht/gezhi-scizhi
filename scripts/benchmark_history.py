"""Measure journal decoding versus rebuilt/reused SQLite history; synthetic data only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def timed(operation):
    start = time.perf_counter()
    result = operation()
    return round((time.perf_counter() - start) * 1000, 3), result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[20000, 100000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--probe', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'docs/research/history-index-performance-2026-09-28.json')
    args = parser.parse_args()
    from src.agent_runtime import SessionStore
    from src.session_history import history, index_path
    from src.workbench import redact
    if args.probe:
        store = SessionStore(args.probe)
        elapsed, page = timed(lambda: history('benchmark', store=store, query='boundary needle'))
        print(json.dumps({'query_ms': elapsed, 'decoded_events': page['decoded_events'], 'matches': page['total'], 'method': page['search_method']}))
        return 0
    results = []
    scratch = ROOT / '_testtmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='history-index-', dir=scratch) as directory:
        for count in args.sizes:
            store = SessionStore(Path(directory) / str(count))
            with store._path('benchmark').open('w', encoding='utf-8') as stream:
                for index in range(count):
                    text = f'科研记录 {index} · ' + ('boundary needle' if index % 101 == 0 else 'compare evidence') + ' · 对照、来源、范围与局限' * 3
                    stream.write(json.dumps({'type': 'user_message', 'payload': {'text': text}}, ensure_ascii=False) + '\n')
            def reference():
                return [text for event in store.read('benchmark') if 'boundary needle' in (text := str(redact(event['payload']['text']))).casefold()]
            baseline = [timed(reference)[0] for _ in range(args.repeats)]
            build_ms, first = timed(lambda: history('benchmark', store=store, query='boundary needle'))
            assert first['total'] == len(reference())
            warm = [timed(lambda: history('benchmark', store=store, query='boundary needle'))[0] for _ in range(args.repeats)]
            cold = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                probe = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--probe', str(store.root)],
                                       capture_output=True, text=True, encoding='utf-8', check=True)
                row = json.loads(probe.stdout)
                row['process_ms'] = round((time.perf_counter() - started) * 1000, 3)
                assert row['decoded_events'] == 0 and row['matches'] == first['total']
                cold.append(row)
            store.append('benchmark', 'steer_applied', {'text': 'new boundary needle'})
            append_ms, appended = timed(lambda: history('benchmark', store=store, query='boundary needle'))
            assert appended['decoded_events'] == 1 and appended['total'] == first['total'] + 1
            results.append({'events': count, 'journal_bytes': store._path('benchmark').stat().st_size,
                'index_bytes': index_path(store, 'benchmark').stat().st_size, 'matches': first['total'],
                'baseline_median_ms': statistics.median(baseline), 'initial_build_ms': build_ms,
                'warm_query_median_ms': statistics.median(warm), 'cold_process_query_median_ms': statistics.median(row['query_ms'] for row in cold),
                'cold_process_total_median_ms': statistics.median(row['process_ms'] for row in cold),
                'append_query_ms': append_ms, 'appended_events_decoded': appended['decoded_events'],
                'cold_samples': cold, 'baseline_samples': baseline, 'warm_samples': warm})
    report = {'ok': True, 'date': '2026-09-28', 'repeats': args.repeats,
        'scope': 'Synthetic journals; literal query equivalent to full redacted history. OS disk caches not cleared. No Flutter rendering, IPC, provider or real research data. Cold-process query excludes interpreter/import cost; process_ms includes it.',
        'results': results}
    args.out.parent.mkdir(exist_ok=True, parents=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
