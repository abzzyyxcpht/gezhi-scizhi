"""通用工作台工具集：在用户工作目录中读写文件、搜索内容和执行命令。

注册方式与 research_notebook_tools 一致：``register_tools(runtime)``。
运行时负责权限审批（read=allow、write=ask）与审计日志；本模块只实现
文件/命令的具体语义，并强制所有路径落在会话绑定的项目根内。

路径边界：会话优先绑定项目根（session_created 事件的 project_id）；
未绑定时回退到 GEZHI_DATA_DIR/sessions/<sid>/workspace（自动创建）。
所有 file.* 与 shell.run 的路径/cwd 都会解析为绝对路径并校验位于项目根内，
越界抛 ValueError("路径越界")，不执行任何操作。

shell.run 选择 shell=True 直接执行用户命令字符串：科研工作目录的命令
（dir/echo/激活环境等内建命令）在 Windows 上只有经 shell 才能运行；
shell=False 无法跨平台解析这些输入。防注入由三层兜底：capability=write
默认 ask 审批、危险命令 deny 名单在审批前直接拒绝、stdout/stderr 有界截断。
"""
from __future__ import annotations

import fnmatch
import codecs
import threading
from collections import OrderedDict
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DATA_DIR

if TYPE_CHECKING:
    from .agent_runtime import AgentRuntime

READ_TEXT_LIMIT = 32 * 1024          # file.read 单文件最多返回的文本字节数
SHELL_OUTPUT_LIMIT = 8 * 1024        # shell.run 单路输出截断阈值
GLOB_LIMIT = 200                     # file.glob 最多返回的条目数
GREP_LIMIT = 200                     # file.grep 最多返回的匹配行数
SHELL_TIMEOUT_DEFAULT = 30           # shell.run 默认超时（秒）
SHELL_TIMEOUT_MAX = 120              # shell.run 超时上限（秒）

# 危险命令 deny 名单：命中即拒绝执行，不进入审批流程。均为大小写不敏感的
# 正则，针对的是"破坏性/不可恢复"语义（递归删除、格式化、强杀进程等）。
_DENY_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-[a-z]*r[a-z]*f", r"\brm\s+-[a-z]*f[a-z]*r",
    r"\brm\s+-rf\b", r"\brm\s+-fr\b", r"\brm\s+--recursive",
    r"\bformat\s+[a-z]:", r"\bmkfs(?:\.[a-z0-9]+)?\b", r"\bdd\s+.*\bof=/dev/",
    r"\bdel(?:ete)?\s+/[a-z]*[fs]", r"\berase\s+/[a-z]*[fs]", r"\brmdir\s+/s",
    r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b",
    r":\(\)\s*\{\s*:\|:\s*;\s*\};:",
    r"\bgit\s+push\s+--force\b", r"\bgit\s+push\s+-f\b",
)
_DENY_RES = tuple(re.compile(p, re.IGNORECASE) for p in _DENY_PATTERNS)


def _object(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def _resolve_root(runtime: AgentRuntime, session_id: str) -> Path:
    """返回会话工作根：优先项目绑定，未绑定时用会话工作区目录。"""
    root: Path | None = None
    if session_id:
        project_id = str(runtime.sessions.snapshot(session_id).get("project_id") or "")
        if project_id:
            from . import project_workspace
            try:
                root = Path(project_workspace.get_project(project_id)["path"])
            except ValueError:
                root = None  # 项目已被移除：回退到会话工作区，不阻断工具
    if root is None:
        root = DATA_DIR / "sessions" / (session_id or "default") / "workspace"
        root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"工作根不是目录：{root}")
    return root


def _resolve_in_root(root: Path, raw: Any, *, label: str = "路径") -> Path:
    """把用户给的相对/绝对路径解析为绝对路径并校验位于 root 内。"""
    text = str(raw or "").strip()
    if not text or "\x00" in text:
        raise ValueError(f"{label}无效")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if path != root and not path.is_relative_to(root):
        raise ValueError(f"路径越界：{text}")
    return path


