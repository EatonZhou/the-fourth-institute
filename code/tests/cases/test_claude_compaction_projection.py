"""Claude Code compactions become the head of the recorded Provider context.

Claude Code compacts a session inside its own process.  Portal reads the newest
``compact_boundary`` from the session transcript after each turn and records it
in AgentData, so the effective window a Fork inherits matches what Claude uses.

:module: resident.fork-handoff
:importance: P1
:design: decision_b1ab45c4b3ec, decision_5bf5bfbd52cb
"""
from __future__ import annotations

import json
from pathlib import Path

from management_portal.execution.agent_data_projection import (
    AgentDataProjector,
    persist_provider_compaction,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backends import BackendInvocation, ClaudeBackend
from management_portal.execution.claude_transcript_compaction import (
    claude_transcript_path,
    read_claude_latest_compaction,
)
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession


SESSION_ID = "0a1b2c3d-0000-4000-8000-00000000c1a0"


def _entry(**fields: object) -> str:
    return json.dumps(fields, ensure_ascii=False)


def _transcript(tmp_path: Path, *, with_latest: bool = True) -> Path:
    project = tmp_path / "config" / "projects" / "-work-resident"
    project.mkdir(parents=True)
    lines = [
        _entry(type="user", uuid="u-old", timestamp="2026-09-01T00:00:00.000Z",
               message={"role": "user", "content": "old question"}),
        _entry(type="system", subtype="compact_boundary", uuid="b-first",
               timestamp="2026-09-01T01:00:00.000Z",
               compactMetadata={"trigger": "auto"}),
        _entry(type="user", uuid="s-first", isCompactSummary=True,
               timestamp="2026-09-01T01:00:00.000Z",
               message={"role": "user", "content": "first summary"}),
        _entry(type="assistant", uuid="a-kept", timestamp="2026-09-02T00:00:00.000Z",
               message={"role": "assistant", "content": [{"type": "text", "text": "kept"}]}),
        _entry(type="user", uuid="u-kept", timestamp="2026-09-02T00:00:01.000Z",
               message={"role": "user", "content": "current question"}),
    ]
    if with_latest:
        lines += [
            _entry(type="system", subtype="compact_boundary", uuid="b-latest",
                   timestamp="2026-09-02T00:02:00.000Z",
                   compactMetadata={
                       "trigger": "auto",
                       "preservedMessages": {
                           "anchorUuid": "s-latest",
                           "uuids": ["a-kept", "u-kept"],
                       },
                   }),
            _entry(type="user", uuid="s-latest", isCompactSummary=True,
                   timestamp="2026-09-02T00:01:59.000Z",
                   message={"role": "user", "content": [
                       {"type": "text", "text": "This session is being continued."},
                   ]}),
            _entry(type="assistant", uuid="a-after", timestamp="2026-09-02T00:03:00.000Z",
                   message={"role": "assistant", "content": [{"type": "text", "text": "after"}]}),
        ]
    path = project / f"{SESSION_ID}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_reader_returns_the_newest_compaction_with_its_kept_messages(tmp_path: Path) -> None:
    """Claude 会话压缩记入有效上下文

    读取 Claude 会话记录中最新的压缩边界：摘要成为替代历史，保留下来的近期消息决定之后保留的起点。
    """
    path = _transcript(tmp_path)

    assert claude_transcript_path(tmp_path / "config", SESSION_ID) == path
    compaction = read_claude_latest_compaction(path)

    assert compaction is not None
    assert compaction.boundary_uuid == "b-latest"
    assert compaction.created_at == "2026-09-02T00:02:00.000Z"
    # The kept messages start the retained part of the context.
    assert compaction.retain_from == "2026-09-02T00:00:00.000Z"
    assert compaction.replacement_history == ({
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "This session is being continued."}],
    },)


def test_reader_without_kept_messages_retains_from_the_boundary(tmp_path: Path) -> None:
    """Claude 会话压缩记入有效上下文

    压缩没有原样保留近期消息时，以压缩边界的时间作为保留起点。
    """
    path = _transcript(tmp_path, with_latest=False)

    compaction = read_claude_latest_compaction(path)

    assert compaction is not None
    assert compaction.boundary_uuid == "b-first"
    assert compaction.retain_from == compaction.created_at
    assert compaction.replacement_history[0]["content"][0]["text"] == "first summary"


def test_reader_ignores_a_session_that_never_compacted(tmp_path: Path) -> None:
    """Claude 会话压缩记入有效上下文

    从未压缩过的会话不产生压缩记录；找不到会话记录时不做任何事。
    """
    project = tmp_path / "config" / "projects" / "-work"
    project.mkdir(parents=True)
    path = project / f"{SESSION_ID}.jsonl"
    path.write_text(_entry(type="user", uuid="u", message={"content": "hi"}) + "\n")

    assert read_claude_latest_compaction(path) is None
    assert claude_transcript_path(tmp_path / "config", "missing-session") is None


