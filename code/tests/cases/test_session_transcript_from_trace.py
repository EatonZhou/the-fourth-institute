"""A Provider session's transcript and context are read from its Agent's trace.

Since 2026-10-06 the trace is the one record of what happened: a session's
transcript (what a Fork from one of its turns cuts) is the session's own work
in the trace, its compactions where they happened, and the inherited context
its context seed marks list.  Since 2026-10-07 its current context is that
transcript from its last compaction on.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations
from tests.cases.agent_data_test_support import patch_store_clock, session_transcript_ids

from management_portal.execution.agent_data_session_capture import persist_provider_compaction
from management_portal.execution.agent_data_store import AgentDataStore

AGENT = "resident_trace_transcript"


def _session(store: AgentDataStore, backend_session_id: str, agent_id: str = AGENT, **source: str) -> str:
    return store.ensure_provider_session(
        provider="codex", backend_route_id="zero", backend_session_id=backend_session_id,
        agent_id=agent_id, actor_ref="sample_admin", **source,
    )["provider_session_ref"]


def _turn(store: AgentDataStore, session_ref: str, number: int, agent_id: str = AGENT) -> list[str]:
    """One turn of the session: its input and reply, in the trace and the context."""

    execution_id = f"execution-{agent_id}-{number}"
    store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="sample_admin",
                          provider="codex", started_at=f"2026-10-01T00:{number:02d}:00Z")
    store.bind_execution_provider_session(execution_id, provider_session_ref=session_ref)
    ids = [
        store.put_segment(
            segment_id=f"segment-{agent_id}-{number}-{kind}", segment_type=kind, content=f"turn {number} {kind}",
            started_at=f"2026-10-01T00:{number:02d}:{second}Z", created_by_agent_id=agent_id,
            execution_id=execution_id, metadata={"role": "user" if kind == "input" else "assistant"},
        )
        for kind, second in (("input", "10"), ("model", "40"))
    ]
    store.append_agent_segments(agent_id, ids, actor_ref='sample_admin')
    store.finish_execution(execution_id, status="completed", finished_at=f"2026-10-01T00:{number:02d}:50Z",
                           provider_turn_id=f"turn-{number}")
    return ids


def _trace(store: AgentDataStore, agent_id: str = AGENT) -> list[str]:
    """The trace without the receipts a finished turn leaves."""

    return [
        item["segment_id"] for item in store.read_sequence(store.trace_sequence_id(agent_id), limit=100)["segments"]
        if item["metadata"].get("kind") != "session_input_acceptance"
    ]


def _snapshot_ids(store: AgentDataStore, backend_session_id: str, turn_id: str, agent_id: str = AGENT) -> list[str]:
    snapshot = store.provider_context_snapshot(
        provider="codex", backend_route_id="zero", backend_session_id=backend_session_id,
        agent_id=agent_id, through_turn_id=turn_id,
    )
    return [item["segment_id"] for item in snapshot["segments"]]


def test_transcript_reads_current_content_and_keeps_equal_occurrences() -> None:
    """会话 transcript 读取被改写后的当前片段，内容相同的两条输入都保留

    :importance: P2"""
    store = AgentDataStore()
    session = _session(store, "thread-main")
    first = _turn(store, session, 1)
    execution_id = "execution-next"
    store.begin_execution(execution_id=execution_id, agent_id=AGENT, actor_ref="sample_admin", provider="codex", started_at="2026-10-08T01:00:00Z")
    store.bind_execution_provider_session(execution_id, provider_session_ref=session)
    ids = [store.put_segment(segment_type="input", content="same", execution_id=execution_id,
                             created_by_agent_id=AGENT) for _ in range(2)]
    store.append_agent_segments(AGENT, ids, actor_ref="sample_admin")
    store.transform_agent_segments(AGENT, [ids[0]], segment_id="segment_changed", content="changed",
        actor_ref="agent_harness", operation_id=execution_id, operation_type="agent_harness.prepare_invocation")
    store.record_input_acceptance(execution_id, agent_id=AGENT, provider_turn_id="turn-next")
    assert session_transcript_ids(store, session) == [*first, "segment_changed", ids[1]]
    assert _trace(store) == [*first, "segment_changed", ids[1]]


def test_fork_from_history_starts_at_the_last_compaction_before_the_cut() -> None:
    """从历史 Fork 在 trace 中截断并从截断前最后一次压缩开始

    事后记录的压缩挪到 trace 中它保留的第一条消息之前；从压缩之前的轮次 Fork 不含压缩，从之后的轮次 Fork 从该压缩开始，而不是整段未压缩历史。

    :design: decision_b1ab45c4b3ec
    """
    store = AgentDataStore()
    session_ref = _session(store, "thread-main")
    turns = [_turn(store, session_ref, number) for number in (1, 2, 3)]
    # Observed after turn 3 ended: the Provider compacted during turn 3 and
    # kept its input and reply.
    compaction, created = persist_provider_compaction(
        store, provider_session_ref=session_ref, agent_id=AGENT, operation_id=f"execution-{AGENT}-3",
        actor_ref="sample_admin", provider="codex", created_at="2026-10-01T00:03:05Z",
        retain_from="2026-10-01T00:03:10Z", content={"replacement_history": ["summary of turns 1 and 2"]},
    )
    assert created
    assert _trace(store) == [*turns[0], *turns[1], compaction, *turns[2]]
    assert store.session_context_ids(session_ref) == [compaction, *turns[2]]

    assert _snapshot_ids(store, "thread-main", "turn-2") == [*turns[0], *turns[1]]
    assert _snapshot_ids(store, "thread-main", "turn-3") == [compaction, *turns[2]]


def test_recovered_turn_does_not_bring_back_what_a_compaction_replaced(monkeypatch) -> None:
    """Provider 会话的 transcript 从 Agent trace 读出

    :importance: P2
    """
    # The transformed input is prepared before the replies/compaction below.
    patch_store_clock(monkeypatch, lambda: '2026-10-01T00:02:01Z')
    store = AgentDataStore()
    session_ref = _session(store, "thread-main")
    first = _turn(store, session_ref, 1)
    execution_id = "execution-long-turn"
    store.begin_execution(execution_id=execution_id, agent_id=AGENT, actor_ref="sample_admin",
                          provider="codex", started_at="2026-10-01T00:02:00Z")
    store.bind_execution_provider_session(execution_id, provider_session_ref=session_ref)
    earlier, kept = (
        store.put_segment(
            segment_id=f"segment-long-{name}", segment_type="model", content=name,
            started_at=f"2026-10-01T00:02:{second}Z", created_by_agent_id=AGENT, execution_id=execution_id,
        )
        for name, second in (("earlier", "20"), ("kept", "40"))
    )
    message, prepared = (
        store.put_segment(
            segment_id=f"segment-long-{name}", segment_type=segment_type, content=content,
            started_at="2026-10-01T00:02:00Z", created_by_agent_id=AGENT, execution_id=execution_id,
            metadata={"role": "user", "kind": kind},
        )
        for name, segment_type, content, kind in (
            ("input", "input", "a long task", "provider_input"),
            ("prepared", "harness", "a long task, as prepared", "effective_provider_input"),
        )
    )
    turn = [message, prepared, earlier, kept]
    store.append_agent_segments(AGENT, [message, earlier, kept], actor_ref='sample_admin')
    store.transform_agent_segments(AGENT, [message], segment_id="segment-long-current", content="prepared",
        actor_ref="agent_harness", operation_id=execution_id, operation_type="agent_harness.prepare_invocation",
        execution_id=execution_id)
    prepared = "segment-long-current"
    turn = [prepared, earlier, kept]
    # The trace already contains the current input; reads need no suppression.
    assert store.session_context_ids(session_ref) == [*first, prepared, earlier, kept]

    def compact() -> tuple[str, bool]:
        # The Provider compacted mid-turn and kept the latest message (as
        # Claude Code does); the turn's end reads the same compaction again.
        return persist_provider_compaction(
            store, provider_session_ref=session_ref, agent_id=AGENT, operation_id=execution_id,
            actor_ref="sample_admin", provider="codex", created_at="2026-10-01T00:02:45Z",
            retain_from="2026-10-01T00:02:40Z", content={"replacement_history": ["summary"]},
            provider_compaction_ref="claude-boundary-1",
        )

    compaction, created = compact()
    assert created
    assert store.session_context_ids(session_ref) == [compaction, kept]
    trace = [*first, prepared, earlier, compaction, kept]
    assert _trace(store) == trace

    # A Worker handover: the next attempt records the turn's input and
    # events again, then reads the same compaction at the turn's end.
    store.append_agent_segments(AGENT, turn, actor_ref='sample_admin')
    assert compact() == (compaction, False)
    assert store.session_context_ids(session_ref) == [compaction, kept]
    assert _trace(store) == trace


def test_context_seed_mark_puts_inherited_context_where_the_session_received_it() -> None:
    """继承上下文标记把 Fork 继承的内容放进会话 transcript

    context seed 标记写在会话收到继承上下文的位置（本轮输入之前），会话 transcript 在该处包含被列出的继承片段；重复标记不变；再 Fork 时标记随历史一起复制。

    :design: decision_b1ab45c4b3ec
    """
    store = AgentDataStore()
    source = _session(store, "thread-source", agent_id="resident_trace_source")
    inherited = _turn(store, source, 1, agent_id="resident_trace_source")
    child = "resident_trace_child"
    store.fork_agent(source_agent_id="resident_trace_source", target_agent_id=child, actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_trace_source")))
    child_session = _session(store, "thread-child", agent_id=child)
    own = _turn(store, child_session, 2, agent_id=child)

    mark = store.record_context_received(child, child_session, inherited, actor_ref="sample_admin")
    trace = store.sequence_segment_ids(store.trace_sequence_id(child))
    assert _trace(store, child) == [*inherited, *own]
    assert store.operation_record(mark)["first_segment_id"] == inherited[0]
    # Marking the same context again changes nothing.
    assert store.record_context_received(child, child_session, inherited, actor_ref="sample_admin") == mark
    assert store.sequence_segment_ids(store.trace_sequence_id(child)) == trace

    assert session_transcript_ids(store, child_session) == [*inherited, *own]
    assert _snapshot_ids(store, "thread-child", "turn-2", agent_id=child) == [*inherited, *own]
    # The source session's own transcript is unchanged.
    assert session_transcript_ids(store, source) == inherited

    # A Fork owns its selected references; source delivery receipts are not copied.
    store.fork_agent(source_agent_id=child, target_agent_id="resident_trace_grandchild", actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(child)))
    snapshot = store.agent_context_snapshot("resident_trace_grandchild", provider_session_ref=child_session,
                                            through_turn_id="turn-2")
    assert [item["segment_id"] for item in snapshot["segments"]] == [*inherited, *own]
    assert store.agent_context_snapshot("resident_trace_grandchild")["segments"] == snapshot["segments"]


def test_native_fork_marks_its_source_context_in_the_working_agents_trace() -> None:
    """原生 Fork 会话在实际工作的 Resident trace 中标记源上下文

    原生 fork（如重启会话）以绑定名执行时，源会话上下文的标记写进 context_agent_id 指定的 Resident trace，新会话 transcript 为源上下文加自身轮次，源会话不变。

    :design: decision_b1ab45c4b3ec
    """
    store = AgentDataStore()
    source = _session(store, "thread-main")
    held = [*_turn(store, source, 1), *_turn(store, source, 2)]
    native = _session(
        store, "thread-native", agent_id="session-restart:" + AGENT,
        source_backend_session_id="thread-main", context_agent_id=AGENT,
    )
    assert store.session_context_ids(native, agent_id=AGENT) == held
    own = _turn(store, native, 3)

    trace = store.read_sequence(store.trace_sequence_id(AGENT))["segments"]
    marks = [item for item in trace if item["metadata"].get("kind") == "context_seed"]
    assert marks == []
    assert session_transcript_ids(store, native) == [*held, *own]
    assert session_transcript_ids(store, source) == held
    assert store.sequence_segment_ids(store.trace_sequence_id('session-restart:' + AGENT)) == []


def test_trace_members_move_to_where_they_happened() -> None:
    """trace 成员可移到发生处

    move_in_trace 把成员移到指定成员之前或之后，已在位或邻居不在 trace 时不动；place_in_trace_before 只在成员位于给定成员之后时把它移到其中最前一个之前。

    :design: decision_b1ab45c4b3ec
    """
    store = AgentDataStore()
    ids = [store.put_segment(segment_id=f"segment-move-{name}", segment_type="model", content=name,
                             created_by_agent_id=AGENT) for name in "abcd"]
    a, b, c, d = ids
    store.append_agent_segments(AGENT, ids, actor_ref='sample_admin')

    # Before the first of the given members, and only when it is after it.
    assert store.place_in_trace_before(AGENT, d, [c, b], actor_ref="sample_admin")
    assert store.sequence_segment_ids(store.trace_sequence_id(AGENT)) == [a, d, b, c]
    assert not store.place_in_trace_before(AGENT, a, [b, c], actor_ref="sample_admin")
    assert store.sequence_segment_ids(store.trace_sequence_id(AGENT)) == [a, d, b, c]
