"""工作台工具集（file.*/shell.run）测试：语义、边界、审批集成。"""
from __future__ import annotations

import sys
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.agent_runtime import AgentRuntime, SessionStore
from src.config import DATA_DIR
from src import tools_workspace
from src.tools_workspace import (GLOB_LIMIT, GREP_LIMIT, READ_TEXT_LIMIT,
                                 SHELL_OUTPUT_LIMIT)


class WorkspaceToolsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        self.runtime = AgentRuntime(llm_enabled=False, session_root=self.tmp / "sessions")
        self.sid = "testws" + uuid.uuid4().hex[:8]
        self.runtime.sessions.append(self.sid, "session_created", {"project_id": ""})
        self.root = DATA_DIR / "sessions" / self.sid / "workspace"

    def call(self, name: str, **arguments):
        arguments["__session_id"] = self.sid
        return self.runtime.tools[name].handler(arguments)

    def test_file_read_returns_content_and_line_stats(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "a.txt").write_text("一\n二\n三\n", encoding="utf-8")
        result = self.call("file.read", path="a.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "一\n二\n三")
        self.assertEqual(result["total_lines"], 3)
        self.assertEqual(result["returned_lines"], 3)
        self.assertFalse(result["truncated"])

    def test_file_read_offset_limit_paginates(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)),
                                         encoding="utf-8")
        result = self.call("file.read", path="a.txt", offset=3, limit=4)
        self.assertEqual(result["content"], "line3\nline4\nline5\nline6")
        self.assertEqual(result["total_lines"], 10)

    def test_file_read_rejects_binary(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bin.dat").write_bytes(b"\x00\x01\x02")
        with self.assertRaisesRegex(ValueError, "二进制"):
            self.call("file.read", path="bin.dat")

    def test_file_read_truncates_beyond_32kb(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "big.txt").write_text("x" * (READ_TEXT_LIMIT + 4096),
                                           encoding="utf-8")
        result = self.call("file.read", path="big.txt")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["content"].encode("utf-8")), READ_TEXT_LIMIT)
        self.assertIn("offset/limit", result["note"])

    def test_file_write_creates_and_marks_overwrite(self):
        result = self.call("file.write", path="sub/new.txt", content="你好")
        self.assertTrue(result["ok"])
        self.assertFalse(result["overwritten"])
        self.assertEqual((self.root / "sub" / "new.txt").read_text(encoding="utf-8"), "你好")
        again = self.call("file.write", path="sub/new.txt", content="覆盖")
        self.assertTrue(again["overwritten"])
        self.assertIn("已覆盖", again["note"])
        self.assertEqual((self.root / "sub" / "new.txt").read_text(encoding="utf-8"), "覆盖")

    def test_file_edit_replaces_line_range(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "e.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
        result = self.call("file.edit", path="e.txt", start_line=2, end_line=3,
                           new_text="X\nY\nZ")
        self.assertTrue(result["ok"])
        self.assertEqual((self.root / "e.txt").read_text(encoding="utf-8"),
                         "a\nX\nY\nZ\nd\n")
        self.assertEqual(result["total_lines"], 5)
        with self.assertRaisesRegex(ValueError, "行区间越界"):
            self.call("file.edit", path="e.txt", start_line=2, end_line=99, new_text="x")

    def test_file_str_replace_requires_unique_match(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "s.txt").write_text("alpha beta alpha\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "匹配 2 次"):
            self.call("file.str_replace", path="s.txt", old_str="alpha", new_str="gamma")
        # 唯一性校验失败：文件必须原样未动
        self.assertEqual((self.root / "s.txt").read_text(encoding="utf-8"),
                         "alpha beta alpha\n")
        result = self.call("file.str_replace", path="s.txt", old_str="beta", new_str="B")
        self.assertTrue(result["ok"])
        self.assertEqual((self.root / "s.txt").read_text(encoding="utf-8"),
                         "alpha B alpha\n")

    def test_file_glob_returns_relative_paths(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "b.txt").write_text("", encoding="utf-8")
        (self.root / "top.py").write_text("", encoding="utf-8")
        result = self.call("file.glob", pattern="**/*.py")
        self.assertTrue(result["ok"])
        self.assertEqual(result["matches"], ["pkg/a.py", "top.py"])
        self.assertFalse(result["truncated"])

    def test_file_glob_truncates_at_200(self):
        many = self.root / "many"
        many.mkdir(parents=True)
        for i in range(GLOB_LIMIT + 50):
            (many / f"f{i:03d}.txt").write_text("", encoding="utf-8")
        result = self.call("file.glob", pattern="many/*.txt")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["count"], GLOB_LIMIT)
        self.assertIn("截断", result["note"])

    def test_file_grep_line_hits_and_glob_filter(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "one.py").write_text("def a():\n    pass\ndef b():\n", encoding="utf-8")
        (self.root / "two.txt").write_text("def not_code\n", encoding="utf-8")
        result = self.call("file.grep", pattern=r"^def ")
        self.assertEqual(result["matches"],
                         ["one.py:1:def a():", "one.py:3:def b():", "two.txt:1:def not_code"])
        only_py = self.call("file.grep", pattern=r"^def ", glob="*.py")
        self.assertEqual([m.split(":")[0] for m in only_py["matches"]], ["one.py", "one.py"])

    def test_file_grep_truncates_at_200(self):
        many = self.root / "many"
        many.mkdir(parents=True)
        for i in range(GREP_LIMIT + 50):
            (many / f"f{i:03d}.txt").write_text("needle\n", encoding="utf-8")
        result = self.call("file.grep", pattern="needle")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["count"], GREP_LIMIT)

    def test_path_escape_is_rejected(self):
        self.root.mkdir(parents=True, exist_ok=True)
        outside = self.tmp / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        for args in ({"path": "../outside.txt"}, {"path": str(outside)}):
            with self.assertRaisesRegex(ValueError, "路径越界"):
                self.call("file.read", **args)
        with self.assertRaisesRegex(ValueError, "路径越界"):
            self.call("file.glob", pattern="../**/*.py")
        with self.assertRaisesRegex(ValueError, "路径越界"):
            self.call("shell.run", command="dir", cwd=self.tmp.parent)

    def test_session_bound_to_project_uses_project_root(self):
        project_dir = self.tmp / "project"
        project_dir.mkdir()
        (project_dir / "paper.md").write_text("正文", encoding="utf-8")
        pid = "proj-test-0001"
        self.runtime.sessions.append(self.sid, "user_message",
                                     {"text": "x", "mode": "agent", "agent": "research"})
        # 重新绑定：把 session_created 换成带 project_id 的事件
        self.runtime.sessions.append(self.sid, "session_created", {"project_id": pid})
        with mock.patch("src.project_workspace.get_project",
                        return_value={"id": pid, "name": "p", "path": str(project_dir)}):
            result = self.call("file.read", path="paper.md")
            self.assertEqual(result["content"], "正文")
            with self.assertRaisesRegex(ValueError, "路径越界"):
                self.call("file.read", path="../outside.txt")

    def test_shell_run_success_and_exit_code(self):
        command = f'"{sys.executable}" -c "import sys;print(123);sys.stderr.write(\'456\')"'
        result = self.call("shell.run", command=command)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("123", result["stdout"])
        self.assertIn("456", result["stderr"])
        failed = self.call("shell.run",
                           command=f'"{sys.executable}" -c "import sys;sys.exit(3)"')
        self.assertEqual(failed["exit_code"], 3)

    def test_shell_run_timeout(self):
        command = f'"{sys.executable}" -c "import time;time.sleep(30)"'
        result = self.call("shell.run", command=command, timeout_seconds=2)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    def test_shell_run_denies_dangerous_commands(self):
        for command in ("rm -rf ./x", "del /f /s /q x", "format c:", "shutdown /s"):
            with self.assertRaisesRegex(ValueError, "危险命令"):
                self.call("shell.run", command=command)

    def test_shell_run_deny_normalizes_quoting_spacing_and_separators(self):
        """归一化后逐段匹配：引号混淆、分写标志、&& 拼接、cmd/powershell 内联都要挡住。"""
        for command in (
            "rm -r -f ./build",
            "rm -f -R ./build",
            "rm --recursive --force ./build",
            "del /q /s C:\\temp\\x",
            "rd /s /q C:\\temp",
            "Remove-Item -Recurse -Force .\\build",
            "remove-item -recurse ./dist",
            'cmd /c "del /q /s x"',
            'powershell -NoProfile -Command "Remove-Item -Recurse ./dist"',
            'powershell -c "rd /s /q C:\\temp"',
            "echo ok && rm -rf ./build",
            "echo ok || del /s /q x",
            'r"m" -rf ./build',
            "rm  -r    -f   ./build",
        ):
            with self.subTest(command=command), \
                    self.assertRaisesRegex(ValueError, "危险命令"):
                self.call("shell.run", command=command)

    def test_shell_run_deny_keeps_harmless_commands(self):
        result = self.call("shell.run", command="echo hello")
        self.assertTrue(result["ok"], result)
        self.assertIn("hello", result["stdout"])
        listing = self.call("shell.run", command="dir")
        self.assertTrue(listing["ok"], listing)

    def test_path_rejects_ads_reserved_names_control_chars_and_empty_segments(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sub").mkdir()
        for path in ("a.txt:secret", "sub/a.txt:stream", "CON", "con.txt", "NUL.log",
                     "sub/AUX", "COM1", "lpt9.txt", "bad\x01name"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("file.read", path=path)
        for path in ("a//b.txt", "sub/", "sub\\", "C:relative.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("file.write", path=path, content="x")
        self.assertFalse((self.root / "a").exists())

    def test_absolute_path_inside_root_is_still_allowed(self):
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / "absolute.txt"
        target.write_text("ok", encoding="utf-8")
        result = self.call("file.read", path=str(target))
        self.assertEqual(result["content"], "ok")

    def test_link_escape_is_rejected(self):
        """符号链接/目录联接解析后越界必须拒绝；平台不支持链接时跳过。"""
        self.root.mkdir(parents=True, exist_ok=True)
        outside = self.tmp / "linked-outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside", encoding="utf-8")
        link = self.root / "escape-link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            try:
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError) as exc:
                self.skipTest(f"平台不支持符号链接或目录联接：{exc}")
            if created.returncode != 0:
                self.skipTest("无法创建目录联接："
                              + ((created.stdout or "") + (created.stderr or "")).strip()[:160])
        with self.assertRaisesRegex(ValueError, "路径越界"):
            self.call("file.read", path="escape-link/secret.txt")
        with self.assertRaisesRegex(ValueError, "路径越界"):
            self.call("file.write", path="escape-link/created.txt", content="x")
        self.assertFalse((outside / "created.txt").exists())

    def test_shell_run_output_overflow_written_to_artifacts(self):
        command = f'"{sys.executable}" -c "print(\'z\' * {SHELL_OUTPUT_LIMIT * 3})"'
        result = self.call("shell.run", command=command)
        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        artifact = Path(result["artifact_files"]["stdout"])
        self.assertTrue(artifact.is_file())
        self.assertGreater(artifact.stat().st_size, SHELL_OUTPUT_LIMIT)
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), SHELL_OUTPUT_LIMIT)

    def test_tools_registered_with_expected_capability(self):
        expected = {"file.read": "read", "file.write": "write", "file.edit": "write",
                    "file.str_replace": "write", "file.glob": "read", "file.grep": "read",
                    "shell.run": "write"}
        for name, capability in expected.items():
            self.assertIn(name, self.runtime.tools)
            self.assertEqual(self.runtime.tools[name].capability, capability)
        self.assertEqual(self.runtime.policy.decision("file.read", "read"), "allow")
        self.assertEqual(self.runtime.policy.decision("shell.run", "write"), "ask")


