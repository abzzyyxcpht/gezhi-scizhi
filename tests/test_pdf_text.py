# -*- coding: utf-8 -*-
"""PDF 全文证据链：提取、附件集成、本地入池与全文抽取。

最小 PDF 夹具在内存/临时目录中构造（未压缩与 FlateDecode 两种内容流、
字面字符串转义、TJ 数组、Info 元数据），两条提取后端（pypdf/PyPDF2 与
标准库兜底）都覆盖；失败路径一律断言"如实报错、不静默成空文本"。
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from src import pdf_text
from src.agents import local_docs

MARK = "Pelican rides a bicycle"


def _build_pdf(text: str = MARK, *, compress: bool = False,
               title: str = "", author: str = "", created: str = "") -> bytes:
    """构造可被 pypdf 与标准库兜底同时解析的最小 PDF。"""
    content = ("BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET").encode("latin-1")
    if compress:
        content = zlib.compress(content)
        length = b"<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>"
    else:
        length = b"<< /Length " + str(len(content)).encode() + b" >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        length + b"\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    info_ref = b""
    if title or author or created:
        objects.append(b"<< /Title (" + title.encode("latin-1") + b") /Author ("
                       + author.encode("latin-1") + b") /CreationDate ("
                       + created.encode("latin-1") + b") >>")
        info_ref = b" /Info 6 0 R"
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += (str(i) + " 0 obj\n").encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += ("xref\n0 " + str(len(objects) + 1) + "\n").encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode()
            + b" /Root 1 0 R" + info_ref + b" >>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF\n")
    return bytes(out)


class StdlibFallbackTests(unittest.TestCase):
    """标准库兜底后端：不依赖 pypdf 时的确定性行为。"""

    def test_plain_stream_extracts_text_and_page_count(self):
        text, pages, _meta = pdf_text._stdlib_extract(_build_pdf())
        self.assertIn(MARK, text)
        self.assertEqual(1, pages)

    def test_flate_stream_is_decompressed(self):
        text, pages, _meta = pdf_text._stdlib_extract(_build_pdf(compress=True))
        self.assertIn(MARK, text)
        self.assertEqual(1, pages)

    def test_escapes_and_hex_strings_are_decoded(self):
        content = (b"BT /F1 12 Tf 72 720 Td (a\\(b\\)c\\\\d \\101\\102) Tj "
                   b"<48544B> Tj ET")
        text, _pages, _meta = pdf_text._stdlib_extract(
            b"%PDF-1.4\n<< /Length 99 >>\nstream\n" + content + b"\nendstream\n%%EOF")
        self.assertIn("a(b)c\\d AB", text)
        self.assertIn("HTK", text)

    def test_tj_array_concatenates_string_elements(self):
        content = b"BT /F1 12 Tf 72 720 Td [(Hello) -120 (World)] TJ ET"
        text, _pages, _meta = pdf_text._stdlib_extract(
            b"%PDF-1.4\n<< /Length 99 >>\nstream\n" + content + b"\nendstream\n%%EOF")
        self.assertIn("HelloWorld", text)

    def test_page_count_falls_back_to_pages_count_entry(self):
        self.assertEqual(3, pdf_text._page_count(b"%PDF-1.4\n<< /Type /Pages /Count 3 >>\n%%EOF"))
        self.assertEqual(1, pdf_text._page_count(b"%PDF-1.4\n%%EOF"))

    def test_garbage_yields_empty_text_not_exception(self):
        text, pages, _meta = pdf_text._stdlib_extract(b"not a pdf at all")
        self.assertEqual("", text)
        self.assertEqual(1, pages)


class ExtractTests(unittest.TestCase):
    """统一入口：后端选择、截断、元数据与失败路径。"""

    def test_extract_reports_method_pages_and_text(self):
        result = pdf_text.extract(_build_pdf())
        self.assertTrue(result["ok"], result)
        self.assertIn(MARK, result["text"])
        self.assertEqual(1, result["pages"])
        self.assertIn(result["method"], {"pypdf", "PyPDF2", "stdlib"})
        self.assertFalse(result["truncated"])

    def test_extract_handles_flate_stream(self):
        result = pdf_text.extract(_build_pdf(compress=True))
        self.assertTrue(result["ok"], result)
        self.assertIn(MARK, result["text"])

    def test_extract_reads_info_metadata(self):
        result = pdf_text.extract(_build_pdf(title="Pelican Paper",
                                             author="Ada Lovelace",
                                             created="D:20240115120000Z"))
        meta = result.get("metadata") or {}
        self.assertEqual("Pelican Paper", meta.get("title"))
        self.assertEqual("Ada Lovelace", meta.get("author"))
        self.assertEqual(2024, meta.get("year"))

    def test_truncation_is_reported_not_silent(self):
        result = pdf_text.extract(_build_pdf(), max_chars=10)
        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        self.assertEqual(10, len(result["text"]))
        self.assertGreater(result["total_chars"], 10)

    def test_non_pdf_empty_and_header_only_fail_honestly(self):
        for name, payload in (("garbage", b"not a pdf"), ("empty", b""),
                              ("header", b"%PDF-1.4\n"), ("none", None)):
            with self.subTest(name=name):
                result = pdf_text.extract(payload)
                self.assertFalse(result["ok"])
                self.assertTrue(result["error"])
                self.assertEqual("", result["text"])

    def test_extract_file_missing_file_reports_error(self):
        result = pdf_text.extract_file("/no/such/file.pdf")
        self.assertFalse(result["ok"])
        self.assertIn("无法读取", result["error"])

    def test_extract_file_reports_sha_size_and_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.pdf"
            payload = _build_pdf()
            path.write_bytes(payload)
            result = pdf_text.extract_file(path)
            self.assertTrue(result["ok"], result)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])
            self.assertEqual(len(payload), result["size"])
            self.assertEqual("paper.pdf", result["name"])
            self.assertIn(MARK, result["text"])


class LocalDocsTests(unittest.TestCase):
    """本地材料入池：PDF/文本读取、跳过明细、去重合并与工作流动作。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(_build_pdf(title="Pelican Paper", created="D:20240101000000Z"))
        self.note = self.root / "notes.md"
        self.note.write_text("# 研究笔记\nWe propose a reading method.\n", encoding="utf-8")
        self.bin = self.root / "data.bin"
        self.bin.write_bytes(b"\x00\xff\x00")

    def test_load_entries_reads_pdf_and_text_with_full_text(self):
        entries, skipped = local_docs.load_entries([str(self.pdf), str(self.note)])
        self.assertEqual([], skipped)
        self.assertEqual(2, len(entries))
        pdf_entry = next(e for e in entries if e["source"] == "local_pdf")
        self.assertEqual("Pelican Paper", pdf_entry["title"])
        self.assertEqual(2024, pdf_entry["year"])
        self.assertEqual(1, pdf_entry["pages"])
        self.assertIn(MARK, pdf_entry["full_text"])
        self.assertIsNone(pdf_entry["score"])          # 用户断言相关性，不参与检索评分
        self.assertEqual("保留", pdf_entry["decision"])
        text_entry = next(e for e in entries if e["source"] == "local_text")
        self.assertIn("reading method", text_entry["full_text"])

    def test_load_entries_reports_skips_without_raising(self):
        entries, skipped = local_docs.load_entries(
            [str(self.root / "missing.pdf"), str(self.bin), "", 42, str(self.pdf), str(self.pdf)])
        self.assertEqual(1, len(entries))
        reasons = {row["reason"] for row in skipped}
        self.assertIn("文件不存在", reasons)
        self.assertIn("格式不支持（仅 PDF 与 UTF-8 文本/Markdown）", reasons)
        self.assertIn("与本批材料重复", reasons)

    def test_load_entries_caps_batch_at_max_docs(self):
        entries, _skipped = local_docs.load_entries([str(self.pdf)] * (local_docs.MAX_DOCS + 5))
        self.assertLessEqual(len(entries), local_docs.MAX_DOCS)

    def test_merge_into_pool_dedups_by_id_and_doi(self):
        pool = [{"id": "local:abc", "doi": ""}, {"id": "x", "doi": "10.1/x"}]
        merged = local_docs.merge_into_pool(pool, [{"id": "local:abc", "doi": ""},
                                                   {"id": "local:def", "doi": ""}])
        self.assertEqual(3, len(merged))

    def test_ingest_action_merges_into_task_pool(self):
        from src.orchestrator.engine import TaskContext
        ctx = TaskContext("localdocs01", "research_pipeline", {"topic": "pelican"})
        result = local_docs.ingest_local_docs(ctx, {"paths": [str(self.pdf), str(self.bin)]})
        self.assertEqual(1, result["pool_size"])
        self.assertEqual(1, len(result["ingested"]))
        self.assertEqual(1, len(result["skipped"]))
        pool = ctx.artifacts["lit_filter"]["pool"]
        self.assertEqual("local_pdf", pool[0]["source_type"])
        self.assertIn(MARK, pool[0]["full_text"])
        self.assertGreater(result["full_text_chars"], 0)

    def test_ingest_action_without_paths_reports_zero(self):
        from src.orchestrator.engine import TaskContext
        ctx = TaskContext("localdocs02", "research_pipeline", {"topic": "t"})
        result = local_docs.ingest_local_docs(ctx, {})
        self.assertEqual(0, result["pool_size"])
        self.assertTrue(result["skipped"])


