from __future__ import annotations

import copy
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.hatg import routing
from tests.cases.isa_test_support import reset_kernels, run
from management_portal.society_service import (
    changes,
    invariant_locks,
    lineage_domain,
    resource_client,
)
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.service import ResourceService
from tests.cases.society_service_test_support import install_resource_boundary


RESOURCE_METHODS = {
    name: getattr(resource_client, name)
    for name in ("sync_lineage",)
}
SYNC_LINEAGE_IDENTITY = lineage_domain._sync_lineage_resource_identity
ACTOR = "alice"


@pytest.fixture
def fence_fixture(tmp_path, monkeypatch, isolate_portal_databases):
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_PROCESS", "0")
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", tmp_path / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "lineages"))
    from management_portal.society_service import resident_project_domain, resident_workspace_domain
    monkeypatch.setattr(resident_project_domain, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(resident_workspace_domain, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")

    def forbidden(*args, **kwargs):
        raise AssertionError("projection fixture prohibits external effects")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    from management_portal.society_service import resident_registration_domain
    monkeypatch.setattr(resident_registration_domain, "_queue_bootstrap_resident_session", forbidden)
    install_resource_boundary(monkeypatch)
    for name, method in RESOURCE_METHODS.items():
        monkeypatch.setattr(resource_client, name, method)
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", SYNC_LINEAGE_IDENTITY)
    service = ResourceService(

        collaboration_store=CollaborationDescriptorStore(),
    )
    mutations = []
    notifications = []
    monkeypatch.setattr(service, "_notify_mutation", lambda **kwargs: mutations.append(kwargs))
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: service)
    monkeypatch.setattr(changes, "lineage_changed", lambda *args, **kwargs: notifications.append(kwargs))
    reset_kernels()
    created = lineage_domain.create_lineage({"title": "publication fence fixture", "nodes": [], "edges": []}, ACTOR)
    before = lineage_domain._read_lineage_state(created["lineage_id"])
    request = {
        "lineage_id": before["lineage_id"],
        "idempotency_key": "fence-original",
        "changes": {"description": "first committed description"},
    }
    yield before, request, service, mutations, notifications
    reset_kernels()


def submit(request, actor=ACTOR):
    """UpdateLineage through the ISA; the result is returned, not raised."""

    return run("UpdateLineage", {"lineage_id": request["lineage_id"], "changes": copy.deepcopy(request["changes"])},
               actor=actor, key=request["idempotency_key"])


def execution_record(instruction_id):
    return routing.kernel_for("lineage", required=True).store.get(instruction_id)


def test_a_commit_covered_by_a_newer_publication_does_not_publish_again(fence_fixture, monkeypatch):
    """已被更新发布覆盖的提交不再重复发布

    验证一次提交的发布开始前更新的提交已经发布了当前状态时，这次提交不再发布，也不报告投影待确认。
    """
    before, request, _service, _mutations, notifications = fence_fixture
    original_publish = lineage_domain._publish_lineage_resource_projection
    original_sync = lineage_domain._sync_lineage_resource_identity
    old_committed = threading.Event()
    release_old = threading.Event()
    projected = []
    original_revision = before["revision"] + 1
    notification_count = len(notifications)

    def pause_normal(lineage_id, revision, **kwargs):
        assert not invariant_locks.lock_held()
        if revision == original_revision:
            old_committed.set()
            assert release_old.wait(5)
        return original_publish(lineage_id, revision, **kwargs)

    def observe_sync(projection):
        original_sync(projection)
        projected.append(projection["revision"])

    monkeypatch.setattr(lineage_domain, "_publish_lineage_resource_projection", pause_normal)
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", observe_sync)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(submit, copy.deepcopy(request))
        try:
            if not old_committed.wait(5):
                first.result(timeout=5)
                pytest.fail("original projection never reached publication")
            current = lineage_domain._read_lineage_state(before["lineage_id"])
            assert current["revision"] == original_revision
            next_request = {
                **request,
                "idempotency_key": "fence-newer",
                "changes": {"description": "newer committed description"},
            }
            assert submit(next_request).status == "committed"
            newer = lineage_domain._read_lineage_state(before["lineage_id"])
            assert projected == [newer["revision"]]
        finally:
            release_old.set()
        covered = first.result(timeout=5)
    # The newer commit's publication carried the newer state; the older
    # commit's own publication found it covered and published nothing.
    assert covered.status == "committed", covered
    assert projected == [newer["revision"]]
    replay = submit(request)
    assert replay.status == "committed" and replay.replayed
    assert projected == [newer["revision"]]
    assert lineage_domain._read_lineage_state(before["lineage_id"])["revision"] == newer["revision"]
    assert len(notifications) == notification_count + 2


@pytest.mark.parametrize("change_phase", ["before_sync", "after_sync"])
def test_recovery_publishes_the_committed_revision_outside_the_lock_once(
    fence_fixture, monkeypatch, change_phase,
):
    """投影恢复在锁外只发布一次已提交修订，重放不再发布"""
    before, request, service, mutations, notifications = fence_fixture
    original_sync = lineage_domain._sync_lineage_resource_identity

    def unavailable(_projection):
        raise OSError("temporary Resource publication unavailable")

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    initial = submit(request)
    assert initial.error["code"] == "instruction.projection_pending"
    instruction_id = initial.error["details"]["instruction_id"]
    lineage_id = before["lineage_id"]
    committed = lineage_domain._read_lineage_state(lineage_id)
    notification_count = len(notifications)
    snapshot_authorized = threading.Event()
    release_projection = threading.Event()
    published = []

    def pause_projection(projection):
        assert not invariant_locks.lock_held()
        assert "_resource_acl_fingerprint" not in projection
        if change_phase == "after_sync":
            original_sync(projection)
            published.append(projection["revision"])
        snapshot_authorized.set()
        assert release_projection.wait(5)
        if change_phase == "before_sync":
            original_sync(projection)
            published.append(projection["revision"])

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", pause_projection)
    with ThreadPoolExecutor(max_workers=1) as executor:
        recovery = executor.submit(submit, copy.deepcopy(request))
        try:
            assert snapshot_authorized.wait(5)
            after_change_mutations = len(mutations)
        finally:
            release_projection.set()
        assert recovery.result(timeout=5).status == "committed"
    assert published == [committed["revision"]]
    assert len(mutations) == after_change_mutations + (1 if change_phase == "before_sync" else 0)
    assert execution_record(instruction_id).get("error") is None
    assert "owner_ids" not in service.get(lineage_id, actor=ACTOR)["attributes"]
    replay = submit(request)
    assert replay.status == "committed"
    assert execution_record(instruction_id).get("error") is None
    assert published == [committed["revision"]]
    assert lineage_domain._read_lineage_state(lineage_id) == committed
    assert "_resource_acl_fingerprint" not in committed
    assert len(notifications) == notification_count


def test_canonical_title_update_reaches_resources_and_replay_is_silent(fence_fixture):
    """规范标题更新同步到资源服务，重放不再通知或变更"""
    before, request, service, mutations, notifications = fence_fixture
    lineage_id = before["lineage_id"]
    request["changes"] = {"title": "New canonical title"}
    submit(request).raise_for_error()
    canonical = lineage_domain._read_lineage_state(lineage_id)
    assert canonical["revision"] == before["revision"] + 1
    assert canonical["title"] == "New canonical title"
    assert service.get(lineage_id, actor="panda")["title"] == "New canonical title"
    notification_count, mutation_count = len(notifications), len(mutations)
    assert submit(request).replayed
    assert (len(notifications), len(mutations)) == (notification_count, mutation_count)
    assert "_resource_acl_fingerprint" not in canonical


def test_lineage_publications_keep_current_metadata(fence_fixture, monkeypatch):
    """Lineage 发布更新当前元数据，后续提交先发布时标题变更仍到达资源服务"""
    before, request, service, _mutations, _notifications = fence_fixture
    lineage_id = before["lineage_id"]

    def title():
        return service.get(lineage_id, actor=ACTOR)["title"]

    submit({**request, "idempotency_key": "describe", "changes": {"description": "text only"}}).raise_for_error()
    assert title() == before["title"]

    # A Lineage title change reaches ResourceService even when a later
    # commit's publication runs before the change's own publication.
    original_publish = lineage_domain._publish_lineage_resource_projection
    title_change_committed, release = threading.Event(), threading.Event()
    title_revision = lineage_domain._read_lineage_state(lineage_id)["revision"] + 1

    def hold_title_change(target, revision, **kwargs):
        if revision == title_revision:
            title_change_committed.set()
            assert release.wait(5)
        return original_publish(target, revision, **kwargs)

    monkeypatch.setattr(lineage_domain, "_publish_lineage_resource_projection", hold_title_change)
    with ThreadPoolExecutor(max_workers=1) as executor:
        change = executor.submit(submit, {**request, "idempotency_key": "title", "changes": {"title": "Later published title"}})
        try:
            assert title_change_committed.wait(5)
            submit({**request, "idempotency_key": "later", "changes": {"description": "later"}}).raise_for_error()
            assert title() == "Later published title"
        finally:
            release.set()
        change.result(timeout=5).raise_for_error()
    assert title() == "Later published title"