def _put(store: AgentDataStore, segment_id: str, created_at: str, agent_id: str, execution_id: str = "") -> None:
    store.put_segment(
        segment_id=segment_id,
        segment_type="model",
        content=segment_id,
        started_at=created_at,
        finished_at=created_at,
        created_by_actor_ref="sample_admin",
        performed_by_actor_ref="claude",
        created_by_agent_id=agent_id,
        execution_id=execution_id,
    )


def _earlier_turn(
    store: AgentDataStore, session_ref: str, agent_id: str, members: list[tuple[str, str]],
) -> list[str]:
    """An earlier turn of the session that recorded ``members`` (ID, time) in the trace."""

    execution_id = f"execution-earlier-{agent_id}"
    store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="sample_admin",
                          provider="claude", started_at=members[0][1])
    store.bind_execution_provider_session(execution_id, provider_session_ref=session_ref)
    for segment_id, created_at in members:
        _put(store, segment_id, created_at, agent_id, execution_id)
    ids = [segment_id for segment_id, _ in members]
    store.append_agent_segments(agent_id, ids, actor_ref='claude')
    return ids


def _claude_session(store: AgentDataStore, agent_id: str, backend_session_id: str) -> str:
    provider_session = store.ensure_provider_session(
        provider="claude",
        backend_route_id="claude_one",
        backend_session_id=backend_session_id,
        agent_id=agent_id,
        actor_ref="sample_admin",
    )
    return provider_session["provider_session_ref"]


