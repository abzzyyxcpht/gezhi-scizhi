"""Direct terminal commands share bounded capture and owned-process cleanup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import psutil

from src import bridge


class BridgeShellExecTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="scizhi-bridge-shell-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = mock.patch.object(bridge, "ROOT_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, code):
        script = self.root / f"fixture-{time.time_ns()}.py"
        script.write_text(code, encoding="utf-8")
        args = [sys.executable, "-u", str(script)]
        return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)

    def execute(self, code):
        return bridge.handle({"cmd": "shell_exec", "command": self.command(code)})

    def owned_child(self, record):
        if not record.exists():
            return None
        identity = json.loads(record.read_text(encoding="utf-8"))
        try:
            process = psutil.Process(identity["pid"])
            if process.create_time() == identity["created"] and process.status() != psutil.STATUS_ZOMBIE:
                return process
        except psutil.NoSuchProcess:
            pass
        return None

    def stop_fixture_child(self, record):
        process = self.owned_child(record)
        if process:
            process.kill()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                pass

    def descendant_command(self, *, timeout=False):
        record = self.root / "child-identity.json"
        ready = self.root / "child-ready"
        child = self.root / "child.py"
        child.write_text(
            f"from pathlib import Path\nimport time\nPath({str(ready)!r}).touch()\ntime.sleep(20)\n",
            encoding="utf-8",
        )
        self.addCleanup(self.stop_fixture_child, record)
        return (
            "import json,subprocess,sys,time,psutil\nfrom pathlib import Path\n"
            f"child=subprocess.Popen([sys.executable, {str(child)!r}], "
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            f"Path({str(record)!r}).write_text(json.dumps({{'pid': child.pid, "
            "'created': psutil.Process(child.pid).create_time()}),encoding='utf-8')\n"
            "deadline=time.monotonic()+5\n"
            f"while not Path({str(ready)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "print('diagnostic before exit',flush=True)\n"
            + ("time.sleep(12)\n" if timeout else ""),
            record,
        )

    def test_large_output_is_bounded_and_explicitly_marked(self):
        result = self.execute("import os\nos.write(1,b'x'*50000)\nos.write(2,b'y'*50000)\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], 0)
        self.assertLessEqual(len(result["text"]), 4000)
        self.assertTrue(result.get("truncated"), result)
        self.assertIn("输出已截断", result["text"])

    def test_small_output_and_nonzero_exit_keep_protocol(self):
        result = self.execute("import os,sys\nprint(os.getcwd())\nsys.stderr.write('failure detail')\nsys.exit(7)\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], 7)
        self.assertIn(str(self.root), result["text"])
        self.assertIn("failure detail", result["text"])
        self.assertFalse(result.get("truncated", False))

    def test_command_output_redacts_credentials(self):
        result = self.execute("print('api_key=fixture-only-value')\n")
        self.assertTrue(result["ok"], result)
        self.assertNotIn("fixture-only-value", result["text"])
        self.assertIn("[REDACTED]", result["text"])

    @unittest.skipUnless(os.name == "nt", "Native cmd.exe encoding")
    def test_native_shell_chinese_output_uses_utf8(self):
        result = bridge.handle({"cmd": "shell_exec", "command": "echo 中文输出"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"].strip(), "中文输出")

    def test_successful_command_does_not_leave_a_child_running(self):
        code, record = self.descendant_command()
        result = self.execute(code)
        self.assertTrue(result["ok"], result)
        self.assertIn("diagnostic before exit", result["text"])
        self.assertIsNone(self.owned_child(record), "command left its child running")

    def test_timeout_preserves_diagnostics_and_stops_children(self):
        code, record = self.descendant_command(timeout=True)
        result = self.execute(code)
        self.assertFalse(result["ok"], result)
        self.assertIn("10s", result["error"])
        self.assertIsNone(self.owned_child(record), "timeout left its child running")
        self.assertIn("diagnostic before exit", result.get("text", ""))
        self.assertTrue(result.get("timed_out"))


if __name__ == "__main__":
    unittest.main()
