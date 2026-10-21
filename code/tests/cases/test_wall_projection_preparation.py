"""Creation receipts cross committed domain and external projection boundaries.

:module: resident.identity
:importance: P1
:design: decision_898b8911a2ff
"""
import json

import pytest
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from management_common.projection_receipts import CommittedProjectionError
from management_portal.society_service import lineage_domain, resident_catalog, resident_domain
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload  # noqa: F401
from tests.cases.test_wall import wall_project  # noqa: F401


def test_keyed_creation_is_committed_before_external_projection(future_creation_store, monkeypatch):
    """冻结创建请求与 Resident 提交、投影恢复边界

    :design: decision_0f57656a2acd
    :design-point:
    """
    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    store = resident_catalog.get_catalog().state_store
    original = lineage_domain._sync_lineage_resource_identity
    observations = []

    def independently_read(projection):
        with psycopg.connect(store.database.dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                sql.SQL("SELECT revision FROM {}.lineages WHERE lineage_id=%s").format(sql.Identifier(store.database.schema)),
                (parent["lineage_id"],),
            ).fetchone()
        observations.append((row["revision"], projection["revision"]))
        assert row["revision"] == projection["revision"], "projection must see a committed graph"
        original(projection)

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", independently_read)
    result = lineage_domain.create_lineage_resident(
        parent["lineage_id"], {**_new_session_payload("Child"), "creation_key": "wall-committed-test"}, "sample_admin",
    )
    assert result["resident"]["resident_id"] != parent["resident_id"]
    assert observations and all(a == b for a, b in observations)


@pytest.mark.parametrize("stage", ["lineage", "resident"])
def test_keyed_projection_retry_reuses_committed_identity(future_creation_store, monkeypatch, stage):
    """冻结创建请求与 Resident 提交、投影恢复边界

    :design: decision_0f57656a2acd
    :design-point:
    """
    from management_portal.society_service import resource_client

    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    store = resident_catalog.get_catalog().state_store
    target, name = (lineage_domain, "_sync_lineage_resource_identity") if stage == "lineage" else (resource_client, "sync_resident_resource_bundles")
    original = getattr(target, name)

    def unavailable(*args, **kwargs):
        raise OSError("isolated projection unavailable")

    monkeypatch.setattr(target, name, unavailable)
    payload = {**_new_session_payload("Child"), "creation_key": "wall-recovery-test"}
    with pytest.raises(CommittedProjectionError) as first:
        lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")
    receipt = store.resident_creation_operations.get(parent['lineage_id'], payload['creation_key'])
    rid = receipt["resident_id"]
    assert first.value.details["resident_creation"]["resident_id"] == rid
    assert store.resident_record(rid)["registration_state"] == "resource_pending"
    assert store.resident_resource_projection_pending(rid)
    with pytest.raises(CommittedProjectionError):
        lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")
    monkeypatch.setattr(target, name, original)
    result = lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")
    assert result["resident"]["resident_id"] == rid
    assert {n["id"] for n in result["lineage"]["nodes"]} == {parent["resident_id"], rid}
    assert store.resident_record(rid)["registration_state"] == "ready"
    assert not store.resident_resource_projection_pending(rid)


