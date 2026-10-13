# -*- coding: utf-8 -*-
"""桥 demo_seed / demo_clean 契约测试：预览脚本的动态帧就靠它们注入离线任务。

闭环要求：seed 出的任务要能被 list 看到、处于待人工确认关口、有非空离线池；
clean 之后磁盘与内存都不留痕（否则真实档案里混进演示任务，时间账虚高）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest


def _run_bridge(reqs: list[dict]) -> list[dict]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
    p = subprocess.run([sys.executable, "-m", "src.bridge"], input=payload,
                       text=True, encoding="utf-8", errors="replace",
                       capture_output=True, timeout=120, env=env)
    lines = [line for line in p.stdout.splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines if "ready" not in line]
    assert len(rows) == len(reqs), (lines,)
    return rows


class DemoSeedCleanTest(unittest.TestCase):
    def test_seed_then_clean_roundtrip(self):
        from src.config import CHECKPOINT_DIR

        rows = _run_bridge([
            {"cmd": "demo_seed", "topic": "演示课题"},
            {"cmd": "list"},
            {"cmd": "demo_clean"},
            {"cmd": "list"},
        ])
        seeded, listed, cleaned, listed_after = rows
        self.assertTrue(seeded["ok"], seeded)
        self.assertEqual(seeded["status"], "waiting_human")
        self.assertEqual(seeded["task_id"], "demoprev0001")

        demo_tasks = [t for t in listed["tasks"] if t["task_id"] == "demoprev0001"]
        self.assertEqual(len(demo_tasks), 1)
        self.assertEqual(demo_tasks[0]["status"], "waiting_human")
        self.assertIn("演示课题", demo_tasks[0]["topic"])

        self.assertTrue(cleaned["ok"] and cleaned["cleaned"])
        self.assertFalse([t for t in listed_after["tasks"]
                          if t["task_id"] == "demoprev0001"])
        self.assertFalse((CHECKPOINT_DIR / "demoprev0001.json").exists())

    def test_clean_without_seed_is_ok(self):
        rows = _run_bridge([{"cmd": "demo_clean"}])
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[0]["cleaned"])       # 没有演示任务：幂等返回


if __name__ == "__main__":
    unittest.main()
