"""Evidence graph cache follows persisted literature inputs and image availability."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import service
from src.agents import graph
from src.orchestrator import engine


class EvidenceGraphCacheTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="scizhi-graph-cache-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.ctx = engine.TaskContext("graph-cache", "research_pipeline", {"topic": "Topic"})
        self.ctx.artifacts = {"lit_filter": {"pool": [
            {"id": "W1", "title": "First", "score": 0.9, "referenced_works": ["W2"]},
            {"id": "W2", "title": "Second", "score": 0.5, "referenced_works": []},
        ]}}
        for patcher in (
            mock.patch.object(engine, "CHECKPOINT_DIR", self.root / "checkpoints"),
            mock.patch.object(service, "_tasks", {self.ctx.task_id: self.ctx}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.rendered_inputs = []
        renderer_patch = mock.patch.object(graph, "build_evidence_graph", side_effect=self.render)
        self.renderer = renderer_patch.start()
        self.addCleanup(renderer_patch.stop)

    def render(self, pool, *, topic):
        self.rendered_inputs.append(copy.deepcopy((pool, topic)))
        image = self.root / f"graph-{len(self.rendered_inputs)}.png"
        image.write_bytes(b"offline graph fixture")
        return {"nodes": len(pool), "edges": len(graph.internal_edges(pool)),
                "coverage": 0, "isolated": 0, "file": str(image), "items": [],
                "pool_total": len(pool)}

    def build(self, **kwargs):
        return service.evidence_graph(self.ctx.task_id, **kwargs)

    def test_unchanged_input_reuses_persisted_cache(self):
        first = self.build()
        service._tasks[self.ctx.task_id] = engine.TaskContext.load(self.ctx.task_id)
        cached = self.build()
        self.assertEqual(cached["file"], first["file"])
        self.assertTrue(cached["cached"])
        self.assertEqual(self.renderer.call_count, 1)

    def test_force_renders_new_image_for_unchanged_input(self):
        first = self.build()
        forced = self.build(force=True)
        self.assertNotEqual(forced["file"], first["file"])
        self.assertEqual(self.renderer.call_count, 2)

    def test_drop_invalidates_graph_and_keeps_intervention_history(self):
        self.build()
        service.drop_works(self.ctx.task_id, ["W2"])
        updated = self.build()
        self.assertEqual(updated["nodes"], 1)
        self.assertEqual(updated["edges"], 0)
        self.assertEqual(self.renderer.call_count, 2)
        self.assertEqual(len(self.ctx.artifacts["_history"]), 1)

    def test_equal_sized_input_edits_rebuild_graph(self):
        mutations = (
            lambda pool: pool[0].update(title="Revised title"),
            lambda pool: pool[0].update(score=0.8),
            lambda pool: pool[0]["referenced_works"].clear(),
            lambda pool: pool.reverse(),
            lambda pool: self.ctx.params.update(topic="Revised topic"),
        )
        self.build()
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                previous = self.renderer.call_count
                mutate(self.ctx.artifacts["lit_filter"]["pool"])
                self.build()
                self.assertEqual(self.renderer.call_count, previous + 1)

    def test_empty_pool_clears_old_cache_in_checkpoint(self):
        self.build()
        service.drop_works(self.ctx.task_id, ["W1", "W2"])
        empty = self.build()
        self.assertEqual(empty["nodes"], 0)
        self.assertEqual(empty["file"], "")
        self.assertEqual(empty["pool_total"], 0)
        saved = engine.TaskContext.load(self.ctx.task_id)
        self.assertNotIn("_evidence_graph", saved.artifacts)
        self.assertNotIn("_evidence_graph_signature", saved.artifacts)
        self.assertEqual(self.renderer.call_count, 1)

    def test_legacy_checkpoint_without_signature_does_not_reuse_graph(self):
        first = self.build()
        self.ctx.artifacts.pop("_evidence_graph_signature", None)
        self.ctx.save()
        service._tasks[self.ctx.task_id] = engine.TaskContext.load(self.ctx.task_id)
        self.assertNotEqual(self.build()["file"], first["file"])
        self.assertEqual(self.renderer.call_count, 2)

    def test_missing_image_is_regenerated(self):
        first = self.build()
        Path(first["file"]).unlink()
        self.assertNotEqual(self.build()["file"], first["file"])
        self.assertEqual(self.renderer.call_count, 2)

    def test_directory_cannot_be_used_as_cached_image(self):
        self.build()
        self.ctx.artifacts["_evidence_graph"]["file"] = str(self.root)
        self.build()
        self.assertEqual(self.renderer.call_count, 2)

    def test_cached_graph_validation_rejects_changed_and_empty_pool(self):
        first = self.build()
        self.assertEqual(service._valid_evidence_graph(self.ctx)["file"], first["file"])
        service.drop_works(self.ctx.task_id, ["W1"])
        self.assertIsNone(service._valid_evidence_graph(self.ctx))
        service.drop_works(self.ctx.task_id, ["W2"])
        self.assertIsNone(service._valid_evidence_graph(self.ctx))

    def test_render_uses_snapshot_and_does_not_publish_cache_after_input_change(self):
        def concurrent_render(pool, *, topic):
            self.ctx.artifacts["lit_filter"]["pool"][0]["title"] = "Changed during render"
            self.ctx.artifacts["lit_filter"]["pool"][0]["referenced_works"].clear()
            return self.render(pool, topic=topic)

        self.renderer.side_effect = concurrent_render
        self.build()
        snapshot = self.rendered_inputs[0][0][0]
        self.assertEqual(snapshot["title"], "First")
        self.assertEqual(snapshot["referenced_works"], ["W2"])
        self.assertIsNone(service._valid_evidence_graph(self.ctx))
        self.assertNotIn("_evidence_graph", self.ctx.artifacts)

    def test_signature_ignores_dictionary_insertion_order(self):
        first = [{"id": "W1", "title": "First"}]
        second = [{"title": "First", "id": "W1"}]
        self.assertEqual(service.evidence_graph_signature(first, "Topic"),
                         service.evidence_graph_signature(second, "Topic"))

    def test_equal_length_intervention_changes_poll_revision(self):
        revision = service.artifacts_rev(self.ctx)
        pool = copy.deepcopy(self.ctx.artifacts["lit_filter"]["pool"])
        pool[0]["title"] = "Other"
        engine.intervene(self.ctx, "lit_filter", {"pool": pool})
        self.assertNotEqual(service.artifacts_rev(self.ctx), revision)

    def test_graph_cache_updates_do_not_change_poll_revision(self):
        revision = service.artifacts_rev(self.ctx)
        self.build()
        self.assertEqual(service.artifacts_rev(self.ctx), revision)
        self.build(force=True)
        self.assertEqual(service.artifacts_rev(self.ctx), revision)


if __name__ == "__main__":
    unittest.main()
