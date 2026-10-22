"""世界模型事实卡片测试。

覆盖：CRUD、来源校验（doi/url/凭据拒绝）、synthetic 状态一致性、
版本冲突、投票只迁移 asserted/challenged（票数不产生 verified）、
独立核验记录门槛（verified_by + verified_note + verified_at 缺一不可）、
schema 与边界声明、损坏文件容错。
"""
from __future__ import annotations

import json

import pytest

from src import config
from src import research_world


@pytest.fixture(autouse=True)
def isolated_world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def _fact(fid="wf_1", claim="温度提升产率", **kw):
    fact = {"id": fid, "claim": claim, "topic": "热轧钢缺陷检测",
            "source": {"id": "user_local", "source_type": "user_provided"}}
    fact.update(kw)
    return fact


def test_save_and_list_roundtrip():
    r = research_world.handle({"cmd": "world_fact_save", "fact": _fact()})
    assert r["ok"] and r["created"] and r["fact"]["version"] == 1
    lst = research_world.handle({"cmd": "world_fact_list"})
    assert lst["count"] == 1 and lst["facts"][0]["claim"] == "温度提升产率"
    assert lst["facts"][0]["provenance"]["origin"] == "user_recorded"


def test_claim_required_and_source_identity():
    bad = research_world.handle({"cmd": "world_fact_save",
                                 "fact": {"claim": "x", "source": {"title": "仅题名"}}})
    assert not bad["ok"] and bad["code"] == "invalid_data"
    empty = research_world.handle({"cmd": "world_fact_save",
                                   "fact": {"claim": "", "source": {"id": "u"}}})
    assert not empty["ok"]


def test_doi_normalized_and_url_credential_rejected():
    r = research_world.handle({"cmd": "world_fact_save", "fact": _fact(
        source={"doi": "https://doi.org/10.1000/xyz123", "source_type": "user_provided"})})
    assert r["ok"] and r["fact"]["source"]["doi"] == "10.1000/xyz123"
    bad = research_world.handle({"cmd": "world_fact_save", "fact": _fact(
        source={"url": "https://x.cn/a?token=secret", "source_type": "user_provided"})})
    assert not bad["ok"]


def test_synthetic_status_requires_flag():
    bad = research_world.handle({"cmd": "world_fact_save",
                                 "fact": _fact(status="synthetic", synthetic=False)})
    assert not bad["ok"]
    ok = research_world.handle({"cmd": "world_fact_save",
                                "fact": _fact(status="synthetic", synthetic=True)})
    assert ok["ok"] and ok["fact"]["provenance"]["contains_synthetic_evidence"] is True


def test_version_conflict():
    research_world.handle({"cmd": "world_fact_save", "fact": _fact()})
    stale = research_world.handle({"cmd": "world_fact_save", "fact": _fact(claim="改"),
                                   "expected_version": 0})
    assert not stale["ok"] and stale["code"] == "version_conflict"


