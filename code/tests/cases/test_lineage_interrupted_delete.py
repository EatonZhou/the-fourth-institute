"""Interrupted deletion uses the remaining canonical aggregate and revision."""
from __future__ import annotations

import pytest
from management_portal.society_service import lineage_domain
from management_portal.society_service import lineage_aggregate

LINEAGE = 'lineage_0123456789ab'


def test_a_deletion_stopped_after_the_resource_tombstone_can_be_finished(monkeypatch):
    """资源侧已删除、定义还在的 Lineage 可以再次删除完成

    删除在资源记录标为删除之后、移除定义之前中断时，再次删除按仍在的定义继续完成，不因读不到资源而报找不到。

    :importance: P1
    :design: decision_1b2bdf5c33d6
    :design-point:
    """
    canonical = {'lineage_id': LINEAGE, 'title': 'Interrupted', 'revision': 8}
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', lambda rid: canonical)
    result = lineage_domain._read_lineage_for_deletion(LINEAGE, 'sample_admin')
    assert result['title'] == 'Interrupted' and result['revision'] == 8
    assert canonical == {'lineage_id': LINEAGE, 'title': 'Interrupted', 'revision': 8}


def test_missing_canonical_lineage_stays_an_error_even_with_old_resource_observation(monkeypatch):
    """删除时 Lineage 定义已不存在仍报错，不借旧的资源观测继续"""
    def absent(rid):
        raise KeyError(rid)
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', absent)
    with pytest.raises(KeyError):
        lineage_domain._read_lineage_for_deletion(LINEAGE, 'sample_admin')


def test_unregistered_canonical_lineage_keeps_missing_direct_fields(monkeypatch):
    """为删除读取未登记的 Lineage 定义时不补出 owner 等缺失字段"""
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', lambda rid: {'lineage_id': rid, 'revision': 3})
    result = lineage_domain._read_lineage_for_deletion(LINEAGE, 'sample_admin')
    assert 'owner_ids' not in result and result['revision'] == 3
