"""Real stdio and loopback HTTP access to archived, session-scoped output."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.agent_runtime import AgentRuntime, ToolCall
from src import remote_control
from src.tool_output_runtime import handle


class ToolOutputWireTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = AgentRuntime(session_root=self.root/'sessions', offline=True)
        self.runtime._register('fixture.output','read','fixture',lambda _: {'ok':True,'text':'x'*50000+'尾部验证内容'})
        result = self.runtime._execute_call('session-a',ToolCall('output-call','fixture.output',{},'read'))
        self.ref = result['output_ref']
        self.runtime.sessions.append('session-b','session_created',{})

    def requests(self):
        return [
            {'cmd':'session_tool_outputs','session_id':'session-a'},
            {'cmd':'session_tool_output_search','session_id':'session-a','output_id':self.ref['output_id'],'query':'尾部验证内容'},
            {'cmd':'session_tool_output_read','session_id':'session-a','output_id':self.ref['output_id'],'offset':50000,'limit':100},
            {'cmd':'session_tool_output_read','session_id':'session-b','output_id':self.ref['output_id']},
        ]

    def verify(self, replies):
        self.assertEqual(self.ref['output_id'],replies[0]['output']['items'][0]['output_id'])
        self.assertIn('尾部验证内容',replies[1]['output']['matches'][0]['preview'])
        self.assertIn('尾部验证内容',replies[2]['output']['text'])
        self.assertFalse(replies[3]['ok'])
        self.assertNotIn(str(self.root),json.dumps(replies[3]))

    def test_real_stdio_bridge(self):
        env = dict(os.environ,GEZHI_DATA_DIR=str(self.root),PYTHONUTF8='1')
        for key in list(env):
            if key.startswith(('GEZHI_LLM_','GEZHI_EMB_')):
                del env[key]
        requests = [{**row,'request_id':f'wire-{i}'} for i,row in enumerate(self.requests())]
        process = subprocess.run([sys.executable,'-m','src.bridge'], cwd=Path(__file__).resolve().parents[1],
            input=''.join(json.dumps(row,ensure_ascii=True)+'\n' for row in requests),capture_output=True,
            text=True,encoding='utf-8',env=env,timeout=30)
        self.assertEqual(0,process.returncode,process.stderr)
        replies = [json.loads(row) for row in process.stdout.splitlines() if row.startswith('{')]
        self.assertTrue(replies[0].get('ready'))
        replies = sorted((row for row in replies if 'request_id' in row), key=lambda row: row['request_id'])
        self.assertEqual(4,len(replies),process.stdout)
        for i,row in enumerate(replies):
            self.assertEqual(f'wire-{i}',row['request_id'])
        self.verify(replies)

    def test_real_authenticated_http(self):
        remote_control.stop_server()
        self.addCleanup(remote_control.stop_server)
        def dispatch(request):
            try:
                return handle(request,session_root=self.runtime.sessions.root)
            except (ValueError,OSError):
                return {'ok':False,'error':'output unavailable'}
        info = remote_control.start_server(dispatch)
        replies=[]
        for request in self.requests():
            conn=http.client.HTTPConnection('127.0.0.1',info['port'],timeout=5)
            try:
                conn.request('POST','/api/command',json.dumps(request),{'Authorization':'Bearer '+info['token'],'Content-Type':'application/json'})
                response=conn.getresponse()
                self.assertEqual(200,response.status)
                replies.append(json.loads(response.read()))
            finally:
                conn.close()
        self.verify(replies)
