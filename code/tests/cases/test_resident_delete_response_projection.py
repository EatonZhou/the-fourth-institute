"""DeleteResident confirms deletion without building an unused full graph view.

:module: lineage.team
:design: decision_0f57656a2acd
"""
from unittest.mock import Mock

import pytest

from management_common.json_columns import json_value
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import lineage_domain, resident_session_backend_domain, resident_workspace_domain
from tests.cases.isa_test_support import run
from tests.cases.test_lineage_batch_deletion import deletion_set, future_creation_store  # noqa: F401


@pytest.mark.parametrize('delete_leader', [False, True])
def test_delete_resident_skips_unused_view_but_keeps_committed_effects(deletion_set, monkeypatch, delete_leader):
    """DeleteResident 返回精简回执并保留全部删除效果

    成员与Leader删除均不生成无用全图投影；提交删除状态、活动关系清理和持久生命周期工作，保留Agent与trace，Provider清理由worker执行。

    :module: resident.identity
    :design: decision_d19b9909a674
    :design-point:
    """
    lineage_id, leader, members, _, store = deletion_set
    lineage_domain.update_lineage_metadata(lineage_id, {"leader_resident_id": leader}, "sample_admin")
    target = leader if delete_leader else members[0]
    agent_data = get_agent_data_store()
    agent = agent_data.agent(target)
    trace = agent_data.sequence_segment_ids(agent['trace_sequence_id'])
    confirmation = Mock(wraps=resident_workspace_domain._confirm_deleted_resident_resources)
    monkeypatch.setattr(resident_workspace_domain, '_confirm_deleted_resident_resources', confirmation)
    view = Mock(side_effect=AssertionError('DeleteResident must not build a discarded full graph response'))
    monkeypatch.setattr(lineage_domain, '_verified_lineage_view', view)
    release = Mock(side_effect=AssertionError('Provider cleanup must remain in the durable lifecycle worker'))
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', release)

    result = run('DeleteResident', {"lineage_id": lineage_id, 'resident_id': target}, key='minimal-delete-response')
    assert result.status == 'committed', result
    assert result.result['resident_id'] == target
    assert 'lineage' not in result.result
    view.assert_not_called()
    release.assert_not_called()
    confirmation.assert_called_once()
    tombstone = store.resident_record(target, include_deleted=True)
    assert tombstone['deleted_at'] and tombstone['deleted_by'] == 'sample_admin'
    with pytest.raises(KeyError):
        store.resident_record(target)
    graph = store.lineage_definition(lineage_id)
    assert target not in {node['id'] for node in graph['nodes']}
    if delete_leader:
        assert result.result['new_leader_resident_id'] in members
        assert graph['leader_resident_id'] == result.result['new_leader_resident_id']
    else:
        assert graph['leader_resident_id'] == leader
        assert 'new_leader_resident_id' not in result.result
    assert agent_data.agent(target) == agent
    assert agent_data.sequence_segment_ids(agent['trace_sequence_id']) == trace
    with store.database.read() as connection:
        assert connection.execute('SELECT count(*) FROM resident_resource_projection_outbox WHERE resident_id=%s', (target,)).fetchone()[0] == 0
        operation = connection.execute('SELECT payload_json FROM resident_lifecycle_operations WHERE resident_id=%s', (target,)).fetchone()
    assert operation is not None
    steps = json_value(operation['payload_json'])['steps']
    assert {'release', 'forget_deleted', 'remove'} <= {step['kind'] for step in steps}


def test_graph_deletion_still_returns_verified_lineage(deletion_set, monkeypatch):
    """图删除仍返回验证后的 Lineage

    通用图删除接口仍返回当前已验证Lineage，不因DeleteResident精简回执而丢失其结果。

    :design: decision_4637ca4a69e2
    :design-point:
    """
    lineage_id, _, members, _, _ = deletion_set
    view = Mock(wraps=lineage_domain._verified_lineage_view)
    monkeypatch.setattr(lineage_domain, '_verified_lineage_view', view)
    result = lineage_domain.delete_lineage_graph_objects(lineage_id, {'resident_ids': [members[0]]}, 'sample_admin')
    assert result['lineage']['lineage_id'] == lineage_id
    assert members[0] not in {node['id'] for node in result['lineage']['nodes']}
    view.assert_called_once()
