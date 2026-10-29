""":module: spec.design-governance"""
from management_portal.society_service import business_fact_views
"""Resident direct facts and Lineage readonly derivation stay independent."""
import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_lineage, resident_catalog, lineage_catalog
from management_portal.society_service import resource_client, lineage_domain
from management_portal.society_service import lineage_aggregate

R = 'resident_0123456789ab'
L = 'lineage_123456789abc'


def test_resident_view_preserves_direct_fields_without_lineage_copies(monkeypatch):
    """Resident 视图保留直接字段且不复制 Lineage 所有者"""
    canonical = {'resident_id': R, 'lineage_id': L,
                 'watchboard': {'enabled': True}, 'last_error': 'runtime'}
    before = copy.deepcopy(canonical)
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda target: copy.deepcopy(canonical)))
    monkeypatch.setattr(lineage_catalog, 'get_catalog', lambda *a: pytest.fail('redundant Lineage lookup'))
    result = resident_lineage.find(R)
    assert result['watchboard'] == {'enabled': True} and result['last_error'] == 'runtime'
    assert 'owner_ids' not in result and 'lineage_owner_ids' not in result
    assert canonical == before


def test_scoped_resident_view_never_replays_cached_direct_fields_over_current_record(monkeypatch):
    """作用域缓存不覆盖 Resident 最新直接字段与运行状态"""
    canonical = {'resident_id': R, 'lineage_id': L, 'watchboard': {'enabled': True}}
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(
        root=None, get=lambda target: copy.deepcopy(canonical)))
    monkeypatch.setattr(lineage_catalog, 'get_catalog', lambda *a: SimpleNamespace(
        get_access_metadata=lambda target: {'lineage_id': target}))
    with resident_lineage.access_snapshot_scope():
        first = resident_lineage.find(R)
        assert first['watchboard'] == {'enabled': True}
        canonical.update(last_error='new runtime', watchboard={'enabled': False})
        second = resident_lineage.find(R)
    assert second['last_error'] == 'new runtime' and second['watchboard'] == {'enabled': False}


@pytest.mark.parametrize('kind', ['resident', 'lineage'])
def test_domain_read_hydration_keeps_direct_facts(monkeypatch, kind):
    """领域读取保留直接事实且不叠加权限字段"""
    target = R if kind == 'resident' else L
    canonical = {kind + '_id': target, 'watchboard': {'enabled': True}}
    got = business_fact_views.canonical_view(canonical, resource_id=target, actor='CASE-A')
    assert got['watchboard'] == {'enabled': True} and canonical['watchboard'] == {'enabled': True}
    assert '_resource_acl_fingerprint' not in got
    assert 'resource_capabilities' not in got


def test_interrupted_lineage_deletion_reads_canonical_fields(monkeypatch):
    """中断的 Lineage 删除读取规范字段与原修订号"""
    canonical = {'lineage_id': L, 'revision': 7, 'title': 'Interrupted',
                 'deleted_at': '2026-10-10T00:00:00Z'}
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', lambda target: copy.deepcopy(canonical))
    got = lineage_domain._read_lineage_for_deletion(L, 'CASE-A')
    assert got['title'] == 'Interrupted' and got['revision'] == 7
