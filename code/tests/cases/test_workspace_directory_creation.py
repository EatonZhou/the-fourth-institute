"""Operation retries retain identity and never adopt somebody else's files.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace


@pytest.fixture
def creator():
    return WorkspaceCreationStore()


def create(creator, path, **options):
    return creator.create(path=str(path), actor='alice', owner_id='', operation_id='test-create', **options)


def test_create_and_retry_preserve_identity_and_origin(creator, tmp_path):
    path = tmp_path / 'owned'
    first = create(creator, path)
    assert path.is_dir() and not list(path.iterdir())
    assert first['origin'] == 'created'
    assert first['owner_id'] == first['workspace_id']
    assert create(WorkspaceCreationStore(), path) == first
    with pytest.raises(ValueError, match='different arguments'):
        create(creator, tmp_path / 'different')


@pytest.mark.parametrize('origin', ['created', 'adopted'])
def test_creation_records_committed_allocation_once_in_original_scope(creator, tmp_path, origin):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    lifecycle = LifecycleStore()
    first_scope = lifecycle.open('first', actor='alice', run_id='run-first', case_id='workspace')
    other_scope = lifecycle.open('other', actor='alice', run_id='run-other', case_id='workspace')
    path = tmp_path / 'scoped'
    if origin == 'adopted':
        path.mkdir()
    with lifecycle_scope(first_scope, actor='alice'):
        row = create(creator, path, origin=origin)
        assert lifecycle.pending(first_scope) == [row['workspace_id']]
        assert create(creator, path, origin=origin) == row
        assert lifecycle.pending(first_scope) == [row['workspace_id']]
    with lifecycle_scope(other_scope, actor='alice'):
        assert create(creator, path, origin=origin) == row
        assert lifecycle.pending(other_scope) == []
    assert lifecycle.pending(first_scope) == [row['workspace_id']]


def test_completed_unscoped_creation_is_not_claimed_by_retry_scope(creator, tmp_path):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    path = tmp_path / 'preexisting'
    row = create(creator, path)
    lifecycle = LifecycleStore()
    scope = lifecycle.open('later', actor='alice', run_id='later-run', case_id='workspace')
    with lifecycle_scope(scope, actor='alice'):
        assert create(creator, path) == row
    assert lifecycle.pending(scope) == []


def test_failed_creation_does_not_record_allocation_before_commit(creator, tmp_path, monkeypatch):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    lifecycle = LifecycleStore()
    scope = lifecycle.open('failed', actor='alice', run_id='failed-run', case_id='workspace')
    original = creator.workspaces.rows

    def fail_read(*args, **kwargs):
        raise RuntimeError('interrupt final registration transaction')

    monkeypatch.setattr(creator.workspaces, 'rows', fail_read)
    with lifecycle_scope(scope, actor='alice'):
        with pytest.raises(RuntimeError, match='interrupt final'):
            create(creator, tmp_path / 'failed')
        assert lifecycle.pending(scope) == []
        monkeypatch.setattr(creator.workspaces, 'rows', original)
        row = create(creator, tmp_path / 'failed')
        assert lifecycle.pending(scope) == [row['workspace_id']]


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink'])
def test_create_refuses_existing_target(creator, tmp_path, kind):
    path = tmp_path / 'present'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'file':
        path.write_text('keep')
    else:
        path.symlink_to(tmp_path / 'missing', target_is_directory=True)
    with pytest.raises(FileExistsError):
        create(creator, path)
    assert path.exists() or path.is_symlink()


def test_adopt_requires_existing_directory_and_preserves_contents(creator, tmp_path):
    path = tmp_path / 'existing'
    with pytest.raises(FileNotFoundError):
        create(creator, path, origin='adopted')
    path.mkdir()
    (path / 'keep').write_text('existing user files')
    row = create(creator, path, origin='adopted')
    assert row['origin'] == 'adopted'
    assert (path / 'keep').read_text() == 'existing user files'
    assert set(child.name for child in path.iterdir()) == {'keep'}


def test_failure_after_files_before_registration_retries_same_object(creator, tmp_path, monkeypatch):
    path = tmp_path / 'owned'
    original = creator.workspaces.ensure
    def fail(*args, **kwargs):
        raise RuntimeError('process interrupted before registration')
    monkeypatch.setattr(creator.workspaces, 'ensure', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        create(creator, path)
    assert path.is_dir()
    from management_common.portal_database import portal_database
    with portal_database(schema='directory_lifecycle').read() as connection:
        operation = connection.execute('SELECT resource_id FROM resource_creations').fetchone()
    assert creator.workspaces.rows([operation['resource_id']]) == {}
    monkeypatch.setattr(creator.workspaces, 'ensure', original)
    row = create(WorkspaceCreationStore(), path)
    assert row['workspace_id'] == operation['resource_id']
    assert not list(path.iterdir())


def test_concurrent_same_operation_has_one_identity(creator, tmp_path):
    path = tmp_path / 'shared'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(WorkspaceCreationStore(), path), range(2)))
    assert results[0]['workspace_id'] == results[1]['workspace_id']
    assert not list(path.iterdir())


def test_unfinished_creation_reserves_path_for_every_registration(creator, tmp_path, monkeypatch):
    path = tmp_path / 'owned'
    import management_portal.resources.creation as module
    def interrupted(*args):
        raise RuntimeError('interrupted')
    monkeypatch.setattr(module, 'rename_directory_no_replace', interrupted)
    with pytest.raises(RuntimeError):
        create(creator, path)
    with pytest.raises(FileExistsError, match='reserved'):
        creator.workspaces.ensure('workspace_ffffffffffff', str(path))


def test_workspace_creation_has_no_marker_or_separate_ledger(creator, tmp_path):
    from management_common.portal_database import portal_database
    row = create(creator, tmp_path / 'owned')
    assert creator.workspaces.rows([row['workspace_id']])
    assert not list((tmp_path / 'owned').iterdir())
    with portal_database(schema='directory_lifecycle').read() as connection:
        assert connection.execute("SELECT to_regclass('workspace_creations')").fetchone()[0] is None
        assert connection.execute('SELECT completed FROM resource_creations').fetchone()[0]
    assert create(creator, tmp_path / 'owned') == row


@pytest.mark.parametrize('instruction', ['CreateWorkspace', 'RegisterWorkspace'])
def test_official_workspace_directory_entries(resource_workspace, instruction):
    resource_workspace.service._index().project_registered = lambda *args: None
    root = resource_workspace.root / instruction
    if instruction == 'RegisterWorkspace':
        root.mkdir()
    result = resource_workspace.call(instruction, {
        'path': str(root), 'operation_id': 'official-operation', 'title': 'Directory',
    }).raise_for_error()
    assert result['resource']['title'] == 'Directory'
    wid = result['workspace_id']
    assert resource_workspace.store.rows([wid])[wid]['origin'] == (
        'created' if instruction == 'CreateWorkspace' else 'adopted')
    assert resource_workspace.call(instruction, {
        'path': str(root), 'operation_id': 'official-operation', 'title': 'Directory',
    }).raise_for_error()['workspace_id'] == wid


def test_creation_checks_owner_permission_before_touching_directory(resource_workspace, monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    permissions = enable(monkeypatch)
    permissions.users('bob', category='intern')
    rid = 'resident_123456789abc'
    permissions.place(Fact('resident', rid))
    permissions.grant('bob', rid, '@viewer')
    root = resource_workspace.root / 'denied'
    result = resource_workspace.call('CreateWorkspace', {
        'path': str(root), 'operation_id': 'denied', 'owner_id': rid,
    }, actor='bob')
    assert result.error['code'] == 'instruction.forbidden'
    assert not root.exists()


def test_directory_instruction_descriptions_are_translated():
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.localization import translate_message

    catalog = instruction_catalog()
    assert 'AdoptWorkspace' not in catalog
    for name in ('CreateWorkspace', 'RegisterWorkspace', 'GetDirectoryCleanup'):
        declaration = catalog[name]
        descriptions = [declaration.description, *(field.description for field in declaration.parameters),
                        *(field.description for field in declaration.result)]
        for description in descriptions:
            assert translate_message(description, 'en') != description, (name, description)
    for name in ('ReadResource', 'ResolveResource'):
        description = catalog[name].parameter_map['address'].description
        assert translate_message(description, 'en') != description
