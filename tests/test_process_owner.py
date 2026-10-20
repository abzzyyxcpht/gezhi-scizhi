"""Real-process ownership tests; every marker lives in a temporary directory."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import psutil


class ProcessOwnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("src.process_owner"),
                             "owned process spawning is not implemented")
        from src import process_owner
        self.module = process_owner
        self.directory = tempfile.TemporaryDirectory(prefix="owned-process-")
        self.addCleanup(self._cleanup_directory)
        self.root = Path(self.directory.name)

    def _cleanup_directory(self):
        """Windows releases a killed child's cwd handle asynchronously.

        The stop semantics are asserted inside each test; this retry only keeps
        temporary-directory removal from failing on that short kernel window.
        """
        for _ in range(100):
            try:
                self.directory.cleanup()
                return
            except PermissionError:
                time.sleep(0.05)
        self.directory.cleanup()

    def command(self, name, source):
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        if os.name == "nt":
            return subprocess.list2cmdline([sys.executable, "-u", str(path)])
        import shlex
        return shlex.join([sys.executable, "-u", str(path)])

    def spawn(self, command):
        process = self.module.spawn_owned(command, self.root)
        self.addCleanup(process.close)
        return process

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("owned process did not reach expected state before deadline")

    def marker_ready(self, name):
        path = self.root / name
        return path.exists() and path.stat().st_size > 0

    def test_output_exit_code_cwd_and_devnull_stdin(self):
        process = self.spawn(self.command("basic.py", """
import os, sys
print(os.getcwd(), flush=True)
print(repr(sys.stdin.buffer.read()), flush=True)
sys.stderr.buffer.write(b'error-output')
sys.exit(7)
"""))
        self.wait_for(lambda: process.poll() is not None)
        output = process.stdout.read()
        self.assertIsInstance(output, bytes)
        self.assertIn(str(self.root).encode(), output)
        self.assertIn(b"b''", output)
        self.assertEqual(process.stderr.read(), b"error-output")
        self.assertEqual(process.poll(), 7)
        self.wait_for(process.tree_empty)
        process.close()
        process.close()
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(process.poll(), 7)

    def test_stop_terminates_single_process(self):
        process = self.spawn(self.command("sleep.py", """
from pathlib import Path
import os, time
Path('ready').write_text(str(os.getpid()))
time.sleep(30)
Path('late').write_text('unexpected')
"""))
        self.wait_for(lambda: self.marker_ready("ready"))
        pid = int((self.root / "ready").read_text())
        process.stop()
        self.wait_for(process.tree_empty)
        self.wait_for(lambda: process.poll() is not None)
        self.wait_for(lambda: not psutil.pid_exists(pid))
        self.assertFalse((self.root / "late").exists())
        process.stop()

    def make_tree(self, parent_exits=False):
        self.command("grandchild.py", """
from pathlib import Path
import os, time
Path('grandchild-ready').write_text(str(os.getpid()))
time.sleep(30)
Path('late').write_text('unexpected')
""")
        self.command("child.py", """
from pathlib import Path
import os, subprocess, sys, time
subprocess.Popen([sys.executable, '-u', 'grandchild.py'])
Path('child-ready').write_text(str(os.getpid()))
time.sleep(30)
""")
        return self.command("parent.py", """
