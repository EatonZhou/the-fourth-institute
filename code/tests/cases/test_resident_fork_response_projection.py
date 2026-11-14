"""ForkResident returns committed identity facts without a discarded graph view.

:module: lineage.team
:design: decision_5bf5bfbd52cb
"""
from unittest.mock import Mock

import pytest

from management_portal.society_service import lineage_branches, lineage_state
from tests.cases.isa_test_support import isolated_instruction_store, run  # noqa: F401
from tests.cases.test_resident_registration_resource_ack import registration  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import _append_conversation_turn


def test_isa_fork_and_replay_skip_display_but_keep_identity_ack(registration, monkeypatch):
    parent, resources = registration
    _append_conversation_turn(parent['resident_id'], 0)
    forbidden_view = Mock(side_effect=AssertionError('unused full graph projection'))
    monkeypatch.setattr(lineage_state, '_verified_lineage_view', forbidden_view)
    args = {'lineage_id': parent['lineage_id'], 'source_resident_id': parent['resident_id'],
            'title': 'Minimal Fork', 'fork_mode': 'full'}
    first = run('ForkResident', args, key='minimal-fork-reply')
    assert first.status == 'committed', first
    child = first.result['resident_id']
    assert child in resources
    assert set(first.result) == {'resident_id', 'lineage_id', 'inheritance_edge_id', 'created'}
    # A new instruction exercises the existing-edge replay, rather than only
    # reading the first instruction's stored idempotency receipt.
    replay = lineage_branches.fork_lineage_resident(parent['lineage_id'], {
        'source': parent['resident_id'], 'idempotency_key': 'minimal-fork-reply',
    }, 'sample_admin', include_lineage=False)
    assert replay['created_resident']['id'] == child and replay['created'] is False
    assert 'lineage' not in replay
    forbidden_view.assert_not_called()


def test_direct_graph_fork_still_returns_verified_display(registration, monkeypatch):
    parent, _ = registration
    _append_conversation_turn(parent['resident_id'], 0)
    view = Mock(wraps=lineage_state._verified_lineage_view)
    monkeypatch.setattr(lineage_state, '_verified_lineage_view', view)
    result = lineage_branches.fork_lineage_resident(parent['lineage_id'], {
        'source': parent['resident_id'], 'child_title': 'Full display',
    }, 'sample_admin')
    assert result['lineage']['lineage_id'] == parent['lineage_id']
    assert result['created_resident']['id'] in {n['id'] for n in result['lineage']['nodes']}
    view.assert_called_once()


@pytest.mark.parametrize('missing', ['child', 'edge'])
def test_minimal_reply_still_requires_child_and_inheritance_edge(monkeypatch, missing):
    from types import SimpleNamespace
    rid = 'resident_111111111111'
    graph = {'nodes': [] if missing == 'child' else [{'id': rid}],
             'edges': [] if missing == 'edge' else [{'type': 'inheritance', 'target': rid}]}
    confirmation = Mock()
    monkeypatch.setattr(lineage_branches, 'resident_domain', SimpleNamespace(confirm_resident_registration=confirmation))
    with pytest.raises(RuntimeError, match='Fork'):
        lineage_branches._lineage_branch_response(graph, rid, 'sample_admin', created=True, include_lineage=False)
    confirmation.assert_called_once_with(rid, 'sample_admin')
