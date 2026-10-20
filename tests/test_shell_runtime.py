from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from src import tools_workspace, llm, settings
from src.agent_runtime import AgentRuntime
from tests.test_adaptive_driver import response


class ShellRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for patch in (mock.patch.object(tools_workspace,'DATA_DIR',self.root),
                      mock.patch.object(settings,'_PATH',self.root/'settings.json')):
            patch.start()
            self.addCleanup(patch.stop)

    def test_live_journal_then_cancel_reaches_real_process_and_preserves_observation(self):
        # C2 三档门后：shell.run 需 full-access 档位才进入审批流（档位拒绝有专测）
        (self.root/'settings.json').write_text(
            json.dumps({"agent_permission_tier": "full-access"}),encoding='utf-8')
        runtime=AgentRuntime(session_root=self.root/'sessions',offline=False,llm_enabled=True,permissions={'shell.run':'allow'})
        script=self.root/'worker.py'
        marker=self.root/'late-marker'
        script.write_text(f"import time\nfrom pathlib import Path\nprint('READY',flush=True)\ntime.sleep(15)\nPath({str(marker)!r}).write_text('bad')",encoding='utf-8')
        command=f'"{sys.executable}" -u "{script}"'
        result={}
        with mock.patch.object(llm,'available',return_value=True), \
                mock.patch.object(llm,'chat',return_value=response(tool='shell.run',args={'command':command,'timeout_seconds':20})):
            worker=threading.Thread(target=lambda: result.update(runtime.start('受控取消测试')))
            worker.start()
            deadline=time.monotonic()+5
            sid=None
            try:
                while time.monotonic()<deadline:
                    rows=runtime.sessions.list()
                    if rows:
                        sid=rows[0]['session_id']
                        if any(e['type']=='shell_output' and 'READY' in e['payload'].get('text','') for e in runtime.sessions.read(sid)):
                            break
                    time.sleep(.02)
                else:
                    self.fail('No actual shell output arrived before completion')
                self.assertTrue(worker.is_alive())
                before=time.monotonic()
                runtime.cancel_session(sid)
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertLess(time.monotonic()-before,3)
            finally:
                if worker.is_alive():
                    from src.shell_execution import cancel_all
                    cancel_all()
                    worker.join(3)
        self.assertEqual('cancelled',result['status'])
        self.assertFalse(marker.exists())
        events=runtime.sessions.read(sid)
        output=next(e['payload']['result'] for e in events if e['type']=='tool_result')
        self.assertTrue(output['tree_empty'])
        self.assertTrue(output['cancelled'])
        self.assertIn('READY',output['stdout'])

    def test_preview_journal_is_bounded_and_does_not_leak_line_secret(self):
        # C2 三档门后：shell.run 需 full-access 档位才进入执行（与上方取消用例一致）
        (self.root/'settings.json').write_text(
            json.dumps({"agent_permission_tier": "full-access"}),encoding='utf-8')
        runtime=AgentRuntime(session_root=self.root/'sessions',offline=True)
        from src.agent_runtime import ToolCall
        script=self.root/'output.py'
        script.write_text("print('api_key=fixture-PRIVATE')\nfor i in range(6000): print('line '+str(i))",encoding='utf-8')
        result=runtime._execute_call('bounded',ToolCall('shell-call','shell.run',{'command':f'"{sys.executable}" -u "{script}"'},'write'))
        self.assertTrue(result['ok'])
        journal=runtime.sessions._path('bounded').read_text('utf-8')
        self.assertNotIn('fixture-PRIVATE',journal)
        previews=[e for e in runtime.sessions.read('bounded') if e['type']=='shell_output']
        self.assertLess(sum(len(e['payload']['text']) for e in previews),16500)
        self.assertLess(len(journal),38000)
        self.assertIn('output_refs',result)

    def test_direct_capture_limit_marks_partial_archive(self):
        from src.shell_execution import run_command
        runtime=AgentRuntime(session_root=self.root/'sessions',offline=True)
        command=f'"{sys.executable}" -c "print(\'x\'*40000)"'
        captured=run_command(command,self.root,capture_limit=16000)
        with mock.patch('src.shell_execution.run_command',return_value=captured):
            result=runtime.tools['shell.run'].handler({'__session_id':'partial','command':'fixture'})
        self.assertFalse(result['capture_complete'])
        from src.tool_outputs import ToolOutputStore
        ref=result['output_refs']['stdout']
        first=ToolOutputStore(runtime.sessions.root).read('partial',ref['output_id'])
        self.assertIn('输出不完整',first['text'])

    def test_manual_terminal_does_not_inherit_historical_agent_cancel(self):
        runtime=AgentRuntime(session_root=self.root/'sessions',offline=True)
        runtime.sessions.root.mkdir(parents=True,exist_ok=True)
        (runtime.sessions.root/'stopped.cancel').touch()
        command=f'"{sys.executable}" -c "print(\'manual request\')"'
        result=runtime.tools['shell.run'].handler({'__session_id':'stopped','__cancel_event':threading.Event(),'command':command})
        self.assertTrue(result['ok'])
        self.assertIn('manual request',result['stdout'])


if __name__=='__main__':
    unittest.main()