def _read_text(path: Path) -> str:
    """读取 UTF-8 文本；二进制（含 NUL 字节）与非 UTF-8 内容一律拒绝。"""
    with path.open("rb") as stream:
        raw = stream.read()
    if b"\x00" in raw[:64 * 1024]:
        raise ValueError("二进制文件无法按文本读取")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是有效的 UTF-8 文本，拒绝读取") from exc
    # Windows 上统一换行为 \n，保证编辑/替换的往返一致（写回用 newline="\n"）
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_text(path: Path, text: str) -> None:
    """以 UTF-8 写回文本；newline=\"\\n\" 禁止 OS 换行翻译，避免 \\r\\r\\n 污染。"""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_TEXT_METADATA_CACHE: OrderedDict[tuple, int] = OrderedDict()
_TEXT_METADATA_LOCK = threading.Lock()


def _windows_file_usn(stream) -> int | None:
    """Read a journal sequence, or disable caching when no reliable marker exists."""
    import ctypes
    import msvcrt

    query = ctypes.windll.kernel32.DeviceIoControl
    query.argtypes = (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                      ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p)
    query.restype = ctypes.c_int
    versions = (ctypes.c_ushort * 2)(2, 3)
    output = ctypes.create_string_buffer(4096)
    returned = ctypes.c_ulong()
    if not query(msvcrt.get_osfhandle(stream.fileno()), 0x900EB,
                 ctypes.byref(versions), ctypes.sizeof(versions), output, len(output),
                 ctypes.byref(returned), None):
        return None
    data = output.raw[:returned.value]
    major = int.from_bytes(data[4:6], 'little')
    minimum, offset = {2: (60, 24), 3: (76, 40)}.get(major, (0, 0))
    if not minimum or not minimum <= int.from_bytes(data[:4], 'little') <= len(data):
        return None
    usn = int.from_bytes(data[offset:offset + 8], 'little', signed=True)
    return usn if usn > 0 else None


def _text_file_signature(path: Path, stream) -> tuple | None:
    """Metadata for cache invalidation, not a cryptographic content identity."""
    stat = os.fstat(stream.fileno())
    signature = (str(path), stat.st_dev, stat.st_ino, stat.st_size,
                 stat.st_mtime_ns, stat.st_ctime_ns)
    if os.name == "nt":
        # Windows st_ctime may mean creation time on some Python versions.
        # ChangeTime has clock granularity and can collide on rapid writes.
        # A journal sequence distinguishes completed writes even at one tick.
        import ctypes
        import msvcrt

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("created", "accessed", "written", "changed")] + [("attributes", ctypes.c_ulong)]

        query = ctypes.windll.kernel32.GetFileInformationByHandleEx
        query.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
        query.restype = ctypes.c_int
        info = BasicInfo()
        if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
            return None  # An unsupported filesystem can still use uncached scans.
        usn = _windows_file_usn(stream)
        if usn is None:
            return None
        signature += (info.changed, usn)
    return signature


def _text_byte_slice(stream, cursor: int, limit: int, size: int) -> tuple[str, int]:
    """Read only this page plus at most three UTF-8 continuation bytes."""
    stream.seek(cursor)
    raw = stream.read(min(size - cursor, limit + 3))
    if b"\x00" in raw:
        raise ValueError("二进制文件无法按文本读取")
    if raw and 0x80 <= raw[0] <= 0xBF:
        raise ValueError("byte_offset 不在 UTF-8 字符边界")
    try:
        decoder = codecs.getincrementaldecoder("utf-8")()
        prefix = raw[:limit]
        text = decoder.decode(prefix, final=len(prefix) == size - cursor)
        # At least one complete character, even if the caller asks for 1 byte.
        for index in range(len(prefix), min(4, len(raw))):
            if text:
                break
            text += decoder.decode(raw[index:index + 1], final=cursor + index + 1 == size)
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是有效的 UTF-8 文本，拒绝读取") from exc
    return text, cursor + len(text.encode("utf-8"))


