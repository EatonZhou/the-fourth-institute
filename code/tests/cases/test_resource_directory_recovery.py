from pathlib import Path

import pytest

from management_portal.resources.registry_store import get_registry_store
from management_portal.resources import directory_registrations as directories


def test_projection_failure_survives_owner_restart(tmp_path, monkeypatch):
    from management_portal.workbench_projections import resource_lifecycle
    root = tmp_path / 'skill'
    root.mkdir()
    resource_id = 'skill_111111111111'
    directories.register_owned('knowledge', resource_id, [root])
    directories.unregister_owned('knowledge', resource_id, actor='owner')
    def fail(*args, **kwargs):
        raise RuntimeError('projection unavailable')
    monkeypatch.setattr(resource_lifecycle, 'release_resource', fail)
    first = resource_lifecycle.ResourceProjectionCleanup()
    assert first.run_pending()[0]['error'] == 'projection unavailable'
    assert get_registry_store().get('knowledge', resource_id) is None
    restarted = resource_lifecycle.ResourceProjectionCleanup()
    assert restarted.pending()[0]['attempts'] == 1
    calls = []
    monkeypatch.setattr(resource_lifecycle, 'release_resource', lambda rid, **kw: calls.append((rid, kw)))
    assert restarted.run_pending()[0]['status'] == 'completed'
    assert calls == [(resource_id, {'actor': 'owner'})]
    assert not resource_lifecycle.ResourceProjectionCleanup().pending()


@pytest.mark.parametrize('nested', [False, True])
def test_unregister_releases_retention_in_same_transaction(tmp_path, nested):
    from management_common.directory_retention import RetentionStore
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    root, upload = tmp_path / 'resource', tmp_path / 'upload'
    root.mkdir()
    upload.write_text('object data')
    resource_id = 'skill_111111111111'
    directories.register_owned('knowledge', resource_id, [root], origin='adopted')
    retention = RetentionStore()
    retention.declare(upload, category='object_data', owner_id=resource_id)
    registry = get_registry_store()
    if nested:
        with pytest.raises(RuntimeError, match='rollback'):
            with registry.database.transaction() as connection:
                directories.unregister_nested([tmp_path], connection=connection, actor='owner')
                raise RuntimeError('rollback')
        assert registry.get('knowledge', resource_id)
        assert not get_directory_cleanup_store().pending_at(upload)
        with registry.database.transaction() as connection:
            directories.unregister_nested([tmp_path], connection=connection, actor='owner')
    else:
        directories.unregister_owned('knowledge', resource_id, actor='owner')
    assert get_directory_cleanup_store().pending_at(upload)
    with retention.database.read() as connection:
        assert not connection.execute(f'SELECT 1 FROM {retention.table} WHERE owner_id=%s', (resource_id,)).fetchone()


@pytest.mark.parametrize('case_variant', [False, True])
def test_paper_manifest_creation_does_not_take_existing_directory(tmp_path, monkeypatch, case_variant):
    from management_portal.paper_store import PaperStore
    root = tmp_path / 'papers' / 'package'
    occupied = root.with_name('PACKAGE') if case_variant else root
    occupied.mkdir(parents=True)
    (occupied / 'keep').write_text('external content')
    if case_variant:
        from management_common import directory_paths
        monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
    with pytest.raises(FileExistsError):
        PaperStore(tmp_path / 'papers').write_manifest({'id': 'paper_111111111111', 'title': 'Test'}, root)
    assert (occupied / 'keep').read_text() == 'external content'
    assert get_registry_store().get('paper', 'paper_111111111111') is None


def test_new_paper_creation_cannot_take_registered_adopted_identity(tmp_path):
    from management_portal.paper_store import PaperStore
    store = PaperStore(tmp_path / 'papers')
    root = tmp_path / 'adopted'
    root.mkdir()
    resource_id = 'paper_111111111111'
    directories.register_owned('paper', resource_id, [root], origin='adopted')
    store.write_manifest({'id': resource_id, 'title': 'Original'}, root)
    before = (root / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError, match='already registered'):
        store.register({'id': resource_id, 'title': 'Different title'}, '')
    assert (root / 'manifest.json').read_bytes() == before
    assert get_registry_store().get('paper', resource_id)['directory']['origin'] == 'adopted'


def test_knowledge_create_rejects_unregistered_matching_file(tmp_path, monkeypatch):
    from knowledge_board_lan import knowledge_backend as kb
    monkeypatch.setattr(kb, '_registered_project', lambda _: {'path': str(tmp_path)})
    monkeypatch.setattr(kb, '_get_tag_config', lambda: (['decision'], {'decision': 'decision'}, {}, 'test'))
    resource_id = 'decision_111111111111'
    payload = {'title': 'Original', 'project_id': 'project'}
    first = kb.register_item('decision', resource_id, payload)
    with get_registry_store().writer('knowledge') as writer:
        writer.delete('knowledge', resource_id)
    with pytest.raises(FileExistsError):
        kb.register_item('decision', resource_id, payload)
    assert Path(first['path']).exists()
    assert get_registry_store().get('knowledge', resource_id) is None


