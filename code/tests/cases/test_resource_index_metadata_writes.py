"""Adapter health must persist without rewriting an unchanged index marker.

:module: resource.index-search
:design: decision_8dc6b3d03ca7
"""
from management_portal import resource_index_store
from management_portal.resource_index_records import ChangeSet, resource_record


def test_adapter_checks_preserve_index_marker_but_content_changes_advance_it(monkeypatch):
    """Real PG row versions separate adapter freshness from actual index changes.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    clock = ['2026-11-04T00:00:00Z']
    monkeypatch.setattr(resource_index_store, '_now_iso', lambda: clock[0])
    store = resource_index_store.IndexStore()
    item = resource_record(resource_id='skill_aaaaaaaaaaaa', resource_type='skill',
                           title='before', adapter='skill')
    store.apply(ChangeSet(adapter='skill', upserts=(item,)))
    def marker():
        with store.database.read() as connection:
            return tuple(connection.execute("SELECT ctid::text,xmin::text,value FROM resource_index_meta "
                                            "WHERE key='index_updated_at'").fetchone())
    first = marker()
    clock[0] = '2026-11-04T00:00:01Z'
    store.mark_checked('skill', {'fixture_cursor': 1})
    assert marker() == first
    reopened = resource_index_store.IndexStore()
    assert reopened.status()['adapters']['skill']['last_checked_at'] == clock[0]
    assert reopened.adapter_source_state('skill') == {'fixture_cursor': 1}
    clock[0] = '2026-11-04T00:00:02Z'
    store.apply(ChangeSet(adapter='skill', upserts=({**item, 'title': 'after'},)))
    latest = marker()
    assert latest[:2] != first[:2] and latest[2] == clock[0]
    assert resource_index_store.IndexStore().internal_get(item['resource_id'])['title'] == 'after'
