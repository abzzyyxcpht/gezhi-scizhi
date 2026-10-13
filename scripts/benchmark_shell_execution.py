"""Synthetic owned shell capture benchmark with bounded retained output."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import tracemalloc

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    scratch=ROOT/'_testtmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shell-bench-',dir=scratch) as folder:
        root=Path(folder)
        os.environ['GEZHI_DATA_DIR']=str(root/'data')
        from src.shell_execution import run_command
        samples=[]
        for megabytes in (4,48):
            script=root/f'output-{megabytes}.py'
            script.write_text(f"import os\nchunk=b'x'*65536\nfor i in range({megabytes}*16):\n os.write(1,chunk);os.write(2,chunk)\nos.write(1,b'\\nEND_MARKER\\n')",encoding='utf-8')
            tracemalloc.start()
            start=time.perf_counter()
            result=run_command(f'"{sys.executable}" -u "{script}"',root,timeout=30,spool_dir=root)
            elapsed=time.perf_counter()-start
            _,peak=tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert result['tree_empty'] and result['exit_code']==0
            assert 'END_MARKER' in result['streams']['stdout']['text']
            samples.append({'megabytes_per_stream':megabytes,'seconds_with_tracemalloc':round(elapsed,3),
                'python_peak_bytes':peak,'tree_empty':result['tree_empty'],'capture_complete':result['capture_complete'],
                'streams':{name:{k:v for k,v in row.items() if k not in {'text','preview'}} for name,row in result['streams'].items()}})
        print(json.dumps({'python':platform.python_version(),'platform':sys.platform,
            'method':'real subprocess writes equal stdout/stderr; parent drains and retains bounded prefix plus display tail. tracemalloc includes final redaction/materialization, not OS total RSS or child interpreter.',
            'samples':samples},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
