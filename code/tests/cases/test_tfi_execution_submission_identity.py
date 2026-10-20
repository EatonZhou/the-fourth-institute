"""Only exact canonical admissions retry; durable digests are never rewritten."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
from itertools import product
import threading
import time
from types import SimpleNamespace

import pytest

from management_common.json_columns import json_value
from management_common.principal import execution_identity
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.scheduler import Scheduler, _json
from management_portal.execution.service import ExecutionServiceServer


NAMESPACES = ("archive", "tfi")
ALIASES = tuple(product(NAMESPACES, repeat=2))


def request(top="tfi", seed="tfi"):
    return replace(ExecutionRequest.new(
        actor="test", agent_id="resident_aaaaaaaaaaaa", prompt="synthetic prompt",
        service_type="agent_session", provider="codex", operation="turn",
        metadata={"fork_context_input": {
            "schema": f"{seed}.provider-context-seed.v1", "segments": [],
            "prompt": "synthetic inherited context", "portable_prompt": "synthetic portable context",
        }, "opaque": {"schema": "archive.user-defined.v1", "nested": {
            "schema": "archive.provider-context-seed.v1"}}},
    ), schema=f"{top}.execution-request.v8")


def renamed(original, top="tfi", seed="tfi"):
    payload = original.to_payload()
    payload["schema"] = f"{top}.execution-request.v8"
    payload["metadata"]["fork_context_input"]["schema"] = f"{seed}.provider-context-seed.v1"
    return ExecutionRequest.from_payload(payload)


def scheduler():
    return Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)


def durable_rows(queue, store, execution_id):
    with queue.database.read() as connection:
        queued = connection.execute(
            "SELECT request_json,xmin::text AS row_version,status FROM executions WHERE execution_id=%s",
            (execution_id,),
        ).fetchone()
        digest = connection.execute(
            "SELECT value,xmin::text AS row_version FROM execution_store_meta WHERE key=%s",
            ("request_digest:" + execution_id,),
        ).fetchone()
    with store.database.read() as connection:
        receipt = connection.execute(
            "SELECT data_json,xmin::text AS row_version FROM agent_operations WHERE operation_id=%s",
            ("execution_submission:" + execution_id,),
        ).fetchone()
    return tuple(dict(row) if row is not None else None for row in (queued, digest, receipt))


def forbid_prepare(_):
    pytest.fail("an existing admission must not prepare again")


@pytest.mark.parametrize("prepared", [False, True])
def test_rpc_exact_retry_reuses_original_payload_digest_and_receipt(prepared):
    store, queue = get_agent_data_store(), scheduler()
    original = request()
    first_payload = deepcopy(original.to_payload())
    calls = []
    def prepare(value):
        calls.append(value.execution_id)
        return replace(value, priority=8, metadata={**value.metadata, "prepared": True}) if prepared else value
    server = object.__new__(ExecutionServiceServer)
    server.scheduler = queue
    server._changed = lambda value: value
    server.preparation = SimpleNamespace(prepare=prepare)
    first = server.dispatch("execution.submit", first_payload)
    before = durable_rows(queue, store, original.execution_id)
    saved_digest = hashlib.sha256(_json(first_payload).encode()).hexdigest()
    assert before[1]["value"] == saved_digest
    assert json_value(before[2]["data_json"])["request_digest"] == saved_digest
    server.preparation.prepare = forbid_prepare
    retry = renamed(original).to_payload()
    untouched = deepcopy(retry)
    result = server.dispatch("execution.submit", retry)
    assert result == first and retry == untouched
    assert durable_rows(queue, store, original.execution_id) == before
    assert calls == [original.execution_id] and first_payload == original.to_payload()


def test_canonical_queue_without_digest_does_not_gain_or_rewrite_one():
    store, queue = get_agent_data_store(), scheduler()
    original = request()
    queue.submit(original)
    queue.database.write(lambda connection: connection.execute(
        "DELETE FROM execution_store_meta WHERE key=%s", ("request_digest:" + original.execution_id,),
    ))
    before = durable_rows(queue, store, original.execution_id)
    assert before[1] is None
    assert queue.submit(renamed(original), prepare=forbid_prepare)["execution_id"] == original.execution_id
    assert durable_rows(queue, store, original.execution_id) == before


def test_ack_retention_and_rebuilt_store_keep_original_admission():
    store, queue = get_agent_data_store(), scheduler()
    original = request()
    queue.submit(original)
    queue.cancel(original.execution_id, actor=original.actor)
    queue.acknowledge(original.execution_id, actor=original.actor)
    before = durable_rows(queue, store, original.execution_id)
    assert json_value(before[0]["request_json"]) == {}
    result = queue.submit(renamed(original), prepare=forbid_prepare)
    assert result["execution_id"] == original.execution_id and result["status"] == "cancelled"
    assert durable_rows(queue, store, original.execution_id) == before
    queue.prune_terminal(older_than_epoch=time.time() + 86401)
    rebuilt_store, rebuilt_queue = AgentDataStore(), scheduler()
    pruned = durable_rows(rebuilt_queue, rebuilt_store, original.execution_id)
    assert pruned[:2] == (None, None) and pruned[2] == before[2]
    with pytest.raises(ValueError, match="already submitted; scheduler receipt expired"):
        rebuilt_queue.submit(renamed(original), prepare=forbid_prepare)
    with pytest.raises(ValueError, match="different request"):
        rebuilt_queue.submit(replace(renamed(original), prompt="changed"), prepare=forbid_prepare)
    assert durable_rows(rebuilt_queue, rebuilt_store, original.execution_id) == pruned


@pytest.mark.parametrize("change", [
    "prompt", "provider", "priority", "agent", "opaque_schema", "opaque_nested_schema",
    "seed_prompt", "seed_segments", "seed_unknown_version", "seed_unknown_namespace",
    "missing_seed_schema", "missing_seed", "top_unknown_version", "top_numeric_version",
])
def test_business_and_unowned_changes_conflict_with_original_digest(change):
    store, queue = get_agent_data_store(), scheduler()
    original = request()
    queue.submit(original)
    before = durable_rows(queue, store, original.execution_id)
    payload = renamed(original).to_payload()
    seed = payload["metadata"]["fork_context_input"]
    if change == "prompt": payload["prompt"] = "changed"
    elif change == "provider": payload["provider"] = "claude"
    elif change == "priority": payload["priority"] = 8
    elif change == "agent":
        payload["agent_id"] = "resident_bbbbbbbbbbbb"
        payload["identity"] = execution_identity(payload["agent_id"], payload["actor"]).to_dict()
    elif change == "opaque_schema": payload["metadata"]["opaque"]["schema"] = "tfi.user-defined.v1"
    elif change == "opaque_nested_schema": payload["metadata"]["opaque"]["nested"]["schema"] = "tfi.provider-context-seed.v1"
    elif change == "seed_prompt": seed["prompt"] = "changed"
    elif change == "seed_segments": seed["segments"] = [{"segment_id": "segment_changed"}]
    elif change == "seed_unknown_version": seed["schema"] = "tfi.provider-context-seed.v99"
    elif change == "seed_unknown_namespace": seed["schema"] = "other.provider-context-seed.v1"
    elif change == "missing_seed_schema": seed.pop("schema")
    elif change == "missing_seed": payload["metadata"].pop("fork_context_input")
    elif change == "top_unknown_version": payload["schema"] = "tfi.execution-request.v99"
    elif change == "top_numeric_version": payload["schema_version"] = 99
    with pytest.raises(ValueError, match="schema|different request"):
        queue.submit(ExecutionRequest.from_payload(payload), prepare=forbid_prepare)
    assert durable_rows(queue, store, original.execution_id) == before


def test_concurrent_exact_admission_has_one_preparation_and_original_digest():
    store, queue = get_agent_data_store(), scheduler()
    original = request()
    entered, release = threading.Event(), threading.Event()
    prepared = []
    def prepare(value):
        prepared.append(value.execution_id)
        entered.set()
        assert release.wait(5)
        return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(queue.submit, original, prepare=prepare)
        try:
            assert entered.wait(5)
            second = pool.submit(scheduler().submit, renamed(original), prepare=forbid_prepare)
        finally:
            release.set()
        admitted, retried = first.result(timeout=10), second.result(timeout=10)
        # Transient event buffers belong to each Scheduler instance. Compare
        # the durable admission coordinates, not that process-local buffer.
        assert {key: value for key, value in admitted.items() if key != "events"} == {
            key: value for key, value in retried.items() if key != "events"}
    assert prepared == [original.execution_id]
    rows = durable_rows(queue, store, original.execution_id)
    assert json_value(rows[0]["request_json"]) == original.to_payload()
    assert rows[1]["value"] == json_value(rows[2]["data_json"])["request_digest"]


def test_withdrawn_admission_preserves_handoff_before_and_after_retention():
    from management_portal.execution.contracts import SessionOptions

    store, queue = get_agent_data_store(), scheduler()
    original = replace(request(), session=SessionOptions(mode="persistent", binding_id="fixture:bound"))
    queue.submit(original)
    receipt = queue.withdraw_unstarted(
        original.execution_id, actor=original.actor, binding_id="fixture:bound",
        operation_id="fixture-migration", replacement_execution_id=request().execution_id,
        performed_by=original.actor,
    )
    assert receipt["withdrawn"]
    def handoff_row():
        with store.database.read() as connection:
            return dict(connection.execute(
                "SELECT data_json,xmin::text AS row_version FROM agent_operations WHERE operation_id=%s",
                ("execution_handoff:" + original.execution_id,),
            ).fetchone())
    handoff_before = handoff_row()
    before = durable_rows(queue, store, original.execution_id)
    result = queue.submit(renamed(original), prepare=forbid_prepare)
    assert result["execution_id"] == original.execution_id and result["status"] == "cancelled"
    assert result["result"]["unstarted_handoff"] == json_value(handoff_before["data_json"])["receipt"]
    assert durable_rows(queue, store, original.execution_id) == before
    assert handoff_row() == handoff_before
    queue.acknowledge(original.execution_id, actor=original.actor)
    queue.prune_terminal(older_than_epoch=time.time() + 86401)
    with pytest.raises(ValueError, match="already submitted; scheduler receipt expired"):
        scheduler().submit(renamed(original), prepare=forbid_prepare)
    assert handoff_row() == handoff_before
    assert durable_rows(queue, store, original.execution_id)[2] == before[2]


@pytest.mark.parametrize("top,seed", [tags for tags in ALIASES if tags != ("tfi", "tfi")])
@pytest.mark.parametrize("stored", [False, True])
def test_namespace_changes_do_not_match_historical_digests(top, seed, stored):
    from management_portal.execution.submission_identity import SubmissionIdentity, submission_digest
    from tests.cases.compatibility_test_support import synthetic_site

    original = request()
    payload = original.to_payload()
    old = deepcopy(payload)
    old["schema"] = f"{top}.execution-request.v8"
    old["metadata"]["fork_context_input"]["schema"] = f"{seed}.provider-context-seed.v1"
    digest = submission_digest(old)
    with synthetic_site():
        identity = SubmissionIdentity.from_request(original)
        assert identity.matches(submission_digest(payload))
        assert not identity.matches(digest)
        assert original.to_payload() == payload
        if stored:
            store, queue = get_agent_data_store(), scheduler()
            queue.submit(original)
            # Model an existing digest without changing or rehashing its payload.
            queue.database.write(lambda connection: connection.execute(
                "UPDATE execution_store_meta SET value=%s WHERE key=%s",
                (digest, "request_digest:" + original.execution_id),
            ))
            before = durable_rows(queue, store, original.execution_id)
            with pytest.raises(ValueError, match="different request"):
                queue.submit(original, prepare=forbid_prepare)
            assert durable_rows(queue, store, original.execution_id) == before


def test_rpc_rejects_old_request_schema_before_admission():
    from tests.cases.compatibility_test_support import synthetic_site

    original = request()
    payload = original.to_payload()
    payload["schema"] = "archive.execution-request.v8"
    before = deepcopy(payload)
    server = object.__new__(ExecutionServiceServer)
    server.scheduler = scheduler()
    server.preparation = SimpleNamespace(prepare=forbid_prepare)
    with synthetic_site(), pytest.raises(ValueError, match="schema"):
        server.dispatch("execution.submit", payload)
    assert payload == before
    assert durable_rows(server.scheduler, get_agent_data_store(), original.execution_id) == (None, None, None)
