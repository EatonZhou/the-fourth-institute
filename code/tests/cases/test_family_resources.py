"""Family resource identity shares the canonical Family lifecycle and roster."""
import json

import pytest

from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.resources import get_resource_service
from management_portal.society_service import lineage_state, resource_client
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, resident, graph  # noqa: F401
from tests.cases import permission_test_support

REAL_SYNC_LINEAGE = resource_client.sync_lineage


@pytest.mark.parametrize('already_removed', [False, True])
def test_tau_family_cleanup_uses_domain_owner_and_preserves_existing_member(lineage, charters, monkeypatch, already_removed):
    """TAU 清理活跃或已解除的 Family，不依赖不存在的注销 provider，也不删除原有成员。"""
    from management_portal.test_framework import tau_fixtures

    member = resident(lineage, 'Existing member')
    made = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member], 'title': 'TAU family'})
    monkeypatch.setattr(tau_fixtures, 'TAU_ACTOR', 'sample_admin')
    if already_removed:
        ok('Unfamilize', {'lineage_id': lineage, 'family_id': made['resource_id']})
    tau_fixtures._release_resource(made['resource_id'], 'tau-family-cleanup')
    tau_fixtures._release_resource(made['resource_id'], 'tau-family-cleanup-recheck')
    assert get_resource_service().resolve(made['resource_id'], actor='sample_admin')['exists'] is False
    nodes = {node['id'] for node in graph(lineage)['nodes']}
    assert member in nodes
    assert made['facade_resident_id'] not in nodes


@pytest.fixture(autouse=True)
def family_publication(lineage, monkeypatch):
    # Creation fixtures stub resource publication; exercise the real owner here.
    monkeypatch.setattr(lineage_state, "_sync_lineage_resource_identity", REAL_SYNC_LINEAGE)
    # In production the ResourceService dispatcher authenticates SocietyService
    # publications as the society-service domain; the in-process service gets none.
    service = get_resource_service()
    sync = service.sync_collaboration_resources
    monkeypatch.setattr(service, "sync_collaboration_resources",
                        lambda resources, **kw: sync(resources, **{"domain_identity": "society-service", **kw}))
    REAL_SYNC_LINEAGE(graph(lineage))


def test_family_resource_lifecycle_and_legacy_function_block_address(lineage, charters):
    """Family 资源支持完整生命周期和旧功能块地址"""
    member = resident(lineage, "Family member")
    made = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Resource Family"})
    rid, bid = made["resource_id"], made["family_id"]
    assert rid.startswith("family_") and bid.startswith("fbi_") and rid != made["facade_resident_id"]
    service = get_resource_service()
    got = service.reference(rid, actor="sample_admin")
    assert got["title"] == "Resource Family"
    assert got["attributes"]["permission_parents"] == [lineage]
    assert got["attributes"]["member_resident_ids"] == [member]
    assert f"program_item={bid}" in service.resolve(rid, actor="sample_admin")["portal_url"]
    read = ok("ReadResource", {"address": rid})
    payload = json.loads(read["text"])
    assert payload["resource_id"] == rid and payload["family_id"] == bid
    legacy = ok("ReadResource", {"address": f"{lineage}/program/function-block/{bid}"})
    assert json.loads(legacy["text"])["item"]["family"]["resource_id"] == rid

    changed = ok("UpdateFamily", {"lineage_id": lineage, "family_id": rid, "title": "Updated Family", "goal": "Updated goal"})
    assert changed["resource_id"] == rid and changed["family_id"] == bid
    assert service.reference(rid, actor="sample_admin")["title"] == "Updated Family"
    extra = ok("CreateResident", {"lineage_id": lineage, "family_id": rid, "title": "Added member"})["resident_id"]
    assert extra in service.reference(rid, actor="sample_admin")["attributes"]["member_resident_ids"]
    ok("UpdateResident", {"lineage_id": lineage, "resident_id": made["facade_resident_id"], "title": "Renamed facade"})
    assert service.reference(rid, actor="sample_admin")["title"] == "Renamed facade"
    # The published descriptor carries the new title at once, not after the reconciliation poll.
    from management_portal.resources.registry_store import get_registry_store
    assert get_registry_store().get("family", rid)["title"] == "Renamed facade"
    assert service.query(q=rid, resource_types=["family"], actor="sample_admin")["total"] == 1

    removed = ok("Unfamilize", {"lineage_id": lineage, "family_id": rid})
    assert removed["resource_id"] == rid and removed["family_id"] == bid
    assert service.resolve(rid, actor="sample_admin")["exists"] is False
    assert service.query(q=rid, resource_types=["family"], actor="sample_admin")["total"] == 0
    with pytest.raises(KeyError):
        service.reference(rid, actor="sample_admin")
    assert {member, extra} <= {node["id"] for node in graph(lineage)["nodes"]}


