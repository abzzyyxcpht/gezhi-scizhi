"""Independent shell runner regressions using owned native child processes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

import psutil

from src import shell_execution


class ShellExecutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="shell-review-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def command(self, code):
        path = self.root / f"fixture-{time.time_ns()}.py"
        path.write_text(code, encoding="utf-8")
        if os.name == "nt":
            return subprocess.list2cmdline([sys.executable, "-u", str(path)])
        import shlex
        return shlex.join([sys.executable, "-u", str(path)])

    def run_command(self, code, **kwargs):
        return shell_execution.run_command(self.command(code), self.root,
                                           spool_dir=self.root, **kwargs)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("expected process or thread state was not reached")

    @unittest.skipUnless(os.name == "nt", "Native cmd.exe encoding")
    def test_native_cmd_chinese_output_is_not_corrupted(self):
        result = shell_execution.run_command("echo 中文输出", self.root, timeout=3,
                                             spool_dir=self.root)
        self.assertEqual(result["streams"]["stdout"]["text"].strip(), "中文输出")

    @unittest.skipUnless(os.name == "nt", "Native cmd.exe quoting")
    def test_windows_shell_keeps_quoted_space_paths_and_exit_status(self):
        folder = self.root / "folder with spaces"
        folder.mkdir()
        script = folder / "script with spaces.py"
        script.write_text("import sys\nprint('quoted path works')\nsys.exit(7)\n", encoding="utf-8")
        command = f'"{sys.executable}" -u "{script}"'
        result = shell_execution.run_command(command, folder, timeout=3, spool_dir=self.root)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["streams"]["stdout"]["text"].strip(), "quoted path works")
        self.assertEqual(result["streams"]["stderr"]["text"], "")

    def test_reader_failure_stops_owned_writer_without_waiting_for_timeout(self):
        original_drain = shell_execution._Capture.drain

        def broken_reader(capture, stream):
            if capture.name == "stdout":
                capture.error = "OSError"
                capture.done.set()
            else:
                original_drain(capture, stream)

        started = time.monotonic()
        with mock.patch.object(shell_execution._Capture, "drain", broken_reader):
            result = self.run_command("import os\nwhile True: os.write(1, b'x'*32768)\n",
                                      timeout=2)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.2, "a failed reader left the writer blocked until timeout")
        self.assertTrue(result["tree_empty"])
        self.assertFalse(result["capture_complete"])
        self.assertEqual(result["streams"]["stdout"]["reader_error"], "OSError")
        self.assertFalse(result["timed_out"], "a pipe failure should not be reported as timeout")

    def test_partial_capture_initialization_closes_first_spool(self):
        original = shell_execution.tempfile.TemporaryFile
        opened = []

        def open_spool(*args, **kwargs):
            if opened:
                raise OSError("second spool denied")
            stream = original(*args, **kwargs)
            opened.append(stream)
            return stream

        try:
            with mock.patch.object(shell_execution.tempfile, "TemporaryFile", side_effect=open_spool):
                result = self.run_command("print('must not execute')", timeout=2)
            self.assertIn("second spool denied", result["error"])
            self.assertTrue(opened[0].closed, "stdout spool leaked when stderr initialization failed")
        finally:
            for stream in opened:
                stream.close()

    def test_native_close_error_still_finishes_both_spools(self):
        original_spawn = shell_execution.spawn_owned
        original_spool = shell_execution.tempfile.TemporaryFile
        opened = []
        owners = []

        def open_spool(*args, **kwargs):
            stream = original_spool(*args, **kwargs)
            opened.append(stream)
            return stream

        def spawn(*args, **kwargs):
            owner = original_spawn(*args, **kwargs)
            owners.append(owner)
            close = owner.close

            def failing_close():
                close()
                raise OSError("native close reporting error")

            owner.close = failing_close
            return owner

        try:
            with mock.patch.object(shell_execution, "spawn_owned", side_effect=spawn), \
                    mock.patch.object(shell_execution.tempfile, "TemporaryFile", side_effect=open_spool):
                try:
                    self.run_command("print('diagnostic output')", timeout=2)
                except OSError:
                    pass
            self.assertEqual(len(opened), 2)
            self.assertTrue(all(stream.closed for stream in opened),
                            "native close failure skipped capture cleanup")
            self.assertTrue(owners[0].tree_empty())
            self.assertFalse(shell_execution._ACTIVE)
        finally:
            for stream in opened:
                stream.close()

    def test_capture_does_not_skip_uncaptured_multibyte_character(self):
        capture = shell_execution._Capture("stdout", 5, queue.Queue(maxsize=64), self.root)
        try:
            capture.feed(b"abcd")
            capture.feed("中".encode())
            capture.feed(b"Z")
            capture.feed(b"", final=True)
            capture.done.set()
            result = capture.finish()
            self.assertEqual(result["retained_bytes"], 4)
            self.assertNotIn("abcdZ", result["text"], "archived prefix skipped an intervening character")
        finally:
            capture.file.close()

    def test_slow_callback_does_not_prevent_cancel_all(self):
        callback_entered = threading.Event()
        release_callback = threading.Event()
        marker = self.root / "late"

        def callback(channel, text):
            callback_entered.set()
            release_callback.wait(timeout=4)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.run_command, (
                "import time\nfrom pathlib import Path\nprint('READY',flush=True)\n"
                f"time.sleep(20)\nPath({str(marker)!r}).write_text('bad')\n"),
                timeout=5, on_output=callback)
            try:
                self.assertTrue(callback_entered.wait(timeout=3))
                still_active = shell_execution.cancel_all(wait=True, timeout=.8)
                self.assertEqual(still_active, 0, "blocked callback prevented ownership cancellation")
                self.assertTrue(future.done())
                self.assertTrue(future.result()["cancelled"])
            finally:
                release_callback.set()
                future.result(timeout=5)
        self.assertFalse(marker.exists())

    def test_repeated_native_runs_leave_no_readers_handles_or_active_entries(self):
        if os.name == "nt":
            # Windows lazily keeps one pipe-device handle and one process-creation
            # ETW registration. Initialize the standard-library paths before
            # measuring handles owned by the shell runner; keep the leak limit.
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            os.close(write_fd)
            subprocess.run([sys.executable, "-c", "pass"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        native_process = psutil.Process()
        baseline_handles = native_process.num_handles() if os.name == "nt" else native_process.num_fds()
        baseline_threads = {thread.ident for thread in threading.enumerate()}
        for _ in range(8):
            result = self.run_command("import os\nos.write(1,b'out\\n')\nos.write(2,b'err\\n')\n", timeout=2)
            self.assertTrue(result["capture_complete"])
            self.assertTrue(result["tree_empty"])
        self.wait_for(lambda: not any(thread.ident not in baseline_threads and
                                     thread.name.startswith("scizhi-shell-")
                                     for thread in threading.enumerate()))
        self.assertFalse(shell_execution._ACTIVE)
        handles = native_process.num_handles() if os.name == "nt" else native_process.num_fds()
        self.assertLessEqual(handles, baseline_handles + 1)

    def test_failed_first_capture_finish_still_closes_remaining_spools(self):
        original_spool = shell_execution.tempfile.TemporaryFile
        original_finish = shell_execution._Capture.finish
        opened = []

        def open_spool(*args, **kwargs):
            stream = original_spool(*args, **kwargs)
            opened.append(stream)
            return stream

        def finish(capture):
            if capture.name == "stdout":
                raise OSError("stdout spool read failed")
            return original_finish(capture)

        try:
            with mock.patch.object(shell_execution.tempfile, "TemporaryFile", side_effect=open_spool), \
                    mock.patch.object(shell_execution._Capture, "finish", finish):
                try:
                    self.run_command("print('kept output')", timeout=2)
                except OSError:
                    pass
            self.assertEqual(len(opened), 2)
            self.assertTrue(all(stream.closed for stream in opened),
                            "first finish failure skipped closing sibling capture")
        finally:
            for stream in opened:
                stream.close()

    def test_decode_loss_is_explicit_and_real_replacement_character_is_preserved(self):
        result = self.run_command("import os\nos.write(1,b'valid\\xef\\xbf\\xbd\\xff\\n')\n", timeout=2)
        output = result["streams"]["stdout"]
        self.assertEqual(output["decode_loss_bytes"], 1)
        self.assertFalse(result["capture_complete"])
        self.assertTrue(output["truncated"])
        self.assertIn("valid\ufffd\ufffd", output["text"])

    def test_multibyte_characters_split_across_every_byte_are_lossless(self):
        code = ("import os,time\n"
                "for value in '中文🧪\\n'.encode():\n"
                " os.write(1,bytes([value]));time.sleep(.01)\n")
        result = self.run_command(code, timeout=3)
        self.assertEqual(result["streams"]["stdout"]["text"], "中文🧪\n")
        self.assertTrue(result["capture_complete"])

    def test_cancel_all_cleans_concurrent_native_commands(self):
        ready = [threading.Event() for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(self.run_command,
                                       "import time\nprint('READY',flush=True)\ntime.sleep(20)\n",
                                       timeout=5,
                                       on_output=lambda channel, text, event=event: event.set())
                       for event in ready]
            try:
                self.assertTrue(all(event.wait(timeout=3) for event in ready))
                self.assertEqual(shell_execution.cancel_all(wait=True, timeout=1.5), 0)
                for future in futures:
                    result = future.result(timeout=1)
                    self.assertTrue(result["cancelled"])
                    self.assertTrue(result["tree_empty"])
            finally:
                shell_execution.cancel_all(wait=True, timeout=2)
                for future in futures:
                    future.result(timeout=5)
        self.assertFalse(shell_execution._ACTIVE)


if __name__ == "__main__":
    unittest.main()