def test_votes_only_migrate_asserted_and_challenged():
    research_world.handle({"cmd": "world_fact_save", "fact": _fact()})
    research_world.handle({"cmd": "world_fact_support", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "asserted" and fact["supports"] == 1
    # 两张、四张支持票都不产生 verified：支持票不等于独立核验。
    for _ in range(3):
        research_world.handle({"cmd": "world_fact_support", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["supports"] == 4 and fact["status"] == "asserted"
    assert "verified_by" in fact and fact["verified_by"] == ""
    for _ in range(5):
        research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "challenged" and fact["challenges"] == 5
    # synthetic 事实的 status 不被投票迁移
    research_world.handle({"cmd": "world_fact_save",
                          "fact": _fact(fid="wf_s", status="synthetic", synthetic=True)})
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_s"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_s"})["fact"]
    assert fact["status"] == "synthetic"


_VERIFICATION = {"status": "verified", "verified_by": "复核人：张工",
                 "verified_note": "独立复算原始运行记录，结果一致。",
                 "verified_at": "2026-10-20T09:30:00Z"}


def test_verified_requires_independent_record():
    r = research_world.handle({"cmd": "world_fact_save", "fact": _fact(**_VERIFICATION)})
    assert r["ok"] and r["fact"]["status"] == "verified"
    assert r["fact"]["verified_by"] == "复核人：张工"
    assert r["fact"]["verified_note"] and r["fact"]["verified_at"]
    # 独立核验记录存在时，票数不再改写 verified
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "verified" and fact["challenges"] == 2


def test_verified_without_full_record_is_asserted_and_annotated():
    partial = research_world.handle({"cmd": "world_fact_save", "fact": _fact(
        fid="wf_p", status="verified", verified_by="张工")})
    assert partial["ok"] and partial["fact"]["status"] == "asserted"
    assert "缺少独立核验记录" in partial["fact"]["notes"]
    no_verifier = research_world.handle({"cmd": "world_fact_save", "fact": _fact(
        fid="wf_q", status="verified", verified_note="看起来没问题",
        verified_at="2026-10-20T09:30:00Z")})
    assert no_verifier["fact"]["status"] == "asserted"
    assert "缺少独立核验记录" in no_verifier["fact"]["notes"]


def test_legacy_verified_without_record_reads_as_asserted(isolated_world):
    research_world.handle({"cmd": "world_fact_save", "fact": _fact(fid="wf_legacy")})
    store = isolated_world / "world_facts.json"
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["facts"]["wf_legacy"]["status"] = "verified"
    store.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_legacy"})["fact"]
    assert fact["status"] == "asserted"
    assert "缺少独立核验记录" in fact["notes"]


def test_synthetic_material_cannot_be_verified():
    r = research_world.handle({"cmd": "world_fact_save",
                               "fact": _fact(fid="wf_syn", synthetic=True, **_VERIFICATION)})
    assert r["ok"] and r["fact"]["status"] == "asserted"
    assert "合成材料" in r["fact"]["notes"]


def test_vote_negative_rejected():
    research_world.handle({"cmd": "world_fact_save", "fact": _fact()})
    bad = research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1", "delta": -1})
    assert not bad["ok"] and bad["code"] == "invalid_data"


def test_filters_and_delete():
    research_world.handle({"cmd": "world_fact_save", "fact": _fact(fid="a", topic="T1")})
    research_world.handle({"cmd": "world_fact_save", "fact": _fact(fid="b", topic="T2")})
    only_t1 = research_world.handle({"cmd": "world_fact_list", "topic": "T1"})
    assert only_t1["count"] == 1 and only_t1["facts"][0]["id"] == "a"
    missing = research_world.handle({"cmd": "world_fact_delete", "id": "nope"})
    assert not missing["ok"] and missing["code"] == "not_found"
    assert research_world.handle({"cmd": "world_fact_delete", "id": "a"})["ok"]
    assert research_world.handle({"cmd": "world_fact_list"})["count"] == 1


def test_schema_and_boundary():
    s = research_world.handle({"cmd": "world_fact_schema"})
    assert s["ok"] and s["schema"]["required"] == ["claim", "source"]
    assert "未验证" in s["boundary"] and "支持票不等于核验" in s["boundary"]
    for field in ("verified_by", "verified_note", "verified_at"):
        assert field in s["schema"]["properties"]
    assert any("只影响 asserted/challenged" in rule for rule in s["rules"])
    unknown = research_world.handle({"cmd": "world_fact_frobnicate"})
    assert not unknown["ok"] and unknown["code"] == "unknown_command"


def test_corrupt_store_keeps_file(tmp_path):
    path = tmp_path / "world_facts.json"
    path.write_text("{ broken", encoding="utf-8")
    r = research_world.handle({"cmd": "world_fact_list"})
    assert not r["ok"] and r["code"] == "corrupt_store"
    assert path.read_text(encoding="utf-8") == "{ broken"
