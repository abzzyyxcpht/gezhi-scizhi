"""Background launch publishes identity before long model/tool work completes."""
import threading
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.agent_launch import AgentLauncher
from src.agent_runtime import AgentRuntime, SessionStore


@pytest.fixture
def tmp_path():
    # Avoid pytest's shared numbered-temp root during concurrent release checks.
    with tempfile.TemporaryDirectory(prefix="scizhi-agent-launch-") as directory:
        yield Path(directory)


def runtime(tmp_path):
    value = AgentRuntime(session_root=tmp_path, offline=True, llm_enabled=False)
    return value


def test_launch_publishes_session_before_plan_finishes_and_retries_do_not_duplicate(tmp_path):
    rt, launcher = runtime(tmp_path), AgentLauncher()
    release = threading.Event()
    original = rt.plan
    def slow_plan(*args):
        assert release.wait(5)
        return original(*args)
    with mock.patch.object(rt, "plan", side_effect=slow_plan), mock.patch.object(rt, "start", wraps=rt.start) as start:
        try:
            first = launcher.launch(rt, "调研证据", launch_id="test-request")
            second = launcher.launch(rt, "调研证据", launch_id="test-request")
            assert first["session_id"] == second["session_id"]
            assert start.call_count == 1
            assert rt.sessions.snapshot(first["session_id"])["status"] == "running"
            assert not launcher.status(first["session_id"])["finished"]
        finally:
            release.set()
        assert launcher.join(first["session_id"], 5)
        assert launcher.status(first["session_id"])["result"]["status"] == "waiting_approval"


@pytest.mark.parametrize("prompt,mode,agent", [("", "agent", "research"), (None, "agent", "research"),
    ("研究", "invalid", "research"), ("研究", "agent", "unknown")])
def test_invalid_launch_does_not_start_a_worker(tmp_path, prompt, mode, agent):
    rt, launcher = runtime(tmp_path), AgentLauncher()
    with mock.patch.object(rt, "start") as start, pytest.raises(ValueError):
        launcher.launch(rt, prompt, mode, agent)
    start.assert_not_called()
    assert rt.sessions.list() == []


def test_background_exception_is_persisted_as_error(tmp_path):
    rt, launcher = runtime(tmp_path), AgentLauncher()
    with mock.patch.object(rt, "plan", side_effect=RuntimeError("fixture failure")):
        launched = launcher.launch(rt, "调研证据")
        assert launcher.join(launched["session_id"], 5)
    state = launcher.status(launched["session_id"])
    assert state["snapshot"]["status"] == "error"
    assert state["result"]["status"] == "error"
    assert "fixture failure" in state["result"]["error"]


@pytest.mark.parametrize("committed", ["done", "cancelled"])
def test_background_exception_does_not_replace_published_terminal(tmp_path, committed):
    rt, launcher = runtime(tmp_path), AgentLauncher()

    def finish_then_raise(request, mode, agent, *, on_started):
        sid = "sealed-worker"
        rt.sessions.append(sid, "user_message", {"text": request, "mode": mode, "agent": agent})
        on_started(sid)
        rt._publish_terminal(sid, committed)
        raise OSError("post-completion storage failure")

    with mock.patch.object(rt, "start", side_effect=finish_then_raise):
        launched = launcher.launch(rt, "Inspect workspace", agent="general")
        assert launcher.join(launched["session_id"], 5)
    state = launcher.status(launched["session_id"])
    assert state["snapshot"]["status"] == committed
    assert state["result"]["status"] == committed
    terminal = [event for event in rt.sessions.read(launched["session_id"])
                if event["type"] in {"completed", "error"}]
    assert len(terminal) == 1


def test_launch_id_cannot_be_reused_for_a_different_request(tmp_path):
    rt, launcher = runtime(tmp_path), AgentLauncher()
    first = launcher.launch(rt, "调研证据", launch_id="same-id")
    with pytest.raises(ValueError, match="不同"):
        launcher.launch(rt, "另一项研究", launch_id="same-id")
    assert launcher.join(first["session_id"], 5)


def test_missing_worker_is_not_reported_as_completed(tmp_path):
    store = SessionStore(tmp_path)
    store.append("lost-session", "user_message", {"text": "尚未结束", "mode": "agent"})
    launcher = AgentLauncher()
    state = launcher.status("lost-session", fallback_store=store)
    assert state["snapshot"]["status"] == "running"
    assert state["result"] is None
    assert state["worker_missing"] is True


def test_eviction_of_completed_turn_keeps_live_continuation_for_same_session(tmp_path):
    """Cross the result-cache boundary while a second turn owns the old session."""
    store = SessionStore(tmp_path)
    rt = mock.Mock(offline=True, sessions=store)
    rt._resolve_agent.side_effect = lambda request, agent: (request, agent)
    launcher = AgentLauncher()
    entered, release = threading.Event(), threading.Event()
    active_workers = []

    def completed_start(request, mode, agent, *, on_started):
        store.append(request, 'user_message', {'text': request, 'mode': mode, 'agent': agent})
        on_started(request)
        store.append(request, 'completed', {'status': 'done'})
        return {'session_id': request, 'status': 'done', 'agent': agent}

    def active_turn(req, *, on_started):
        active_workers.append(threading.current_thread())
        sid = req['session_id']
        store.append(sid, 'user_message', {'text': req['request'], 'mode': req['mode'], 'agent': req['agent']})
        on_started(sid)
        entered.set()
        assert release.wait(15)
        store.append(sid, 'completed', {'status': 'done'})
        return {'ok': True, 'result': {'session_id': sid, 'status': 'done', 'agent': req['agent']}}

    rt.start.side_effect = completed_start
    original = launcher.launch(rt, 'shared', launch_id='old-turn')
    assert launcher.join(original['session_id'], 5)
    with mock.patch('src.session_workspace._turn', side_effect=active_turn):
        try:
            launcher.launch(rt, 'continue', session_id='shared', launch_id='active-turn')
            assert entered.wait(5)
            assert not launcher.status('shared')['finished']
            for index in range(254):
                sid = f'padding-{index}'
                launcher.launch(rt, sid, launch_id=sid)
                assert launcher.join(sid, 5)
            launcher.launch(rt, 'overflow', launch_id='overflow')
            assert launcher.join('overflow', 5)
            status = launcher.status('shared')
            assert not status['finished']
            assert not status['worker_missing']
            assert status['snapshot']['status'] == 'running'
        finally:
            release.set()
            # Join the worker directly even when the broken cache loses its mapping.
            for worker in active_workers:
                worker.join(5)
                assert not worker.is_alive()
    assert launcher.status('shared')['result']['status'] == 'done'
