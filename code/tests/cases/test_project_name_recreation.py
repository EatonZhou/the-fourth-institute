"""A deleted Project name is reusable for a new instance (Project Design)."""
from __future__ import annotations

import json
import pytest

from tests.cases.directory_fixture_support import install_directory_owners


def test_project_name_can_be_recreated_after_directory_cleanup(tmp_path, monkeypatch):
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal import portal_features, project_registry
    from management_portal.resources.directory_registrations import is_deleted
    from management_portal.resources.providers import project
    from management_portal.society_service import project_domain
    from tests.cases.registry_support import write_projects

    install_directory_owners()
    projects_root = tmp_path / "Projects"
    monkeypatch.setattr(project, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(project_domain, "PROJECTS_ROOT", projects_root)
    # This empty Project has no application-owned workbenches or Lineages.
    contributions = portal_features.contributions
    monkeypatch.setattr(portal_features, "contributions",
                        lambda hook: [] if hook.startswith('project.') else contributions(hook))
    write_projects({"projects": [], "status_vocab": ["active"]})
    payload = {"id": "SameName", "project_type": "company"}
    first = project.create_project(payload, actor="alice", operation_id="first-instance")
    assert first["resource_id"] == "SameName"
    assert (projects_root / "SameName" / "summary.md").is_file()

    deleted = project_domain.delete_project("SameName", "alice")
    queue = get_directory_cleanup_store()
    queue.run_pending()
    from management_portal.resources import project_deletion_effects
    from management_portal.workbench_projections.resource_lifecycle import finish_resource_releases
    completion = project_deletion_effects.run('SameName')['effects']
    assert completion['status'] == 'complete', completion
    assert finish_resource_releases(['SameName']) == []
    rows = queue.status(owner_id="SameName")
    assert rows and all(row["status"] == "completed" for row in rows)
    assert project_registry.load()["projects"] == []
    assert not (projects_root / "SameName").exists()
    print(json.dumps({"deleted_project": deleted["project_id"],
                      "cleanup_statuses": [row["status"] for row in rows],
                      "directory_exists": (projects_root / "SameName").exists(),
                      "identity_tombstone": is_deleted("SameName")}))

    second = project.create_project(payload, actor="alice", operation_id="second-instance")
    assert second["resource_id"] == "SameName"
    assert (projects_root / "SameName" / "summary.md").is_file()
    with pytest.raises(ValueError, match='retired instance'):
        project.create_project(payload, actor='alice', operation_id='first-instance')
    # A second deletion must enqueue a fresh physical operation for this name.
    project_domain.delete_project('SameName', 'alice')
    queue.run_pending()
    assert not (projects_root / 'SameName').exists()
    assert project_deletion_effects.run('SameName')['effects']['status'] == 'complete'
    assert finish_resource_releases(['SameName']) == []
    project.create_project(payload, actor='bob', operation_id='third-instance')
    assert project_registry.project('SameName')['created_by'] == 'bob'


@pytest.fixture
def project_cycle(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal import portal_features, project_registry
    from management_portal.resources import project_deletion_effects as effects
    from management_portal.resources.providers import project
    from management_portal.society_service import project_domain
    from management_portal.workbench_projections.resource_lifecycle import finish_resource_releases
    from tests.cases.registry_support import write_projects

    install_directory_owners()
    root = tmp_path / 'Projects'
    monkeypatch.setattr(project, 'PROJECTS_ROOT', root)
    monkeypatch.setattr(project_domain, 'PROJECTS_ROOT', root)
    contributions = portal_features.contributions
    monkeypatch.setattr(portal_features, 'contributions',
                        lambda hook: [] if hook.startswith('project.') else contributions(hook))
    write_projects({'projects': [], 'status_vocab': ['active']})
    payload = {'id': 'SameName', 'project_type': 'company'}
    def create(operation='first', actor='alice'):
        return project.create_project(payload, actor=actor, operation_id=operation)
    def finish(*, omit=None):
        if omit != 'directory':
            get_directory_cleanup_store().run_pending()
        if omit != 'effects':
            result = effects.run('SameName')['effects']
            assert result['status'] == 'complete', result
        if omit != 'projection':
            assert finish_resource_releases(['SameName']) == []
    return SimpleNamespace(create=create, delete=lambda: project_domain.delete_project('SameName', 'alice'),
                           finish=finish, root=root/'SameName', registry=project_registry, effects=effects)


@pytest.mark.parametrize('pending', ['directory', 'effects', 'projection'])
def test_recreation_waits_for_all_old_deletion_owners(project_cycle, pending):
    from management_portal.resources.directory_registrations import is_deleted
    from management_portal.resources.creation import get_resource_creation
    c = project_cycle
    c.create()
    original = get_resource_creation('SameName')
    c.delete()
    c.finish(omit=pending)
    with pytest.raises(RuntimeError, match='pending'):
        c.create('replacement', 'bob')
    assert is_deleted('SameName')
    assert get_resource_creation('SameName') == original
    assert c.registry.project('SameName') is None
    c.finish()
    c.create('replacement', 'bob')
    assert not is_deleted('SameName')


def test_recreated_project_gets_new_creator_permissions_and_registration_retry(project_cycle, monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal.permission_system import par
    from management_portal.resources.registry_store import get_registry_store
    c = project_cycle
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    c.create()
    assert permissions.holds('alice', '@admin', 'SameName')
    c.delete()
    c.finish()
    def refused(*args, **kwargs):
        raise RuntimeError('placement rejected')
    with monkeypatch.context() as patch:
        patch.setattr(par.PARPermissions, 'sync_resource', refused)
        with pytest.raises(RuntimeError, match='placement rejected'):
            c.create('replacement', 'bob')
    assert c.registry.project('SameName') is None
    assert not permissions.holds('bob', '@admin', 'SameName')
    c.create('replacement', 'bob')
    assert permissions.holds('bob', '@admin', 'SameName')
    assert not permissions.holds('alice', '@admin', 'SameName')
    assert c.registry.project('SameName')['created_by'] == 'bob'
    with pytest.raises(KeyError):
        c.effects.status_for_actor('SameName', 'alice')
    for _ in range(2):
        c.effects.run('SameName')
    assert permissions.holds('bob', '@admin', 'SameName')
    history = get_registry_store().documents('project_instance_history')
    assert len(history) == 1 and next(iter(history.values()))['deleted_by'] == 'alice'


@pytest.mark.parametrize('replacement', ['directory', 'symlink'])
def test_reuse_never_adopts_a_foreign_path_and_rolls_back_archival(project_cycle, tmp_path, replacement):
    from management_portal.resources.creation import get_resource_creation
    from management_portal.resources.directory_registrations import is_deleted
    from management_portal.resources.registry_store import get_registry_store
    c = project_cycle
    c.create()
    original = get_resource_creation('SameName')
    c.delete()
    c.finish()
    if replacement == 'directory':
        c.root.mkdir()
        (c.root/'foreign').write_text('keep')
    else:
        destination = tmp_path/'foreign'
        destination.mkdir()
        (destination/'foreign').write_text('keep')
        c.root.symlink_to(destination, target_is_directory=True)
    with pytest.raises((FileExistsError, PermissionError)):
        c.create('replacement')
    assert (c.root/'foreign').read_text() == 'keep'
    assert is_deleted('SameName')
    assert get_resource_creation('SameName') == original
    assert get_registry_store().documents('retired_resource_creations') == {}


def test_normal_creation_cannot_reuse_project_tombstone(project_cycle):
    from management_portal.resources.creation import Creation
    c = project_cycle
    c.create()
    c.delete()
    c.finish()
    with pytest.raises(KeyError):
        with Creation('SameName', actor='alice', payload={}, operation_id='generic').prepare([(c.root, 'directory')]):
            pytest.fail('generic Creation must not revive deleted identities')


def test_old_scope_cannot_claim_replacement_and_provenance_survives(project_cycle):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    c = project_cycle
    store = LifecycleStore()
    old = store.open('old', actor='alice', run_id='old', case_id='case')
    new = store.open('new', actor='bob', run_id='new', case_id='case')
    with lifecycle_scope(old, actor='alice'):
        c.create()
    c.delete()
    c.finish()
    with lifecycle_scope(new, actor='bob'), pytest.raises(RuntimeError, match='lifecycle cleanup'):
        c.create('replacement', 'bob')
    store.seal(old)
    store.released(old, 'SameName')
    store.complete(old)
    with lifecycle_scope(new, actor='bob'):
        c.create('replacement', 'bob')
    assert store.owns(old, 'SameName')
    assert store.owns(new, 'SameName')
    assert store.pending(old) == []
    assert store.pending(new) == ['SameName']
    store.released(old, 'SameName')
    assert store.pending(new) == ['SameName']


def test_same_open_scope_keeps_ownership_of_recreated_project(project_cycle):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    c = project_cycle
    store = LifecycleStore()
    scope = store.open('same', actor='alice', run_id='same', case_id='case')
    with lifecycle_scope(scope, actor='alice'):
        c.create()
        c.delete()
        c.finish()
        c.create('replacement')
    assert store.pending(scope) == ['SameName']


def test_running_old_effects_finish_before_new_creation(project_cycle, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    c = project_cycle
    c.create()
    c.delete()
    c.finish(omit='effects')
    entered, release, creating = Event(), Event(), Event()
    original = c.effects._avatar
    def delayed(entry):
        entered.set()
        assert release.wait(5)
        return original(entry)
    monkeypatch.setattr(c.effects, '_avatar', delayed)
    def create():
        creating.set()
        return c.create('replacement', 'bob')
    with ThreadPoolExecutor(2) as pool:
        effects = pool.submit(c.effects.run, 'SameName')
        assert entered.wait(5)
        creation = pool.submit(create)
        try:
            assert creating.wait(5)
            with pytest.raises(TimeoutError):
                creation.result(timeout=0.1)
        finally:
            release.set()
        assert effects.result(timeout=5)['effects']['status'] == 'complete'
        assert creation.result(timeout=5)['resource_id'] == 'SameName'
    assert c.registry.project('SameName')['created_by'] == 'bob'


def test_legacy_project_without_creation_journal_can_be_recreated(project_cycle):
    from management_common.portal_database import portal_database
    from management_portal.resources.creation import get_resource_creation
    c = project_cycle
    c.create()
    # Historical Project registrations predate the durable creation journal.
    with portal_database(schema='directory_lifecycle').transaction() as connection:
        connection.execute('DELETE FROM resource_creations WHERE resource_id=%s', ('SameName',))
    c.delete()
    c.finish()
    c.create('replacement', 'bob')
    assert get_resource_creation('SameName')['actor'] == 'bob'
