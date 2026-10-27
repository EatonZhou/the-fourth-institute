"""Stop a precise input while a sessionless Resident waits for quota.

:importance: P1
"""
from dataclasses import replace
import time
import uuid

import pytest

from tests.cases.test_resident_execution_protocol import resident_record
from tests.cases.resident_delivery_test_support import published_delivery
from tests.cases.isa_test_support import reset_kernels, run
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.family_test_support import set_role
from tests.cases.society_service_test_support import publish_registered_lineage_members
from management_portal.execution.codex_quota import quota_sample
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution import slash_commands
from management_portal.society_service import (
    family_facade,
    lifecycle,
    resident_backend,
    resident_conversation_stream,
    resident_execution_work,
    resident_domain,
    resident_intake_domain,
    resident_lifecycle_domain,
    resident_protocol,
    resident_record_domain,
    resident_registration_domain,
    resident_session_backend_domain,
)


pytestmark = pytest.mark.usefixtures("registered_execution_defaults")


@pytest.fixture(autouse=True)
def isolated_isa():
    reset_kernels()
    yield
    reset_kernels()


def instruction(name, record, *, actor="CASE-A", **arguments):
    return run(name, {"resident_id": record["resident_id"], **arguments}, actor=actor, key=uuid.uuid4().hex)


@pytest.fixture
def sessionless(resident_record, monkeypatch):
    resident_backend.delete_session_identity(resident_record)
    resident_record["session_state"] = "pending_create"
    resident_record_domain.save_resident_record(resident_record)
    monkeypatch.setattr(resident_session_backend_domain, "handle_resident_native_command", lambda *a, **kw: {})
    monkeypatch.setattr(slash_commands, "handle_slash_command", lambda *a, **kw: {"handled": False})
    monkeypatch.setattr(resident_record_domain, "resolve_context_refs", lambda *a, **kw: {})
    monkeypatch.setattr(resident_registration_domain, "build_resident_prompt", lambda *a, **kw: "test prompt")
    scheduler = Scheduler(quota_reserve_enabled=True, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="quota-stop-test", build_id="test", capabilities=["agent_session:codex"])
    scheduler.update_account_quota("zero", quota_sample(
        {"rateLimits": {"primary": {"usedPercent": 100}}}, observed_at=time.time(),
    ))
    # Replace only the process boundary; the scheduler and durable state are real.
    monkeypatch.setattr(lifecycle, "notify_execution_cancellation", lambda eid, actor: scheduler.cancel(eid, actor=actor) if eid else None)
    return resident_record, scheduler


def deliver(record, text):
    return published_delivery(resident_domain.deliver_resident_message_content(
        record["resident_id"], "CASE-A", text,
    ))["message_id"]


def wait_for_quota(record, scheduler):
    job = resident_execution_work.read(record)
    request = replace(ExecutionRequest.new(
        actor="CASE-A", agent_id=record["resident_id"], prompt="isolated quota wait", service_type="agent_session", provider="codex", operation="create",
        session=SessionOptions(mode="persistent", binding_id=resident_backend.execution_binding_id(record), backend_route_id="zero"),
    ), execution_id=job["execution_id"])
    scheduler.submit(request)
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["scheduler"]["queue_reason"] == "quota_reserve"
    resident_execution_work.bind_execution(job, execution_id=request.execution_id, now="2026-10-19T12:00:00Z")
    resident_execution_work.write(record, job, snapshot=snapshot)
    assert not resident_backend.record_session_id(record)
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(record["resident_id"], job["user_message_id"])["state"] == "queued"
    return request.execution_id


def test_new_resident_quota_stop_is_repeatable_and_keeps_following(sessionless):
    record, scheduler = sessionless
    first = deliver(record, "first input")
    execution_id = wait_for_quota(record, scheduler)
    following = deliver(record, "leave queued")
    assert resident_execution_work.read(record)["user_message_id"] == first
    for _ in range(2):
        receipt = resident_protocol.cancel_resident_input(record["resident_id"], "CASE-A", first)
        assert receipt["cancellation_requested"] is True
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == first
    assert job["stop_requested_by"] == "CASE-A"
    assert [item["user_message_id"] for item in job["queue"]] == [following]
    assert scheduler.snapshot(execution_id)["status"] == "cancelled"
    assert resident_conversation_stream.get_repository().input_state(record["resident_id"], following)["state"] == "queued"


