from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor


from management_portal.society_service import changes, invariant_locks, lineage_state
from management_portal.society_service import lineage_aggregate
from tests.cases.test_hatg_projection_receipts import execution_record, projection_fixture, submit  # noqa: F401
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401 - fixture


def test_projection_retry_and_a_concurrent_commit_publish_in_order_and_end_current(
    projection_fixture, monkeypatch, record_property
):
    """投影补齐与并发提交按顺序发布并以当前状态结束

    验证投影补齐发布期间另一次提交到达时，两次发布依次进行，最后一次发布携带当前状态，补齐后执行记录不再待确认。

    Publications of one Lineage are serialized; the last one carries the current state."""

    before, request, _records = projection_fixture
    original_projection = lineage_state._sync_lineage_resource_identity
    notifications = []
    monkeypatch.setattr(changes, "lineage_changed", lambda *args, **kwargs: notifications.append(kwargs))

    def unavailable(_projection):
        raise OSError("isolated initial projection failure")

    monkeypatch.setattr(lineage_state, "_sync_lineage_resource_identity", unavailable)
    initial = submit(request)
    assert initial.error["code"] == "instruction.projection_pending"
    committed_revision = initial.error["details"]["projection_failures"][0]["committed_revision"]
    assert committed_revision == before["revision"] + 1
    instruction_id = initial.error["details"]["instruction_id"]
    initial_record = execution_record(instruction_id)
    notifications_after_commit = len(notifications)
    snapshot_read = threading.Event()
    allow_old_publish = threading.Event()
    newer_committed = threading.Event()
    projections = []
    original_publish = lineage_state._publish_lineage_resource_projection

    def observe_publish(lineage_id, revision, **kwargs):
        if revision is not None and revision > committed_revision:
            newer_committed.set()
        return original_publish(lineage_id, revision, **kwargs)

    def delayed_projection(projection):
        assert not invariant_locks.lock_held()
        if projection["revision"] == committed_revision:
            snapshot_read.set()
            assert allow_old_publish.wait(5), "fixture must release the delayed old projection"
        original_projection(projection)
        projections.append(copy.deepcopy(projection))

    monkeypatch.setattr(lineage_state, "_sync_lineage_resource_identity", delayed_projection)
    monkeypatch.setattr(lineage_state, "_publish_lineage_resource_projection", observe_publish)
    with ThreadPoolExecutor(max_workers=2) as executor:
        recovery = executor.submit(submit, copy.deepcopy(request))
        try:
            assert snapshot_read.wait(5), "retry must first read and authorize its old snapshot"
            newer_request = {
                **request,
                "idempotency_key": "wall02-newer-commit-during-projection-recovery",
                "description": "newer committed while projection retry waits",
            }
            normal_commit = executor.submit(submit, newer_request)
            assert newer_committed.wait(5), "the domain commit must not wait for a slow external projection"
            assert lineage_aggregate.read_lineage_state(before["lineage_id"])["revision"] == committed_revision + 1
            assert projections == []
        finally:
            allow_old_publish.set()
        recovered = recovery.result(timeout=5)
        assert recovered.status == "committed" and recovered.replayed, recovered
        normal_commit.result(timeout=5).raise_for_error()
        newer = lineage_aggregate.read_lineage_state(before["lineage_id"])

    # The retry published the snapshot it read; the newer commit's publication
    # waited for it and then published the newer state.
    assert [projection["revision"] for projection in projections] == [committed_revision, newer["revision"]]
    assert execution_record(instruction_id)["references"] == initial_record["references"]
    assert submit(request).status == "committed"
    current = lineage_aggregate.read_lineage_state(before["lineage_id"])
    assert current["revision"] == newer["revision"]
    assert current["description"] == newer["description"]
    assert len(notifications) == notifications_after_commit + 1
    receipt = execution_record(instruction_id)
    assert receipt["status"] == "committed"
    assert receipt.get("error") is None
    observed = {
        "original_committed_revision": committed_revision,
        "current_domain_revision": current["revision"],
        "published_projection_revisions": [projection["revision"] for projection in projections],
        "last_projection_description": projections[-1]["description"],
        "current_domain_description": current["description"],
        "receipt_status": receipt["status"],
        "receipt_error": receipt.get("error"),
        "domain_or_notifications_repeated_by_retry": False,
    }
    record_property("projection_race_observation", json.dumps(observed, ensure_ascii=False))
    assert projections[-1]["revision"] == current["revision"], json.dumps(observed, ensure_ascii=False)
    assert projections[-1]["description"] == current["description"]
