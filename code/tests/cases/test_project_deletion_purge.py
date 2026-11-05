"""DeleteProject commits once; projections and relations retry from its receipt.

:module: spec.design-governance
:design: decision_4aa13bc21e8e
"""
import json
from types import SimpleNamespace

import pytest

from management_portal.resources import isa, project_deletion_effects as effects
from management_portal.resources.registry_store import get_registry_store
from management_portal.society_service import project_domain
from tests.cases.test_project_deletion import _configure_project


@pytest.fixture
def deletion(monkeypatch, tmp_path):
    root, registry, _, _ = _configure_project(monkeypatch, tmp_path)
    from management_portal import society_service, portal_features
    monkeypatch.setattr(society_service, 'get_society_service',
                        lambda: SimpleNamespace(project=project_domain))
    monkeypatch.setattr(portal_features, 'contributions', lambda key: [])
    monkeypatch.setattr(effects, '_index', lambda entry: None)
    inv = SimpleNamespace(caller='alice', argument=lambda key: 'Example')
    return root, inv


def test_entry_uses_owner_transaction_without_resource_index_queries(deletion, monkeypatch):
    root, inv = deletion
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.resources.directory_registrations import is_deleted
    workspace = root / 'workspace'
    workspace.mkdir()
    get_workspace_store().ensure('workspace_aaaaaaaaaaaa', str(workspace))
    rid = 'repo_aaaaaaaaaaaa'
    registry = get_registry_store()
    with registry.writer('repo') as writer:
        writer.put('repo', rid, {'id': rid, 'checkouts': [{'path': str(root / 'repo'), 'owner_id': rid}]})
    monkeypatch.setattr(isa, '_service', lambda: pytest.fail('Deletion must not query/unregister each index record'))
    result = isa.delete_project(inv)
    assert result['registration_status'] == 'unregistered'
    assert rid in result['resource_ids']
    assert all(is_deleted(value) for value in ('Example', rid, 'workspace_aaaaaaaaaaaa'))
    assert not get_workspace_store().rows(['workspace_aaaaaaaaaaaa'])
    assert registry.get('repo', rid) is None
    assert root.exists()
    assert result['directory_cleanup']


@pytest.mark.parametrize('failed_effect', ['relations', 'permissions', 'avatar', 'index'])
def test_effect_failure_keeps_committed_identity_and_retries_durably(deletion, monkeypatch, failed_effect):
    root, inv = deletion
    from management_portal import portal_features
    from management_portal.resources.directory_registrations import is_deleted
    calls = []
    def fail(entry):
        calls.append('failed')
        raise OSError('effect unavailable')
    if failed_effect == 'relations':
        monkeypatch.setattr(portal_features, 'contributions',
                            lambda key: [('test', fail)] if key == 'project.relations' else [])
    else:
        monkeypatch.setattr(effects, '_' + failed_effect, fail)
    result = isa.delete_project(inv)
    assert result['registration_status'] == 'unregistered'
    assert result['effects']['status'] == 'pending'
    assert is_deleted('Example')
    assert get_registry_store().get('project_deletions', 'Example')['effects']['errors']
    if failed_effect == 'relations':
        monkeypatch.setattr(portal_features, 'contributions',
                            lambda key: [('test', lambda entry: ['kept-object'])] if key == 'project.relations' else [])
    else:
        monkeypatch.setattr(effects, '_' + failed_effect, lambda entry: calls.append('retried'))
    # Simulate another process using the durable receipt and no external grants.
    effects.run_pending()
    entry = get_registry_store().get('project_deletions', 'Example')
    assert entry['effects']['status'] == 'complete'
    assert entry['effects']['attempts'] == 2
    assert isa.delete_project(inv)['registration_status'] == 'unregistered'
    assert len(calls) == (1 if failed_effect == 'relations' else 2)
    assert root.exists()


