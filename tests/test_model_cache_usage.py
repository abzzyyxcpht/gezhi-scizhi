"""Provider-cache accounting uses temporary logs, never user call history."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import logging_util as lu


class ModelCacheUsageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model_log = Path(self.temp.name) / "model.jsonl"
        self.tool_log = Path(self.temp.name) / "tool.jsonl"
        for attr, path in (("_MODEL_LOG", self.model_log), ("_TOOL_LOG", self.tool_log)):
            patcher = mock.patch.object(lu, attr, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_rows(self, rows):
        self.model_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    @staticmethod
    def row(**fields):
        return {"node": "review", "model_route": "remote(model-a)",
                "model": "model-a", "protocol": "responses", "connection_id": "one",
                "prompt_tokens": 100, "completion_tokens": 10,
                "latency_ms": 50, "status": "success", **fields}

    def test_weighted_rate_excludes_unknown_input_and_separates_writes(self):
        self.write_rows([
            self.row(prompt_tokens=1000, cached_prompt_tokens=800, cache_write_tokens=100,
                     reasoning_tokens=3, cache_reported=True),
            self.row(prompt_tokens=100, cached_prompt_tokens=0, cache_reported=True),
            self.row(prompt_tokens=9000),
        ])
        model = lu.usage_summary()["model"]
        self.assertEqual(model["prompt_tokens"], 10100)
        self.assertEqual(model["cache_eligible_prompt_tokens"], 1100)
        self.assertEqual(model["cached_prompt_tokens"], 800)
        self.assertEqual(model["cache_write_tokens"], 100)
        self.assertEqual(model["reasoning_tokens"], 3)
        self.assertEqual(model["cache_reported_calls"], 2)
        self.assertEqual(model["cache_unknown_calls"], 1)
        self.assertAlmostEqual(model["cache_hit_rate"], 800 / 1100)
        self.assertAlmostEqual(model["cache_reported_ratio"], 2 / 3)
        self.assertEqual(model["total_tokens"], 10130)

    def test_groups_connection_protocol_and_actual_model_without_collisions(self):
        self.write_rows([
            self.row(connection_id="a|b", protocol="c", model="m"),
            self.row(connection_id="a", protocol="b|c", model="m"),
            self.row(connection_id="a", protocol="b|c", model="other"),
            self.row(connection_id="a", protocol="b|c", model="m", status="truncated"),
        ])
        groups = list(lu.usage_summary()["model"]["by_model"].values())
        self.assertEqual(len(groups), 3)
        target = next(g for g in groups if g["calls"] == 2)
        self.assertEqual((target["connection_id"], target["protocol"], target["model"]),
                         ("a", "b|c", "m"))
        self.assertEqual(target["failures"], 1)
        self.assertEqual(target["by_status"], {"success": 1, "truncated": 1})
        self.assertEqual(target["avg_latency_ms"], 50)
        self.assertEqual(target["route"], "remote(model-a)")

    def test_unknown_zero_and_zero_denominator_remain_distinct(self):
        for fields, expected, reported in [({}, None, 0),
                ({"cached_prompt_tokens": 0, "cache_reported": True}, 0.0, 1),
                ({"prompt_tokens": 0, "cached_prompt_tokens": 0, "cache_reported": True}, None, 1),
                ({"cached_prompt_tokens": 10, "cache_reported": True, "usage_reported": False}, 0.1, 1)]:
            with self.subTest(fields=fields):
                self.write_rows([self.row(**fields)])
                stats = lu.usage_summary()["model"]
                self.assertEqual(stats["cache_hit_rate"], expected)
                self.assertEqual(stats["cache_reported_calls"], reported)

    def test_valid_cached_input_survives_missing_output_usage(self):
        lu.log_model("partial", "remote(m)", 100, 0, 15, "incomplete",
                     cached_prompt_tokens=80, cache_reported=True, usage_reported=False)
        stats = lu.usage_summary()["model"]
        self.assertEqual(stats["cache_hit_rate"], 0.8)
        self.assertEqual(stats["cache_reported_calls"], 1)
        self.assertEqual(stats["usage_unknown_calls"], 1)

    def test_old_logs_keep_totals_but_report_cache_unknown(self):
        self.write_rows([{"node": "old", "model_route": "remote(old)", "prompt_tokens": 15,
                          "completion_tokens": 5, "latency_ms": 20, "status": "success"}])
        stats = lu.usage_summary()["model"]
        self.assertEqual(stats["by_node"]["old"], {"calls": 1, "tokens": 20})
        self.assertIsNone(stats["cache_hit_rate"])
        group = next(iter(stats["by_model"].values()))
        self.assertEqual(group["route"], "remote(old)")
        self.assertEqual(group["cache_unknown_calls"], 1)
        self.assertIn("未报告", lu.format_usage_text())

    def test_malformed_metadata_cannot_fabricate_cache_hits(self):
        malformed = [None, -1, {}, [], True, 1.5, "oops"]
        for bad in malformed:
            with self.subTest(bad=bad):
                self.write_rows([self.row(cached_prompt_tokens=bad, cache_reported=True,
                                          latency_ms=bad, reasoning_tokens=bad)])
                stats = lu.usage_summary()["model"]
                self.assertEqual(stats["cache_unknown_calls"], 1)
                self.assertEqual(stats["cached_prompt_tokens"], 0)
                self.assertIsNone(stats["cache_hit_rate"])
                self.assertGreaterEqual(stats["avg_latency_ms"], 0)
        self.write_rows([self.row(prompt_tokens=-1, completion_tokens=-2, cached_prompt_tokens=1,
                                  cache_reported=True, node={"bad": "node"}),
                         self.row(cached_prompt_tokens=101, cache_reported=True),
                         self.row(cache_reported="true", cached_prompt_tokens=50)])
        stats = lu.usage_summary()["model"]
        self.assertEqual(stats["cache_unknown_calls"], 3)
        self.assertEqual(stats["prompt_tokens"], 200)

    def test_invalid_json_non_object_and_unrecognized_fields_are_safe(self):
        self.write_rows([None, [], 42, "bad", self.row(new_provider_metadata={"x": [1, 2]})])
        with self.model_log.open("a", encoding="utf-8") as stream:
            stream.write('{"unfinished":\n')
        self.tool_log.write_text(json.dumps({"tool": [], "latency_ms": "bad", "status": "failed"}),
                                 encoding="utf-8")
        stats = lu.usage_summary()
        self.assertEqual(stats["model"]["calls"], 1)
        self.assertEqual(stats["tool"]["failures"], 1)
        self.assertIsInstance(lu.format_usage_text(), str)

    def test_missing_and_unreadable_logs_do_not_crash(self):
        self.assertEqual(lu.usage_summary()["model"]["calls"], 0)
        self.write_rows([self.row()])
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("locked")):
            self.assertEqual(lu.usage_summary()["model"]["calls"], 0)
        self.model_log.write_bytes(b'\xff\xfe\n')
        self.assertEqual(lu.usage_summary()["model"]["calls"], 0)

    def test_log_model_preserves_optional_usage_and_unknown_price(self):
        trace = lu.log_model("review", "remote(m)", 100, 10, 20, "success",
                             connection_id="local", protocol="responses", model="m",
                             cached_prompt_tokens=80, cache_write_tokens=5, reasoning_tokens=2,
                             cache_reported=True, usage_reported=True, prompt_fingerprint="sha256:abc")
        record = json.loads(self.model_log.read_text(encoding="utf-8"))
        self.assertEqual(record["trace_id"], trace)
        self.assertEqual(record["cached_prompt_tokens"], 80)
        self.assertEqual(record["prompt_fingerprint"], "sha256:abc")
        self.assertTrue(record["usage_reported"])
        self.assertIsNone(record["cost_cny"])
        self.assertNotIn("prompt", record)
        text = lu.format_usage_text()
        self.assertIn("80.0%", text)
        self.assertIn("local", text)
        self.assertIn("写入 5", text)

    def test_concurrent_model_and_tool_writes_keep_every_complete_line(self):
        def log_one(index):
            lu.log_model("thread", "remote(m)", 1, 1, 1, "success", prompt_key=str(index),
                         route_reason="并发" * 3000)
            lu.log_tool("thread", "example", str(index), "ok", 1, "success")
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(log_one, range(120)))
        records = [json.loads(line) for line in self.model_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 120)
        self.assertEqual({r["prompt_key"] for r in records}, {str(i) for i in range(120)})
        stats = lu.usage_summary()
        self.assertEqual(stats["model"]["calls"], 120)
        self.assertEqual(stats["tool"]["calls"], 120)


if __name__ == "__main__":
    unittest.main()
