"""Delayed terminal events retain their execution after an input is retried.

:module: resident.conversation
"""
import pytest

from management_common.principal import OperationIdentity
from management_portal.execution import execution_inputs
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import lineage_primitives, trigger_paths

RESIDENT = "resident_111111111111"
LINEAGE = "lineage_111111111111"
SOURCE = "event_source_111111111111"
MESSAGE = "msg_retried_input"
CREATED = "2026-11-01T00:00:00Z"
FINISHED = "2026-11-01T00:01:00Z"
OBSERVED = "2026-11-01T00:01:01Z"
RETRIED = "2026-11-01T00:02:00Z"
LATER = "2026-11-01T00:03:00Z"
EVENT_TYPES = {"completed": "resident.work_finished", "failed": "resident.work_failed", "cancelled": "resident.work_cancelled"}


def attempt(store, identity, *, at, status=None, finished_at=FINISHED):
    store.reserve_execution(execution_id=identity, agent_id=RESIDENT, actor_ref="sample_admin", provider="codex", created_at=at)
    store.begin_execution(execution_id=identity, agent_id=RESIDENT, actor_ref="sample_admin", provider="codex", started_at=at)
    execution_inputs.attach(store, identity, [{"message_id": MESSAGE, "role": "primary", "accepted_at": at}])
    if status:
        store.finish_execution(identity, status=status, finished_at=finished_at)


def event(status, *, at=OBSERVED):
    return lineage_primitives.make_event(EVENT_TYPES[status], subject_resident_id=RESIDENT,
        occurred_at=at, correlation=f"{SOURCE}:{RESIDENT}:{MESSAGE}", payload={"message_id": MESSAGE},
        identity=OperationIdentity.for_actor("sample_admin").to_dict())


@pytest.mark.parametrize("status", list(EVENT_TYPES))
def test_terminal_notice_resolves_its_original_attempt_while_retry_runs(status):
    store = get_agent_data_store()
    attempt(store, "execution-original", at=CREATED, status=status)
    attempt(store, "execution-retry", at=RETRIED)
    assert execution_inputs.latest(store, RESIDENT, MESSAGE)["status"] == "running"
    resolved = trigger_paths.resident_terminal_event(LINEAGE, event(status), RESIDENT, MESSAGE)
    assert resolved.payload["cause"] == {"kind": "execution", "id": "execution-original"}
    assert resolved.payload["execution_id"] == "execution-original"


@pytest.mark.parametrize("status", list(EVENT_TYPES))
def test_accepted_terminal_occurrence_never_changes_cause_when_input_finishes_again(status):
    store = get_agent_data_store()
    attempt(store, "execution-original", at=CREATED, status=status)
    original = trigger_paths.resident_terminal_event(LINEAGE, event(status), RESIDENT, MESSAGE)
    path = trigger_paths.begin_event(LINEAGE, SOURCE, original.to_dict(), cause_kind="execution", cause_id="execution-original")
    attempt(store, "execution-retry", at=RETRIED, status=status, finished_at=LATER)
    redelivery = trigger_paths.resident_terminal_event(LINEAGE, event(status, at=LATER), RESIDENT, MESSAGE)
    assert redelivery.to_dict() == original.to_dict()
    assert trigger_paths.begin_event(LINEAGE, SOURCE, redelivery.to_dict(),
        cause_kind="execution", cause_id=redelivery.payload["cause"]["id"]) == path


def test_future_or_wrong_terminal_outcome_cannot_prove_an_earlier_notice():
    store = get_agent_data_store()
    attempt(store, "execution-original", at=CREATED, status="failed")
    attempt(store, "execution-retry", at=RETRIED, status="completed", finished_at=LATER)
    with pytest.raises(ValueError, match="terminal Agent Execution"):
        trigger_paths.resident_terminal_event(LINEAGE, event("completed"), RESIDENT, MESSAGE)


@pytest.mark.parametrize("observed,finished,next_bucket", [
    ("2026-11-01T00:01:00Z", "2026-11-01T00:01:00.123456Z", "2026-11-01T00:01:01Z"),
    ("2026-11-01T00:01:00.123Z", "2026-11-01T00:01:00.123456Z", "2026-11-01T00:01:00.124Z"),
    ("2026-11-01T08:01:00+08:00", "2026-11-01T00:01:00.123456Z", "2026-11-01T00:01:01Z"),
])
def test_notification_precision_matches_microsecond_execution_records(observed, finished, next_bucket):
    store = get_agent_data_store()
    attempt(store, "execution-original", at=CREATED, status="failed", finished_at=finished)
    attempt(store, "execution-later", at=finished, status="failed", finished_at=next_bucket)
    resolved = trigger_paths.resident_terminal_event(LINEAGE, event("failed", at=observed), RESIDENT, MESSAGE)
    assert resolved.payload["execution_id"] == "execution-original"