def test_concurrent_frozen_creation_request_allocates_once(future_creation_store):
    """冻结创建请求与 Resident 提交、投影恢复边界

    :design: decision_0f57656a2acd
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor

    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    payload = {**_new_session_payload("Child"), "creation_key": "wall-concurrent-test"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin"), range(2)))
    assert len({r["resident"]["resident_id"] for r in results}) == 1
    assert len(lineage_domain.get_lineage(parent["lineage_id"], "sample_admin")["nodes"]) == 2


@pytest.mark.parametrize("state", ["committed", "unknown"])
def test_creation_failure_retains_request_without_inventing_identity(state):
    """冻结创建请求与 Resident 提交、投影恢复边界

    :design: decision_0f57656a2acd
    :design-point:
    """
    from types import SimpleNamespace
    from management_common.operation_errors import OperationError
    from management_portal import resident_creation

    saved = {}
    requests = []

    def create(lid, payload, actor):
        requests.append(payload)
        raise OperationError("pending", code="projection.pending", http_status=503, commit_state=state)

    def save(value):
        saved.clear()
        saved.update(value)

    for _ in range(2):
        with pytest.raises(OperationError) as caught:
            resident_creation.create(actor="alice", lineage_id="lineage_111111111111", arguments={"backend": "codex"}, load=lambda: saved, save=save, service=SimpleNamespace(lineage=SimpleNamespace(create_lineage_resident=create)))
        assert caught.value.failure_payload()["commit_state"] == state
        assert "resident_id" not in caught.value.details["resident_creation"]
        assert "resident_id" not in saved
    assert requests[0] == requests[1]
    assert saved["operation_error"]["details"]["resident_creation"]["request_id"] == requests[0]["creation_key"]


@pytest.mark.parametrize("known_identity", [False, True])
def test_wall_pending_preparation_preserves_receipt_without_delivery(wall_project, monkeypatch, known_identity):
    """WALL 创建未确认回执与安全呈现

    :module: workflow.wall
    :design: decision_06defc47e960
    :design-point:
    """
    from management_portal import wall_backend as wall, resident_recommendation_backend
    from management_portal.workbench_projections import native
    from management_common.operation_errors import OperationError

    monkeypatch.setattr(resident_recommendation_backend, "recommend_residents", lambda **_: {"recommendations": []})
    creation = {"lineage_id": "lineage_111111111111", "request_id": "invocation-create:fixture"}
    if known_identity:
        creation["resident_id"] = "resident_222222222222"
    error = OperationError("projection pending", code="projection.pending", http_status=503, commit_state="committed", details={"domain_committed": True, "recovery": "projection_only", "resident_creation": creation})
    monkeypatch.setattr(native, "binding", lambda *a, **k: (_ for _ in ()).throw(error))
    monkeypatch.setattr(wall.resident_handoff, "submit_invocation", lambda **_: pytest.fail("pending creation must not deliver"))
    job = wall.action({"action": "implement", "block": "plan.md#plan"}, "owner")["job"]
    wall._run_job(job["id"])
    actual = wall._read_state()["jobs"][job["id"]]
    assert actual["status"] == "blocked"
    assert actual["operation_error"] == error.failure_payload()
    assert bool(actual.get("resident_id")) == known_identity
    assert not actual.get("message_id")
    assert (wall_project / "plan.md").exists()


def test_wall_http_preserves_remote_committed_projection_receipt(monkeypatch):
    """WALL 创建未确认回执与安全呈现

    :module: workflow.wall
    :design: decision_06defc47e960
    :design-point:
    """
    import urllib.request
    import urllib.error
    from management_common.operation_errors import OperationError
    from management_portal import http_wall, serve_management_portal as portal, wall_backend
    from tests.cases.test_resource_portal_routes import _server

    error = OperationError("projection pending", code="projection.pending", http_status=503, commit_state="committed", details={"domain_committed": True, "recovery": "projection_only", "resident_creation": {"lineage_id": "lineage_111111111111", "request_id": "invocation-create:fixture"}})
    monkeypatch.setattr(portal, "_check_session", lambda _: True)
    monkeypatch.setattr(http_wall, "api_actor", lambda shell, request, *, write: "alice" if write else pytest.fail("write authentication required"))
    monkeypatch.setattr(wall_backend, "handle_post", lambda *args: (_ for _ in ()).throw(error))
    server, thread, base = _server()
    try:
        request = urllib.request.Request(base + "/api/wall/action", data=b'{"action":"discuss"}', headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        assert caught.value.code == 503
        response = json.load(caught.value)
        assert response["commit_state"] == "committed"
        assert response["retryable"] is False
        assert response["details"] == error.details
        assert "resident_id" not in response["details"]["resident_creation"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
