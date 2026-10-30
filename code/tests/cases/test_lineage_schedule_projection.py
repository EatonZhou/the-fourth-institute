"""Public GetLineage must expose the scheduler's persisted due time."""

from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import lineage_program_runtime, lineage_program_schedules
from management_portal.society_service.isa import build_kernel
from tests.cases.test_isa_audit_g4 import make_lineage, registered_members  # noqa: F401
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR  # noqa: F401


def test_get_lineage_preserves_time_source_schedule_projection(primitive_store):
    """GetLineage 返回周期与定点时间事件源的下次触发时间和调度代次，且不改写运行时"""
    recurring_id = "event_source_recurring_projection"
    one_shot_id = "event_source_at_projection"
    recurring = {
        "event_type": "time.reached",
        "schedule_mode": "recurring",
        "recurrence_kind": "interval",
        "interval_seconds": 1800,
        "jitter_seconds": 60,
    }
    one_shot = {
        "event_type": "time.reached",
        "schedule_mode": "at",
        "scheduled_at": "2099-01-01T00:00:00Z",
    }
    created = make_lineage([
        {"id": source_id, "kind": "event_source", "title": source_id, "config": config}
        for source_id, config in ((recurring_id, recurring), (one_shot_id, one_shot))
    ])
    lineage_id = created["lineage_id"]
    lineage_program_schedules.observe_recurring_schedule(
        lineage_id, recurring_id, config=recurring,
    )
    before = lineage_program_runtime.read_runtime(lineage_id)
    persisted = before["event_sources"][recurring_id]
    assert persisted["next_due_at"]
    assert persisted["generation"] > 0

    result = build_kernel().execute(
        InstructionCall.from_payload({
            "instruction": "GetLineage", "arguments": {"lineage_id": lineage_id},
        }),
        InstructionContext(caller=ACTOR, entry="web"),
    ).raise_for_error()
    projected = result["lineage"]["program_event_source_runtime"]
    assert projected[recurring_id]["schedule_generation"] == persisted["generation"]
    assert projected[recurring_id]["next_scheduled_at"] == persisted["next_scheduled_at"]
    assert projected[recurring_id]["next_due_at"] == persisted["next_due_at"]
    assert projected[one_shot_id]["next_due_at"] == one_shot["scheduled_at"]
    assert lineage_program_runtime.read_runtime(lineage_id) == before