def _text_segments(stream, start: int):
    """Yield bounded UTF-8 text/newline segments with physical byte offsets.

    Counting all lines and rejecting invalid UTF-8 still require a full scan,
    but neither a huge line nor a huge file is retained in memory. Carry CR
    across blocks so CRLF is always one logical line boundary.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    position, carry = start, ""
    while True:
        raw = stream.read(64 * 1024)
        if b"\x00" in raw:
            raise ValueError("二进制文件无法按文本读取")
        try:
            text = carry + decoder.decode(raw, final=not raw)
        except UnicodeDecodeError as exc:
            raise ValueError("文件不是有效的 UTF-8 文本，拒绝读取") from exc
        carry = ""
        if raw and text.endswith("\r"):
            text, carry = text[:-1], "\r"
        begin = 0
        for match in _LINE_BREAK.finditer(text):
            for part, boundary in ((text[begin:match.start()], False), (match.group(), True)):
                if part:
                    end = position + len(part.encode("utf-8"))
                    yield part, position, end, boundary
                    position = end
            begin = match.end()
        if begin < len(text):
            part = text[begin:]
            end = position + len(part.encode("utf-8"))
            yield part, position, end, False
            position = end
        if not raw:
            break


def _file_read(args: dict[str, Any]) -> dict[str, Any]:
    """Read by lines, or by physical UTF-8 byte cursor (never both).

    Byte mode preserves newline bytes and skips an initial UTF-8 BOM. Its
    budget can grow to at most four bytes when necessary to return one whole
    character, guaranteeing progress even for byte_limit=1 and emoji.
    """
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    path = _resolve_in_root(root, args.get("path"))
    if not path.is_file():
        raise ValueError("文件不存在")
    byte_mode = "byte_offset" in args or "byte_limit" in args
    if byte_mode and ("offset" in args or "limit" in args):
        raise ValueError("offset/limit 与 byte_offset/byte_limit 不能同时使用")
    byte_offset = args.get("byte_offset", 0)
    byte_limit = args.get("byte_limit", READ_TEXT_LIMIT)
    if byte_mode and (type(byte_offset) is not int or byte_offset < 0
                      or type(byte_limit) is not int or not 1 <= byte_limit <= READ_TEXT_LIMIT):
        raise ValueError("byte_offset 须为非负整数，byte_limit 须为 1–32768 的整数")
    offset = max(1, int(args.get("offset") or 1))
    limit = args.get("limit")
    limit_n = int(limit) if limit else None
    if limit_n is not None and limit_n < 0:
        raise ValueError("limit 必须为正整数")
    pieces: list[str] = []
    used = returned = last_selected = 0
    line, breaks, last_boundary, seen = 1, 0, False, False
    truncated, byte_done = False, False
    with path.open("rb") as stream:
        signature = _text_file_signature(path, stream)
        with _TEXT_METADATA_LOCK:
            cached_lines = _TEXT_METADATA_CACHE.get(signature) if signature is not None else None
            if cached_lines is not None:
                _TEXT_METADATA_CACHE.move_to_end(signature)
        size = os.fstat(stream.fileno()).st_size
        bom = 3 if stream.read(3) == codecs.BOM_UTF8 else 0
        if byte_mode and (byte_offset > size or 0 < byte_offset < bom):
            raise ValueError("byte_offset 越界或不在 UTF-8 字符边界")
        cursor = max(byte_offset, bom) if byte_mode else size
        previous_break = (bom, bom)
        stream.seek(bom)
        cached_byte_page = byte_mode and cached_lines is not None
        if cached_byte_page:
            text, cursor = _text_byte_slice(stream, cursor, byte_limit, size)
            pieces.append(text)
        segments = () if cached_byte_page else _text_segments(stream, bom)
        for text, start, end, boundary in segments:
            seen, last_boundary = True, boundary
            if byte_mode and not byte_done and end > cursor:
                raw = text.encode("utf-8")[max(0, cursor - start):]
                if raw and 0x80 <= raw[0] <= 0xBF:
                    raise ValueError("byte_offset 不在 UTF-8 字符边界")
                remaining = byte_limit - used
                part = raw[:remaining].decode("utf-8", errors="ignore")
                if not part and not used and raw:
                    # The source is strictly validated by the streaming decoder.
                    width = next(n for n in range(1, 5) if raw[:n].decode("utf-8", errors="ignore"))
                    part = raw[:width].decode("utf-8")
                consumed = len(part.encode("utf-8"))
                pieces.append(part)
                used += consumed
                cursor = max(cursor, start) + consumed
                byte_done = consumed < len(raw) or used >= byte_limit
            elif not byte_mode and not truncated and offset <= line and (limit_n is None or line < offset + limit_n):
                if last_selected != line:
                    if returned:
                        if used >= READ_TEXT_LIMIT:
                            truncated, cursor = True, previous_break[0]
                        else:
                            pieces.append("\n")
                            used += 1
                    if not truncated:
                        returned += 1
                        last_selected = line
                if not truncated:
                    if boundary:
                        cursor = end  # A completed selected line consumes its separator.
                    else:
                        raw = text.encode("utf-8")
                        part = raw[:READ_TEXT_LIMIT - used].decode("utf-8", errors="ignore")
                        consumed = len(part.encode("utf-8"))
                        pieces.append(part)
                        used += consumed
                        cursor = start + consumed
                        truncated = consumed < len(raw)
            if boundary:
                breaks += 1
                previous_break = (start, end)
                line += 1
        total_lines = cached_lines if cached_byte_page else breaks + int(seen and not last_boundary)
        if signature is not None:
            if _text_file_signature(path, stream) != signature:
                with _TEXT_METADATA_LOCK:
                    _TEXT_METADATA_CACHE.pop(signature, None)
                raise ValueError("读取期间文件已变化，请重新读取")
            with _TEXT_METADATA_LOCK:
                _TEXT_METADATA_CACHE[signature] = total_lines
                _TEXT_METADATA_CACHE.move_to_end(signature)
                while len(_TEXT_METADATA_CACHE) > 32:
                    _TEXT_METADATA_CACHE.popitem(last=False)
    blob = "".join(pieces)
    if byte_mode:
        truncated = cursor < size
        returned = len(blob.splitlines())
    elif truncated:
        returned = len(blob.splitlines())
    note = ("字节模式保留原始换行；用 next_byte_offset 继续。byte_limit 不足一个 UTF-8 字符时，"
            "至少返回一个完整字符（最多 4 字节）。") if byte_mode else (
        "内容超出 32KB，已截断；可用 offset/limit 按行读取，超长行请用 byte_offset=next_byte_offset 继续。"
        if truncated else "")
    return {"ok": True, "path": path.relative_to(root).as_posix(),
            "content": blob, "offset": None if byte_mode else offset,
            "returned_lines": returned, "total_lines": total_lines,
            "truncated": truncated, "eof": cursor >= size,
            "next_byte_offset": cursor, "byte_offset": byte_offset if byte_mode else None,
            "note": note}


def _file_write(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    path = _resolve_in_root(root, args.get("path"))
    content = str(args.get("content") or "")
    existed = path.exists()
    if existed and not path.is_file():
        raise ValueError("目标已存在且不是普通文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content)
    return {"ok": True, "path": path.relative_to(root).as_posix(),
            "overwritten": existed, "bytes": len(content.encode("utf-8")),
            "note": "目标已存在，已覆盖原内容" if existed else ""}


def _file_edit(args: dict[str, Any]) -> dict[str, Any]:
    """行区间替换：start_line/end_line 为 1-based 闭区间，替换为 new_text。"""
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    path = _resolve_in_root(root, args.get("path"))
    if not path.is_file():
        raise ValueError("文件不存在")
    start = int(args.get("start_line") or 0)
    end = int(args.get("end_line") or 0)
    new_text = str(args.get("new_text") or "")
    lines = _read_text(path).splitlines()
    if not 1 <= start <= end <= len(lines):
        raise ValueError(f"行区间越界：文件共 {len(lines)} 行，收到 {start}-{end}")
    updated = lines[:start - 1] + new_text.splitlines() + lines[end:]
    _write_text(path, "\n".join(updated) + "\n")
    return {"ok": True, "path": path.relative_to(root).as_posix(),
            "total_lines": len(updated), "replaced_lines": end - start + 1}


def _file_str_replace(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    path = _resolve_in_root(root, args.get("path"))
    if not path.is_file():
        raise ValueError("文件不存在")
    old = str(args.get("old_str") or "")
    new = str(args.get("new_str") or "")
    if not old:
        raise ValueError("old_str 不能为空")
    text = _read_text(path)
    count = text.count(old)
    if count != 1:
        raise ValueError(f"old_str 在文件中匹配 {count} 次（要求唯一匹配），未写入")
    _write_text(path, text.replace(old, new, 1))
    return {"ok": True, "path": path.relative_to(root).as_posix(), "replacements": 1}


def _file_glob(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    pattern = str(args.get("pattern") or "").strip()
    if not pattern or "\x00" in pattern:
        raise ValueError("pattern 无效")
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        raise ValueError("路径越界：pattern 必须是项目内相对模式")
    matches = sorted(p for p in root.glob(pattern) if p.is_file())
    truncated = len(matches) > GLOB_LIMIT
    rel = [p.relative_to(root).as_posix() for p in matches[:GLOB_LIMIT]]
    return {"ok": True, "matches": rel, "count": len(rel), "truncated": truncated,
            "note": f"超过 {GLOB_LIMIT} 条已截断" if truncated else ""}


def _iter_grep_files(base: Path, glob_filter: str):
    """按 glob 过滤（可选）产出待搜索的文件，跳过隐藏目录与符号链接。"""
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            if glob_filter and not fnmatch.fnmatch(path.relative_to(base).as_posix(),
                                                   glob_filter):
                continue
            yield path


def _file_grep(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args["__runtime"], str(args.get("__session_id") or ""))
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("pattern 不能为空")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"正则表达式无效：{exc}") from exc
    base = _resolve_in_root(root, args.get("path") or ".")
    if not base.exists():
        raise ValueError("搜索路径不存在")
    glob_filter = str(args.get("glob") or "").strip()
    hits: list[str] = []
    truncated = False
    for path in _iter_grep_files(base, glob_filter):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:64 * 1024]:
            continue  # 二进制文件跳过（ripgrep 默认行为）
        text = raw.decode("utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{rel}:{lineno}:{line}")
                if len(hits) >= GREP_LIMIT:
                    truncated = True
                    break
        if truncated:
            break
    return {"ok": True, "matches": hits, "count": len(hits), "truncated": truncated,
            "note": f"超过 {GREP_LIMIT} 条已截断，请缩小 pattern/path/glob" if truncated else ""}


def _shell_run(args: dict[str, Any]) -> dict[str, Any]:
    runtime = args["__runtime"]
    session_id = str(args.get("__session_id") or "")
    root = _resolve_root(runtime, session_id)
    command = str(args.get("command") or "").strip()
    if not command or "\x00" in command:
        raise ValueError("command 无效")
    for deny in _DENY_RES:
        if deny.search(command):
            raise ValueError(f"命中危险命令 deny 名单（{deny.pattern}），拒绝执行")
    cwd = _resolve_in_root(root, args.get("cwd") or ".", label="cwd")
    if not cwd.is_dir():
        raise ValueError("cwd 不是目录")
    timeout = args.get("timeout_seconds")
    timeout = SHELL_TIMEOUT_DEFAULT if timeout in (None, "") else int(timeout)
    if not 1 <= timeout <= SHELL_TIMEOUT_MAX:
        raise ValueError(f"timeout_seconds 须在 1–{SHELL_TIMEOUT_MAX} 之间")
    from .shell_execution import run_command
    from .tool_output_runtime import output_store as store_for
    from .workbench import redact
    callback = args.get('__on_output')
    call_id = str(args.get('__tool_call_id') or uuid.uuid4().hex)
    live = {'characters': 0, 'folded': False, 'finished': False}
    live_lock = threading.Lock()

    def on_output(channel, text):
        with live_lock:
            if live['finished']:
                return
            if args.get('__tool_call_id') and session_id:
                room = 16000-live['characters']
                value = text[:max(0, room)]
                live['characters'] += len(value)
                if len(text) > room and not live['folded']:
                    value += '\n（实时记录已折叠，结束后读取已保存输出）\n'
                    live['folded'] = True
                if value:
                    runtime.sessions.append(session_id, 'shell_output', {
                        'tool_call_id': call_id, 'stream': channel, 'text': value, 'partial': True})
        if callback:
            callback(channel, text)

    execution = run_command(command, cwd, timeout=timeout, cancel_event=args.get('__cancel_event'),
        cancelled=lambda: bool(args.get('__tool_call_id') and session_id
                               and (runtime.sessions.root/f'{session_id}.cancel').exists()),
        on_output=on_output)
    with live_lock:
        live['finished'] = True
    timed_out, cancelled = execution['timed_out'], execution['cancelled']
    returncode = execution['exit_code']
    output_store = store_for(runtime.sessions.root)
    output_refs, output_errors = {}, {}
    truncated = False
    stdout = stderr = ''
    artifacts: dict[str, str] = {}
    for name, captured in execution['streams'].items():
        blob = captured['text']
        if len(blob.encode("utf-8")) > SHELL_OUTPUT_LIMIT or captured['truncated']:
            truncated = True
            try:
                ref = output_store.publish(session_id, call_id, 'shell.run', blob, kind='text')
                output_refs[name] = ref
                # Compatibility for existing local consumers; model and UI
                # readers use the opaque reference, never this filesystem path.
                artifacts[name] = str(output_store.root / session_id / (ref['output_id'] + '.txt'))
            except Exception as exc:
                output_errors[name] = str(redact(str(exc)))[:300]
        blob = captured['preview']
        if name == "stdout":
            stdout = blob
        else:
            stderr = blob
    result: dict[str, Any] = {"ok": not (timed_out or cancelled or execution['error']) and execution['tree_empty'], "command": command,
                              "cwd": cwd.relative_to(root).as_posix(),
                              "exit_code": returncode, "stdout": stdout,
                              "stderr": stderr, "timeout": timeout,
                              "truncated": truncated, 'timed_out': timed_out, 'cancelled': cancelled,
                              'tree_empty': execution['tree_empty'], 'capture_complete': execution['capture_complete'],
                              'elapsed_seconds': execution['elapsed_seconds'],
                              'capture': {name: {key: value for key, value in row.items() if key not in {'text','preview'}}
                                          for name, row in execution['streams'].items()}}
    if artifacts:
        result["artifact_files"] = artifacts
        result['output_refs'] = output_refs
        result["note"] = ('已保存部分输出（捕获预算或编码限制），用 tool_output.read/search 或工具输出面板读取；不要当作完整结果。'
                          if not execution['capture_complete'] else
                          '输出预览有界；完整已脱敏正文用 tool_output.read/search 或工具输出面板读取，不必重跑命令。')
    if output_errors:
        result.update(output_unavailable=True, output_errors=output_errors,
            note='部分完整输出未能归档；当前只有预览，不应自动重跑有副作用的命令。')
    if timed_out:
        result["error"] = f"命令超时（{timeout}s），已请求停止拥有的进程树并保留已采集输出"
    if cancelled:
        result['error'] = '命令已取消，保留已采集输出'
    if execution['error'] or not execution['tree_empty']:
        result['error'] = execution['error'] or '进程树尚未确认全部退出'
    return result


def register_tools(runtime: AgentRuntime) -> None:
    """注册 6 个工作台工具；运行时负责审批与审计，本函数不改策略。"""

    def wrap(handler):
        def run(arguments: dict[str, Any]) -> dict[str, Any]:
            args = dict(arguments)
            args["__runtime"] = runtime
            return handler(args)
        return run

    int_or_empty = {"type": "integer", "minimum": 1}
    runtime._register("file.read", "read",
        "读取项目内 UTF-8 文本文件；拒绝二进制；单文件最多返回 32KB，"
        "offset/limit 按行分页，byte_offset/byte_limit 按字节分页，两种参数不能混用。"
        "超长行用 next_byte_offset 继续；字节页保证至少一个完整 UTF-8 字符。",
        wrap(_file_read), _object({"path": {"type": "string"},
                                   "offset": int_or_empty, "limit": int_or_empty,
                                   "byte_offset": {"type": "integer", "minimum": 0},
                                   "byte_limit": {"type": "integer", "minimum": 1,
                                                  "maximum": READ_TEXT_LIMIT}}, ["path"]))
    runtime._register("file.write", "write",
        "向项目内写入文本文件（父目录自动创建）；目标已存在时会覆盖并在结果中标注。"
        "经 write 审批后执行。",
        wrap(_file_write), _object({"path": {"type": "string"},
                                    "content": {"type": "string"}}, ["path", "content"]))
    runtime._register("file.edit", "write",
        "按行区间替换文件内容：start_line/end_line 为 1-based 闭区间，替换为 new_text。"
        "经 write 审批后执行。",
        wrap(_file_edit), _object({"path": {"type": "string"}, "start_line": int_or_empty,
                                   "end_line": int_or_empty, "new_text": {"type": "string"}},
                                  ["path", "start_line", "end_line", "new_text"]))
    runtime._register("file.str_replace", "write",
        "精确字符串替换：old_str 必须在文件中唯一匹配（否则报错并列出匹配次数，不写入）。"
        "经 write 审批后执行。",
        wrap(_file_str_replace), _object({"path": {"type": "string"},
                                          "old_str": {"type": "string"},
                                          "new_str": {"type": "string"}},
                                         ["path", "old_str", "new_str"]))
    runtime._register("file.glob", "read",
        "按 glob 模式（如 **/*.py）列出项目内文件，返回相对路径，最多 200 条。",
        wrap(_file_glob), _object({"pattern": {"type": "string"}}, ["pattern"]))
    runtime._register("file.grep", "read",
        "逐行正则搜索（ripgrep 语义），返回 path:line:内容，最多 200 条；"
        "path 默认项目根，glob 可按相对模式过滤文件。",
        wrap(_file_grep), _object({"pattern": {"type": "string"},
                                   "path": {"type": "string"},
                                   "glob": {"type": "string"}}, ["pattern"]))
    runtime._register("shell.run", "write",
        "在项目根内执行 shell 命令（兼容 Windows 内建命令；"
        "write 审批 + 危险命令 deny 名单双保险）；stdout/stderr 各截断 8KB，"
        "分块采集、可读归档，超量明确标注；取消或超时停止本次命令拥有的进程树。",
        wrap(_shell_run), _object({"command": {"type": "string"},
                                   "cwd": {"type": "string"},
                                   "timeout_seconds": {"type": "integer",
                                                       "minimum": 1,
                                                       "maximum": SHELL_TIMEOUT_MAX}},
                                  ["command"]))
