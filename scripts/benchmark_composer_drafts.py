"""Measure isolated draft storage and batching, without models or Flutter draws."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.composer_drafts import DraftStore  # noqa: E402


def summary(values):
    ordered = sorted(values)
    return {'samples': len(values), 'mean_ms': round(statistics.mean(values), 3),
            'p50_ms': round(statistics.median(values), 3),
            'p95_ms': round(ordered[min(len(values) - 1, int(len(values) * .95))], 3)}


async def batching(root):
    import flet as ft
    from src.ui.composer_drafts import ComposerDrafts

    class Page:
        updates = 0
        def update(self):
            self.updates += 1
        def run_task(self, callback, *args):
            return asyncio.create_task(callback(*args))

    class Store(DraftStore):
        saves = 0
        def save(self, *args, **kwargs):
            self.saves += 1
            return super().save(*args, **kwargs)

    page, store = Page(), Store(root)
    ui = ComposerDrafts(page, ft.TextField(), ft.Dropdown(value='research'), ft.Dropdown(value='agent'), store=store)
    ui.select('benchmark')
    durations = []
    for index in range(100):
        ui.request.value = '研究草稿 ' + '测' * index
        start = time.perf_counter()
        ui.capture()
        durations.append((time.perf_counter() - start) * 1000)
        await asyncio.sleep(.002)
    before = store.saves
    deadline = time.monotonic() + 5
    while store.saves == 0 and time.monotonic() < deadline:
        await asyncio.sleep(.01)
    saved = store.load('session:benchmark')
    assert saved['text'] == ui.request.value and before == 0 and store.saves == 1
    ui.close()
    return {'events': 100, 'interval_seconds': .002, 'debounce_seconds': .4,
            'save_calls_during_burst': before, 'save_calls_after_idle': store.saves,
            'capture': summary(durations), 'page_update_calls': page.updates,
            'latest_text_persisted': True, 'scheduler': 'real asyncio; stub page, no Flutter renderer'}


def main():
    scratch = ROOT / '_testtmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='draft-benchmark-', dir=scratch) as name:
        root = Path(name)
        store = DraftStore(root / 'latency')
        rows = []
        for length in (100, 4000, 32000):
            revision = ''
            writes, reads = [], []
            scope = f'session:length-{length}'
            for index in range(50):
                text = '测' * (length - 2) + f'{index:02d}'
                start = time.perf_counter()
                row = store.save(scope, text, expected_revision=revision)
                writes.append((time.perf_counter() - start) * 1000)
                revision = row['revision']
                start = time.perf_counter()
                assert DraftStore(root / 'latency').load(scope) == row
                reads.append((time.perf_counter() - start) * 1000)
            rows.append({'python_characters': length, 'save': summary(writes), 'load_new_instance': summary(reads)})
        print(json.dumps({'python': platform.python_version(), 'platform': sys.platform,
            'method': '50 atomic CAS saves and fresh-instance loads per synthetic Chinese length; local filesystem; no fsync/power-loss guarantee or model/render time',
            'latency': rows, 'batching': asyncio.run(batching(root / 'batch'))}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