def test_manual_close_does_not_need_a_session_or_stop_quota_execution(sessionless):
    record, scheduler = sessionless
    first = deliver(record, "first input")
    execution_id = wait_for_quota(record, scheduler)
    for _ in range(2):
        receipt = resident_protocol.change_resident_state(record["resident_id"], "CASE-A", state_domain="intake", transition="close")
        assert receipt["intake_state"] == "closed"
    current = resident_record_domain.find_resident(record["resident_id"])
    assert not resident_backend.record_session_id(current)
    assert resident_lifecycle_domain.resident_intake_state(current) == "closed"
    assert resident_execution_work.read(current)["user_message_id"] == first
    assert not resident_execution_work.read(current).get("stop_requested_at")
    assert scheduler.snapshot(execution_id)["status"] == "queued"


def test_isa_cancel_stops_submitted_quota_waiting_bootstrap(sessionless):
    record, scheduler = sessionless
    first = deliver(record, "first input")
    execution_id = wait_for_quota(record, scheduler)
    following = deliver(record, "preserve following")
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(record["resident_id"], first)["state"] == "queued"
    for _ in range(2):
        result = instruction("CancelInput", record, message_id=first)
        assert result.status == "committed", result.error
        assert result.result["cancellation_requested"] is True
        assert result.result["message_id"] == first
    assert scheduler.snapshot(execution_id)["status"] == "cancelled"
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == first
    assert job["stop_requested_by"] == "CASE-A"
    assert [item["user_message_id"] for item in job["queue"]] == [following]
    assert repository.input_state(record["resident_id"], following)["state"] == "queued"


def test_isa_manual_close_without_session_preserves_quota_waiting_input(sessionless):
    record, scheduler = sessionless
    first = deliver(record, "first input")
    execution_id = wait_for_quota(record, scheduler)
    for _ in range(2):
        result = instruction("OpenIntake", record, open=False)
        assert result.status == "committed", result.error
        assert result.result["current"] == "closed"
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == first
    assert not job.get("stop_requested_at")
    assert scheduler.snapshot(execution_id)["status"] == "queued"
    assert not resident_backend.record_session_id(resident_record_domain.find_resident(record["resident_id"]))


def test_isa_cancel_waiting_input_leaves_current_quota_execution_alone(sessionless):
    record, scheduler = sessionless
    first = deliver(record, "quota wait")
    execution_id = wait_for_quota(record, scheduler)
    target = deliver(record, "withdraw only me")
    following = deliver(record, "preserve me")
    for _ in range(2):
        result = instruction("CancelInput", record, message_id=target)
        assert result.status == "committed", result.error
        assert result.result["input_state"]["state"] == "cancelled"
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == first
    assert not job.get("stop_requested_at")
    assert [item["user_message_id"] for item in job["queue"]] == [following]
    assert scheduler.snapshot(execution_id)["status"] == "queued"


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_isa_finished_input_preserves_result_and_new_work(sessionless, state):
    record, scheduler = sessionless
    target = "msg_" + uuid.uuid4().hex
    repository = resident_conversation_stream.get_repository()
    resident_lifecycle_domain.accept_resident_user_message(record["resident_id"],
        resident_intake_domain.build_message("user", "finished input", "CASE-A", message_id=target))
    repository.set_input_state(record["resident_id"], target, state)
    first = deliver(record, "new work")
    execution_id = wait_for_quota(record, scheduler)
    original = repository.input_state(record["resident_id"], target)
    for _ in range(2):
        result = instruction("CancelInput", record, message_id=target)
        assert result.status == "committed", result.error
        assert result.result["already_terminal"] is True
        assert result.result["input_state"] == original
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == first
    assert not job.get("stop_requested_at")
    assert scheduler.snapshot(execution_id)["status"] == "queued"


def test_isa_unauthorized_cancellation_does_not_write_stop_intent(sessionless, permissions):
    record, scheduler = sessionless
    target = deliver(record, "quota wait")
    execution_id = wait_for_quota(record, scheduler)
    permissions.users("CASE-A", "intruder")
    permissions.place(Fact("resident", record["resident_id"]))
    permissions.grant("CASE-A", record["resident_id"], "@admin")
    result = instruction("CancelInput", record, actor="intruder", message_id=target)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.forbidden"
    assert not resident_execution_work.read(record).get("stop_requested_at")
    assert scheduler.snapshot(execution_id)["status"] == "queued"


def test_isa_missing_input_is_not_reported_as_cancelled(sessionless):
    record, scheduler = sessionless
    target = deliver(record, "quota wait")
    execution_id = wait_for_quota(record, scheduler)
    result = instruction("CancelInput", record, message_id="msg_" + uuid.uuid4().hex)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.not_found"
    assert resident_execution_work.read(record)["user_message_id"] == target
    assert not resident_execution_work.read(record).get("stop_requested_at")
    assert scheduler.snapshot(execution_id)["status"] == "queued"


