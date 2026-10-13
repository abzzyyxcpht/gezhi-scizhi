# -*- coding: utf-8 -*-
"""技能包与 GEZHI.md 研究上下文单元测试（settings/根目录打桩到临时区）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import context_file, config, prompts, settings, skills


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(settings, "_PATH",
                                        Path(self._tmp.name) / "settings.json")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_builtin_discovered(self):
        names = {s["name"] for s in skills.list_skills()}
        self.assertIn("academic-writing", names)
        self.assertIn("lit-critique", names)

    def test_toggle_persists(self):
        active = skills.set_active("academic-writing", True)
        self.assertIn("academic-writing", active)
        self.assertIn("academic-writing", skills.active_names())  # 重新 load 仍在
        skills.set_active("academic-writing", False)
        self.assertNotIn("academic-writing", skills.active_names())

    def test_active_bodies_shape(self):
        skills.set_active("academic-writing", True)
        try:
            bodies = skills.active_bodies()
            self.assertTrue(any(n == "academic-writing" and "学术" in b
                                for n, b in bodies))
        finally:
            skills.set_active("academic-writing", False)

    def test_prompts_build_indexes_active_skill(self):
        skills.set_active("academic-writing", True)
        try:
            msgs = prompts.build("任务内容")
            self.assertIn("- academic-writing：", msgs[0]["content"])
            self.assertIn("skills.load", msgs[0]["content"])  # 引导按需加载正文
            self.assertIn("格致", msgs[0]["content"])  # 全局提示词仍在
            # 渐进式披露：正文不进系统提示，仅索引行
            self.assertNotIn("## 技能包：", msgs[0]["content"])
        finally:
            skills.set_active("academic-writing", False)

    def test_prompts_build_without_skills(self):
        msgs = prompts.build("任务内容")
        self.assertNotIn("技能索引", msgs[0]["content"])

    def test_stage_selection_and_explicit_choice_have_deterministic_order(self):
        skills.set_active("academic-writing", True)
        one = skills.select_for_stage("audit")
        two = skills.select_for_stage("audit")
        self.assertEqual(one, two)
        names = [s["name"] for s in one["selected"]]
        self.assertIn("academic-writing", names)
        self.assertIn("evidence-audit", names)
        self.assertNotIn("experiment-review", names)

    def test_budget_deferral_is_visible_and_does_not_disable_choice(self):
        skills.set_active("academic-writing", True)
        selection = skills.select_for_stage("audit", context_budget=1)
        self.assertEqual(selection["selected"], [])
        self.assertTrue(any(s["name"] == "academic-writing" for s in selection["deferred"]))
        self.assertIn("academic-writing", skills.active_names())
        self.assertLessEqual(selection["budget"]["used_chars"], 1)
        self.assertIn("延期", prompts.build("任务", stage="audit", context_budget=1)[0]["content"])

    def test_unknown_enabled_skill_is_reported(self):
        skills.set_active("missing-skill", True)
        self.assertIn("missing-skill", skills.select_for_stage("planning")["missing"])

    def test_explicit_disable_prevents_stage_autoload(self):
        skills.set_active("evidence-audit", False)
        selected = skills.select_for_stage("audit")["selected"]
        self.assertNotIn("evidence-audit", [s["name"] for s in selected])

    def test_dynamic_request_does_not_change_stable_prefix(self):
        one = prompts.build("材料一", stage="audit")
        two = prompts.build("材料二", stage="audit")
        self.assertEqual(one[0], two[0])
        self.assertNotEqual(one[-1], two[-1])


class SkillProgressiveDisclosureTest(unittest.TestCase):
    """L1 索引 / L2 按需加载（skills.load）的行为测试。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(settings, "_PATH",
                                        Path(self._tmp.name) / "settings.json")
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_load_skill_body_strips_frontmatter(self):
        body = skills.load_skill_body("academic-writing")
        self.assertTrue(body)
        self.assertNotIn("description:", body.split("\n")[0])
        self.assertIn("学术", body)

    def test_load_skill_body_unknown_raises(self):
        with self.assertRaises(ValueError):
            skills.load_skill_body("no-such-skill")

    def test_skills_load_tool_returns_body(self):
        from src.agent_runtime import AgentRuntime
        runtime = AgentRuntime(offline=True, llm_enabled=False,
                               session_root=Path(self._tmp.name) / "sessions")
        spec = runtime.tools["skills.load"]
        self.assertEqual(spec.capability, "read")
        result = spec.handler({"name": "academic-writing"})
        self.assertTrue(result["ok"])
        self.assertIn("已加载技能", result["note"])
        self.assertIn("学术", result["body"])
        missing = spec.handler({"name": "no-such-skill"})
        self.assertFalse(missing["ok"])

    def test_index_line_budget_uses_metadata_only(self):
        line = skills.index_line({"name": "academic-writing", "description": "测试描述"})
        self.assertEqual(line, "- academic-writing：测试描述")
        selection = skills.select_for_stage("audit", context_budget=80)
        # 索引行仅几十字符，远小于正文；预算口径必须按行而非按正文
        for item in selection["selected"]:
            self.assertLessEqual(item["cost_chars"], 200)


class ContextFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p1 = mock.patch.object(config, "ROOT_DIR", Path(self._tmp.name))
        self._p2 = mock.patch.object(config, "DATA_DIR",
                                     Path(self._tmp.name) / "data")
        self._p3 = mock.patch.object(context_file, "_cwd",
                                     lambda: Path(self._tmp.name) / "work")
        self._p1.start()
        self._p2.start()
        self._p3.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        self._p3.stop()
        self._tmp.cleanup()

    def test_status_not_found_when_absent(self):
        st = context_file.status()
        self.assertFalse(st["found"])
        self.assertEqual(st["bytes"], 0)

    def test_init_template_and_reload(self):
        p = context_file.init_template()
        self.assertTrue(p.exists())
        st = context_file.status()
        self.assertTrue(st["found"])
        self.assertIn("研究方向", " ".join(st["sections"]))
        with self.assertRaises(FileExistsError):
            context_file.init_template()

    def test_load_body_truncated_to_2000(self):
        p = Path(self._tmp.name) / "GEZHI.md"
        p.write_text("## 术语表\n" + "字" * 3000, encoding="utf-8")
        self.assertLessEqual(len(context_file.load_body()), 2000)

    def test_prompts_build_merges_context(self):
        p = Path(self._tmp.name) / "GEZHI.md"
        p.write_text("## 术语表\n检索增强生成 = RAG\n", encoding="utf-8")
        msgs = prompts.build("任务")
        self.assertIn("GEZHI.md", msgs[0]["content"])
        self.assertIn("检索增强生成 = RAG", msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()