from pathlib import Path
import subprocess, sys, time
subprocess.Popen([sys.executable, '-u', 'child.py'])
""" + ("" if parent_exits else "time.sleep(30)\n"))

    def assert_tree_gone(self):
        for name in ("child-ready", "grandchild-ready"):
            pid = int((self.root / name).read_text())
            self.wait_for(lambda: not psutil.pid_exists(pid))
        self.assertFalse((self.root / "late").exists())

    def test_stop_terminates_grandchildren(self):
        process = self.spawn(self.make_tree())
        self.wait_for(lambda: self.marker_ready("grandchild-ready"))
        process.stop()
        self.wait_for(process.tree_empty)
        self.assert_tree_gone()

    def test_close_cleans_descendants_after_parent_exit(self):
        process = self.spawn(self.make_tree(parent_exits=True))
        self.wait_for(lambda: self.marker_ready("grandchild-ready"))
        self.wait_for(lambda: process.poll() is not None)
        self.assertFalse(process.tree_empty())
        process.close()
        self.wait_for(process.tree_empty)
        self.assert_tree_gone()

    def test_async_close_releases_readers_and_is_idempotent(self):
        process = self.spawn(self.make_tree())
        self.wait_for(lambda: self.marker_ready("grandchild-ready"))
        with ThreadPoolExecutor(max_workers=4) as executor:
            output = executor.submit(process.stdout.read)
            error = executor.submit(process.stderr.read)
            first = executor.submit(process.close)
            second = executor.submit(process.close)
            first.result(timeout=5)
            second.result(timeout=5)
            self.assertEqual(output.result(timeout=5), b"")
            self.assertEqual(error.result(timeout=5), b"")
        self.assert_tree_gone()

    def test_stop_does_not_terminate_an_independent_process(self):
        self.command("independent.py", "import time\ntime.sleep(30)\n")
        sibling = subprocess.Popen([sys.executable, str(self.root / "independent.py")],
                                   cwd=self.root, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def clean_sibling():
            if sibling.poll() is None:
                sibling.terminate()
            sibling.wait(timeout=5)

        self.addCleanup(clean_sibling)
        process = self.spawn(self.make_tree())
        self.wait_for(lambda: self.marker_ready("grandchild-ready"))
        process.stop()
        self.assert_tree_gone()
        self.assertIsNone(sibling.poll())

    def test_argv_spawn_pipes_line_protocol_and_owns_descendants(self):
        self.command("argv-server.py", """
import os, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(30)'])
Path('argv-child').write_text(str(child.pid))
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line == 'env':
        print('env=' + os.environ.get('ONLY_FOR_CHILD', '<missing>'), flush=True)
    else:
        print('line=' + line, flush=True)
""")
        process = self.module.spawn_owned_argv(
            [sys.executable, "-u", str(self.root / "argv-server.py")], self.root,
            env={"PATH": os.environ.get("PATH", ""),
                 "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                 "ONLY_FOR_CHILD": "yes"})
        self.addCleanup(process.close)
        self.assertIsNotNone(process.stdin)
        process.stdin.write(b"ping\n")
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), b"line=ping")
        process.stdin.write(b"env\n")
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), b"env=yes")
        self.wait_for(lambda: self.marker_ready("argv-child"))
        child_pid = int((self.root / "argv-child").read_text())
        process.close()
        self.wait_for(lambda: not psutil.pid_exists(child_pid))
        self.assertTrue(process.tree_empty())

    def test_argv_spawn_rejects_invalid_arguments(self):
        for argv in ((), [], [""], ["ok", b"bytes"], ["bad\x00arg"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.module.spawn_owned_argv(argv, self.root)

    @unittest.skipUnless(os.name == "nt", "Windows native process ownership")
    def test_frozen_executable_is_not_used_as_python_trampoline(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(self.root / "app.exe")):
            process = self.spawn("echo frozen-command")
        self.wait_for(lambda: process.poll() is not None)
        self.assertEqual(process.poll(), 0)
        self.assertIn(b"frozen-command", process.stdout.read())

    @unittest.skipUnless(os.name == "nt", "Windows native process ownership")
    def test_job_assignment_failure_never_executes_command(self):
        command = self.command("must-not-run.py", "from pathlib import Path\nPath('forbidden').write_text('ran')\n")
        real_create = self.module._create_windows_process
        attempts = []

        def create(command, cwd, job, handles):
            attempts.append(True)
            return real_create(command, cwd, -1, handles)

        with mock.patch.object(self.module, "_create_windows_process", side_effect=create):
            with self.assertRaises(OSError):
                self.module.spawn_owned(command, self.root)
        self.assertEqual(len(attempts), 1)
        self.assertFalse((self.root / "forbidden").exists())

    @unittest.skipUnless(os.name == "nt", "Windows native process ownership")
    def test_resume_failure_never_executes_command_and_cleans_handles(self):
        command = self.command("must-not-resume.py", "from pathlib import Path\nPath('forbidden').write_text('ran')\n")
        baseline = psutil.Process().num_handles()
        with mock.patch.object(self.module, "_resume_thread", return_value=0xFFFFFFFF):
            for _ in range(12):
                with self.assertRaises(OSError):
                    self.module.spawn_owned(command, self.root)
        self.assertLessEqual(psutil.Process().num_handles(), baseline + 1)
        self.assertFalse((self.root / "forbidden").exists())

    @unittest.skipUnless(os.name == "nt", "Windows native process ownership")
    def test_success_and_assignment_failure_release_handles(self):
        baseline = psutil.Process().num_handles()
        for _ in range(12):
            process = self.spawn("echo leak-check")
            self.wait_for(lambda: process.poll() is not None)
            self.assertIn(b"leak-check", process.stdout.read())
            process.close()
        real_create = self.module._create_windows_process

        def invalid_job(command, cwd, job, handles):
            return real_create(command, cwd, -1, handles)

        with mock.patch.object(self.module, "_create_windows_process", side_effect=invalid_job):
            for _ in range(12):
                with self.assertRaises(OSError):
                    self.module.spawn_owned("echo forbidden", self.root)
        self.assertLessEqual(psutil.Process().num_handles(), baseline + 1)

    @unittest.skipUnless(os.name == "nt", "Windows kernel Job close ownership")
    def test_owner_crash_before_resume_does_not_leave_suspended_orphan(self):
        repo = Path(__file__).resolve().parents[1]
        self.command("owner-crash.py", f"""
