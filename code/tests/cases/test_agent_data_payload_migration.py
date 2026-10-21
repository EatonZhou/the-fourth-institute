from __future__ import annotations

from management_portal.execution.agent_data_store import AgentDataStore


def test_review_reads_payload_body_and_following_input():
    """Harness 评审包从 AgentData 读取请求、最终回答正文、下一条用户输入及间隔秒数"""
    from management_portal.execution.harness_evolution_evaluation import TurnOutcome, _review_packet

    data = AgentDataStore()
    data.ensure_agent("review-agent")
    for eid, start in (("review-first", "2026-10-11T18:00:00Z"), ("review-next", "2026-10-11T18:01:00Z")):
        data.begin_execution(execution_id=eid, agent_id="review-agent", actor_ref="resident", provider="codex", started_at=start)
    data.put_segment(segment_type="input", content="<user-message>request</user-message>", execution_id="review-first")
    data.put_segment(segment_type="model", content="answer " * 3000, metadata={"kind": "final_answer"}, execution_id="review-first")
    data.put_segment(segment_type="input", content="<user-message>follow up</user-message>", execution_id="review-next")
    data.finish_execution("review-first", status="completed", finished_at="2026-10-11T18:00:30Z")
    turn = TurnOutcome(execution_id="review-first", provider="codex", cell="codex/general", arm="champion", version=1,
                       origin="user", agent_id="review-agent", status="completed", failure_kind="none", input_tokens=1,
                       uncached_input_tokens=1, output_tokens=1, provider_latency_ms=1, quality="unknown", recorded_at=0)
    packet = _review_packet(turn)
    assert packet["request"] == "request"
    assert packet["answer"].startswith("answer " * 100)
    assert packet["next_message"] == "follow up"
    assert packet["next_message_after_seconds"] == 30