def test_cancel_does_not_swallow_queue_error_without_owner_change(sessionless, monkeypatch):
    record, scheduler = sessionless
    deliver(record, "quota wait")
    execution_id = wait_for_quota(record, scheduler)
    target = deliver(record, "still held in queue")
    before = resident_execution_work.read(record)

    def broken_queue(*args, **kwargs):
        raise KeyError("unchanged queue failure")

    monkeypatch.setattr(resident_intake_domain, "cancel_resident_queued_input", broken_queue)
    with pytest.raises(KeyError, match="unchanged queue failure"):
        resident_protocol.cancel_resident_input(record["resident_id"], "CASE-A", target)
    assert resident_execution_work.read(record) == before
    assert resident_conversation_stream.get_repository().input_state(record["resident_id"], target)["state"] == "queued"
    assert scheduler.snapshot(execution_id)["status"] == "queued"


@pytest.mark.parametrize("waiting", [False, True])
def test_nested_family_cancel_resolves_member_owner_under_quota(sessionless, waiting):
    record, scheduler = sessionless
    first = deliver(record, "current quota wait")
    execution_id = wait_for_quota(record, scheduler)
    target = deliver(record, "waiting target") if waiting else first
    following = deliver(record, "preserve following")
    facades = []
    for index in range(2):
        facade_id = "resident_" + ("b" if index == 0 else "c") * 12
        publish_registered_lineage_members({
            "lineage_id": record["lineage_id"],
            "nodes": [{"id": facade_id, "title": f"Nested test facade {index}"}],
        }, actor="CASE-A")
        set_role(facade_id, family_id=f"fbi_00000000000{index}", actor="CASE-A")
        facades.append(resident_record_domain.find_resident(facade_id))
    outer, inner = facades
    outer_message = deliver(outer, "outer request")
    inner_message = deliver(inner, "inner request")
    family_facade.complete_relay((inner["resident_id"], inner_message), record["resident_id"], target)
    family_facade.complete_relay((outer["resident_id"], outer_message), inner["resident_id"], inner_message)
    result = instruction("CancelInput", outer, message_id=outer_message)
    assert result.status == "committed", result.error
    assert result.result["facade_message_id"] == outer_message
    assert result.result["member_message_id"] == target
    assert result.result["resident_id"] == record["resident_id"]
    job = resident_execution_work.read(record)
    assert [item["user_message_id"] for item in job["queue"]] == [following]
    if waiting:
        assert result.result["input_state"]["state"] == "cancelled"
        assert not job.get("stop_requested_at")
        assert scheduler.snapshot(execution_id)["status"] == "queued"
    else:
        assert result.result["cancellation_requested"] is True
        assert job["stop_requested_by"] == "CASE-A"
        assert scheduler.snapshot(execution_id)["status"] == "cancelled"


def test_cancel_input_rechecks_owner_when_queued_input_is_promoted(sessionless, monkeypatch):
    """A queue-to-current handoff can keep the input queued under quota protection."""
    record, scheduler = sessionless
    first = deliver(record, "prior input")
    wait_for_quota(record, scheduler)
    target = deliver(record, "cancel this input")
    following = deliver(record, "preserve this input")
    assert resident_execution_work.read(record)["user_message_id"] == first
    cancel_queue = resident_protocol.cancel_resident_queue_item
    execution_ids = []

    def promote_before_cancel(resident_id, actor, message_id):
        assert message_id == target
        job = resident_execution_work.read(record)
        queue = job["queue"]
        resident_conversation_stream.get_repository().set_input_state(resident_id, first, "completed")
        resident_execution_work.promote_queue_item(job, queue.pop(0), queue, "2026-10-19T12:00:00Z")
        resident_execution_work.write(record, job)
        execution_ids.append(wait_for_quota(record, scheduler))
        return cancel_queue(resident_id, actor, message_id)

    monkeypatch.setattr(resident_protocol, "cancel_resident_queue_item", promote_before_cancel)
    receipt = resident_protocol.cancel_resident_input(record["resident_id"], "CASE-A", target)
    assert receipt["cancellation_requested"] is True
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == target
    assert job["stop_requested_by"] == "CASE-A"
    assert [item["user_message_id"] for item in job["queue"]] == [following]
    assert scheduler.snapshot(execution_ids[0])["status"] == "cancelled"
    assert resident_conversation_stream.get_repository().input_state(record["resident_id"], following)["state"] == "queued"
