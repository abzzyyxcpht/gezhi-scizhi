"""Isolated synthetic output storage/logging measurements; no model invocation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    scratch = ROOT/'_testtmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='output-benchmark-',dir=scratch) as directory:
        os.environ['GEZHI_DATA_DIR'] = directory
        for key in list(os.environ):
            if key.startswith(('GEZHI_LLM_','GEZHI_EMB_')):
                del os.environ[key]
        from src.agent_runtime import AgentRuntime, ToolCall
        from src.agent_driver import AdaptiveDriver
        from src.tool_output_runtime import output_store
        runtime = AgentRuntime(session_root=Path(directory)/'sessions',offline=True)
        raw = 'x'*(4*1024*1024)+'尾部证据：需要核查'
        runtime._register('fixture.large','read','synthetic output',lambda _: {'ok':True,'text':raw})
        tracemalloc.start()
        start=time.perf_counter()
        result=runtime._execute_call('benchmark',ToolCall('large','fixture.large',{},'read'))
        elapsed=time.perf_counter()-start
        _,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        state={'session_id':'benchmark','messages':[],'queue':[],'active_call':None,'active_calls':[], 'unresolved':{}}
        driver=AdaptiveDriver(runtime)
        driver._observe(state,{'id':'large','name':'fixture.large','arguments':{}},result)
        store=output_store(runtime.sessions.root)
        ref=result['output_ref']
        start=time.perf_counter()
        found=store.search('benchmark',ref['output_id'],'尾部证据')
        cold_search=time.perf_counter()-start
        assert found['matches']
        durations=[]
        for _ in range(30):
            start=time.perf_counter()
            page=store.read('benchmark',ref['output_id'],offset=found['matches'][0]['offset'],limit=4000)
            durations.append((time.perf_counter()-start)*1000)
            assert '需要核查' in page['text']
        report={'python':platform.python_version(),'platform':sys.platform,
            'method':'4 MiB synthetic tool string; real AgentRuntime journal and AdaptiveDriver observation; no LLM, UI renderer, or competitor timing. tracemalloc enabled only around execute_call.',
            'source_characters':len(raw),'archived_utf8_bytes':ref['total_bytes'],
            'journal_bytes':runtime.sessions._path('benchmark').stat().st_size,
            'driver_bytes':driver.path('benchmark').stat().st_size,
            'execute_with_tracemalloc_seconds':round(elapsed,6),'execute_peak_python_bytes':peak,
            'first_search_seconds':round(cold_search,6),'tail_read_samples':30,
            'tail_read_p50_ms':round(statistics.median(durations),3),'tail_read_p95_ms':round(sorted(durations)[28],3),
            'archive_files':len(list((runtime.sessions.root/'.tool-outputs'/'benchmark').glob('*.txt'))),
            'tail_read_verified':True,'tool_executions':1,'model_calls':0}
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
