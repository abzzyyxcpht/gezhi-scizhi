# -*- coding: utf-8 -*-
"""eval_retrieval --live 的支撑语义测试：缓存 TTL、封顶、无网 skipped 降级。

openalex.search 全部打桩，不触网；缓存目录隔离到临时目录。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from scripts import eval_retrieval
from src.connectors import openalex


@pytest.fixture()
def live_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "eval_live"
    monkeypatch.setattr(eval_retrieval, "LIVE_CACHE_DIR", cache_dir)
    return cache_dir


def _write_cache(cache_dir: Path, topic_id: str, works: list, age_seconds: int = 0) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{topic_id}.json"
    f.write_text(json.dumps({"fetched_at": "x", "works": works}, ensure_ascii=False),
                 encoding="utf-8")
    old = time.time() - age_seconds
    os.utime(f, (old, old))


def test_cache_hit_skips_network(live_cache, monkeypatch):
    _write_cache(live_cache, "rag", [{"id": "w1"}])
    def _boom(*a, **kw):
        raise AssertionError("命中缓存时不允许触网")
    monkeypatch.setattr(openalex, "search", _boom)
    works, state = eval_retrieval.fetch_live_works("rag", "anything")
    assert works == [{"id": "w1"}] and state == "hit"


def test_cache_expires_after_ttl(live_cache, monkeypatch):
    stale_age = eval_retrieval.LIVE_TTL_SECONDS + 1
    _write_cache(live_cache, "rag", [{"id": "old"}], age_seconds=stale_age)
    monkeypatch.setattr(openalex, "search", lambda *a, **kw: [{"id": "new"}])
    works, state = eval_retrieval.fetch_live_works("rag", "anything")
    assert works == [{"id": "new"}] and state == "miss"
    assert json.loads((live_cache / "rag.json").read_text(encoding="utf-8"))["works"] == [{"id": "new"}]


def test_fetch_caps_at_twenty(live_cache, monkeypatch):
    monkeypatch.setattr(openalex, "search", lambda *a, **kw: [{"id": f"w{i}"} for i in range(25)])
    works, state = eval_retrieval.fetch_live_works("gnn", "anything", cap=20)
    assert len(works) == 20 and state == "miss"


def test_run_live_eval_marks_skipped_without_network(live_cache, monkeypatch):
    """无网/查询失败：逐课题记 skipped，整体不抛错，口径字段完整。"""
    def _fail(query, **kw):
        raise RuntimeError("网络不可达")
    monkeypatch.setattr(openalex, "search", _fail)
    live = eval_retrieval.run_live_eval([{"id": "rag", "topic": "检索增强生成"}])
    assert live["topics_total"] == 1 and live["topics_ok"] == 0 and live["topics_skipped"] == 1
    entry = live["topics"][0]
    assert entry["status"] == "skipped" and "网络不可达" in entry["reason"]
    assert live["notice"], "必须带口径声明，防止与合成标定混用"


def test_run_live_eval_ok_path(live_cache, monkeypatch):
    works = [{"id": f"w{i}", "title": f"retrieval augmented generation paper {i}",
              "abstract": "retrieval augmented generation"} for i in range(3)]
    monkeypatch.setattr(openalex, "search", lambda query, **kw: list(works))
    monkeypatch.setattr(eval_retrieval.embedding, "get_provider", lambda: None)
    live = eval_retrieval.run_live_eval([{"id": "rag", "topic": "retrieval augmented generation"}])
    assert live["topics_ok"] == 1
    entry = live["topics"][0]
    assert entry["status"] == "ok" and entry["candidates"] == 3 and entry["cache"] == "miss"
    assert entry["modes"]["lexical"]["pool_size"] >= 1
    assert entry["modes"]["lexical+embedding"]["embedding"] == "unavailable"