import os, sys
from pathlib import Path
sys.path.insert(0, {str(repo)!r})
from src import process_owner
original = process_owner._create_windows_process
def create(*args):
    process = original(*args)
    Path('owned-pid').write_text(str(process[2]))
    return process
process_owner._create_windows_process = create
process_owner._resume_thread = lambda handle: os._exit(23)
process_owner.spawn_owned('echo forbidden > forbidden', Path.cwd())
""")
        parent = subprocess.Popen([sys.executable, str(self.root / "owner-crash.py")],
                                  cwd=self.root, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: parent.kill() if parent.poll() is None else None)
        self.assertEqual(parent.wait(timeout=5), 23)
        pid = int((self.root / "owned-pid").read_text())
        self.wait_for(lambda: not psutil.pid_exists(pid))
        self.assertFalse((self.root / "forbidden").exists())

    @unittest.skipUnless(os.name == "nt", "Windows kernel Job close ownership")
    def test_owner_crash_cleans_running_descendants(self):
        command = self.make_tree()
        repo = Path(__file__).resolve().parents[1]
        self.command("owner-crash.py", f"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, {str(repo)!r})
from src.process_owner import spawn_owned
owned = spawn_owned({command!r}, Path.cwd())
deadline = time.monotonic() + 5
while not Path('grandchild-ready').exists() or not Path('grandchild-ready').stat().st_size:
    if time.monotonic() > deadline:
        os._exit(24)
    time.sleep(0.01)
os._exit(23)
""")
        parent = subprocess.Popen([sys.executable, str(self.root / "owner-crash.py")],
                                  cwd=self.root, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: parent.kill() if parent.poll() is None else None)
        self.assertEqual(parent.wait(timeout=7), 23)
        self.assert_tree_gone()

    @unittest.skipUnless(os.name == "nt", "Windows handle-list isolation")
    def test_concurrent_spawns_do_not_keep_each_others_pipes_alive(self):
        count = 8
        gate = threading.Barrier(count)

        def start(index):
            command = self.command(f"concurrent-{index}.py", (
                "import time\nprint('long', flush=True)\ntime.sleep(30)\n"
                if index % 2 else f"print('short-{index}', flush=True)\n"))
            gate.wait(timeout=5)
            return self.module.spawn_owned(command, self.root)

        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(start, index) for index in range(count)]
            processes = []
            try:
                for future in futures:
                    process = future.result(timeout=5)
                    processes.append(process)
                    self.addCleanup(process.close)
                for index in range(0, count, 2):
                    output = executor.submit(processes[index].stdout.read)
                    self.assertEqual(output.result(timeout=5).strip(), f"short-{index}".encode())
                for index in range(1, count, 2):
                    self.assertIsNone(processes[index].poll())
            finally:
                for future in futures:
                    if future.done() and future.exception() is None:
                        future.result().close()


if __name__ == "__main__":
    unittest.main()
