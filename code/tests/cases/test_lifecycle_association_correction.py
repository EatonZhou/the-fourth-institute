"""Persistent Project roots are dependencies, not disposable TAU allocations.

:module: operations.test-workbench
:design: decision_c953eab654cd
"""
from types import SimpleNamespace
from threading import Event, Thread

import pytest

from management_common.resource_identity import get_resource_type_registry
from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope, record_allocation
from management_portal import project_registry
from management_portal.resources import lifecycle_associations
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.service import ResourceService
from management_portal.resources.workspace_store import get_workspace_store


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / 'persistent'; root.mkdir()
    (root / 'marker.txt').write_text('persistent contents')
    with get_registry_store().writer(project_registry.COLLECTION) as writer:
        writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID,
                   {'schema_version': 1, 'projects': [{'id': 'Persistent', 'path': str(root),
                     'directory': {'owner_id': 'Persistent'}}]})
    lifecycle = LifecycleStore()
    scope = lifecycle.open(str(tmp_path / 'case'), actor='owner', run_id='run', case_id='case')
    service = ResourceService.__new__(ResourceService)
    service._identity = get_resource_type_registry()
    return root, lifecycle, scope, service


@pytest.mark.parametrize('owns_project', [False, True])
def test_lazy_root_allocation_follows_project_provenance(sample, owns_project):
    """Real Workspace registration distinguishes an existing Project from this case's Project.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    root, lifecycle, scope, service = sample
    with lifecycle_scope(scope, actor='owner'):
        if owns_project:
            record_allocation('Persistent')
        wid = service.workspace_id_for_path(root)
        assert service.workspace_id_for_path(root) == wid
        child = root / 'fixture-child'; child.mkdir()
        child_wid = service.workspace_id_for_path(child)
    assert lifecycle.owns(scope, wid) is owns_project
    assert lifecycle.owns(scope, child_wid)
    assert (root / 'marker.txt').read_text() == 'persistent contents'


def legacy(sample, *, operation=''):
    root, lifecycle, scope, service = sample
    wid = 'workspace_abcdefabcdef'
    get_workspace_store().ensure(wid, str(root), operation_id=operation)
    with lifecycle_scope(scope, actor='owner'):
        record_allocation(wid)
    return wid


def invoke(sample, wid, *, actor='owner', readable=True):
    root, lifecycle, scope, _ = sample
    def resolve(resource_id, *, actor):
        if not readable:
            raise PermissionError('read denied')
        assert resource_id == wid
    service = SimpleNamespace(validate_id=get_resource_type_registry().validate, resolve=resolve)
    args = {'scope_id': scope, 'resource_id': wid, 'reason': 'confirmed legacy implicit persistent root'}
    return lifecycle_associations.correct(SimpleNamespace(caller=actor, argument=args.__getitem__), service)


def test_correction_is_audited_idempotent_and_preserves_actual_resource(sample):
    """Correct only the erroneous association; ordinary completion follows with no object deletion.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    root, lifecycle, scope, _ = sample
    wid = legacy(sample)
    before = get_workspace_store().rows([wid])[wid]
    lifecycle.seal(scope)
    result = invoke(sample, wid)
    assert result['evidence']['dependency_owner_id'] == 'Persistent'
    assert result['outcome'] == 'association_corrected' and result['original_ordinal'] > 0
    assert not lifecycle.owns(scope, wid) and lifecycle.pending(scope) == []
    assert lifecycle.scope(scope)['state'] == 'closing'
    assert get_workspace_store().rows([wid])[wid] == before
    assert (root / 'marker.txt').read_text() == 'persistent contents'
    lifecycle.complete(scope)
    assert invoke(sample, wid) == result
    with lifecycle.db.read() as db:
        assert db.execute('SELECT count(*) FROM allocation_corrections').fetchone()[0] == 1


@pytest.mark.parametrize('reject', ['actor', 'open', 'owned_project', 'explicit', 'read', 'changed_root', 'released'])
def test_unproven_or_unauthorized_corrections_keep_allocation_and_object(sample, reject):
    """No caller assertion, permission failure or changed evidence can retire a real allocation.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    root, lifecycle, scope, _ = sample
    wid = legacy(sample, operation='explicit-registration' if reject == 'explicit' else '')
    if reject == 'owned_project':
        with lifecycle_scope(scope, actor='owner'):
            record_allocation('Persistent')
    if reject == 'changed_root':
        with get_registry_store().writer(project_registry.COLLECTION) as writer:
            writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, {'projects': []})
    if reject == 'released':
        lifecycle.released(scope, wid)
    if reject != 'open':
        lifecycle.seal(scope)
    before = get_workspace_store().rows([wid])[wid]
    with pytest.raises((ValueError, PermissionError)):
        invoke(sample, wid, actor='other' if reject == 'actor' else 'owner', readable=reject != 'read')
    assert lifecycle.owns(scope, wid)
    assert get_workspace_store().rows([wid])[wid] == before and root.is_dir()
    with lifecycle.db.read() as db:
        assert db.execute('SELECT count(*) FROM allocation_corrections').fetchone()[0] == 0


def test_active_owner_blocks_correction_without_waiting_or_touching_resource(sample):
    """An in-flight owner holds admission until its actual work ends.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    root, lifecycle, scope, _ = sample
    wid = legacy(sample)
    entered, release = Event(), Event()
    def holder():
        with lifecycle.owner_lease(scope):
            entered.set()
            release.wait(5)
    thread = Thread(target=holder); thread.start()
    try:
        assert entered.wait(2)
        lifecycle.request_close(scope)
        with pytest.raises(RuntimeError, match='active requests'):
            invoke(sample, wid)
        assert lifecycle.pending(scope) == [wid] and root.is_dir()
    finally:
        release.set(); thread.join(5)