def test_experiment_retry_cannot_claim_unregistered_document(tmp_path, monkeypatch):
    from management_portal.resources.providers import experiment
    from management_portal.resources import ResourceService
    monkeypatch.setattr(experiment, '_project_roots', lambda: [('project', tmp_path)])
    resource_id = 'experiment_111111111111'
    payload = {'project_id': 'project', 'title': 'Original'}
    experiment.register_experiment(ResourceService(), payload, resource_id=resource_id)
    with get_registry_store().writer('knowledge') as writer:
        writer.delete('knowledge', resource_id)
    with pytest.raises(FileExistsError):
        experiment.register_experiment(ResourceService(), payload, resource_id=resource_id)


@pytest.mark.parametrize('kind', ['paper', 'knowledge', 'experiment'])
@pytest.mark.parametrize('occupancy', ['casefold', 'pending'])
def test_creation_respects_casefold_and_cleanup_occupancy(tmp_path, monkeypatch, kind, occupancy):
    from management_portal.paper_store import PaperStore
    from management_portal.resources.providers import experiment
    from management_portal.resources import ResourceService
    from knowledge_board_lan import knowledge_backend as kb
    from management_common.directory_cleanup_store import get_directory_cleanup_store, DirectoryPendingCleanup
    from datetime import date
    if kind == 'paper':
        store = PaperStore(tmp_path / 'papers')
        payload = {'id': 'paper_111111111111', 'title': 'Original'}
        path = store.paper_dir_for_new(payload)
        create = lambda: store.register(payload, '')
    elif kind == 'knowledge':
        monkeypatch.setattr(kb, '_registered_project', lambda _: {'path': str(tmp_path)})
        monkeypatch.setattr(kb, '_get_tag_config', lambda: (['decision'], {'decision': 'decision'}, {}, 'test'))
        path = tmp_path / 'knowledge_base' / 'decision' / f'{date.today():%Y%m%d}__decision__Original__decision_111111111111.md'
        create = lambda: kb.register_item('decision', 'decision_111111111111', {'title': 'Original', 'project_id': 'project'})
    else:
        monkeypatch.setattr(experiment, '_project_roots', lambda: [('project', tmp_path)])
        path = tmp_path / 'knowledge_base' / 'experiment' / 'original__experiment_111111111111'
        create = lambda: experiment.register_experiment(ResourceService(), {'project_id': 'project', 'title': 'Original'}, resource_id='experiment_111111111111')
    if occupancy == 'casefold':
        from management_common import directory_paths
        monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
        occupied = path.with_name(path.name.upper())
        occupied.mkdir(parents=True)
        error = FileExistsError
    else:
        queue = get_directory_cleanup_store()
        with get_registry_store().database.transaction() as connection:
            queue.enqueue(path, owner_id='previous', operation_id='previous', actor='', connection=connection)
        error = DirectoryPendingCleanup
    with pytest.raises(error):
        create()


def test_projection_request_rolls_back_with_formal_unregistration(tmp_path):
    from management_portal.workbench_projections.resource_lifecycle import ResourceProjectionCleanup
    root = tmp_path / 'skill'
    root.mkdir()
    directories.register_owned('knowledge', 'skill_111111111111', [root])
    with pytest.raises(RuntimeError):
        with get_registry_store().database.transaction() as connection:
            directories.unregister_nested([tmp_path], connection=connection, actor='owner')
            raise RuntimeError('rollback')
    assert get_registry_store().get('knowledge', 'skill_111111111111')
    assert not ResourceProjectionCleanup().pending()


def test_migration_requires_evidence_not_directory_names(tmp_path):
    from scripts.directory_protocol_resource_migration import migrate
    store = get_registry_store()
    # Both names look generated; only one has caller-supplied evidence.
    unknown = tmp_path / 'handoff-workspaces' / 'paper_111111111111'
    proven = tmp_path / 'ordinary'
    with store.writer('repo') as writer:
        writer.put('repo', 'repo_111111111111', {'checkouts': [{'path': str(unknown)}, {'path': str(proven)}]})
    with store.database.transaction() as connection:
        migrate(connection, registry_schema=store.database.schema, created_paths=[proven])
    assert [item['origin'] for item in store.get('repo', 'repo_111111111111')['checkouts']] == ['adopted', 'created']


