"""Deterministic outcome checks: states may only come from recorded facts."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src import outcome_checks as oc


def artifacts(**overrides):
    base = {
        "lit_filter": {"pool": [{"id": "W1", "title": "Paper", "sha256": ""}]},
        "citation": {"entries": [{"title": "Paper", "authors": "A"}]},
        "digest": "摘要正文",
        "review_frame": {"sections": [{"title": "背景"}]},
    }
    base.update(overrides)
    return base


def material(tmp_path: Path, content=b"paper") -> tuple[str, str]:
    path = tmp_path / "paper.pdf"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return str(path), digest


def test_every_check_reports_one_of_the_documented_states():
    report = oc.run(artifacts(), {})
    assert report["total"] == len(oc.ARTIFACT_CHECKS) + 4
    for item in report["items"]:
        assert item["state"] in oc.STATES
    assert report["boundary"]


def test_missing_artifact_fails_with_named_evidence():
    items = oc.build_checklist(artifacts(digest=None), {})
    digest_item = next(item for item in items if item["id"] == "artifact:digest")
    assert digest_item["state"] == "failed"
    assert digest_item["evidence"] == []
    assert "没有该产物" in digest_item["detail"]


def test_material_hash_match_is_satisfied_and_change_makes_it_stale(tmp_path):
    path, digest = material(tmp_path)
    art = artifacts(lit_filter={"pool": [{"id": "W1", "title": "P", "sha256": digest,
                                         "path": path}]})
    ok = next(item for item in oc.build_checklist(art, {}) if item["id"] == "material:integrity")
    assert ok["state"] == "satisfied"
    assert ok["evidence"][0]["sha256"] == digest

    Path(path).write_bytes(b"changed")
    stale = next(item for item in oc.build_checklist(art, {}) if item["id"] == "material:integrity")
    assert stale["state"] == "stale"
    assert stale["evidence"][0]["current_sha256"] != digest


def test_missing_material_file_is_stale_not_satisfied(tmp_path):
    path, digest = material(tmp_path)
    Path(path).unlink()
    art = artifacts(lit_filter={"pool": [{"id": "W1", "title": "P", "sha256": digest,
                                         "path": path}]})
    item = next(row for row in oc.build_checklist(art, {}) if row["id"] == "material:integrity")
    assert item["state"] == "stale"
    assert "不可访问" in item["detail"]


def test_material_without_hash_is_unknown_not_satisfied():
    art = artifacts(lit_filter={"pool": [{"id": "W1", "title": "P"}]})
    item = next(row for row in oc.build_checklist(art, {}) if row["id"] == "material:integrity")
    assert item["state"] == "unknown"


def test_citation_entries_must_carry_the_required_fields():
    art = artifacts(citation={"entries": [{"authors": "A"}]})
    item = next(row for row in oc.build_checklist(art, {}) if row["id"] == "citations:format")
    assert item["state"] == "failed"
    assert item["evidence"][0]["citation"] == "(无标题)"


def test_empty_citation_list_fails():
    art = artifacts(citation={"entries": []})
    item = next(row for row in oc.build_checklist(art, {}) if row["id"] == "citations:format")
    assert item["state"] == "failed"


@pytest.mark.parametrize("style", ["gbt7714", "apa"])
def test_real_reference_formatter_output_passes_citation_check(style):
    from src.citation.gbt7714 import format_references
    from src.orchestrator.engine import TaskContext

    ctx = TaskContext("citation-schema", "research_pipeline", {"cite_style": style})
    ctx.artifacts["lit_filter"] = {"pool": [{
        "id": "W1", "title": "Source title", "authors": ["Jane Doe"],
        "year": 2025, "venue": "Example Journal", "pages": "1-5", "doi": "10.1234/example",
    }]}
    ctx.artifacts["citation"] = format_references(ctx, {})
    entry = ctx.artifacts["citation"]["entries"][0]
    assert "title" not in entry and entry["text"] and entry["source_id"] == "W1"
    item = next(row for row in oc.build_checklist(ctx.artifacts) if row["id"] == "citations:format")
    assert item["state"] == "satisfied"


def test_formatted_reference_with_legacy_id_is_supported():
    art = artifacts(citation={"entries": [{"id": "W1", "text": "[1] Doe J. Source title. 2025."}]})
    item = next(row for row in oc.build_checklist(art) if row["id"] == "citations:format")
    assert item["state"] == "satisfied"


@pytest.mark.parametrize("entry", [
    {}, None, "formatted text", {"text": ""}, {"id": "W1", "text": "   "},
    {"id": "W1", "text": ["not a string"]}, {"text": "formatted text"},
    {"text": "formatted text", "source_id": []}, {"title": []}, {"title": 123},
    {"title": "Paper", "text": "", "id": "W1"},
])
def test_invalid_reference_rows_are_not_silently_ignored(entry):
    art = artifacts(citation={"entries": [{"title": "Valid legacy entry"}, entry]})
    item = next(row for row in oc.build_checklist(art) if row["id"] == "citations:format")
    assert item["state"] == "failed"


def test_formatter_reported_missing_fields_are_visible_as_failed_evidence():
    from src.citation.gbt7714 import format_references
    from src.orchestrator.engine import TaskContext

    ctx = TaskContext("citation-incomplete", "research_pipeline", {"cite_style": "gbt7714"})
    ctx.artifacts["lit_filter"] = {"pool": [{"id": "W1", "title": "Incomplete source"}]}
    ctx.artifacts["citation"] = format_references(ctx, {})
    item = next(row for row in oc.build_checklist(ctx.artifacts) if row["id"] == "citations:format")
    assert item["state"] == "failed"
    assert item["evidence"][0]["citation"] == "W1"
    assert "缺作者" in item["evidence"][0]["message"]


def test_formatter_optional_page_and_doi_advice_does_not_fail_valid_reference():
    from src.citation.gbt7714 import format_references
    from src.orchestrator.engine import TaskContext

    ctx = TaskContext("citation-advice", "research_pipeline", {"cite_style": "gbt7714"})
    ctx.artifacts["lit_filter"] = {"pool": [{"id": "W1", "title": "Source title",
                                            "authors": ["Jane Doe"], "year": 2025,
                                            "venue": "Example Journal"}]}
    ctx.artifacts["citation"] = format_references(ctx, {})
    item = next(row for row in oc.build_checklist(ctx.artifacts) if row["id"] == "citations:format")
    assert item["state"] == "satisfied"
    assert "补全建议" in item["detail"]
    messages = " ".join(row.get("message", "") for row in item["evidence"])
    assert "缺页码" in messages and "缺 DOI" in messages


@pytest.mark.parametrize("missing", ["authors", "year", "venue"])
def test_formatter_required_metadata_gaps_fail_individually(missing):
    from src.citation.gbt7714 import format_references
    from src.orchestrator.engine import TaskContext

    source = {"id": "W1", "title": "Source title", "authors": ["Jane Doe"],
              "year": 2025, "venue": "Example Journal", "pages": "1-5", "doi": "10.1234/example"}
    source.pop(missing)
    ctx = TaskContext("citation-required", "research_pipeline", {"cite_style": "gbt7714"})
    ctx.artifacts["lit_filter"] = {"pool": [source]}
    ctx.artifacts["citation"] = format_references(ctx, {})
    item = next(row for row in oc.build_checklist(ctx.artifacts) if row["id"] == "citations:format")
    assert item["state"] == "failed"
    assert "必需元数据" in item["criterion"]


def test_unrecognized_citation_issue_needs_confirmation():
    art = artifacts(citation={"entries": [{"source_id": "W1", "text": "Formatted reference",
                                          "issues": ["new checker warning"]}]})
    item = next(row for row in oc.build_checklist(art) if row["id"] == "citations:format")
    assert item["state"] == "unknown"
    assert "new checker warning" in item["evidence"][0]["message"]


def test_legacy_title_record_check_describes_its_limited_scope():
    item = next(row for row in oc.build_checklist(artifacts()) if row["id"] == "citations:format")
    assert item["state"] == "satisfied"
    assert "已有字段" in item["detail"]


def test_recorded_deficits_block_the_deficit_check():
    item = next(row for row in oc.build_checklist(artifacts(), {"deficits": [
        {"code": "synthetic_material", "message": "含模拟材料"}]}) if row["id"] == "package:deficits")
    assert item["state"] == "failed"
    assert item["evidence"][0]["code"] == "synthetic_material"


def test_missing_manifest_is_unknown_not_satisfied():
    item = next(row for row in oc.build_checklist(artifacts(), {}) if row["id"] == "package:deficits")
    assert item["state"] == "unknown"


def test_manual_review_requires_a_complete_human_record():
    item = next(row for row in oc.build_checklist(artifacts(), {}, {}) if row["id"] == "review:manual")
    assert item["state"] == "pending"
    partial = next(row for row in oc.build_checklist(
        artifacts(), {}, {"outcome_review": {"by": "张三"}}) if row["id"] == "review:manual")
    assert partial["state"] == "pending"
    assert "复核说明" in partial["detail"] and "记录时间" in partial["detail"]
    done = next(row for row in oc.build_checklist(
        artifacts(), {}, {"outcome_review": {"by": "张三", "note": "已核对引用", "at": "2026-10-22T10:00:00",
                                             "decision": "approved", "revision": oc.run(artifacts(), {})["revision"]}})
        if row["id"] == "review:manual")
    assert done["state"] == "satisfied"
    assert done["evidence"][0]["by"] == "张三"
    assert done["evidence"][0]["note"] == "已核对引用"


def test_a_model_statement_cannot_close_the_manual_review():
    """No model-authored field is accepted as a human confirmation."""
    fake = {"outcome_review": {"by": "assistant", "note": "我认为没问题",
                               "at": "2026-10-22T10:00:00", "model": "gpt-5"}}
    item = next(row for row in oc.build_checklist(artifacts(), {}, fake) if row["id"] == "review:manual")
    assert item["state"] == "pending"
    assert item["kind"] == "manual_review"


def test_verdict_never_claims_success_with_an_open_item():
    report = oc.run(artifacts(), {})
    assert report["verdict"] != "all_satisfied"
    assert report["counts"]["pending"] >= 1
    assert report["satisfied"] < report["total"]


def test_verdict_all_satisfied_only_when_every_item_passes():
    items = [{"id": "x", "state": "satisfied"} for _ in range(3)]
    report = oc.evaluate(items)
    assert report["verdict"] == "all_satisfied"
    assert report["satisfied"] == 3


def test_failed_or_stale_item_yields_needs_review():
    assert oc.evaluate([{"state": "satisfied"}, {"state": "failed"}])["verdict"] == "needs_review"
    assert oc.evaluate([{"state": "satisfied"}, {"state": "stale"}])["verdict"] == "needs_review"


def test_pending_or_unknown_item_yields_incomplete():
    assert oc.evaluate([{"state": "satisfied"}, {"state": "pending"}])["verdict"] == "incomplete"
    assert oc.evaluate([{"state": "satisfied"}, {"state": "unknown"}])["verdict"] == "incomplete"


def test_unknown_state_is_counted_as_unknown_not_dropped():
    report = oc.evaluate([{"state": "weird"}, {"state": "satisfied"}])
    assert report["counts"]["unknown"] == 1
    assert report["total"] == 2


def test_empty_checklist_does_not_claim_success():
    report = oc.evaluate([])
    assert report["verdict"] == "unknown"
    assert report["total"] == 0


def test_checklist_is_deterministic_for_the_same_inputs(tmp_path):
    path, digest = material(tmp_path)
    art = artifacts(lit_filter={"pool": [{"id": "W1", "title": "P", "sha256": digest,
                                         "path": path}]})
    first = oc.run(art, {"deficits": []}, {"outcome_review": {"by": "A", "note": "n",
                                                              "at": "t"}})
    second = oc.run(art, {"deficits": []}, {"outcome_review": {"by": "A", "note": "n",
                                                               "at": "t"}})
    assert first == second
