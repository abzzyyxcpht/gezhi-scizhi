"""End-to-end effort trail: the receipt must match the bytes actually sent.

These tests drive the real transport against the scripted fake endpoint and then
read the production usage log, so the trail cannot be satisfied by mocking the
receipt itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SERVER = Path(__file__).resolve().parent / "fake_llm_server.py"

from src import llm, logging_util, reasoning_receipt as rr  # noqa: E402


@pytest.fixture(scope="module")
def port():
    proc = subprocess.Popen([sys.executable, str(_SERVER), "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    line = (proc.stdout.readline() or "").strip()
    if not line.startswith("LISTENING"):
        proc.kill()
        raise RuntimeError(f"假端点启动失败：{line!r}")
    yield int(line.split()[1])
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def trail(port, monkeypatch, tmp_path):
    """Isolated model log plus a spy on the real request builder.

    ``build_request`` produces the dict that is serialized as the HTTP body, so
    recording its output is the same evidence as recording the socket bytes,
    without re-implementing the transport.
    """
    log_path = tmp_path / "model_calls.jsonl"
    monkeypatch.setattr(logging_util, "_MODEL_LOG", log_path)
    sent: list[dict] = []
    real_build = llm.model_protocols.build_request

    def spy(profile, *args, **kwargs):
        request = real_build(profile, *args, **kwargs)
        sent.append(request["json"])
        return request

    monkeypatch.setattr(llm.model_protocols, "build_request", spy)
    return {"sent": sent, "base": f"http://127.0.0.1:{port}/v1",
            "log": log_path, "tmp": tmp_path}


def _profile(trail, *, effort="high", style="auto", model="gpt-5", **extra):
    return {"id": "et", "label": "effort", "protocol": "chat_completions",
            "base_url": trail["base"], "model": model, "api_key": "",
            "thinking_style": style, "reasoning_effort": effort,
            "max_output_tokens": 4096, "context_window": 32768, **extra}


def _receipts(trail) -> list[dict]:
    if not trail["log"].exists():
        return []
    rows = [json.loads(line) for line in
            trail["log"].read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row["reasoning"] for row in rows if isinstance(row.get("reasoning"), dict)]


def test_receipt_matches_request_bytes_and_honest_provider_report(trail):
    with mock.patch.object(llm, "_resolve_config",
                           return_value=_profile(trail, effort="high")):
        llm.chat([{"role": "user", "content": "hello"}], node="trail", max_tokens=64)
    assert trail["sent"][-1].get("reasoning_effort") == "high"
    receipt = _receipts(trail)[-1]
    assert receipt["requested"] == "high"
    assert receipt["state"] == "sent"
    assert receipt["wire"] == [{"key": "reasoning_effort", "value": "high"}]
    # The scripted endpoint reports prompt/completion only: reasoning stays
    # unknown rather than being flattened into a zero.
    assert receipt["reported"]["known"] is False
    assert receipt["reported"]["reasoning_tokens"] is None


def test_auto_records_an_omitted_request_without_claiming_delivery(trail):
    with mock.patch.object(llm, "_resolve_config",
                           return_value=_profile(trail, effort="auto")):
        llm.chat([{"role": "user", "content": "hello"}], node="trail", max_tokens=64)
    assert "reasoning_effort" not in trail["sent"][-1]
    receipt = _receipts(trail)[-1]
    assert receipt["state"] == "omitted"
    assert receipt["wire"] == []
    assert receipt["delivered"] is False
    assert "供应商默认" in receipt["reason"]


def test_local_adapter_refusal_is_recorded_not_silently_omitted(trail):
    with mock.patch.object(llm, "_resolve_config",
                           return_value=_profile(trail, effort="max",
                                                 style="openai_effort")):
        with pytest.raises(Exception):
            llm.chat([{"role": "user", "content": "hello"}], node="trail", max_tokens=64)
    receipt = _receipts(trail)[-1]
    assert receipt["state"] == "unsupported"
    assert receipt["delivered"] is False
    assert receipt["settled"] is True
    assert receipt["reported"]["known"] is False


def test_streaming_path_records_the_same_trail(trail):
    with mock.patch.object(llm, "_resolve_config",
                           return_value=_profile(trail, effort="low")):
        events = list(llm.chat_stream_agent(
            [{"role": "user", "content": "SCN_STREAM_TEXT"}],
            node="trail", tier="planning", max_tokens=64))
    assert any(event.get("delta") for event in events)
    assert trail["sent"][-1].get("reasoning_effort") == "low"
    receipt = _receipts(trail)[-1]
    assert receipt["requested"] == "low"
    assert receipt["state"] == "sent"
    assert receipt["wire"] == [{"key": "reasoning_effort", "value": "low"}]


def test_usage_summary_and_rows_describe_the_recorded_trail(trail, monkeypatch):
    with mock.patch.object(llm, "_resolve_config",
                           return_value=_profile(trail, effort="high")):
        llm.chat([{"role": "user", "content": "hello"}], node="trail", max_tokens=64)
    monkeypatch.setattr(logging_util, "_MODEL_LOG", trail["log"])
    summary = logging_util.usage_summary()["reasoning"]
    assert summary["recorded"] >= 1
    rows = rr.rows(summary)
    high = next(row for row in rows if row["requested"] == "high")
    assert high["calls"] >= 1
    assert high["sent"] == high["calls"]
    assert high["reported"] == 0
    assert "选择 深入" in rr.summary(_receipts(trail)[-1])


def test_log_records_written_before_this_feature_are_ignored_not_assumed_sent(tmp_path,
                                                                           monkeypatch):
    log = tmp_path / "legacy.jsonl"
    log.write_text(json.dumps({"prompt_tokens": 10, "completion_tokens": 5,
                               "status": "success", "ts": "2026-10-01T00:00:00"}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(logging_util, "_MODEL_LOG", log)
    summary = logging_util.usage_summary()["reasoning"]
    assert summary["recorded"] == 0
    assert summary["calls"] == 1
    assert rr.rows(summary) == []


if __name__ == "__main__":
    unittest.main()
