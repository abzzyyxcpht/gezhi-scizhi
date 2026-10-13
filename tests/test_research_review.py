"""AI 审稿人（启发式多维评分）测试。

覆盖：结构/证据/可检验/闭合/深度五维计分、verdict 分档、建议生成、
completed 必须有 result 的联动（notebook 自身规则）、边界声明、未知命令容错。
"""
from __future__ import annotations

import pytest

from src import config
from src import research_notebook
from src import research_review


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def _record(rid="nb_1", **kw):
    rec = {"id": rid, "kind": "hypothesis", "title": "审稿对象：温度与产率",
           "status": "completed",
           "question": "温度是否提升产率？",
           "prediction": "温度每升 10℃，产率提升 5%",
           "method": "正交实验，五水平", "metrics": "产率%、能耗 kWh/t",
           "result": "产率提升 4.2%", "limitations": "仅单批次",
           "next_action": "扩大样本",
           "evidence": [{"relation": "supports", "source": {"id": "s1"},
                         "quote": "q", "scope": "sc"},
                        {"relation": "supports", "source": {"id": "s2"},
                         "quote": "q", "scope": "sc"}],
           "observations": ["第一次观察", "第二次观察"]}
    rec.update(kw)
    return rec


def test_full_record_scores_high():
    research_notebook.handle({"cmd": "notebook_save", "record": _record()})
    r = research_review.handle({"cmd": "peer_review", "id": "nb_1"})
    assert r["ok"]
    assert r["dimensions"]["structure"] == 5.0
    assert r["dimensions"]["testability"] == 5.0
    assert r["dimensions"]["closure"] == 5.0
    assert r["overall"] >= 3.5 and r["verdict"] == "结构良好"
    assert r["suggestions"] == []
    assert "不构成同行评审" in r["boundary"]


def test_empty_record_scores_low_with_suggestions():
    research_notebook.handle({"cmd": "notebook_save", "record": _record(
        rid="nb_2", status="planned", question="q", prediction="", method="", metrics="",
        result="", limitations="", next_action="", evidence=[], observations=[])})
    r = research_review.handle({"cmd": "peer_review", "id": "nb_2"})
    assert r["dimensions"]["structure"] < 2
    assert r["dimensions"]["testability"] == 0.0
    assert r["dimensions"]["depth"] == 0.0
    assert r["overall"] < 2 and r["verdict"] == "优先补齐"
    assert len(r["suggestions"]) >= 4


def test_evidence_balance_penalises_challenges():
    research_notebook.handle({"cmd": "notebook_save", "record": _record(
        rid="nb_3", evidence=[
            {"relation": "challenges", "source": {"id": "s1"}, "quote": "q", "scope": "sc"},
            {"relation": "challenges", "source": {"id": "s2"}, "quote": "q", "scope": "sc"},
        ])})
    r = research_review.handle({"cmd": "peer_review", "id": "nb_3"})
    assert r["dimensions"]["evidence"] < 2.5
    assert any("证据" in s for s in r["suggestions"])


def test_planned_and_failed_closure():
    research_notebook.handle({"cmd": "notebook_save", "record": _record(rid="nb_4", status="planned")})
    r4 = research_review.handle({"cmd": "peer_review", "id": "nb_4"})
    assert r4["dimensions"]["closure"] == 1.0
    research_notebook.handle({"cmd": "notebook_save",
                              "record": _record(rid="nb_5", status="failed", result="复现失败：依赖缺失")})
    r5 = research_review.handle({"cmd": "peer_review", "id": "nb_5"})
    assert r5["dimensions"]["closure"] == 2.5


def test_missing_record_and_bad_request():
    r = research_review.handle({"cmd": "peer_review", "id": "nope"})
    assert not r["ok"] and r["code"] == "not_found"
    bad = research_review.handle({"cmd": "peer_review"})
    assert not bad["ok"] and bad["code"] == "invalid_data"
    unknown = research_review.handle({"cmd": "peer_frobnicate"})
    assert not unknown["ok"] and unknown["code"] == "unknown_command"