def test_repo_initialization_checks_pending_before_git_writes(tmp_path, monkeypatch):
    from management_common import directory_paths
    monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
    from management_portal.resource_identity_backend import initialize_git_checkout
    from management_common.directory_cleanup_store import get_directory_cleanup_store, DirectoryPendingCleanup
    root = tmp_path / 'checkout'
    root.mkdir()
    queue = get_directory_cleanup_store()
    with get_registry_store().database.transaction() as connection:
        queue.enqueue(root.with_name('CHECKOUT'), owner_id='old', operation_id='old', actor='', connection=connection)
    with pytest.raises(DirectoryPendingCleanup):
        initialize_git_checkout(root)
    assert not (root / '.git').exists()


@pytest.mark.parametrize('kind', ['knowledge', 'experiment'])
def test_creation_retry_keeps_adopted_owner_and_original_content(tmp_path, monkeypatch, kind):
    from knowledge_board_lan import knowledge_backend as kb
    from management_portal.resources.providers import experiment
    from management_portal.resources import ResourceService
    if kind == 'knowledge':
        monkeypatch.setattr(kb, '_registered_project', lambda _: {'path': str(tmp_path)})
        monkeypatch.setattr(kb, '_get_tag_config', lambda: (['decision'], {'decision': 'decision'}, {}, 'test'))
        resource_id = 'decision_111111111111'
        create = lambda payload: kb.register_item('decision', resource_id, payload, operation_id='original')
    else:
        monkeypatch.setattr(experiment, '_project_roots', lambda: [('project', tmp_path)])
        resource_id = 'experiment_111111111111'
        create = lambda payload: experiment.register_experiment(ResourceService(), payload, resource_id=resource_id, operation_id='original')
    payload = {'project_id': 'project', 'title': 'Original', 'body': 'Preserve'}
    first = create(payload)
    path = Path(first['path'])
    original = path.read_bytes()
    with get_registry_store().writer('knowledge') as writer:
        record = writer.get('knowledge', resource_id)
        for entry in record['directories']:
            entry['origin'] = 'adopted'
        record['directory']['origin'] = 'adopted'
        writer.put('knowledge', resource_id, record)
    with pytest.raises(ValueError, match='different arguments'):
        create({**payload, 'title': 'Changed', 'body': 'Must not replace'})
    repeated = create(payload)
    assert Path(repeated['path']) == path
    assert repeated['created'] is False
    assert repeated['title'] == 'Original'
    assert path.read_bytes() == original
    assert get_registry_store().get('knowledge', resource_id)['directory']['origin'] == 'adopted'


def test_skill_scan_registers_source_and_retains_adoption(tmp_path, monkeypatch):
    from skill_board_lan import skill_backend as skills
    root = tmp_path / 'skills' / 'example'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_text('# Example')
    monkeypatch.setattr(skills, 'CORE_SKILL_NAMES', ())
    monkeypatch.setattr(skills, 'SHARED_SKILLS_ROOT', root.parent)
    monkeypatch.setattr(skills, 'THIRD_PARTY_SKILLS_ROOT', root.parent / 'third-party')
    monkeypatch.setattr(skills, '_parse_projects', lambda **kw: [])
    monkeypatch.setattr(skills, '_get_item_cached', lambda **kw: {'skill_id': 'skill_111111111111'})
    skills.scan_index_source()
    assert get_registry_store().get('knowledge', 'skill_111111111111')['directory']['origin'] == 'created'
    with get_registry_store().writer('knowledge') as writer:
        record = writer.get('knowledge', 'skill_111111111111')
        record['directories'][0]['origin'] = 'adopted'
        record['directory']['origin'] = 'adopted'
        writer.put('knowledge', 'skill_111111111111', record)
    skills.scan_index_source()
    assert get_registry_store().get('knowledge', 'skill_111111111111')['directory']['origin'] == 'adopted'


def test_projection_retry_batch_does_not_starve_later_resources(monkeypatch):
    from management_portal.workbench_projections import resource_lifecycle
    with directories.directory_writer(directories.PROJECTION_RELEASES) as writer:
        for resource_id in ('skill_111111111111', 'skill_222222222222'):
            directories.request_projection_release(resource_id, actor='owner', connection=writer.connection)
    def release(resource_id, **kw):
        if resource_id == 'skill_111111111111':
            raise RuntimeError('still busy')
    monkeypatch.setattr(resource_lifecycle, 'release_resource', release)
    resource_lifecycle.ResourceProjectionCleanup().run_pending(limit=1)
    second = resource_lifecycle.ResourceProjectionCleanup().run_pending(limit=1)
    assert second[0]['resource_id'] == 'skill_222222222222'
    assert second[0]['status'] == 'completed'
