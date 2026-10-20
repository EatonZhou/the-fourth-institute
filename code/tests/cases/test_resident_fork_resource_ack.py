"""A successful Fork must be immediately readable by the opening Chat."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from management_portal.society_service import (
    invariant_locks, lineage_domain, resident_catalog, resident_domain, resource_client,
)
from tests.cases.isa_test_support import isolated_instruction_store, run  # noqa: F401
from tests.cases.test_resident_registration_resource_ack import registration  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import _append_conversation_turn


def fork_request(parent, mode="full"):
    _append_conversation_turn(parent["resident_id"], 0)
    arguments = {
        "lineage_id": parent["lineage_id"], "source_resident_id": parent["resident_id"],
        "title": "ACK fork", "fork_mode": mode,
    }
    if mode == "message":
        arguments["message"] = {"message_id": f"msg_{1:032x}"}
    revision = lineage_domain.get_lineage(parent["lineage_id"], "sample_admin")["revision"]
    return lambda: run("ForkResident", arguments, key="fork-resource-ack", version={parent["lineage_id"]: revision})


@pytest.mark.parametrize("mode", ["full", "recent-5", "recent-10", "message", "compacted"])
def test_fork_waits_for_resource_ack_before_navigation(registration, monkeypatch, mode):
    """各模式的 ForkResident 等新 Resident 资源发布确认后才返回，返回后即可打开其 Chat"""
    parent, resources = registration
    fork = fork_request(parent, mode)
    publish = resource_client.sync_resident_resource_bundles
    entered, release = threading.Event(), threading.Event()
    children = []

    def delayed(records, **kwargs):
        assert not invariant_locks.lock_held()
        if records[0]["title"] == "ACK fork":
            children.append(records[0]["resident_id"])
            entered.set()
            assert release.wait(5)
        return publish(records, **kwargs)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fork)
        try:
            if not entered.wait(3):
                future.result(timeout=1).raise_for_error()
                pytest.fail("Fork returned success before Resource publication")
            assert not future.done()
            assert children[0] not in resources
            # Publication must not hold the source's state lock.
            assert resident_catalog.get_catalog().get(parent["resident_id"])
        finally:
            release.set()
        result = future.result(timeout=5).raise_for_error()
    child_id = result["resident_id"]
    assert child_id == children[0]
    assert child_id in resources
    assert not resident_catalog.get_catalog().state_store.resident_resource_projection_pending(child_id)
    assert resident_domain.get_resident_workspace_roots(child_id, "sample_admin")["resident_id"] == child_id
    assert run("GetConversation", {"resident_id": child_id}).status == "committed"


def test_fork_publication_failure_replays_same_child_without_recreating(registration, monkeypatch):
    """fork 资源发布失败后重放返回同一个子 Resident，不重新创建"""
    parent, resources = registration
    fork = fork_request(parent)
    publish = resource_client.sync_resident_resource_bundles

    def unavailable(*args, **kwargs):
        raise OSError("Resource publication unavailable")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    failure = fork()
    assert failure.error["code"] == "instruction.projection_pending"
    assert failure.error["commit_state"] == "committed"
    child_id = next(item["resident_id"] for item in failure.error["details"]["projection_failures"]
                    if item["projection"] == "resident.resource_bundle")
    store = resident_catalog.get_catalog().state_store
    before_ids = {row["resident_id"] for row in store.all_residents()}
    revision = store.lineage_definition(parent["lineage_id"])["revision"]
    assert child_id not in resources
    assert any(edge["target"] == child_id for edge in store.lineage_definition(parent["lineage_id"])["edges"])
    monkeypatch.setattr(lineage_domain, "_create_derived_resident", lambda *a, **kw: pytest.fail("Fork recreated"))
    assert fork().error["code"] == "instruction.projection_pending"
    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", publish)
    recovered = fork()
    assert recovered.status == "committed" and recovered.replayed
    assert recovered.result["resident_id"] == child_id
    assert child_id in resources
    assert {row["resident_id"] for row in store.all_residents()} == before_ids
    assert store.lineage_definition(parent["lineage_id"])["revision"] == revision
