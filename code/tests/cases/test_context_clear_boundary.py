"""Clearing a Resident's context leaves nothing of it for a restart or a Fork.

The Provider sessions that ran before the clear hold the cleared context.
A restart must not hand their compaction summary to the new session, a Fork
that has not run yet loses the context it inherited, a cleared Resident forks
with an empty context, and a native session that is still running a turn is
not released (its daemon would carry it on into the next turn).

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations
from tests.cases.agent_data_test_support import sequence_members

from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import backends
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution.agent_data_projection import persist_provider_compaction
from management_portal.society_service import (
    project_domain,
    resident_domain,
    resident_execution_work,
    resident_fork_domain,
    resident_lifecycle_domain,
    resident_restart_domain,
    resident_session_backend_domain,
    resident_workspace_domain,
)
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store,  # noqa: F401 - pytest fixture
    _append_conversation_turn,
    _new_session_payload,
)


def _session_with_execution(store: AgentDataStore, agent_id: str, *, started_at: str) -> str:
    session = store.ensure_provider_session(
        provider="codex", backend_route_id="zero",
        backend_session_id=f"thread-{agent_id}", agent_id=agent_id, actor_ref="sample_admin",
    )
    execution_id = f"execution-{agent_id}"
    store.begin_execution(
        execution_id=execution_id, agent_id=agent_id,
        actor_ref="sample_admin", provider="codex", started_at=started_at,
    )
    store.bind_execution_provider_session(execution_id, provider_session_ref=session["provider_session_ref"])
    return str(session["provider_session_ref"])


def test_sessions_from_before_a_clear_are_not_the_newest_context() -> None:
    """清空之前的会话不再作为最新上下文"""
    store = AgentDataStore()
    agent_id = "resident_clear_boundary_store"
    ref = _session_with_execution(store, agent_id, started_at="2026-10-05T09:13:18.194Z")
    persist_provider_compaction(
        store, provider_session_ref=ref, agent_id=agent_id,
        operation_id=f"execution-{agent_id}", actor_ref="sample_admin", provider="codex",
        content={"replacement_history": [{"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "CODEX-WALRUS-93"}]}]},
        created_at="2026-10-05T09:13:30Z",
    )

    assert store.latest_provider_session_ref(agent_id) == ref
    assert store.latest_context_compaction(agent_id) is not None
    # Cleared at 09:13:38: the session ran before, so it holds cleared context.
    assert store.latest_provider_session_ref(agent_id, since="2026-10-05T09:13:38.000Z") == ""
    assert store.latest_context_compaction(agent_id, since="2026-10-05T09:13:38Z") is None
    # A clear before the session started does not hide it; precision may differ.
    assert store.latest_provider_session_ref(agent_id, since="2026-10-05T09:13:18Z") == ref


def test_a_restart_after_a_clear_hands_over_no_compaction_summary(monkeypatch) -> None:
    """清空后重启不交接压缩摘要"""
    requested = []
    monkeypatch.setattr(resident_restart_domain, "execution_port", SimpleNamespace(
        latest_context_compaction=lambda resident_id, since="": requested.append(since) or None,
    ))

    prompt = resident_restart_domain._session_restart_handoff_prompt(
        {"resident_id": "resident_aaaaaaaaaaaa", "context_reset_at": "2026-10-05T09:13:38.000Z"},
        {"messages": []},
        strategy="compact",
    )

    assert requested == ["2026-10-05T09:13:38.000Z"]
    assert "压缩摘要" not in prompt


def test_a_restart_right_after_a_clear_only_configures(monkeypatch) -> None:
    """清空后立即重启只应用配置"""
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.society_service import resident_backend

    record, operations, _saved = _restart_domain(monkeypatch)
    record["context_reset_at"] = "2026-10-05T09:13:38.000Z"
    monkeypatch.setattr(resident_backend, "session_record", lambda *_a, **_kw: {})
    # The newest Provider session ran before the clear.
    monkeypatch.setattr(resident_restart_domain.execution_port, "latest_provider_session_ref",
                        lambda _resident_id, since="": "" if since else "provider_session_cleared", raising=False)

    result = resident_domain.restart_resident_session(record["resident_id"], "alice", {})

    assert result["configuration_only"] is True
    assert operations == []


def _clear(resident_id: str, monkeypatch, *, expected_releases: int = 1) -> dict:
    released = []
    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation",
                        lambda **kwargs: released.append(kwargs) or {})
    monkeypatch.setattr("management_portal.society_service.resident_execution_domain.resume_resident_execution",
                        lambda _resident_id: None)
    result = resident_domain.reset_resident_context(resident_id, "sample_admin")
    assert resident_lifecycle_domain.resume_resident_lifecycle_operation(result["operation_id"])
    assert [call["operation"] for call in released] == ["release"] * expected_releases
    return resident_workspace_domain.find_resident(resident_id)


def test_a_cleared_resident_forks_with_an_empty_context(future_creation_store, monkeypatch) -> None:  # noqa: F811
    """已清空的 Resident Fork 出的上下文为空"""
    source = resident_domain.register_resident(_new_session_payload("Clear fork fixture"), "sample_admin")
    _append_conversation_turn(source["resident_id"], 0)
    before = resident_workspace_domain.find_resident(source["resident_id"])
    assert resident_fork_domain._fork_provider_context_snapshot(before)["segments"]

    cleared = _clear(source["resident_id"], monkeypatch)

    assert cleared["context_reset_at"]
    snapshot = resident_fork_domain._fork_provider_context_snapshot(cleared)
    assert snapshot is not None and snapshot["empty_context"] is True
    assert snapshot["segments"] == []
    assert get_agent_data_store().latest_provider_session_ref(source["resident_id"])  # history is kept


def test_a_fork_cleared_before_its_first_turn_loses_the_inherited_context(
    future_creation_store, monkeypatch,  # noqa: F811
) -> None:
    """Fork 在首轮前被清空时丢弃继承的上下文"""
    monkeypatch.setattr(project_domain, "project_root_for_id", lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload("Seed source"), "sample_admin")
    _append_conversation_turn(source["resident_id"], 0)
    child_id = resident_domain.create_resident_context_fork(
        source["resident_id"], "sample_admin", 5,
    )["resident_id"]
    store = get_agent_data_store()
    inherited_ids = [segment["segment_id"] for segment in store.agent_context_snapshot(child_id)["segments"]]
    assert inherited_ids

    # The Fork's creation job has ended; its first turn has not started.
    monkeypatch.setattr(resident_execution_work, "is_active", lambda _job: False)
    cleared = _clear(child_id, monkeypatch, expected_releases=0)

    assert cleared["context_reset_at"]
    assert store.agent_context_snapshot(child_id)["segments"] == []
    assert set(inherited_ids) <= set(store.sequence_segment_ids(store.trace_sequence_id(child_id)))


class _Daemon:
    def __init__(self, closed: dict) -> None:
        self.result, self.calls = closed, []

    def close_session(self, session_key: str, session_id: str = "") -> dict:
        self.calls.append((session_key, session_id))
        return self.result


def _release(backend_class, daemon: _Daemon):
    backend = backend_class()
    backend.client_factory = lambda: daemon  # type: ignore[method-assign]
    invocation = mock.Mock()
    invocation.request.operation = "release"
    invocation.request.input_media = ()
    invocation.request.runtime_options = {"session_key": "resident:resident_x", "provider_profile": "claude_one"}
    invocation.session.backend_route_id = "claude_one"
    invocation.session.backend_session_id = "claude-session-old"
    return backend.execute(invocation, mock.Mock())


@pytest.mark.parametrize("closed", [
    {"closed": True},
    {"closed": False, "reason": "session not loaded"},
])
def test_a_native_release_closes_an_idle_or_unloaded_session(closed) -> None:
    """原生释放关闭空闲或未加载的会话"""
    daemon = _Daemon(closed)
    result = _release(backends.ClaudeBackend, daemon)
    assert daemon.calls == [("resident:resident_x", "claude-session-old")]
    assert result.output["operation"] == "release"


def test_a_native_release_waits_for_the_running_turn() -> None:
    """原生释放等待正在运行的一轮结束"""
    daemon = _Daemon({"closed": False, "reason": "session has an active turn", "session_id": "claude-session-old"})
    with pytest.raises(RuntimeError, match="active turn"):
        _release(backends.ClaudeBackend, daemon)


def _trace(store: AgentDataStore, agent_id: str) -> list[tuple[str, str]]:
    return [(item["segment_id"], item["segment_type"]) for item in sequence_members(store, store.trace_sequence_id(agent_id))]


def _recorded(store: AgentDataStore, agent_id: str, segment_id: str, created_at: str, kind: str = "input") -> None:
    store.put_segment(segment_id=segment_id, segment_type=kind, content=segment_id,
                      started_at=created_at, created_by_agent_id=agent_id)
    store.append_agent_segments(agent_id, [segment_id], actor_ref='sample_admin')


def _operation_positions(store, agent):
    with store.database.read() as connection:
        return {row["operation_id"]: int(row["ordinal"]) for row in connection.execute(
            "SELECT operation_id,ordinal FROM operation_positions WHERE sequence_id=%s",
            (store.trace_sequence_id(agent),)).fetchall()}




def _turn_output(store: AgentDataStore, agent_id: str, segment_id: str, started_at: str, created_at: str,
                 kind: str) -> None:
    execution_id = "execution_" + segment_id
    store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="sample_admin", provider="codex",
                          started_at=started_at)
    store.put_segment(segment_id=segment_id, segment_type=kind, content=segment_id, started_at=created_at,
                      created_by_agent_id=agent_id, execution_id=execution_id)
    store.append_agent_segments(agent_id, [segment_id], actor_ref='sample_admin')






def test_clearing_a_resident_marks_its_trace(future_creation_store, monkeypatch) -> None:  # noqa: F811
    """ClearContext 在清空时的 trace 末尾写入清空标记

    清空记下当时 trace 的长度，生命周期步骤把 context_reset 段放在那里：之前的 trace 不变，清空后、写标记前送达的消息排在标记之后；标记记下 Resident 记录上的 context_reset_at、清空者和被归档的消息。

    :module: execution.persistence
    """
    source = resident_domain.register_resident(_new_session_payload("Clear mark fixture"), "sample_admin")
    resident_id = source["resident_id"]
    _append_conversation_turn(resident_id, 0)
    store = get_agent_data_store()
    before = _trace(store, resident_id)
    shown = store.displayed_chat_message_ids(resident_id)
    released = []
    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation",
                        lambda **kwargs: released.append(kwargs) or {})
    monkeypatch.setattr("management_portal.society_service.resident_execution_domain.resume_resident_execution",
                        lambda _resident_id: None)

    result = resident_domain.reset_resident_context(resident_id, "sample_admin")
    # A message sent right after the clear is recorded before the mark is written.
    _recorded(store, resident_id, "segment_clear_mark_next_request", "2026-01-01T00:00:00Z")
    assert resident_lifecycle_domain.resume_resident_lifecycle_operation(result["operation_id"])
    cleared = resident_workspace_domain.find_resident(resident_id)

    after = _trace(store, resident_id)
    assert after[:len(before)] == before and len(after) == len(before) + 1
    assert after[-1] == ("segment_clear_mark_next_request", "input")
    assert store.read_chat_messages(resident_id) == []
    positions = _operation_positions(store, resident_id)
    assert len(positions) == 1
    with store.database.read() as connection:
        operation = connection.execute("SELECT kind,created_at,data_json FROM agent_operations WHERE operation_id=%s", (next(iter(positions)),)).fetchone()
    assert operation["kind"] == "context_reset"
    from management_common.database_time import instant
    assert operation["created_at"] == instant(cleared["context_reset_at"])
    assert shown
