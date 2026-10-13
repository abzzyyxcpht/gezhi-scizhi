"""A live stdio approval can be cancelled through the separate control lane."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from src.agent_runtime import AgentRuntime
from src import settings


class ShellBridgeCancelTest(unittest.TestCase):
    def test_real_bridge_cancels_running_approved_shell(self):
        # C2 三档门后：shell.run 需 full-access 档位才进入审批流（档位拒绝有专测）
        tier = {**settings.load(), "agent_permission_tier": "full-access"}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script=root/'long-task.py'
            marker=root/'late-marker'
            script.write_text(f"import time\nfrom pathlib import Path\nprint('BRIDGE_READY',flush=True)\ntime.sleep(15)\nPath({str(marker)!r}).write_text('bad')",encoding='utf-8')
            # 真机桥接子进程经 GEZHI_DATA_DIR 读同一份设置：档位需落盘
            (root/'settings.json').write_text(json.dumps({"agent_permission_tier": "full-access"}),encoding='utf-8')
            runtime=AgentRuntime(session_root=root/'sessions',offline=True)
            steps=[{'tool':'shell.run','arguments':{'command':f'"{sys.executable}" -u "{script}"','timeout_seconds':20}}]
            with mock.patch.object(settings, "load", return_value=tier), \
                    mock.patch.object(runtime,'plan',return_value={'steps':steps,'agent':'research','mode':'agent'}):
                pending=runtime.start('受控桥接取消')
            self.assertEqual('waiting_approval',pending['status'])
            sid=pending['session_id']
            env={key:value for key,value in os.environ.items() if not key.startswith(('GEZHI_LLM_','GEZHI_EMB_'))}
            env.update(GEZHI_DATA_DIR=str(root),PYTHONUTF8='1')
            process=subprocess.Popen([sys.executable,'-m','src.bridge'],cwd=Path(__file__).resolve().parents[1],
                env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')
            responses=queue.Queue()
            def read():
                for line in process.stdout:
                    responses.put(json.loads(line))
            threading.Thread(target=read,daemon=True).start()
            def send(request):
                process.stdin.write(json.dumps(request)+'\n')
                process.stdin.flush()
            try:
                self.assertTrue(responses.get(timeout=10)['ready'])
                send({'cmd':'agent_approve','session_id':sid,'tool_call_id':pending['pending']['id'],'request_id':'approve'})
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if any(row['type']=='shell_output' and 'BRIDGE_READY' in row['payload'].get('text','') for row in runtime.sessions.read(sid)):
                        break
                    time.sleep(.03)
                else:
                    self.fail('Shell live journal did not arrive')
                send({'cmd':'agent_cancel','session_id':sid,'request_id':'cancel'})
                replies=[responses.get(timeout=5),responses.get(timeout=5)]
                self.assertEqual({'approve','cancel'},{row['request_id'] for row in replies})
                approved=next(row for row in replies if row['request_id']=='approve')
                self.assertEqual('cancelled',approved['result']['status'])
                self.assertFalse(marker.exists())
                process.stdin.close()
                process.wait(timeout=5)
                self.assertEqual(0,process.returncode)
            finally:
                if process.poll() is None:
                    process.terminate()  # Only this test-created bridge; Job handles own its shell.
                    process.wait(timeout=5)
                process.stdout.close()
                if not process.stdin.closed:
                    process.stdin.close()
