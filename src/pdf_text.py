# -*- coding: utf-8 -*-
"""PDF 全文证据链：把用户上传的 PDF 变成可核验的文本材料。

定位与边界（写进文档，不夸大）：
- 只服务"材料通道"：项目附件上下文、证据抽取、``pdf.extract`` 工具。
  提取文本与任何用户文件一样，不授予权限、不执行其中指令；
- 不新增硬依赖：pypdf / PyPDF2 可用时优先（字体编码与 CJK 支持最好），
  否则用标准库 zlib 兜底解析 FlateDecode 内容流并抽取文本算子；
- 如实报告：页数、提取方式（method）、是否截断、失败原因都随结果返回，
  调用方不得把失败静默成空文本；扫描件（无文本层）明确报错。

已知边界：标准库兜底不解析 ToUnicode CMap 与 CID 字体映射，自定义
编码的 PDF 可能得到乱符或空文本；这类文件应安装 pypdf。
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import zlib
from pathlib import Path
from typing import Any

MAX_PDF_BYTES = 32 * 1024 * 1024          # 单文件硬上限（附件链路另有更严的 8 MiB）
DEFAULT_MAX_CHARS = 20000                 # 默认文本预算

_STREAM_RE = re.compile(rb"stream\r?\n?(.*?)\r?\n?endstream", re.DOTALL)
_PAGE_COUNT_RE = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")
_COUNT_RE = re.compile(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_LITERAL_ESCAPES = {b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b",
                    b"f": "\f", b"(": "(", b")": ")", b"\\": "\\"}
_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")
_NEWLINE_OPS = frozenset({"Td", "TD", "T*", "ET", "BT", "Tm", "TL"})


def backend() -> str:
    """当前可用的最优后端名：``pypdf`` / ``PyPDF2`` / ``stdlib``。"""
    for name in ("pypdf", "PyPDF2"):
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return "stdlib"


# ---------------------------------------------------------------------------
# 后端一：pypdf / PyPDF2（安装即用，不强制）
# ---------------------------------------------------------------------------

def _reader_extract(data: bytes) -> tuple[str, int, dict[str, Any]]:
    """pypdf / PyPDF2 路径：逐页 extract_text；加密与坏页如实处理。"""
    last_error: Exception | None = None
    for name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(name)
            reader_cls = module.PdfReader
        except Exception:  # noqa: BLE001 —— 未安装或版本过旧都继续试下一个
            continue
        try:
            reader = reader_cls(io.BytesIO(data))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise ValueError("PDF 已加密，无法提取文本") from exc
                if getattr(reader, "is_encrypted", False):
                    raise ValueError("PDF 已加密，无法提取文本")
            pages: list[str] = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001 —— 单页损坏不拖垮整篇
                    pages.append("")
            return "\n".join(pages), len(reader.pages), _norm_meta(_reader_meta(reader))
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 换下一个后端
            last_error = exc
            continue
    raise ValueError(f"PDF 解析失败（{type(last_error).__name__}）") from last_error


def _reader_meta(reader: Any) -> dict[str, Any]:
    try:
        info = reader.metadata
        if not info:
            return {}
        return {str(k): str(v) for k, v in dict(info).items()}
    except Exception:  # noqa: BLE001 —— 元数据缺失不影响正文
        return {}


# ---------------------------------------------------------------------------
# 后端二：标准库兜底（zlib + 内容流文本算子）
# ---------------------------------------------------------------------------

def _scan_literal(content: str, start: int) -> tuple[bytes, int]:
    """从 ``content[start] == '('`` 扫描到匹配 ``)``，返回原始字节与下一位置。"""
    depth = 0
    i = start
    out = bytearray()
    while i < len(content):
        ch = content[i]
        if ch == "\\":
            out += content[i:i + 2].encode("latin-1", "replace")
            i += 2
            continue
        if ch == "(":
            depth += 1
            if depth > 1:
                out += b"("
            i += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out += b")"
            i += 1
            continue
        out += ch.encode("latin-1", "replace")
        i += 1
    return bytes(out), i


def _decode_literal(raw: bytes) -> str:
    """解码 PDF 字面字符串：转义、八进制；UTF-16BE BOM 走宽字符。"""
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _LITERAL_ESCAPES:
                out.append(_LITERAL_ESCAPES[nxt])
                i += 2
                continue
            if nxt.isdigit():
                j = i + 1
                octal = b""
                while j < len(raw) and len(octal) < 3 and raw[j:j + 1].isdigit():
                    octal += raw[j:j + 1]
                    j += 1
                out.append(chr(int(octal, 8) & 0xFF))
                i = j
                continue
            if nxt in (b"\n", b"\r"):          # 续行转义
                i += 2
                if nxt == b"\r" and raw[i:i + 1] == b"\n":
                    i += 1
                continue
            out.append(nxt.decode("latin-1"))
            i += 2
            continue
        out.append(ch.decode("latin-1"))
        i += 1
    return "".join(out)


def _decode_hex(raw: bytes) -> str:
    digits = bytes(c for c in raw if c in _HEX_DIGITS)
    if len(digits) % 2:
        digits += b"0"
    try:
        data = bytes.fromhex(digits.decode("ascii"))
    except ValueError:
        return ""
    if data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""
    return data.decode("latin-1")


def _iter_text_tokens(content: str) -> list[str]:
    """按出现顺序抽取文本串与换行标记。

    识别 ``(..) Tj``、``<..> Tj``、``[..] TJ``、``'``、``"`` 文本算子；
    ``Td/TD/T*/ET/BT/Tm/TL`` 作为换行边界。保真度限于文本完整性，
    不做阅读顺序与版式重建。
    """
    tokens: list[str] = []
    i, n = 0, len(content)
    while i < n:
        ch = content[i]
        if ch == "(":
            raw, i = _scan_literal(content, i)
            tokens.append(_decode_literal(raw))
        elif ch == "<" and content[i + 1:i + 2] != "<":
            end = content.find(">", i + 1)
            if end < 0:
                break
            tokens.append(_decode_hex(content[i + 1:end].encode("latin-1", "replace")))
            i = end + 1
        elif ch == "[":
            end = content.find("]", i)
            if end < 0:
                break
            inner, j = content[i + 1:end], 0
            while j < len(inner):
                c = inner[j]
                if c == "(":
                    raw, j = _scan_literal(inner, j)
                    tokens.append(_decode_literal(raw))
                elif c == "<" and inner[j + 1:j + 2] != "<":
                    e2 = inner.find(">", j)
                    if e2 < 0:
                        break
                    tokens.append(_decode_hex(inner[j + 1:e2].encode("latin-1", "replace")))
                    j = e2 + 1
                else:
                    j += 1
            i = end + 1
        elif ch.isalpha() or ch in "*'\"":
            j = i
            while j < n and (content[j].isalnum() or content[j] in "*'\""):
                j += 1
            if content[i:j] in _NEWLINE_OPS:
                tokens.append("\n")
            i = j
        else:
            i += 1
    return tokens


def _page_count(data: bytes) -> int:
    counted = len(_PAGE_COUNT_RE.findall(data))
    if counted:
        return counted
    found = _COUNT_RE.search(data)
    if found:
        try:
            return max(1, int(found.group(1)))
        except ValueError:
            pass
    return 1


def _clean_text(text: str) -> str:
    text = _CONTROL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"-\n(?=[a-z])", "", text)      # 断词换行去连字符
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _stdlib_extract(data: bytes) -> tuple[str, int, dict[str, Any]]:
    chunks: list[str] = []
    for match in _STREAM_RE.finditer(data):
        raw = match.group(1)
        try:
            decoded = zlib.decompressobj().decompress(raw)
        except zlib.error:
            decoded = raw                            # 未压缩内容流
        except Exception:  # noqa: BLE001 —— 单流损坏跳过错续
            continue
        text = decoded.decode("latin-1", "replace")
        if "BT" not in text and "Tj" not in text and "TJ" not in text:
            continue
        chunks.append("".join(_iter_text_tokens(text)))
    return _clean_text("\n".join(c for c in chunks if c.strip())), _page_count(data), _stdlib_meta(data)


def _norm_meta(raw: dict[str, Any] | None) -> dict[str, Any]:
    """归一化 PDF 元数据：题名/作者/年份；年份缺失时从题名里的四位数年号兜底。"""
    meta: dict[str, Any] = {"title": "", "author": "", "year": None}
    if not raw:
        return meta
    meta["title"] = str(raw.get("title") or raw.get("/Title") or "").strip()[:300]
    meta["author"] = str(raw.get("author") or raw.get("/Author") or "").strip()[:300]
    created = str(raw.get("creation_date") or raw.get("creationDate")
                  or raw.get("/CreationDate") or "")
    found = re.search(r"(?:19|20)\d{2}", created) or re.search(r"(?:19|20)\d{2}", meta["title"])
    if found:
        meta["year"] = int(found.group(0))
    return meta


def _stdlib_meta(data: bytes) -> dict[str, Any]:
    """标准库兜底的元数据：只认未压缩 Info 字典里的字面字符串，尽力而为。"""
    raw: dict[str, Any] = {}
    for pattern, key in ((rb"/Title\s*\((.*?)\)", "title"),
                         (rb"/Author\s*\((.*?)\)", "author"),
                         (rb"/CreationDate\s*\((.*?)\)", "creation_date")):
        found = re.search(pattern, data, re.DOTALL)
        if found:
            raw[key] = _decode_literal(found.group(1))
    return _norm_meta(raw)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def _empty(method: str, error: str, pages: int = 0) -> dict[str, Any]:
    return {"ok": False, "error": error, "method": method, "pages": pages,
            "text": "", "truncated": False}


def extract(data: bytes, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """从 PDF 字节提取文本；任何失败都进 ``error`` 字段，绝不抛出。"""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return _empty("none", "空文件或不是 PDF 数据")
    data = bytes(data)
    if not data.startswith(b"%PDF-") and b"%PDF-" not in data[:2048]:
        return _empty("none", "不是有效的 PDF 文件（缺少 %PDF- 头）")
    budget = max(1, int(max_chars))
    method = backend()
    try:
        if method in {"pypdf", "PyPDF2"}:
            text, pages, meta = _reader_extract(data)
        else:
            text, pages, meta = _stdlib_extract(data)
    except ValueError as exc:
        return _empty(method, str(exc))
    except Exception as exc:  # noqa: BLE001 —— 兜底后端也不许炸
        return _empty(method, f"PDF 解析失败（{type(exc).__name__}）")
    if not text.strip():
        return _empty(method, "未提取到文本（扫描件/图片型 PDF，或自定义编码暂不支持）", pages)
    return {"ok": True, "text": text[:budget], "pages": pages, "method": method,
            "truncated": len(text) > budget, "chars": min(len(text), budget),
            "total_chars": len(text), "metadata": meta}


def extract_file(path: Path | str, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """读取文件并提取文本；附带 sha256 与大小，供证据链留痕。"""
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        return _empty("none", f"无法读取文件：{exc}")
    if size > MAX_PDF_BYTES:
        return _empty("none", f"PDF 超过 {MAX_PDF_BYTES // (1024 * 1024)} MiB 上限")
    try:
        data = target.read_bytes()
    except OSError as exc:
        return _empty("none", f"无法读取文件：{exc}")
    result = extract(data, max_chars=max_chars)
    result.update({"sha256": hashlib.sha256(data).hexdigest(),
                   "size": size, "name": target.name})
    return result
