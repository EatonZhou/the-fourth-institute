"""Listing summaries use the same live identities as the authoritative graph.

:module: lineage.team
:design: decision_3e07c6d35cac
"""
from management_portal.society_service import lineage_reads, resident_catalog
from tests.cases.test_principal_hierarchy import family, make_project_graph
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def test_listing_excludes_retained_reference_to_deleted_identity(future_creation_store):
    """Historical reference rows never make deleted Residents active members.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    project, child = family('Project'), family('Child')
    make_project_graph(project['lineage_id'])
    store = resident_catalog.get_catalog().state_store
    # An interrupted/older projection may retain the reference, while deletion
    # truth lives only on residents. Keep that history to exercise both readers.
    store.remove_residents([{'resident_id': child['resident_id'],
                            'deleted_at': '2026-11-05T00:00:00Z', 'deleted_by': 'sample_admin'}])
    with store.database.read() as db:
        assert db.execute('SELECT 1 FROM lineage_node_references WHERE lineage_id=%s AND node_id=%s',
                          (project['lineage_id'], child['resident_id'])).fetchone()
    ids = [project['lineage_id'], child['lineage_id']]
    summaries = store.lineage_listing_graph_summaries(ids)
    for lid in ids:
        graph = lineage_reads.get_lineage(lid, 'sample_admin')
        visible = {n['id'] for n in graph['nodes'] if n['id'].startswith('resident_')}
        assert set(summaries[lid]['lineage_node_ids']) == visible
        assert summaries[lid]['node_count'] == len(visible)
        assert summaries[lid]['statuses'].get('idle', 0) == len(visible)
    assert summaries[project['lineage_id']]['lineage_node_ids'] == [project['resident_id']]
    assert summaries[child['lineage_id']]['lineage_node_ids'] == []
