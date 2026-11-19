"""Read the retained Paper binding without creating or publishing a role."""
from types import SimpleNamespace

import pytest
from management_portal import paper_translation_binding as binding
from management_portal import paper_tools
from management_portal.workbench_projections import service
from management_portal.workbench_projections.store import ProjectionError
from tests.cases.test_workbench_projections import store, fake_team, APP, LINEAGE, RESIDENT  # noqa: F401


@pytest.mark.parametrize('resident_id', [None, RESIDENT])
def test_translation_binding_inspection_preserves_published_role(store, monkeypatch, resident_id):
    """The read-only application operation reports a retained role or its absence.

    :design: decision_e37f240e186d, decision_528bf9985705
    """
    fake_team(monkeypatch, [{'id': RESIDENT}])  # Any attempted Resident creation fails.
    if resident_id:
        store.update(APP, {'bindings': {'main': {'lineage_id': LINEAGE,
                     'residents': {binding.ROLE: resident_id}}}}, 'alice', 1)
    store.publish(APP, 'alice', store.get(APP, 'alice')['revision'])
    with store.connect() as connection:
        connection.execute("INSERT INTO native_entrypoints (name, app_id) VALUES ('paper', %s)", (APP,))
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    before = store.get(APP, 'alice')
    for _ in range(2):
        assert paper_tools.get_translation_binding(SimpleNamespace(caller='alice')) == {
            'workbench_id': APP, 'revision': before['revision'],
            'published_revision': before['published_revision'],
            'lineage_id': LINEAGE, 'resident_id': resident_id,
        }
    assert store.get(APP, 'alice') == before


def test_draft_main_is_not_silently_published(store, monkeypatch):
    """A retained entry pointing at a draft fails without publishing or creating.

    :design: decision_e37f240e186d, decision_528bf9985705
    """
    fake_team(monkeypatch, [{'id': RESIDENT}])
    with store.connect() as connection:
        connection.execute("INSERT INTO native_entrypoints (name, app_id) VALUES ('paper', %s)", (APP,))
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    before = store.get(APP, 'alice')
    with pytest.raises(ProjectionError) as rejected:
        paper_tools.get_translation_binding(SimpleNamespace(caller='alice'))
    assert rejected.value.code == 'projection.unpublished'
    assert store.get(APP, 'alice') == before
    assert before['published_revision'] is None
