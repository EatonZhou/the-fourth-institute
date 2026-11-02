"""The default branch suffix survives the Resident title length limit."""
import pytest
from management_portal.society_service import lineage_branches, lineage_reads, lineage_resident_nodes, lineage_state
from tests.cases.test_principal_hierarchy import family
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, _append_conversation_turn, CANONICAL_RESIDENT_TARGETS,
)


@pytest.mark.parametrize('title,custom', [('源' * 120, ''), ('A' * 119, ''), ('Short', ''), ('源' * 120, 'Explicit child')])
def test_default_branch_title_retains_suffix(future_creation_store, monkeypatch, title, custom):
    root = family('Title regression')
    lineage_id = root['lineage_id']
    source = lineage_resident_nodes.create_lineage_resident(lineage_id, _new_session_payload(title), 'sample_admin')['resident']
    _append_conversation_turn(source['resident_id'], 0)
    monkeypatch.setattr(lineage_state, '_resident_targets', CANONICAL_RESIDENT_TARGETS)
    result = lineage_branches.fork_lineage_resident(lineage_id, {
        'source': source['resident_id'], 'fork_mode': 'full', 'child_title': custom,
    }, 'sample_admin')
    child_id = result['created_resident']['id']
    current = lineage_reads.get_lineage(lineage_id, 'sample_admin')
    child = next(n for n in current['nodes'] if n['id'] == child_id)
    assert len(child['title']) <= 120
    assert child['title'] != title
    if custom:
        assert child['title'] == custom
    else:
        assert child['title'].endswith(' · 分支')
        assert child['title'].startswith(title[:115])
