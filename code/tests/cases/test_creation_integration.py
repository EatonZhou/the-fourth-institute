"""Creation recovery uses operation identity, never user file contents.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from contextlib import contextmanager

import pytest
from pathlib import Path
from unittest.mock import Mock
from tests.cases.test_experiment_resource_provider import experiment_service


@pytest.fixture
def domain_creator(request, tmp_path, monkeypatch):
    kind = request.param
    from management_portal.resources.registry_store import get_registry_store
    if kind == 'paper':
        from management_portal.paper_store import PaperStore
        resource_id = 'paper_abc012345678'
        payload = {'id': resource_id, 'title': 'Recover'}
        create = lambda **kw: PaperStore(tmp_path / 'papers').register(payload, 'alice', **kw)
        exists = lambda: get_registry_store().get('paper', resource_id)
    elif kind == 'project':
        from management_portal.resources.providers import project
        from management_portal import project_registry
        monkeypatch.setattr(project, 'PROJECTS_ROOT', tmp_path / 'projects')
        project_registry.replace(project_registry.empty_registry())
        resource_id = 'creation-project'
        payload = {'id': resource_id, 'description': 'Recover'}
        create = lambda **kw: project.create_project(payload, actor='alice', **kw)
        exists = lambda: next((p for p in project_registry.load()['projects'] if p['id'] == resource_id), None)
    elif kind == 'knowledge':
        from knowledge_board_lan import knowledge_backend as kb
        monkeypatch.setattr(kb, '_registered_project', lambda _: {'path': str(tmp_path)})
        monkeypatch.setattr(kb, '_get_tag_config', lambda: (['decision'], {'decision': 'decision'}, {}, 'test'))
        resource_id = 'decision_abc012345678'
        payload = {'project_id': 'project', 'title': 'Recover', 'body': 'Original'}
        create = lambda **kw: kb.register_item('decision', resource_id, payload, actor='alice', **kw)
        exists = lambda: get_registry_store().get('knowledge', resource_id)
    else:
        from management_portal.resources.providers import experiment
        from management_portal.resources import ResourceService
        monkeypatch.setattr(experiment, '_project_roots', lambda: [('project', tmp_path)])
        resource_id = 'experiment_abc012345678'
        payload = {'project_id': 'project', 'title': 'Recover', 'body': 'Original'}
        create = lambda **kw: experiment.register_experiment(ResourceService(index_provider=Mock()), payload,
                                                            actor='alice', resource_id=resource_id, **kw)
        exists = lambda: get_registry_store().get('knowledge', resource_id)
    return create, exists, resource_id


@pytest.mark.parametrize('domain_creator', ['paper', 'knowledge', 'experiment', 'project'], indirect=True)
@pytest.mark.parametrize('interruption', ['before_publish', 'after_publish', 'before_commit'])
def test_domain_creation_recovers_same_identity_with_real_rollback(domain_creator, monkeypatch, interruption):
    from management_portal.resources import creation
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    create, exists, resource_id = domain_creator
    lifecycle = LifecycleStore()
    scope = lifecycle.open('original', actor='alice', run_id='original', case_id='create')
    other = lifecycle.open('other', actor='alice', run_id='other', case_id='create')
    rename = creation.rename_directory_no_replace
    registration = creation.Creation.registration
    def interrupted_rename(source, target):
        if interruption == 'after_publish':
            rename(source, target)
        raise RuntimeError('injected creation interruption')
    @contextmanager
    def interrupted_registration(self, connection):
        with registration(self, connection):
            yield
        raise RuntimeError('injected creation interruption')
    with lifecycle_scope(scope, actor='alice'):
        with monkeypatch.context() as patch:
            if interruption == 'before_commit':
                patch.setattr(creation.Creation, 'registration', interrupted_registration)
            else:
                patch.setattr(creation, 'rename_directory_no_replace', interrupted_rename)
            with pytest.raises(RuntimeError, match='injected creation'):
                create(operation_id='recover')
        assert exists() is None
        assert lifecycle.pending(scope) == []
    with lifecycle_scope(other, actor='alice'):
        with pytest.raises(ValueError, match='another resource lifecycle'):
            create(operation_id='recover')
        assert lifecycle.pending(other) == []
    with lifecycle_scope(scope, actor='alice'):
        create(operation_id='recover')
        assert exists()
        assert lifecycle.pending(scope) == [resource_id]
    with lifecycle_scope(other, actor='alice'):
        create(operation_id='recover')
        assert lifecycle.pending(other) == []


@pytest.mark.parametrize('kind', ['file', 'directory', 'casefold', 'symlink', 'same_id_file'])
def test_creation_never_takes_existing_target(tmp_path, monkeypatch, kind):
    from management_portal.resources.creation import Creation
    path = tmp_path / 'target'
    occupied = path.with_name('TARGET') if kind == 'casefold' else path
    if kind == 'casefold':
        from management_common import directory_paths
        monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
    if kind in {'directory', 'casefold'}:
        occupied.mkdir()
    elif kind == 'symlink':
        occupied.symlink_to(tmp_path / 'absent')
    else:
        occupied.write_text('---\nkb_id: decision_abc012345678\n---\nUser content')
    before = occupied.lstat()
    with pytest.raises(FileExistsError):
        with Creation('decision_abc012345678', actor='alice', payload={}, operation_id='test').prepare([(path, 'file')]):
            pytest.fail('must reject before filesystem initialization')
    assert occupied.lstat().st_ino == before.st_ino


def test_operation_identity_is_not_payload_identity():
    from management_portal.resources.registration import ResourceRegistrationStore
    from uuid import uuid4
    def reserve(operation):
        return ResourceRegistrationStore().reserve('experiment', {'title': 'same'}, actor='alice',
            operation_id=operation, allocate=lambda: 'experiment_' + uuid4().hex[:12])
    first, fresh = reserve('first')
    assert fresh
    assert reserve('first') == (first, False)
    assert reserve('second')[0] != first
    assert reserve('')[0] != reserve('')[0]
    ResourceRegistrationStore().forget(first)
    with pytest.raises(KeyError):
        reserve('first')


@pytest.mark.parametrize('domain_creator', ['paper', 'knowledge', 'experiment', 'project'], indirect=True)
def test_completed_retry_cannot_resurrect_missing_owner(domain_creator):
    from management_portal.resources.registry_store import get_registry_store
    from management_portal import project_registry
    create, exists, resource_id = domain_creator
    create(operation_id='once')
    if resource_id == 'creation-project':
        with project_registry.mutate() as document:
            document['projects'] = []
    else:
        collection = 'paper' if resource_id.startswith('paper_') else 'knowledge'
        with get_registry_store().writer(collection) as writer:
            writer.delete(collection, resource_id)
    with pytest.raises(KeyError):
        create(operation_id='once')
    assert exists() is None


def test_unknown_staging_inode_is_never_taken_and_remains_reserved(tmp_path, monkeypatch):
    from management_portal.resources import creation
    from management_portal.resources.directory_registrations import directory_writer, register_owned
    from management_common.directory_paths import cleanup_path
    def attempt():
        operation = creation.Creation('decision_abc012345678', actor='alice', payload={}, operation_id='staging-crash')
        with operation.prepare([(tmp_path / 'target', 'file')]):
            operation.publish(lambda index, stage: stage.write_text('ours'))
            with directory_writer('knowledge') as writer, operation.registration(writer.connection):
                register_owned('knowledge', operation.resource_id, operation.paths, connection=writer.connection)
        return operation
    original = creation.Creation._save
    def fail_identity(self, connection):
        if self.artifacts[0].get('identity'):
            raise RuntimeError('crash before inode recorded')
        return original(self, connection)
    with monkeypatch.context() as patch:
        patch.setattr(creation.Creation, '_save', fail_identity)
        with pytest.raises(RuntimeError, match='inode recorded'):
            attempt()
    unproven, = tmp_path.glob('.tfi-create-*')
    unproven.write_text('unproven content stays untouched')
    completed = attempt()
    assert (tmp_path / 'target').read_text() == 'ours'
    assert unproven.read_text() == 'unproven content stays untouched'
    assert cleanup_path(completed.artifacts[0]['unproven_stages'][0]['path']) == unproven
    with completed.db.transaction() as connection:
        with pytest.raises(FileExistsError, match='reserved'):
            completed.queue.assert_available(unproven, connection=connection)


def test_paper_manifest_initializer_recovers_with_new_store(tmp_path, monkeypatch):
    from management_portal.paper_store import PaperStore
    from management_portal.resources import creation
    paper = {'id': 'paper_abc012345678', 'title': 'Manifest'}
    path = tmp_path / 'package'
    rename = creation.rename_directory_no_replace
    def interrupted(source, target):
        rename(source, target)
        raise RuntimeError('published')
    with monkeypatch.context() as patch:
        patch.setattr(creation, 'rename_directory_no_replace', interrupted)
        with pytest.raises(RuntimeError, match='published'):
            PaperStore(tmp_path).write_manifest(paper, path, operation_id='manifest')
    original = (path / 'manifest.json').read_bytes()
    result = PaperStore(tmp_path).write_manifest(paper, path, operation_id='manifest')
    assert result['paper_id'] == paper['id']
    assert (path / 'manifest.json').read_bytes() == original


def test_resource_service_retry_recovers_and_new_operation_is_independent(experiment_service, monkeypatch):
    from management_portal.resources import ResourceService, creation
    from management_portal.resources.providers import experiment
    from management_portal.resources.registration import ResourceRegistrationStore
    service, project_root = experiment_service
    payload = {'project_id': 'OSForAgent', 'title': 'Same payload'}
    actor = 'resident_aaaaaaaaaaaa'
    register = creation.Creation.registration
    @contextmanager
    def interrupted(self, connection):
        with register(self, connection):
            yield
        raise RuntimeError('rollback service creation')
    with monkeypatch.context() as patch:
        patch.setattr(creation.Creation, 'registration', interrupted)
        with pytest.raises(RuntimeError, match='rollback service'):
            service.register('experiment', payload, actor=actor, operation_id='service-first')
    document, = project_root.glob('knowledge_base/experiment/*/experiment.md')
    first_bytes = document.read_bytes()
    restart = ResourceService(index_provider=Mock(), registration_store=ResourceRegistrationStore())
    experiment.install_mutation_provider(restart)
    first = restart.register('experiment', payload, actor=actor, operation_id='service-first')
    assert document.read_bytes() == first_bytes
    assert restart.register('experiment', payload, actor=actor, operation_id='service-first')['resource_id'] == first['resource_id']
    second = restart.register('experiment', payload, actor=actor, operation_id='service-second')
    assert second['resource_id'] != first['resource_id']
    assert len(list(project_root.glob('knowledge_base/experiment/*/experiment.md'))) == 2
    restart.unregister(first['resource_id'], actor=actor, resource_type='experiment')
    with pytest.raises(KeyError):
        restart.register('experiment', payload, actor=actor, operation_id='service-first')


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_retry_rejects_replaced_target_even_with_matching_contents(tmp_path, monkeypatch, kind):
    from management_portal.resources import creation
    path = tmp_path / 'target'
    def initialize(index, stage):
        (stage / 'identity.md' if kind == 'directory' else stage).write_text('decision_abc012345678')
    def publish():
        operation = creation.Creation('decision_abc012345678', actor='alice', payload={}, operation_id='replacement')
        with operation.prepare([(path, kind)]):
            operation.publish(initialize)
    publish()
    path.rename(tmp_path / 'original')
    if kind == 'directory':
        path.mkdir()
    initialize(0, path)
    with pytest.raises(FileExistsError, match='replaced'):
        publish()
    assert (path / 'identity.md' if kind == 'directory' else path).read_text() == 'decision_abc012345678'


def test_failed_initializer_retries_with_fresh_staging_and_cleans_only_owned_stage(tmp_path):
    from management_portal.resources import creation
    from management_portal.resources.directory_registrations import directory_writer, register_owned
    target = tmp_path / 'target'
    def attempt(fail=False):
        operation = creation.Creation('decision_abc012345678', actor='alice', payload={}, operation_id='initializer')
        with operation.prepare([(target, 'directory')]):
            def initialize(index, stage):
                (stage / 'initial').write_text('our file')
                if fail:
                    raise RuntimeError('initializer interrupted')
            operation.publish(initialize)
            with directory_writer('knowledge') as writer, operation.registration(writer.connection):
                register_owned('knowledge', operation.resource_id, operation.paths, connection=writer.connection)
        return operation
    with pytest.raises(RuntimeError, match='initializer interrupted'):
        attempt(True)
    stage, = tmp_path.glob('.tfi-create-*')
    completed = attempt()
    # Creation checks and removes the owned inode itself; it cannot transfer
    # an inode claim to the generic delayed path-only cleanup worker.
    assert not stage.exists()
    assert (target / 'initial').read_text() == 'our file'


def test_workspace_rollback_after_allocation_retains_original_run(tmp_path, monkeypatch):
    from management_portal.resources import workspace_creation_store as workspace
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    lifecycle = LifecycleStore()
    first = lifecycle.open('first', actor='alice', run_id='first', case_id='workspace')
    second = lifecycle.open('second', actor='alice', run_id='second', case_id='workspace')
    def create():
        return workspace.WorkspaceCreationStore().create(path=tmp_path / 'workspace', owner_id='', actor='alice', operation_id='allocation-crash')
    from contextlib import contextmanager
    from management_portal.resources.creation import Creation
    registration = Creation.registration
    @contextmanager
    def interrupted(self, connection):
        with registration(self, connection):
            yield
        raise RuntimeError('crash after allocation before commit')
    with lifecycle_scope(first, actor='alice'):
        with monkeypatch.context() as patch:
            patch.setattr(Creation, 'registration', interrupted)
            with pytest.raises(RuntimeError, match='after allocation'):
                create()
        assert lifecycle.pending(first) == []
    with lifecycle_scope(second, actor='alice'):
        with pytest.raises(ValueError, match='another resource lifecycle'):
            create()
    with lifecycle_scope(first, actor='alice'):
        row = create()
    assert lifecycle.pending(first) == [row['workspace_id']]
    assert lifecycle.pending(second) == []


@pytest.mark.parametrize('kind', ['experiment', 'paper'])
def test_generated_identity_only_allocates_when_owner_commits(experiment_service, tmp_path, monkeypatch, kind):
    from management_portal.resources import creation
    from management_portal.paper_store import PaperStore
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    lifecycle = LifecycleStore()
    actor = 'resident_aaaaaaaaaaaa'
    scope = lifecycle.open('scope', actor=actor, run_id='run', case_id='generated')
    other = lifecycle.open('other', actor=actor, run_id='other', case_id='generated')
    service, _ = experiment_service
    def create():
        if kind == 'paper':
            return PaperStore(tmp_path / 'papers').register({'title': 'Generated'}, actor, operation_id='generated')['id']
        return service.register('experiment', {'title': 'Generated', 'project_id': 'OSForAgent'},
                                actor=actor, operation_id='generated')['resource_id']
    original = creation.Creation.registration
    @contextmanager
    def interrupted(self, connection):
        with original(self, connection):
            yield
        raise RuntimeError('rollback generated allocation')
    with lifecycle_scope(scope, actor=actor):
        with monkeypatch.context() as patch:
            patch.setattr(creation.Creation, 'registration', interrupted)
            with pytest.raises(RuntimeError, match='rollback generated'):
                create()
        assert lifecycle.pending(scope) == []
        resource_id = create()
        assert lifecycle.pending(scope) == [resource_id]
        assert create() == resource_id
        assert lifecycle.pending(scope) == [resource_id]
    with lifecycle_scope(other, actor=actor):
        assert create() == resource_id
        assert lifecycle.pending(other) == []


def test_process_exit_before_postgres_commit_recovers_from_new_store(tmp_path):
    import subprocess
    import sys
    from management_common import host_instances
    from management_portal.paper_store import PaperStore
    from management_portal.resources.registry_store import get_registry_store
    script = '''
from contextlib import contextmanager
from pathlib import Path
import os, sys
from management_portal.resources.creation import Creation
from management_portal.paper_store import PaperStore
from management_common import host_instances
# Exec children must inherit the fixture's isolated host registry as well as PG.
host_instances.user_home = lambda: Path(sys.argv[2])
original = Creation.registration
@contextmanager
def interrupted(self, connection):
    with original(self, connection):
        yield
    os._exit(91)
Creation.registration = interrupted
PaperStore(Path(sys.argv[1])).register(
    {'id': 'paper_abc012345678', 'title': 'Process exit'}, 'alice', operation_id='process-exit')
'''
    # The child inherits only this pytest session's isolated DB/instance.
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'papers'),
                             str(host_instances.user_home())],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, result.stderr
    path, = (tmp_path / 'papers').rglob('manifest.json')
    inode = path.stat().st_ino
    assert get_registry_store().get('paper', 'paper_abc012345678') is None
    record = PaperStore(tmp_path / 'papers').register(
        {'id': 'paper_abc012345678', 'title': 'Process exit'}, 'alice', operation_id='process-exit')
    assert record['id'] == 'paper_abc012345678'
    assert get_registry_store().get('paper', record['id']) is not None
    assert path.stat().st_ino == inode


def test_public_paper_registration_recovers_even_if_index_saw_uncommitted_package(tmp_path, monkeypatch):
    from management_portal import paper_registration, paper_store
    from management_portal.resources import creation
    store = paper_store.PaperStore(tmp_path / 'papers')
    monkeypatch.setattr(paper_registration, 'PAPER_STORE', store)
    monkeypatch.setattr(paper_registration, '_start_summary_task', lambda *a, **kw: None)
    monkeypatch.setattr(paper_registration, '_start_assets_task', lambda *a, **kw: None)
    monkeypatch.setattr(paper_registration, 'get_paper_detail', lambda rid: store.paper_by_id(rid))
    original = creation.Creation.registration
    @contextmanager
    def interrupted(self, connection):
        with original(self, connection):
            yield
        raise RuntimeError('public registration rollback')
    payload = {'title': 'Public retry'}
    with monkeypatch.context() as patch:
        patch.setattr(creation.Creation, 'registration', interrupted)
        with pytest.raises(RuntimeError, match='public registration rollback'):
            paper_registration.register_paper(payload, 'alice', operation_id='public-retry')
    path, = (tmp_path / 'papers').rglob('manifest.json')
    identity = store.read_manifest(path)['paper_id']
    original_bytes = path.read_bytes()
    store.rebuild_fingerprint_index()
    assert store.find_existing_id(payload) == ''
    monkeypatch.setattr(paper_registration, 'PAPER_STORE', paper_store.PaperStore(tmp_path / 'papers'))
    result = paper_registration.register_paper(payload, 'alice', operation_id='public-retry')
    assert result['id'] == identity
    assert path.read_bytes() == original_bytes
    independent = paper_registration.register_paper(payload, 'alice', operation_id='public-independent')
    # A new public registration recognizes an already held paper by title
    # (decision_528bf9985705); failed creations above never enter that index.
    assert independent['id'] == identity


def test_paper_recovers_after_registration_transaction_rollback(tmp_path, monkeypatch):
    from management_portal.paper_store import PaperStore
    from management_portal.resources import directory_registrations as directories
    from management_portal.resources.registry_store import get_registry_store

    original = directories.directory_writer
    @contextmanager
    def interrupted(*args, **kwargs):
        with original(*args, **kwargs) as writer:
            yield writer
            raise RuntimeError('crash before database commit')

    payload = {'id': 'paper_abc012345678', 'title': 'Crash recovery'}
    store = PaperStore(tmp_path / 'papers')
    with monkeypatch.context() as patch:
        patch.setattr(directories, 'directory_writer', interrupted)
        with pytest.raises(RuntimeError, match='crash before'):
            store.register(payload, '', operation_id='paper-crash')
    assert get_registry_store().get('paper', payload['id']) is None
    manifests = list((tmp_path / 'papers').rglob('manifest.json'))
    assert len(manifests) == 1
    recovered = PaperStore(tmp_path / 'papers').register(payload, '', operation_id='paper-crash')
    assert recovered['id'] == payload['id']
    assert get_registry_store().get('paper', payload['id']) is not None