def test_compaction_keeps_members_recorded_after_the_kept_messages() -> None:
    """Claude 会话压缩记入有效上下文

    补记 Claude 压缩后，Provider context 以压缩片段开头，只保留被保留消息及之后的成员；同一次压缩不会重复记录。
    """
    store = AgentDataStore()
    agent_id = "resident_claude_compaction"
    session_ref = _claude_session(store, agent_id, "claude_session_keep")
    _earlier_turn(store, session_ref, agent_id, [
        ("segment_before", "2026-09-01T00:00:00Z"),
        ("segment_kept", "2026-09-02T00:00:00Z"),
        ("segment_after", "2026-09-02T00:03:00Z"),
    ])

    segment_id, created = persist_provider_compaction(
        store,
        provider_session_ref=session_ref,
        agent_id=agent_id,
        operation_id="claude-compaction-backfill:b-latest",
        actor_ref="portal",
        provider="claude",
        content={"replacement_history": [{"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "summary"}]}]},
        created_at="2026-09-02T00:02:00.000Z",
        retain_from="2026-09-02T00:00:00.000Z",
        provider_compaction_ref="b-latest",
    )

    assert created
    assert store.session_context_ids(session_ref) == [
        segment_id, "segment_kept", "segment_after",
    ]
    segment = store.segment(segment_id)
    assert segment["segment_type"] == "compaction"
    assert segment["metadata"]["provider_compaction_ref"] == "b-latest"

    # Observing the same Provider compaction again changes nothing.
    again, created_again = persist_provider_compaction(
        store,
        provider_session_ref=session_ref,
        agent_id=agent_id,
        operation_id="another-execution",
        actor_ref="portal",
        provider="claude",
        content={"replacement_history": [{"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "summary"}]}]},
        created_at="2026-09-02T00:02:00.000Z",
        retain_from="2026-09-02T00:00:00.000Z",
        provider_compaction_ref="b-latest",
    )
    assert (again, created_again) == (segment_id, False)
    assert store.session_context_ids(session_ref) == [
        segment_id, "segment_kept", "segment_after",
    ]


def _claude_turn_request(agent_id: str) -> ExecutionRequest:
    return ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="claude",
        operation="turn",
        agent_id=agent_id,
        correlation_id="job_claude_compaction",
        session=SessionOptions(
            mode="persistent",
            binding_id=f"resident:{agent_id}",
            backend_route_id="claude_one",
        ),
    )


def test_turn_end_compaction_keeps_the_current_turn_after_the_summary() -> None:
    """Claude 会话压缩记入有效上下文

    一轮结束时记录的 Claude 压缩替代更早的上下文，本轮的输入与回答留在压缩片段之后。
    """
    store = AgentDataStore()
    agent_id = "resident_claude_turn"
    session_ref = _claude_session(store, agent_id, "claude_session_turn")
    _earlier_turn(store, session_ref, agent_id, [("segment_old_turn", "2020-01-01T00:00:00Z")])
    projector = AgentDataProjector(store)
    request = _claude_turn_request(agent_id)
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="claude_session_turn",
        backend_route_id="claude_one",
        is_new_backend_session=False,
    )
    projector.begin(request, session)
    projector.observe_invocation(
        request.execution_id,
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        [],
    )
    projector.observe_compaction(
        execution_id=request.execution_id,
        provider="claude",
        backend_session_id="claude_session_turn",
        backend_route_id="claude_one",
        content={"replacement_history": [{"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "summary"}]}]},
        created_at="2021-01-01T00:00:00.000Z",
        # Claude kept the current question: everything recorded since then stays.
        retain_from="2021-01-01T00:00:00.000Z",
        provider_compaction_ref="b-turn",
    )
    final_segment_id = projector.complete(
        execution_id=request.execution_id,
        backend_session_id="claude_session_turn",
        backend_route_id="claude_one",
        turn_id="turn_after_compaction",
        response="answer",
    )
    from tests.cases.test_agent_data_projection import _deliver_captured_message
    _deliver_captured_message(store, agent_id, final_segment_id)

    context_ids = store.session_context_ids(session_ref)
    assert "segment_old_turn" not in context_ids
    assert store.segment(context_ids[0])["segment_type"] == "compaction"
    assert store.segment(context_ids[1])["content"] == "continue"
    assert context_ids[-1] == final_segment_id


class _RecordingContext:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_compaction(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_claude_backend_records_the_transcript_compaction_after_a_turn(tmp_path: Path) -> None:
    """Claude 会话压缩记入有效上下文

    Claude 执行层在一轮结束后把会话记录中的最新压缩交给 AgentData 记录。
    """
    _transcript(tmp_path)
    context = _RecordingContext()

    ClaudeBackend().record_provider_compaction(
        context=context,  # type: ignore[arg-type]
        backend_session_id=SESSION_ID,
        backend_route_id="claude_one",
        config_dir=str(tmp_path / "config"),
    )

    assert len(context.calls) == 1
    call = context.calls[0]
    assert call["provider"] == "claude"
    assert call["backend_session_id"] == SESSION_ID
    assert call["provider_compaction_ref"] == "b-latest"
    assert call["retain_from"] == "2026-09-02T00:00:00.000Z"
    assert call["content"] == {"replacement_history": [{
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "This session is being continued."}],
    }]}


def test_claude_backend_compaction_check_never_fails_the_turn(tmp_path: Path) -> None:
    """Claude 会话压缩记入有效上下文

    读取或记录 Claude 压缩失败时只记录警告，不让已经完成的轮次失败。
    """
    class _Failing:
        def record_compaction(self, **_kwargs: object) -> None:
            raise RuntimeError("store unavailable")

    _transcript(tmp_path)
    ClaudeBackend().record_provider_compaction(
        context=_Failing(),  # type: ignore[arg-type]
        backend_session_id=SESSION_ID,
        backend_route_id="claude_one",
        config_dir=str(tmp_path / "config"),
    )
    context = _RecordingContext()
    ClaudeBackend().record_provider_compaction(
        context=context,  # type: ignore[arg-type]
        backend_session_id="session-without-transcript",
        backend_route_id="claude_one",
        config_dir=str(tmp_path / "config"),
    )
    assert context.calls == []


def test_compaction_sits_where_it_happened_in_the_agent_trace() -> None:
    """Claude 会话压缩记入有效上下文

    压缩片段放在 Agent trace 中第一条被保留消息之前；之前放在 trace 末尾的同一次压缩再次记录时被移回原位。

    A Fork from a later turn starts at this compaction, not the whole history."""
    store = AgentDataStore()
    agent_id = "resident_claude_placement"
    session_ref = _claude_session(store, agent_id, "claude_session_placement")
    _earlier_turn(store, session_ref, agent_id, [
        ("segment_place_before", "2026-09-01T00:00:00Z"),
        ("segment_place_kept", "2026-09-02T00:00:00Z"),
        ("segment_place_after", "2026-09-02T00:03:00Z"),
    ])
    def record(operation_id: str) -> tuple[str, bool]:
        return persist_provider_compaction(
            store,
            provider_session_ref=session_ref,
            agent_id=agent_id,
            operation_id=operation_id,
            actor_ref="portal",
            provider="claude",
            content={"replacement_history": [{"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "summary"}]}]},
            created_at="2026-09-02T00:02:00.000Z",
            retain_from="2026-09-02T00:00:00.000Z",
            provider_compaction_ref="b-place",
        )

    segment_id, _created = record("claude-compaction-backfill:b-place")

    expected = ["segment_place_before", segment_id, "segment_place_kept", "segment_place_after"]
    assert store.sequence_segment_ids(store.trace_sequence_id(agent_id)) == expected

    # Observing the same Provider compaction again leaves it where it is.
    again, created_again = record("later")
    assert (again, created_again) == (segment_id, False)
    assert store.sequence_segment_ids(store.trace_sequence_id(agent_id)) == expected
