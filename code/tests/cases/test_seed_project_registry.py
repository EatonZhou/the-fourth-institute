"""Desktop case selection preserves the project roots used by permission facts."""
import json
from pathlib import Path

import pytest

from scripts import tfi_seed_selection as selection


SEED = Path(__file__).resolve().parents[3] / 'instance/example_instance/seed'


@pytest.mark.parametrize('include_examples', [False, True])
def test_real_seed_keeps_every_selected_project_registered(include_examples):
    manifest = json.loads((SEED / 'manifest.json').read_text())['engineering']
    _, selected_sql, _ = selection.plan(SEED, manifest, include_examples=include_examples)
    sql = selected_sql if selected_sql is not None else (SEED / manifest['database']['path']).read_text()
    rows = list(selection.inserts(sql))
    registry, = [row['document_json'] for _, _, table, row in rows
                 if table == ('resource_registry', 'registry_documents')
                 and row['collection'] == 'project_registry' and row['document_id'] == 'projects']
    project_ids = {project['id'] for project in registry['projects']}
    assert 'AI员工' in project_ids
    assert (selection.LENIA_PROJECT in project_ids) == include_examples
    for _, _, table, row in rows:
        if table == ('lineage', 'lineages'):
            assert row['project_id'] in project_ids


def test_case_filter_preserves_other_project_metadata_and_unrelated_sql():
    retained = {'id': '保留项目', 'path': "/Projects/quote'and\\slash", 'description': 'line\nnext'}
    registry = {'schema_version': 1, 'projects': [retained, {'id': selection.LENIA_PROJECT}],
                'status_vocab': ['active'], 'tag_vocab': ['quote\'s']}
    literal = json.dumps(registry, ensure_ascii=False).replace("'", "''")
    statement = 'INSERT INTO "resource_registry"."registry_documents" ("collection","document_id","revision","document_json") VALUES '
    statement += "('project_registry','projects',7,'" + literal + "'::jsonb);"
    unrelated = 'INSERT INTO "other"."table" ("text") VALUES (\'unchanged\');'
    filtered, _, _ = selection.selection(statement + '\n' + unrelated, {'files': {}})
    row, = [row for _, _, table, row in selection.inserts(filtered)
            if table == ('resource_registry', 'registry_documents')]
    assert row['document_json'] == {**registry, 'projects': [retained]}
    assert row['revision'] == '7'
    assert filtered.endswith(unrelated)


def test_base_seed_index_and_permission_completion_keep_retained_knowledge(monkeypatch):
    """Use real packaged knowledge, registry, indexing and the PostgreSQL PAR store."""
    from types import SimpleNamespace
    from management_portal import project_registry, resource_index_backend
    from management_portal.permission_system.facts import collect_portal_facts, TooManyRemoved
    from management_portal.resources.service import ResourceService
    from tests.cases.permission_test_support import enable
    from tests.cases.registry_support import put_registry_document

    manifest = json.loads((SEED / 'manifest.json').read_text())['engineering']
    _, sql, _ = selection.plan(SEED, manifest, include_examples=False)
    document, = [row['document_json'] for _, _, table, row in selection.inserts(sql)
                 if table == ('resource_registry', 'registry_documents')
                 and row['collection'] == 'project_registry' and row['document_id'] == 'projects']
    # Map the exported project root to this test's immutable seed fixture.
    document = {**document, 'projects': [
        {**project, 'path': str(SEED / 'data/share_root/Projects' / project['id'])}
        for project in document['projects']]}
    put_registry_document('project_registry', 'projects', document)
    project_registry.reset_cache_for_tests()
    adapter = resource_index_backend.ProjectKnowledgeAdapter()
    service = SimpleNamespace(
        _collaboration=SimpleNamespace(list=lambda kind: []),
        _index=lambda: SimpleNamespace(authorization_snapshot=lambda: {'records': adapter.scan()[0]}))
    service.permission_facts = lambda: collect_portal_facts(service)
    permissions = enable(monkeypatch)
    facts = service.permission_facts()
    knowledge_ids = {fact.id for fact in facts if fact.kind in {'concept', 'decision', 'thinking'}}
    assert len(knowledge_ids) > ResourceService.PERMISSION_COMPLETE_MAX_REMOVED
    assert any(fact.id == 'AI员工' for fact in facts)
    permissions.place(*facts)
    with permissions.store.read() as connection:
        before = permissions.store.contents(connection)

    # The original bug removed the aggregate document, making the live fact
    # reader lose both the project and all of its packaged knowledge.
    put_registry_document('project_registry', 'projects', {**document, 'projects': []})
    with pytest.raises(TooManyRemoved):
        ResourceService.complete_permission_instance(service)
    with permissions.store.read() as connection:
        assert permissions.store.contents(connection) == before

    put_registry_document('project_registry', 'projects', document)
    report = ResourceService.complete_permission_instance(service)
    assert report['removed_nodes'] == report['removed_entries'] == 0
    assert knowledge_ids <= {fact.id for fact in service.permission_facts()}