def test_failed_registration_rolls_back_nested_resources_and_receipt(deletion, monkeypatch):
    root, inv = deletion
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal import project_registry
    from management_portal.resources.directory_registrations import is_deleted
    from management_portal.hatg.contracts import InstructionError
    rid = 'repo_bbbbbbbbbbbb'
    registry = get_registry_store()
    with registry.writer('repo') as writer:
        writer.put('repo', rid, {'id': rid, 'checkouts': [{'path': str(root / 'repo'), 'owner_id': rid}]})
    def fail(*args, **kwargs):
        raise OSError('queue unavailable')
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    monkeypatch.setattr(DirectoryCleanupStore, 'enqueue', fail)
    with pytest.raises(InstructionError) as error:
        isa.delete_project(inv)
    assert error.value.commit_state == 'not_committed'
    assert registry.get('repo', rid) is not None
    assert project_registry.load()['projects']
    assert not is_deleted(rid)
    assert registry.get('project_deletions', 'Example') is None


def test_paper_objects_survive_relation_removal(deletion, monkeypatch, tmp_path):
    _, inv = deletion
    from management_portal import paper_store, portal_features
    manifests = {}
    for name, projects in {'only': ['Example'], 'shared': ['Example', 'Other'], 'other': ['Other']}.items():
        path = tmp_path / 'papers/items/2026' / name / 'manifest.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'paper_id': 'paper_' + name, 'relations': {
            'projects': [{'project_id': item, 'relation': 'relevant'} for item in projects]}}))
        manifests[name] = path
    papers = paper_store.PaperStore(tmp_path / 'papers')
    monkeypatch.setattr(portal_features, 'contributions', lambda key: [
        ('paper', lambda project: papers.replace_project(project, ''))] if key == 'project.relations' else [])
    result = isa.delete_project(inv)
    assert sorted(result['unlinked']['paper']) == ['paper_only', 'paper_shared']
    assert {name: [item['project_id'] for item in json.loads(path.read_text())['relations']['projects']]
            for name, path in manifests.items()} == {'only': [], 'shared': ['Other'], 'other': ['Other']}


def test_project_receipt_replay_is_scoped_to_original_actor(deletion):
    _, inv = deletion
    isa.delete_project(inv)
    from management_portal.hatg.contracts import InstructionError
    inv.caller = 'another-user'
    with pytest.raises(InstructionError) as error:
        isa.delete_project(inv)
    assert error.value.code == 'instruction.not_found'


def test_project_provider_refuses_unregistration_of_live_project(monkeypatch):
    from management_portal.resources.providers import project as provider
    providers = {}
    provider.install_mutation_provider(SimpleNamespace(
        register_mutation_provider=lambda resource_type, **handlers: providers.update({resource_type: handlers})))
    monkeypatch.setattr(provider, 'load_projects_payload', lambda: {'projects': [{'id': 'Example'}]})
    with pytest.raises(ValueError, match='DeleteProject'):
        providers['project']['unregister']('Example', actor='alice')
    assert providers['project']['unregister']('Gone', actor='alice') == {'resource_id': 'Gone', 'status': 'deleted'}


def test_relation_retries_delegate_to_original_feature_owner(deletion, monkeypatch):
    _, inv = deletion
    from management_portal import portal_features
    calls = []
    def unlink(project_id):
        calls.append(project_id)
        raise OSError('map owner unavailable')
    monkeypatch.setattr(portal_features, 'contributions',
                        lambda key: [('projects', unlink)] if key == 'project.relations' else [])
    result = isa.delete_project(inv)
    assert result['effects']['status'] == 'pending'
    monkeypatch.setattr(portal_features, 'contributions',
                        lambda key: [('projects', lambda project_id: ['direction_kept'])] if key == 'project.relations' else [])
    effects.run_pending()
    assert isa.delete_project(inv)['unlinked']['projects'] == ['direction_kept']
    assert calls == ['Example']
