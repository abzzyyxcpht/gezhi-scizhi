from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


class ShellExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def command(self,code):
        script=self.root/f'fixture-{time.time_ns()}.py'
        script.write_text(code,encoding='utf-8')
        return f'"{sys.executable}" -u "{script}"'

    def run_shell(self,code,**kwargs):
        from src.shell_execution import run_command
        return run_command(self.command(code),self.root,timeout=5,spool_dir=self.root,**kwargs)

    def test_live_line_arrives_before_process_finishes(self):
        frames=[]
        started=time.monotonic()
        result=self.run_shell("import time; print('first',flush=True); time.sleep(.6); print('last')",
            on_output=lambda channel,text:frames.append((time.monotonic()-started,channel,text)))
        self.assertTrue(result['capture_complete'])
        self.assertIn('first',frames[0][2])
        self.assertLess(frames[0][0],.5)
        self.assertIn('last',result['streams']['stdout']['text'])
        self.assertTrue(result['tree_empty'])

    def test_utf8_split_writes_and_known_secret_do_not_leak_in_live_text(self):
        frames=[]
        code="import os,time\nb='中文🧪\\napi_key=fixture-secret-value\\n'.encode()\nfor byte in b:\n os.write(1,bytes([byte])); time.sleep(.001)\n"
        result=self.run_shell(code,on_output=lambda channel,text:frames.append(text))
        self.assertIn('中文🧪',''.join(frames))
        self.assertNotIn('fixture-secret-value',''.join(frames))
        self.assertNotIn('fixture-secret-value',result['streams']['stdout']['text'])

    def test_both_streams_drained_and_capture_cap_is_explicit(self):
        result=self.run_shell("import os\nfor i in range(80):\n os.write(1,b'x'*4096); os.write(2,b'e'*4096)\nprint('FINAL_TAIL',flush=True)",capture_limit=16384)
        self.assertFalse(result['capture_complete'])
        for name in ['stdout','stderr']:
            stream=result['streams'][name]
            self.assertGreater(stream['received_bytes'],stream['retained_bytes'])
            self.assertLessEqual(stream['retained_bytes'],16384)
            self.assertTrue(stream['truncated'])
        self.assertTrue(result['tree_empty'])

    def test_cancel_returns_partial_output_and_no_late_marker(self):
        cancel=threading.Event()
        marker=self.root/'must-not-run'
        command=self.command(f"import time;from pathlib import Path;print('READY',flush=True);time.sleep(20);Path({str(marker)!r}).write_text('bad')")
        from src.shell_execution import run_command
        result=run_command(command,self.root,timeout=5,spool_dir=self.root,cancel_event=cancel,
            on_output=lambda channel,text:cancel.set() if 'READY' in text else None)
        self.assertTrue(result['cancelled'])
        self.assertIn('READY',result['streams']['stdout']['text'])
        self.assertTrue(result['tree_empty'])
        self.assertFalse(marker.exists())

    def test_timeout_keeps_diagnostics(self):
        from src.shell_execution import run_command
        result=run_command(self.command("import time;print('diagnostics',flush=True);time.sleep(20)"),self.root,
            timeout=.3,spool_dir=self.root)
        self.assertTrue(result['timed_out'])
        self.assertIn('diagnostics',result['streams']['stdout']['text'])
        self.assertTrue(result['tree_empty'])

    def test_callback_failure_does_not_change_exit_or_stop_drain(self):
        def broken(*args):
            raise RuntimeError('consumer closed')
        result=self.run_shell("print('output');print('end')",on_output=broken)
        self.assertEqual(0,result['exit_code'])
        self.assertIn('end',result['streams']['stdout']['text'])
        self.assertTrue(result['callback_failed'])

    def test_pre_cancel_never_runs_command(self):
        cancel=threading.Event()
        cancel.set()
        marker=self.root/'must-not-run'
        result=self.run_shell(f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')",cancel_event=cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(marker.exists())

    def test_parent_exit_does_not_leave_child_holding_pipes(self):
        code="import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);print('parent done')"
        started=time.monotonic()
        result=self.run_shell(code)
        self.assertLess(time.monotonic()-started,3)
        self.assertEqual(0,result['exit_code'])
        self.assertTrue(result['tree_empty'])

    def test_cancel_all_stops_active_owned_command(self):
        from src.shell_execution import cancel_all
        result=self.run_shell("import time;print('READY',flush=True);time.sleep(20)",
            on_output=lambda channel,text:cancel_all(wait=False))
        self.assertTrue(result['cancelled'])
        self.assertTrue(result['tree_empty'])


if __name__=='__main__':
    unittest.main()
