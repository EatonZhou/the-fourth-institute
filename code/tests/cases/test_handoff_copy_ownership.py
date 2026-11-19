"""Copied Workspaces follow the explicit Handoff owner, never physical ancestry."""
from pathlib import Path

import pytest

from tests.cases.directory_fixture_support import directory_owners  # noqa: F401
from tests.cases.test_lineage_backend import lineage_store  # noqa: F401


@pytest.mark.usefixtures('directory_owners')
def test_handoff_copy_owner_is_frozen_and_retry_cannot_transfer(tmp_path):
    from management_portal.society_service.lineage_handoff_backend import materialize_handoff_item
    from management_portal.resources.workspace_store import get_workspace_store
    from tests.cases.test_handoff_directory_creation import copy_item
    item = copy_item(tmp_path)
    options = dict(actor='operator', operation_id='fixed-owner-copy',
                   allowed_target_roots=[tmp_path], checked_at='now')
    first = materialize_handoff_item(item, parent_id='lineage_000000009821', **options)
    store = get_workspace_store()
    wid = first['target_workspace_id']
    assert store.rows([wid])[wid]['owner_id'] == 'lineage_000000009821'
    assert materialize_handoff_item(item, parent_id='lineage_000000009821', **options) == first
    with pytest.raises(ValueError, match='(?i)(payload|different|mismatch|conflict|request)'):
        materialize_handoff_item(item, parent_id='lineage_000000009822', **options)
    assert store.rows([wid])[wid]['owner_id'] == 'lineage_000000009821'
    assert (Path(first['target_path']) / 'file').read_text() == 'source content'


@pytest.mark.usefixtures('directory_owners')
def test_lineage_copy_retirement_preserves_independent_nested_workspace(lineage_store, tmp_path):
    from management_common import directory_dependencies as deps
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service import lineage_handoffs, lineage_lifecycle, lineage_resident_workspaces
    from tests.cases.test_handoff_directory_creation import draft
    lid, handoff, target, source = draft(lineage_store, tmp_path)
    result = lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    copied = result['items'][0]['result']
    store = get_workspace_store()
    wid = copied['target_workspace_id']
    assert store.rows([wid])[wid]['owner_id'] == lid
    with store.database.read() as connection:
        assert connection.execute(
            f"SELECT owner_id FROM {deps.schema()}.directory_dependencies WHERE kind='workspace' AND object_id=%s",
            (wid,),
        ).fetchone()[0] == lid
    nested = Path(copied['target_path']) / 'independent'
    nested.mkdir()
    (nested / 'keep.txt').write_text('independent registered bytes')
    foreign = 'workspace_000000009823'
    store.ensure(foreign, str(nested), owner_id=foreign)
    borrowed = tmp_path / 'borrowed'
    borrowed.mkdir()
    (borrowed / 'keep.txt').write_text('borrowed bytes')
    lineage_resident_workspaces.add_node_reference_workspace(lid, target, 'sample_admin', str(borrowed))
    lineage_lifecycle.delete_lineage(lid, 'sample_admin')
    get_directory_cleanup_store().run_pending()
    assert not store.rows([wid])
    assert store.rows([foreign])[foreign]['owner_id'] == foreign
    assert (nested / 'keep.txt').read_text() == 'independent registered bytes'
    assert not (Path(copied['target_path']) / 'handoff-fixture').exists()
    assert (borrowed / 'keep.txt').read_text() == 'borrowed bytes'