class FileReadPaginationTest(unittest.TestCase):
    """Read pagination stays lossless and bounded, using isolated test data."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        patcher = mock.patch.object(tools_workspace, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = SimpleNamespace(sessions=SessionStore(self.data / "sessions"))
        self.sid = "readpages"
        self.runtime.sessions.append(self.sid, "session_created", {})
        self.root = self.data / "sessions" / self.sid / "workspace"
        self.root.mkdir(parents=True)

    def read(self, path="text.txt", **arguments):
        return tools_workspace._file_read(
            {"path": path, "__session_id": self.sid, "__runtime": self.runtime, **arguments})

    def test_long_single_line_can_continue_at_byte_cursor(self):
        text = "x" * (READ_TEXT_LIMIT + 4096) + "尾部证据😀"
        (self.root / "text.txt").write_bytes(text.encode("utf-8"))
        first = self.read()
        self.assertFalse(first.get("eof", True))
        self.assertTrue(first["truncated"])
        self.assertIn("byte_offset", first["note"])
        second = self.read(byte_offset=first["next_byte_offset"])
        self.assertEqual(first["content"] + second["content"], text)
        self.assertTrue(second["eof"])
        self.assertEqual(second["next_byte_offset"], len(text.encode("utf-8")))

    def test_utf8_byte_pages_never_split_chinese_or_emoji(self):
        text = "中文😀\r\n尾巴\n"
        (self.root / "text.txt").write_bytes(text.encode("utf-8"))
        for limit in (1, 2, 3, 4, 5, 7):
            with self.subTest(limit=limit):
                offset, pieces = 0, []
                for _ in range(50):
                    page = self.read(byte_offset=offset, byte_limit=limit)
                    self.assertGreater(page["next_byte_offset"], offset)
                    self.assertLessEqual(len(page["content"].encode("utf-8")), max(limit, 4))
                    pieces.append(page["content"])
                    offset = page["next_byte_offset"]
                    if page["eof"]:
                        break
                else:
                    self.fail("UTF-8 cursor stopped progressing")
                self.assertEqual("".join(pieces), text)

    def test_byte_mode_rejects_mixed_modes_invalid_ranges_and_continuations(self):
        (self.root / "text.txt").write_bytes("中😀".encode("utf-8"))
        self.read(byte_offset=0, byte_limit=1)  # Exercise cached-page validation too.
        for args in ({"offset": 1, "byte_offset": 0}, {"limit": 1, "byte_limit": 2},
                     {"byte_offset": -1}, {"byte_offset": True}, {"byte_offset": "0"},
                     {"byte_offset": 1}, {"byte_offset": 4}, {"byte_offset": 8},
                     {"byte_limit": 0}, {"byte_limit": READ_TEXT_LIMIT + 1}, {"byte_limit": False}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.read(**args)

    def test_byte_eof_and_empty_file_are_explicit(self):
        (self.root / "text.txt").write_bytes(b"abc")
        page = self.read(byte_offset=3)
        self.assertEqual(page["content"], "")
        self.assertTrue(page["eof"])
        self.assertEqual(page["next_byte_offset"], 3)
        (self.root / "text.txt").write_bytes(b"")
        for args in ({}, {"byte_offset": 0}):
            page = self.read(**args)
            self.assertTrue(page["eof"])
            self.assertEqual(page["total_lines"], 0)
            self.assertEqual(page["returned_lines"], 0)
            self.assertEqual(page["next_byte_offset"], 0)

    def test_line_mode_preserves_newlines_counts_and_raw_byte_cursor(self):
        text = "首行\r\n第二行\r第三行\n\n结尾😀"
        (self.root / "text.txt").write_bytes(text.encode("utf-8"))
        page = self.read(offset=2, limit=2)
        self.assertEqual(page["content"], "第二行\n第三行")
        self.assertEqual(page["total_lines"], 5)
        self.assertEqual(page["returned_lines"], 2)
        self.assertFalse(page["eof"])
        self.assertEqual(page["next_byte_offset"], len("首行\r\n第二行\r第三行\n".encode("utf-8")))
        self.assertEqual(self.read(byte_offset=page["next_byte_offset"])["content"], "\n结尾😀")

    def test_utf8_bom_and_invalid_binary_tail(self):
        (self.root / "text.txt").write_bytes(b"\xef\xbb\xbf" + "正文😀".encode("utf-8"))
        self.assertEqual(self.read()["content"], "正文😀")
        self.assertEqual(self.read(byte_offset=0)["content"], "正文😀")
        for bad in (b"ok\n" + b"x" * 70000 + b"\x00", b"ok\n" + b"x" * 70000 + b"\xff"):
            (self.root / "text.txt").write_bytes(bad)
            with self.assertRaises(ValueError):
                self.read(limit=1)

    def test_utf8_and_crlf_boundaries_between_stream_chunks(self):
        samples = ("x" * (64 * 1024 - 1) + "\r\n最后一行😀",
                   "x" * (64 * 1024 - 1) + "中😀\n末尾",
                   "中" * (READ_TEXT_LIMIT // 3 + 5) + "😀尾")
        for text in samples:
            with self.subTest(tail=text[-20:]):
                (self.root / "text.txt").write_bytes(text.encode("utf-8"))
                first = self.read(limit=1)
                pieces = [first["content"]]
                position = first["next_byte_offset"]
                while not first["eof"]:
                    first = self.read(byte_offset=position)
                    self.assertGreater(first["next_byte_offset"], position)
                    pieces.append(first["content"])
                    position = first["next_byte_offset"]
                self.assertEqual("".join(pieces), text)
                self.assertEqual(first["total_lines"], len(text.splitlines()))

    def test_small_line_pages_match_existing_splitlines_semantics(self):
        for text in ("", "\n", "\n\n", "a\r\nb\rc\n", "一\v二\f三\x85四\u2028五\u2029"):
            (self.root / "text.txt").write_bytes(text.encode("utf-8"))
            expected = text.splitlines()
            for offset in (1, 2, 5, 9):
                for limit in (1, 2, 10):
                    with self.subTest(text=text, offset=offset, limit=limit):
                        page = self.read(offset=offset, limit=limit)
                        selected = expected[offset - 1:offset - 1 + limit]
                        self.assertEqual(page["content"], "\n".join(selected))
                        self.assertEqual(page["returned_lines"], len(selected))
                        self.assertEqual(page["total_lines"], len(expected))

    def test_read_uses_bounded_stream_reads_and_cached_session_metadata(self):
        path = self.root / "text.txt"
        path.write_bytes(b"first\n" + b"long body\n" * 300000)
        original_open = Path.open
        requests = []

        class BoundedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, size=-1):
                requests.append(size)
                if not 0 <= size <= 64 * 1024:
                    raise AssertionError(f"Unbounded file read: {size}")
                return self.stream.read(size)

        def bounded_open(candidate, *args, **kwargs):
            opened = original_open(candidate, *args, **kwargs)
            return BoundedStream(opened) if candidate == path else opened

        with mock.patch.object(Path, "open", bounded_open), \
             mock.patch.object(self.runtime.sessions, "read", side_effect=AssertionError("Full journal reread")):
            page = self.read(limit=1)
        self.assertEqual(page["content"], "first")
        self.assertEqual(page["total_lines"], 300001)
        self.assertTrue(requests)

    def test_second_byte_page_reads_only_its_bounded_slice(self):
        path = self.root / "text.txt"
        path.write_bytes(b"first\n" + b"body line\n" * 30000)
        # Test cache I/O independently of journal support on the test volume.
        signature = mock.patch.object(tools_workspace, '_text_file_signature',
                                      return_value=('verified-stable', str(path)))
        signature.start()
        self.addCleanup(signature.stop)
        first = self.read(limit=1)  # A line read can warm the metadata cache too.
        original_open = Path.open
        read_sizes = []

        class ObservedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, size=-1):
                raw = self.stream.read(size)
                read_sizes.append(len(raw))
                return raw

        def observed_open(candidate, *args, **kwargs):
            opened = original_open(candidate, *args, **kwargs)
            return ObservedStream(opened) if candidate == path else opened

        with mock.patch.object(Path, "open", observed_open):
            page = self.read(byte_offset=first["next_byte_offset"], byte_limit=32)
        self.assertEqual(page["content"], (b"body line\n" * 4)[:32].decode())
        self.assertEqual(page["total_lines"], 30001)
        self.assertLessEqual(sum(read_sizes), 32 + 6)

    def test_metadata_cache_invalidates_after_edit_and_restored_mtime(self):
        import os
        path = self.root / "text.txt"
        original = b"first\n" + b"x" * 70000
        path.write_bytes(original)
        first = self.read(byte_offset=0, byte_limit=8)
        self.assertEqual(first["total_lines"], 2)
        stamp = path.stat()
        path.write_bytes(original[:-1] + b"\x00")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "二进制"):
            self.read(byte_offset=8, byte_limit=8)

    @unittest.skipUnless(os.name == 'nt', 'Windows file-change metadata')
    def test_windows_metadata_clock_collision_does_not_reuse_text_validation(self):
        import ctypes

        path = self.root / 'text.txt'
        original = b'first\n' + b'x' * 70000
        path.write_bytes(original)
        stamp = path.stat()
        query = ctypes.windll.kernel32.GetFileInformationByHandleEx
        real_fstat = os.fstat

        def frozen_stat(descriptor):
            current = real_fstat(descriptor)
            return stamp if (current.st_dev, current.st_ino) == (stamp.st_dev, stamp.st_ino) else current

        def frozen_clock(handle, kind, output, size):
            result = query(handle, kind, output, size)
            if result:
                # Reproduce a real coarse-clock collision without sleeps.
                ctypes.cast(output, ctypes.POINTER(ctypes.c_longlong))[3] = 123
            return result

        with mock.patch.object(tools_workspace.os, 'fstat', side_effect=frozen_stat), \
             mock.patch.object(ctypes.windll.kernel32, 'GetFileInformationByHandleEx', frozen_clock):
            first = self.read(byte_offset=0, byte_limit=8)
            self.assertEqual(first['total_lines'], 2)
            path.write_bytes(original[:-2] + b'\nx')
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(self.read(byte_offset=8, byte_limit=8)['total_lines'], 3)
            path.write_bytes(original[:-1] + b'\x00')
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            with self.assertRaisesRegex(ValueError, '二进制'):
                self.read(byte_offset=8, byte_limit=8)

    @unittest.skipUnless(os.name == 'nt', 'Windows file-change metadata')
    def test_windows_without_journal_rescans_and_rejects_changed_binary_file(self):
        path = self.root / 'text.txt'
        original = b'first\n' + b'x' * 70000
        path.write_bytes(original)
        stamp = path.stat()
        with mock.patch.object(tools_workspace, '_windows_file_usn', return_value=None):
            with path.open('rb') as stream:
                self.assertIsNone(tools_workspace._text_file_signature(path, stream))
            self.read(byte_offset=0, byte_limit=8)
            with mock.patch.object(tools_workspace, '_text_segments', wraps=tools_workspace._text_segments) as scans:
                self.read(byte_offset=8, byte_limit=8)
                scans.assert_called_once()
            path.write_bytes(original[:-1] + b'\x00')
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            with self.assertRaisesRegex(ValueError, '二进制'):
                self.read(byte_offset=8, byte_limit=8)

    @unittest.skipUnless(os.name == 'nt', 'Windows file-change metadata')
    def test_windows_live_journal_marker_controls_cache_availability(self):
        path = self.root / 'text.txt'
        path.write_bytes(b'first\nsecond\n')
        with path.open('rb') as stream:
            signature = tools_workspace._text_file_signature(path, stream)
        self.read(byte_offset=0, byte_limit=4)
        with mock.patch.object(tools_workspace, '_text_segments', wraps=tools_workspace._text_segments) as scans:
            self.read(byte_offset=4, byte_limit=4)
            self.assertEqual(scans.call_count, int(signature is None))

    @unittest.skipUnless(os.name == 'nt', 'Windows file-change metadata')
    def test_windows_usn_versions_and_unavailable_records(self):
        import ctypes
        import msvcrt

        cases = []
        for major, size, offset in ((2, 60, 24), (3, 76, 40)):
            record = bytearray(size)
            record[:4] = size.to_bytes(4, 'little')
            record[4:6] = major.to_bytes(2, 'little')
            record[offset:offset + 8] = (12345).to_bytes(8, 'little', signed=True)
            cases.append((bytes(record), 12345))
            record[offset:offset + 8] = b'\x00' * 8
            cases.append((bytes(record), None))
        cases.extend([(b'', None), (b'\x00' * 60, None),
                      (b'\xff\xff\xff\xff\x02\x00' + b'\x00' * 54, None)])
        for record, expected in cases:
            def query(handle, code, version, version_size, output, capacity, returned, overlapped):
                self.assertEqual(code, 0x900EB)
                ctypes.memmove(output, record, len(record))
                ctypes.cast(returned, ctypes.POINTER(ctypes.c_ulong))[0] = len(record)
                return 1
            with self.subTest(record=record[:8], expected=expected), \
                 mock.patch.object(ctypes.windll.kernel32, 'DeviceIoControl', query), \
                 mock.patch.object(msvcrt, 'get_osfhandle', return_value=123):
                self.assertEqual(tools_workspace._windows_file_usn(mock.Mock()), expected)
        with mock.patch.object(ctypes.windll.kernel32, 'DeviceIoControl', return_value=0), \
             mock.patch.object(msvcrt, 'get_osfhandle', return_value=123):
            self.assertIsNone(tools_workspace._windows_file_usn(mock.Mock()))

    def test_metadata_cache_is_bounded_and_stores_only_line_counts(self):
        for index in range(40):
            name = f"entry-{index}.txt"
            (self.root / name).write_bytes(b"first\nsecond")
            self.read(path=name, byte_offset=0, byte_limit=1)
        cache = getattr(tools_workspace, "_TEXT_METADATA_CACHE", None)
        self.assertIsNotNone(cache)
        self.assertLessEqual(len(cache), 32)
        self.assertTrue(all(type(value) is int for value in cache.values()))


class ScriptedRuntime(AgentRuntime):
    """固定计划的运行时：用于验证 write 工具的审批→执行链路。"""

    def __init__(self, root: Path, steps):
        super().__init__(llm_enabled=False, session_root=root)
        self.steps = steps

    def plan(self, request, mode="plan", agent="research"):
        return self._plan_payload(mode, request, self.steps, agent)


class WorkspaceApprovalIntegrationTest(unittest.TestCase):
    def test_file_write_waits_for_approval_then_persists(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = ScriptedRuntime(Path(d) / "sessions",
                                      [{"tool": "file.write",
                                        "arguments": {"path": "notes/result.txt",
                                                      "content": "实验结论"}}])
            result = runtime.start("记录实验结论", mode="agent")
            self.assertEqual(result["status"], "waiting_approval")
            self.assertEqual(result["pending"]["tool"], "file.write")
            self.assertEqual(result["pending"]["capability"], "write")
            sid = result["session_id"]
            target = DATA_DIR / "sessions" / sid / "workspace" / "notes" / "result.txt"
            self.assertFalse(target.exists())  # 审批前未写入
            resumed = runtime.approve(sid, result["pending"]["id"], "allow")
            self.assertEqual(resumed["status"], "done")
            self.assertEqual(target.read_text(encoding="utf-8"), "实验结论")
            events = runtime.sessions.read(sid)
            self.assertTrue(any(e["type"] == "permission_requested" for e in events))
            self.assertTrue(any(e["type"] == "permission_granted" for e in events))

    def test_read_capability_runs_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = ScriptedRuntime(Path(d) / "sessions",
                                      [{"tool": "file.glob", "arguments": {"pattern": "**/*.py"}}])
            result = runtime.start("列文件", mode="agent")
            self.assertEqual(result["status"], "done")
