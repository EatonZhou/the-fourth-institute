"""
:module: execution.persistence
:importance: P1
:design: decision_b1ab45c4b3ec
"""
from tests.cases.agent_data_test_support import session_transcript_ids
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import persist_provider_compaction
from management_portal.execution.session_manager import RuntimeSession, SessionManager
from tests.cases.test_session_transcript_from_trace import AGENT as TRACE_AGENT, _session, _turn as session_turn

def test_context_reads_only_the_tail_after_compaction(monkeypatch) -> None:
    """当前上下文仅加载压缩边界之后的轨迹尾部

    当前上下文仅加载压缩边界之后的轨迹尾部；覆盖真实格式、重放及边界行为。
    """
    store = AgentDataStore()
    session_ref = _session(store, "long-thread")
    session_turn(store, session_ref, 1)
    ids = [store.put_segment(
        segment_id=f"old-history-{index}", segment_type="model", content=f"old output {index}",
        started_at="2026-10-01T00:01:41Z", execution_id=f"execution-{TRACE_AGENT}-1",
        created_by_agent_id=TRACE_AGENT,
    ) for index in range(600)]
    store.append_agent_segments(TRACE_AGENT, ids, actor_ref='codex')
    session_turn(store, session_ref, 2)
    persist_provider_compaction(
        store, provider_session_ref=session_ref, agent_id=TRACE_AGENT, operation_id="compact-test",
        actor_ref="sample_admin", provider="codex", created_at="2026-10-01T00:02:05Z",
        retain_from="2026-10-01T00:02:10Z", content={"replacement_history": ["earlier summary"]},
    )
    runtime_session = RuntimeSession(
        mode="persistent", binding_id="", backend_session_id="long-thread",
        is_new_backend_session=False, provider_session_ref=session_ref,
    )
    assert SessionManager(agent_data=store).context_compacted_at(
        runtime_session, agent_id=TRACE_AGENT,
    ) == "2026-10-01T00:02:05Z"
    transcript = session_transcript_ids(store, session_ref)
    expected = transcript[next(index for index, identity in enumerate(transcript)
                               if store.segment(identity)["segment_type"] == "compaction"):]
    def reject_full_read(*_args, **_kwargs):
        raise AssertionError("context must not materialize the whole transcript")
    read_rows = store.trace_rows
    fetched = []
    def read_tail(connection, sequence_id, **options):
        page = read_rows(connection, sequence_id, **options)
        fetched.extend(page)
        return page
    monkeypatch.setattr(store, "_session_transcript_ids", reject_full_read)
    monkeypatch.setattr(store, "trace_rows", read_tail)
    assert store.session_context_ids(session_ref) == expected
    assert len(fetched) <= 256
    fetched.clear()
    fork_window = store.agent_context_snapshot(TRACE_AGENT, provider_session_ref=session_ref)
    assert [item['segment_id'] for item in fork_window['segments']] == expected
    assert len(fetched) <= 256


def test_context_tail_preserves_transcript_deduplication_across_later_seeds() -> None:
    """尾部继承标记与完整轨迹保持相同去重语义

    尾部继承标记与完整轨迹保持相同去重语义；覆盖真实格式、重放及边界行为。
    """
    store = AgentDataStore()
    session_ref = _session(store, "seeded-thread")
    earlier = session_turn(store, session_ref, 1)
    session_turn(store, session_ref, 2)
    persist_provider_compaction(
        store, provider_session_ref=session_ref, agent_id=TRACE_AGENT, operation_id="seed-compact",
        actor_ref="sample_admin", provider="codex", created_at="2026-10-01T00:02:05Z",
        retain_from="2026-10-01T00:02:10Z", content={"replacement_history": ["earlier summary"]},
    )
    store.record_context_received(TRACE_AGENT, session_ref, earlier, actor_ref="sample_admin")
    transcript = session_transcript_ids(store, session_ref)
    last_compaction = max(index for index, identity in enumerate(transcript)
                          if store.segment(identity)["segment_type"] == "compaction")
    assert store.session_context_ids(session_ref) == transcript[last_compaction:]
