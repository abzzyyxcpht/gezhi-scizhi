"""Measure local preference/catalog operations using a fresh isolated data root."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def measure(fn, count):
    samples = []
    for index in range(count):
        start = time.perf_counter_ns()
        result = fn(index)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
        if not result.get('ok'):
            raise RuntimeError(result.get('error') or 'Operation failed')
    ordered = sorted(samples)
    return {'samples': count, 'median_ms': round(statistics.median(samples), 3),
            'p95_ms': round(ordered[math.ceil(.95 * count) - 1], 3),
            'max_ms': round(max(samples), 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='docs/dual-mode-performance-2026-10-04.json')
    args = parser.parse_args()
    scratch = ROOT / 'test-output' / 'dual-mode'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mode-perf-', dir=scratch) as data_root:
        os.environ['GEZHI_DATA_DIR'] = data_root
        from src.bridge import handle
        assert handle({'cmd': 'workbench_mode'})['mode'] == 'research'
        handle({'cmd': 'command_catalog'})  # Warm imports; report steady state explicitly.
        metrics = {
            'preference_query': measure(lambda _: handle({'cmd': 'workbench_mode'}), 200),
            'preference_save': measure(lambda n: handle({'cmd': 'workbench_mode',
                'mode': 'general' if n % 2 == 0 else 'research'}), 40),
            'command_catalog': measure(lambda _: handle({'cmd': 'command_catalog',
                'workspace_mode': 'general'}), 100),
            'chinese_command_search': measure(lambda _: handle({'cmd': 'command_catalog',
                'workspace_mode': 'general', 'query': '模型'}), 100),
        }
        assert not handle({'cmd': 'workbench_mode', 'mode': 'invalid'})['ok']
        assert handle({'cmd': 'workbench_mode'})['mode'] == 'research'
    report = {'date': '2026-10-04', 'platform': platform.platform(),
              'python': platform.python_version(), 'scope': 'Local Python Bridge operations after warmup',
              'isolated_data': True, 'supplier_calls': 0,
              'limits': 'Does not measure GUI paint, terminal latency, startup, supplier quality or upstream performance.',
              'metrics': metrics}
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