def test_empty_lineage_family_has_stable_resource_id_and_follows_lineage_deletion(lineage, charters):
    """空 Lineage 的 Family 标识稳定且随 Lineage 删除"""
    made = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    repeated = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    assert repeated["resource_id"] == made["resource_id"]
    payload = json.loads(ok("ReadResource", {"address": made["resource_id"]})["text"])
    assert payload["lineage_scope"] is True and payload["members"] == []
    ok("DeleteLineage", {"lineage_id": lineage})
    assert get_resource_service().resolve(made["resource_id"], actor="sample_admin")["exists"] is False
    # The Family record itself is deleted, not left live under a deleted Lineage.
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT deleted_at FROM families WHERE resource_id=%s", (made["resource_id"],)).fetchone()
        roster = connection.execute("SELECT count(*) AS n FROM family_residents WHERE family_id=%s", (made["family_id"],)).fetchone()
    assert row["deleted_at"] is not None and roster["n"] == 0


def test_family_domain_projection_rejects_stale_live_resource(lineage, charters):
    """Family 领域投影拒绝过期资源记录"""
    from management_portal.society_service.family_storage import resource_records
    from management_portal.society_service.resource_client import _family_resource_projection
    made = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    state = get_lineage_data_store()
    with state.database.snapshot() as connection:
        original = resource_records(connection, resource_ids=[made["resource_id"]])[0]
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": made["family_id"], "goal": "Changed"})
    with pytest.raises(ValueError, match="Stale canonical"):
        get_resource_service().sync_collaboration_resource(**_family_resource_projection(original))


def test_family_projection_reconciles_missing_descriptors_from_domain(lineage, charters):
    """Family 投影从领域记录补齐缺失描述"""
    from management_portal.society_service.family_resource_projection import FamilyResourceProjectionWorker
    from management_portal.resources.registry_store import get_registry_store
    made = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    registry = get_registry_store()
    with registry.writer('family') as writer:
        writer.delete('family', made['resource_id'])
    assert FamilyResourceProjectionWorker().reconcile() == 1
    assert get_resource_service().reference(made['resource_id'], actor='sample_admin')['resource_id'] == made['resource_id']


@pytest.mark.parametrize('deleted', [False, True])
def test_family_reconciliation_rereads_after_concurrent_edit_or_deletion(
    lineage, charters, monkeypatch, caplog, deleted,
):
    from management_portal.society_service.family_resource_projection import FamilyResourceProjectionWorker
    from management_portal.resources.registry_store import get_registry_store
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    sync = resource_client._sync_many
    pending = True

    def race(projections):
        nonlocal pending
        # Freeze the read snapshot, then commit a genuine competing edit before
        # ResourceService validates it, as startup template refresh does.
        projections = list(projections)
        if pending:
            pending = False
            if deleted:
                ok('Unfamilize', {'lineage_id': lineage, 'family_id': made['resource_id']})
            else:
                ok('UpdateFamily', {'lineage_id': lineage, 'family_id': made['resource_id'],
                                    'goal': 'new canonical goal'})
        return sync(projections)

    monkeypatch.setattr(resource_client, '_sync_many', race)
    assert FamilyResourceProjectionWorker().reconcile() == (0 if deleted else 1)
    record = get_registry_store().get('family', made['resource_id'])
    if deleted:
        assert record['status'] == 'deleted'
    else:
        assert record['summary'] == 'new canonical goal'
    assert 'Family resource reconciliation failed' not in caplog.text


def test_family_publication_retries_locked_canonical_facts_without_blocking(lineage, charters):
    import psycopg
    from management_portal.society_service.errors import ResourceProjectionConflict
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    with psycopg.connect(get_lineage_data_store().database.dsn) as other:
        other.execute("SELECT set_config('search_path', %s, true)", (get_lineage_data_store().database.schema,))
        other.execute('SELECT resource_id FROM families WHERE resource_id=%s FOR UPDATE',
                      (made['resource_id'],)).fetchone()
        with pytest.raises(ResourceProjectionConflict, match='busy'):
            resource_client.sync_families([made['resource_id']])
    assert len(resource_client.sync_families([made['resource_id']])) == 1


@pytest.mark.parametrize('conflict', [True, False])
def test_family_reconciliation_bounds_conflicts_but_reports_other_errors(
    lineage, charters, monkeypatch, caplog, conflict,
):
    from unittest.mock import Mock
    from management_portal.resources.errors import resource_error_from_payload, resource_error_payload
    from management_portal.society_service.errors import ResourceProjectionConflict
    from management_portal.society_service.family_resource_projection import FamilyResourceProjectionWorker
    ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    failure = (resource_error_from_payload(resource_error_payload(ResourceProjectionConflict('changed again')))
               if conflict else ValueError('invalid descriptor'))
    publish = Mock(side_effect=failure)
    monkeypatch.setattr(resource_client, '_sync_many', publish)
    assert FamilyResourceProjectionWorker().reconcile() == 0
    assert publish.call_count == (3 if conflict else 1)
    if conflict:
        assert 'deferred' in caplog.text and 'Traceback' not in caplog.text
    else:
        assert 'invalid descriptor' in caplog.text and 'Traceback' in caplog.text


