"""世界模型事实卡片测试。

覆盖：CRUD、来源校验（doi/url/凭据拒绝）、synthetic 状态一致性、
版本冲突、投票状态迁移（supports≥2→verified、challenges 领先→challenged）、
schema 与边界声明、损坏文件容错。
"""
from __future__ import annotations

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


def test_votes_migrate_status():
    research_world.handle({"cmd": "world_fact_save", "fact": _fact()})
    research_world.handle({"cmd": "world_fact_support", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "asserted" and fact["supports"] == 1
    research_world.handle({"cmd": "world_fact_support", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "verified" and fact["supports"] == 2
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_1"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_1"})["fact"]
    assert fact["status"] == "challenged" and fact["challenges"] == 3
    # synthetic 事实的 status 不被投票迁移
    research_world.handle({"cmd": "world_fact_save",
                          "fact": _fact(fid="wf_s", status="synthetic", synthetic=True)})
    research_world.handle({"cmd": "world_fact_challenge", "id": "wf_s"})
    fact = research_world.handle({"cmd": "world_fact_get", "id": "wf_s"})["fact"]
    assert fact["status"] == "synthetic"


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
    assert "未验证" in s["boundary"]
    unknown = research_world.handle({"cmd": "world_fact_frobnicate"})
    assert not unknown["ok"] and unknown["code"] == "unknown_command"


def test_corrupt_store_keeps_file(tmp_path):
    path = tmp_path / "world_facts.json"
    path.write_text("{ broken", encoding="utf-8")
    r = research_world.handle({"cmd": "world_fact_list"})
    assert not r["ok"] and r["code"] == "corrupt_store"
    assert path.read_text(encoding="utf-8") == "{ broken"
