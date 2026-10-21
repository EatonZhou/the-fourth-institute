""":aspects: functional, performance, architecture-conformance"""
from __future__ import annotations

from unittest import mock

import pytest

from management_portal.society_service import resident_catalog, resident_protocol
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.society_service_test_support import publish_registered_lineage_members


ROOT = "resident_aaaaaaaaaaaa"
RUNNING = "resident_bbbbbbbbbbbb"
IDLE = "resident_cccccccccccc"
MISSING = "resident_dddddddddddd"
DEFERRED = "resident_eeeeeeeeeeee"


def _repository(projections: dict[str, dict]) -> mock.Mock:
    repository = mock.Mock()
    repository.current_projection.side_effect = lambda resident_id: projections[resident_id]
    repository.current_projections.side_effect = lambda resident_ids: {
        resident_id: projections[resident_id]
        for resident_id in resident_ids
        if resident_id in projections
    }
    repository.public_message.side_effect = lambda _resident_id, message_id: {
        "message_id": message_id,
        "created_at": "2026-08-02T10:00:00Z",
    }
    return repository


def test_activity_snapshot_reads_canonical_resident_state_and_queue_contracts(monkeypatch, subtests, isolated_portal_case):
    """Resident 活动快照读取规范状态与队列，跳过已删除者并列出缺失的 ID

    :aspects: functional"""
    with subtests.test(contract='test_activity_snapshot_reads_canonical_resident_state_and_queue'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(resident_protocol.resident_lineage, 'all', lambda: [{'resident_id': rid, 'registration_state': 'ready'} for rid in (ROOT, RUNNING, IDLE, DEFERRED)] + [{'resident_id': 'resident_ffffffffffff', 'session_state': 'deleted', 'registration_state': 'ready'}])
            repository = _repository({ROOT: {'state': {'work_state': 'queued', 'active_message_id': 'message_root'}, 'queue': {'items': []}}, RUNNING: {'state': {'work_state': 'running', 'active_message_id': 'message_running'}, 'queue': {'items': [{'message_id': 'message_later', 'created_at': '2026-08-02T10:03:00Z'}, {'message_id': 'message_oldest', 'created_at': '2026-08-02T10:01:00Z'}]}}, IDLE: {'state': {'work_state': 'idle', 'active_message_id': ''}, 'queue': {'items': []}}, DEFERRED: {'state': {'work_state': 'queued', 'active_message_id': ''}, 'queue': {'items': [{'message_id': 'message_deferred', 'created_at': '2026-08-02T10:04:00Z'}]}}})
            monkeypatch.setattr(resident_protocol.resident_conversation_stream, 'get_repository', lambda: repository)
            snapshot = resident_protocol.get_resident_activity_snapshot()
            assert snapshot['schema'] == 'tfi.resident-activity-snapshot.v2'
            assert snapshot['resident_ids'] == [ROOT, RUNNING, IDLE, DEFERRED]
            assert snapshot['active_resident_ids'] == [ROOT, RUNNING, DEFERRED]
            assert snapshot['residents'][ROOT]['message_id'] == 'message_root'
            assert snapshot['residents'][RUNNING]['queue_count'] == 2
            assert snapshot['residents'][RUNNING]['oldest_queued_at'] == '2026-08-02T10:01:00Z'
            assert snapshot['residents'][IDLE] == {'resident_id': IDLE, 'active': False, 'status': 'idle'}
            assert 'message_id' not in snapshot['residents'][DEFERRED]
            assert snapshot['residents'][DEFERRED]['queued_messages'] == [{'message_id': 'message_deferred', 'created_at': '2026-08-02T10:04:00Z'}]
    with subtests.test(contract='test_activity_snapshot_filters_and_reports_missing'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: mock.Mock(get_many=lambda _ids: [{'resident_id': RUNNING, 'registration_state': 'ready'}]))
            monkeypatch.setattr(resident_protocol.resident_conversation_stream, 'get_repository', lambda: _repository({RUNNING: {'state': {'work_state': 'running', 'active_message_id': 'message_running'}, 'queue': {'items': []}}}))
            snapshot = resident_protocol.get_resident_activity_snapshot([RUNNING, MISSING])
            assert snapshot['resident_ids'] == [RUNNING]
            assert snapshot['active_resident_ids'] == [RUNNING]
            assert snapshot['missing_resident_ids'] == [MISSING]


def test_activity_snapshot_rejects_noncanonical_identity() -> None:
    """Resident 活动快照拒绝非 resident_* 格式的 ID

    :aspects: functional"""
    with pytest.raises(ValueError, match=r"canonical resident_\* IDs"):
        resident_protocol.get_resident_activity_snapshot(["not-a-resident"])


@pytest.mark.parametrize("timestamps", [
    {},
    {"created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z"},
], ids=["missing", "explicit"])
def test_registered_members_preserve_canonical_times_across_clock_ticks(
    tmp_path, monkeypatch, timestamps,
) -> None:
    """Resident 注册保留规范时间戳，不受时钟漂移影响

    分别验证缺省与显式时间戳；发布fixture经过时钟变化仍保留已提交的created_at和updated_at。

    :aspects: functional
    :design: decision_1b2bdf5c33d6
    :design-point:
    """
    from datetime import datetime, timedelta, timezone
    from itertools import count
    from management_portal.society_service import resource_client
    from management_portal.resources import collaboration_registry

    catalog = resident_catalog.ResidentCatalog(tmp_path, LineageDataStore())
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    lineage_id = "lineage_000000000001"
    catalog.state_store.put_lineage_definition(
        lineage_id, {"lineage_id": lineage_id, "nodes": [], "edges": []}, {},
    )
    ticks = count()
    monkeypatch.setattr(collaboration_registry, "_now_iso", lambda: (
        datetime(2026, 9, 3, tzinfo=timezone.utc) + timedelta(seconds=next(ticks))
    ).isoformat(timespec="seconds"))

    publish_registered_lineage_members({
        "lineage_id": lineage_id, "nodes": [{"id": IDLE, "title": "Idle"}], **timestamps,
    })
    committed = catalog.state_store.resident_record(IDLE)
    assert committed["created_at"] and committed["updated_at"]
    if timestamps:
        assert {field: committed[field] for field in timestamps} == timestamps
    else:
        assert committed["created_at"] == committed["updated_at"]
    resource_client.sync_resident(committed, updated_by="sample_admin")
    with pytest.raises(ValueError, match="Stale canonical domain projection"):
        resource_client.sync_resident({**committed, "title": "Stale title"}, updated_by="sample_admin")


@pytest.mark.parametrize("locked_table", ["resident_observation_cache", "residents"])
def test_resource_publication_locks_identity_but_not_runtime(locked_table):
    """资源发布与运行状态写入并行且保护身份一致性

    :importance: P1
    :design: decision_71aa039e3211
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from management_portal.society_service import resource_client
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.canonical_domain_fixture import canonical_projection

    canonical_projection(IDLE)
    store = get_lineage_data_store()
    committed = store.resident_record(IDLE)
    resource_client.sync_resident(committed, updated_by="sample_admin")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.database.transaction() as connection:
            if locked_table == "resident_observation_cache":
                connection.execute(
                    "UPDATE resident_observation_cache SET last_error=last_error WHERE resident_id=%s",
                    (IDLE,),
                )
            else:
                connection.execute("UPDATE residents SET title=title WHERE resident_id=%s", (IDLE,))
            future = pool.submit(resource_client.sync_resident, committed, updated_by="sample_admin")
            if locked_table == "resident_observation_cache":
                future.result(timeout=5)
            else:
                with pytest.raises(Exception, match="could not obtain lock"):
                    future.result(timeout=5)


def test_publication_rederives_hierarchy_level_but_rejects_stale_authored_scope():
    """资源发布重新派生层级并拒绝过时业务字段

    验证 Family 归属变化后发布重新派生层级，但仍拒绝过时的 scope 和 category。

    :design: decision_71aa039e3211, decision_b820faf42218
    :design-point:
    """
    from management_portal.society_service import resource_client
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
    from tests.cases.canonical_domain_fixture import canonical_projection

    canonical_projection(IDLE, principal_hierarchy={
        'level': 'resident', 'category': 'lineage_member', 'scope_ids': [],
    })
    record = get_lineage_data_store().resident_record(IDLE)
    queued = {**record, 'principal_hierarchy': {**record['principal_hierarchy'], 'level': 'lineage'}}
    resource_client.sync_resident(queued, updated_by='sample_admin')
    descriptor = CollaborationDescriptorStore().get(IDLE)
    assert descriptor['attributes']['principal_hierarchy'] == record['principal_hierarchy']
    for changed in ({'scope_ids': ['another-project']}, {'category': 'personal_ama'}):
        with pytest.raises(ValueError, match='Stale canonical domain projection'):
            resource_client.sync_resident({**queued, 'principal_hierarchy': {
                **queued['principal_hierarchy'], **changed,
            }}, updated_by='sample_admin')


def test_selected_activity_uses_bounded_canonical_reads(tmp_path, monkeypatch) -> None:
    """指定 Resident 的活动查询工作量有界

    使用真实 PostgreSQL 验证定向查询不扫描全体 Resident、不读取资源权限，并保留去重、已删除及缺失身份语义。

    :importance: P1
    :design: decision_1b2bdf5c33d6
    """
    catalog = resident_catalog.ResidentCatalog(tmp_path, LineageDataStore())
    store = catalog.state_store
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    lineage_id = "lineage_000000000001"
    store.put_lineage_definition(lineage_id, {"lineage_id": lineage_id, "nodes": [], "edges": []}, {})
    deleted = "resident_eeeeeeeeeeee"
    resident_ids = [RUNNING, IDLE, deleted, *[
        f"resident_{index:012x}" for index in range(64)
    ]]
    publish_registered_lineage_members({"lineage_id": lineage_id,
        "nodes": [{"id": resident_id, "title": resident_id} for resident_id in resident_ids]})
    store.database.write(lambda connection: connection.execute(
        "UPDATE residents SET deleted_at=%s,deleted_by=%s WHERE resident_id=%s",
        ("2026-10-08T00:00:00Z", "sample_admin", deleted),
    ))
    repository = ResidentConversationRepository(store, AgentDataStore())
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, "get_repository", lambda: repository)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("selected activity must not scan all Residents or Lineages")

    monkeypatch.setattr(catalog, "all", forbidden)
    monkeypatch.setattr(resident_protocol.resident_lineage, "all", forbidden)
    with mock.patch.object(catalog, "get_many", wraps=catalog.get_many) as reads:
        snapshot = resident_protocol.get_resident_activity_snapshot([IDLE, IDLE.upper(), deleted, MISSING])
        assert reads.call_count == 1
        assert set(reads.call_args.args[0]) == {IDLE, deleted, MISSING}
    assert snapshot["resident_ids"] == [IDLE]
    assert snapshot["residents"][IDLE]["status"] == "idle"
    assert snapshot["missing_resident_ids"] == sorted([deleted, MISSING])
    assert not snapshot["active_resident_ids"]
    assert resident_protocol.get_resident_activity_snapshot([])["residents"] == {}
    repository.agent_data.database.close()