def test_family_reconciliation_does_not_replay_published_deletions(lineage, charters, caplog):
    """Family 巡检只补齐现存 Family，不重放已发布的删除

    A deleted Family's facts can change after its deletion was published, as
    when old rosters were deleted on 2026-10-17. ResourceService refuses that
    different tombstone, which used to fail the Lineage's whole batch every
    minute, so its live Families were never repaired.
    """
    import logging
    from management_portal.society_service.family_resource_projection import FamilyResourceProjectionWorker
    from management_portal.resources.registry_store import get_registry_store
    gone = ok("Familize", {"lineage_id": lineage, "resident_ids": [resident(lineage, "Former member")],
                           "title": "Removed Family"})
    ok("Unfamilize", {"lineage_id": lineage, "family_id": gone["resource_id"]})
    live = ok("Familize", {"lineage_id": lineage, "resident_ids": [resident(lineage, "Current member")],
                           "title": "Current Family"})
    with get_lineage_data_store().database.transaction() as connection:
        connection.execute("UPDATE families SET goal=%s WHERE resource_id=%s",
                           ("changed after deletion", gone["resource_id"]))
    with get_registry_store().writer('family') as writer:
        writer.delete('family', live['resource_id'])
    with caplog.at_level(logging.ERROR):
        assert FamilyResourceProjectionWorker().reconcile() == 1
    assert get_resource_service().reference(live['resource_id'], actor='sample_admin')['resource_id'] == live['resource_id']
    assert "Family resource reconciliation failed" not in caplog.text


def test_family_permission_follows_its_current_lineage_and_deletes(lineage, charters, monkeypatch):
    """Family 在权限树中跟随当前 Lineage，删除后离开权限树

    对 Lineage 的 contributor 覆盖它的 Family；Family 换到另一个 Lineage 后不再覆盖；Family 删除后不再有位置，steward 的表项随之撤除。
    """
    from management_portal import permission_system
    made = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    rid = made['resource_id']
    record = get_resource_service().get(rid, actor='sample_admin')
    destination = 'lineage_aaaaaaaaaaaa'
    permissions = permission_test_support.enable(monkeypatch)
    steward = made['steward_resident_id']
    # Only a live Resident gets steward entries: the steward is placed like every Resident.  Lineages are
    # registered before the objects in them (decision_b820faf42218 §5.7), so both Lineages are placed first.
    permissions.place(permission_test_support.Fact('lineage', lineage), permission_test_support.Fact('lineage', destination),
                      permission_test_support.Fact('resident', steward, {'lineage': lineage}))
    permission_system.sync_resource(record, resource_type='family')
    permissions.grant('bob', lineage, '@contributor')
    assert permissions.holds('bob', '@contributor', rid) and not permissions.holds('bob', '@admin', rid)
    assert permissions.holds(steward, '@admin', lineage)
    rebound = {**record, 'attributes': {**record['attributes'], 'lineage_id': destination},
               'relations': [{'kind': 'lineage', 'target_id': destination}]}
    permission_system.sync_resource(rebound, resource_type='family')
    store = permissions.store
    assert store.paths('R', [rid])[rid][-2] == destination
    assert permissions.holds('bob', '@viewer', rid) and not permissions.holds('bob', '@contributor', rid)
    assert permissions.holds(steward, '@admin', destination)
    permission_system.sync_resource({**rebound, 'status': 'deleted', 'deleted_at': '2026-10-15T00:00:00Z'}, resource_type='family')
    assert store.paths('R', [rid]) == {}
    assert permissions.holds(steward, '@viewer', destination) and not permissions.holds(steward, '@contributor', destination)


def test_family_current_personal_scope_is_applied_before_descriptor_access(lineage, charters):
    """Family 读取描述前应用当前个人隐私范围"""
    made = ok("Familize", {"lineage_id": lineage, "scope": "lineage"})
    # The discovery descriptor still describes a public scope at this point.
    with get_lineage_data_store().database.transaction() as connection:
        connection.execute("UPDATE lineages SET lineage_membership='personal',created_by='alice' WHERE lineage_id=%s", (lineage,))
    service = get_resource_service()
    with pytest.raises(KeyError):
        service.reference(made['resource_id'], actor='bob')
    assert service.reference(made['resource_id'], actor='alice')['attributes']['personal_owner_id'] == 'alice'