class PdfAttachmentTests(unittest.TestCase):
    """附件链路：PDF 预览与全文入上下文，非 PDF 行为不变。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "research"
        self.root.mkdir()
        self.data = self.base / "app-data"
        self.data.mkdir()
        from src import config
        self.patch = patch.object(config, "DATA_DIR", self.data)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        from src import project_workspace
        self.api = project_workspace
        self.project = self.api.handle({"cmd": "project_open", "path": str(self.root)})["project"]
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(_build_pdf())
        (self.root / "binary.bin").write_bytes(b"\x00\xff")
        (self.root / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def test_preview_extracts_pdf_text_with_pages_and_method(self):
        preview = self.api.handle({"cmd": "project_preview", "project_id": self.project["id"],
                                   "path": "paper.pdf"})
        self.assertEqual("pdf", preview["kind"])
        self.assertIn(MARK, preview["content"])
        self.assertEqual(1, preview["pages"])
        self.assertTrue(preview["method"])

    def test_attachment_carries_full_text_and_manifest_provenance(self):
        payload = _build_pdf()
        attachment = self.api.handle({"cmd": "project_attachment",
                                      "project_id": self.project["id"],
                                      "path": "paper.pdf"})["attachment"]
        self.assertEqual("pdf", attachment["kind"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), attachment["sha256"])
        self.assertIn(MARK, attachment["text"])
        self.assertEqual(1, attachment["pages"])
        self.assertFalse(attachment["truncated"])
        context, manifest = self.api.attachment_context(self.project["id"], ["paper.pdf"])
        self.assertIn(MARK, context)
        self.assertEqual(1, manifest[0]["pages"])
        self.assertEqual(attachment["sha256"], manifest[0]["sha256"])

    def test_non_pdf_attachment_behaviour_unchanged(self):
        for path in ("binary.bin", "photo.png"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.api.attachment_context(self.project["id"], [path])

    def test_scanned_like_pdf_without_text_fails_honestly(self):
        (self.root / "scan.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n")
        with self.assertRaises(ValueError) as caught:
            self.api.handle({"cmd": "project_attachment", "project_id": self.project["id"],
                             "path": "scan.pdf"})
        self.assertIn("PDF 文本提取失败", str(caught.exception))


class FullTextExtractionTests(unittest.TestCase):
    """抽取节点消费 full_text：材料范围、出处字段与产物标注。"""

    def _ctx(self, pool):
        from src.orchestrator.engine import TaskContext
        ctx = TaskContext("fulltext01", "research_pipeline",
                          {"topic": "pelican research", "offline": True})
        ctx.artifacts = {"lit_filter": {"pool": pool}}
        return ctx

    def test_full_text_record_marks_scope_and_provenance(self):
        from src.agents import extraction
        work = {"id": "L1", "title": "Pelican Cycles", "authors": ["Ada"], "year": 2024,
                "abstract": "Abstract only.",
                "full_text": "Introduction. We propose a pelican method. "
                             "Results show stable cycling. Conclusion: it works."}
        payload = extraction.extract_pool(self._ctx([work]), {"limit": 5})
        record = payload["records"][0]
        self.assertEqual("fulltext", record["material_scope"])
        self.assertEqual("fulltext", record["source_type"])
        self.assertGreater(record["full_text_chars"], 0)
        self.assertEqual("fulltext", record["provenance"]["method"]["field"])
        self.assertEqual("exact_quote", record["provenance"]["method"]["status"])
        self.assertIn("We propose a pelican method.", record["method"])
        self.assertIn("（全文", payload["digest_md"])

    def test_abstract_only_record_behaviour_unchanged(self):
        from src.agents import extraction
        work = {"id": "L2", "title": "No Full Text", "abstract": "We propose a method. Results show gains."}
        payload = extraction.extract_pool(self._ctx([work]), {"limit": 5})
        record = payload["records"][0]
        self.assertEqual("abstract", record["material_scope"])
        self.assertEqual("abstract_only", record["source_type"])
        self.assertNotIn("full_text_chars", record)
        self.assertEqual("abstract", record["provenance"]["method"]["field"])

    def test_metadata_only_record_behaviour_unchanged(self):
        from src.agents import extraction
        work = {"id": "L3", "title": "Metadata Only"}
        payload = extraction.extract_pool(self._ctx([work]), {"limit": 5})
        record = payload["records"][0]
        self.assertEqual("metadata", record["material_scope"])
        self.assertEqual("metadata_only", record["source_type"])


class PromptFullTextTests(unittest.TestCase):
    """提示词版本化：v3 增加 full_text 材料，无全文时载荷不变。"""

    def test_key_is_v3_and_full_text_reaches_materials(self):
        from src import prompts
        key, msgs = prompts.extract_one("主题", {"title": "T", "abstract": "A",
                                                "full_text": "全文证据句"})
        self.assertEqual("extract_one.v3", key)
        self.assertIn("全文证据句", msgs[1]["content"])
        self.assertIn("fulltext", msgs[1]["content"])

    def test_without_full_text_payload_has_no_fulltext_marker(self):
        from src import prompts
        _key, msgs = prompts.extract_one("主题", {"title": "T", "abstract": "A"})
        self.assertNotIn('"material_scope"', msgs[1]["content"])


if __name__ == "__main__":
    unittest.main()
