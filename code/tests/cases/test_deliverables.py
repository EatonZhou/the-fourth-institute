"""Deliverable owner contracts against isolated real PostgreSQL transactions."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.resources.deliverables import COLLECTION, REVISIONS, DeliverableStore
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.registry_store import RegistryStore

RID = 'deliverable_000000000001'
AUTHOR = 'resident_000000000001'


@pytest.fixture
def owner(monkeypatch):
    denied = set()
    def allows(actor, instruction, arguments):
        if actor in denied:
            return False
        if instruction == 'UnregisterResource':
            return actor == 'manager'
        return actor in {AUTHOR, 'reader', 'requester', 'manager'}
    monkeypatch.setattr(permission_system, 'on_behalf_of', lambda actor: 'requester')
    service = SimpleNamespace(get=lambda project, actor: dict(resource_id=project, resource_type='project'))
    return DeliverableStore(authorize=allows, service=service), denied


def create(store, **fields):
    return store.create(RID, dict(project_id='P', title='Initial', body='原文',
        documents=[{'name': '实验记录.txt', 'content': '数据甲'}], **fields), AUTHOR)


def test_reopen_immutable_documents_withdrawal_and_retired_author(owner):
    store, _ = owner
    record = create(store, status='published')
    assert record['created_by'] == record['author_id'] == AUTHOR
    assert record['created_for'] == 'requester'
    store.update(RID, {'expected_revision': 1, 'body': 'changed',
        'documents': [{'name': '实验记录.txt', 'content': '数据乙'}]}, AUTHOR)
    store.update(RID, {'expected_revision': 2, 'status': 'withdrawn', 'withdrawal_reason': 'obsolete'}, AUTHOR)
    reopened = DeliverableStore(registry=RegistryStore(), authorize=store.authorize, service=store.service)
    detail = reopened.get(RID, 'reader', 1)
    assert detail['record']['status'] == 'withdrawn'
    assert detail['record']['withdrawal_reason'] == 'obsolete'
    assert detail['snapshot']['body'] == '原文'
    assert detail['snapshot']['body_sha256'] == hashlib.sha256('原文'.encode()).hexdigest()
    doc = detail['snapshot']['documents'][0]
    assert doc == dict(name='实验记录.txt', content='数据甲', sha256=hashlib.sha256('数据甲'.encode()).hexdigest())
    assert detail['latest_revision'] == 3
    # Author is an immutable provenance ID; no Resident lookup or workspace is required.
    assert reopened.list('reader', author_id=AUTHOR)['total'] == 1


def test_private_draft_historical_draft_and_current_project_revocation(owner):
    store, denied = owner
    create(store)
    assert store.get(RID, 'requester')['revision'] == 1
    assert store.list('reader')['total'] == 0
    with pytest.raises(PermissionError):
        store.get(RID, 'reader')
    store.update(RID, {'expected_revision': 1, 'status': 'published'}, AUTHOR)
    assert store.get(RID, 'reader')['revision'] == 2
    with pytest.raises(PermissionError):
        store.get(RID, 'reader', 1)
    with pytest.raises(PermissionError):
        store.update(RID, {'expected_revision': 2, 'body': 'forged'}, 'reader')
    denied.add(AUTHOR)
    with pytest.raises(PermissionError):
        store.get(RID, AUTHOR)
    with pytest.raises(PermissionError):
        store.update(RID, {'expected_revision': 2}, AUTHOR)
    assert store.list(AUTHOR)['total'] == 0


def test_atomic_permission_failure_rolls_back_both_collections(owner, monkeypatch):
    store, _ = owner
    def fail(*args, **kwargs):
        assert kwargs['connection'].in_transaction
        raise RuntimeError('injected PAR failure')
    original = permission_system.sync_resource
    monkeypatch.setattr(permission_system, 'sync_resource', fail)
    with pytest.raises(RuntimeError, match='PAR failure'):
        create(store)
    assert RegistryStore().get(COLLECTION, RID) is None
    assert RegistryStore().get(REVISIONS, RID + '/1') is None
    monkeypatch.setattr(permission_system, 'sync_resource', original)
    create(store)
    monkeypatch.setattr(permission_system, 'sync_resource', fail)
    with pytest.raises(RuntimeError, match='PAR failure'):
        store.update(RID, {'expected_revision': 1, 'status': 'published'}, AUTHOR)
    assert RegistryStore().get(COLLECTION, RID)['revision'] == 1
    assert RegistryStore().get(REVISIONS, RID + '/2') is None


def test_concurrent_cas_has_one_winner(owner):
    store, _ = owner
    create(store)
    barrier = Barrier(2)
    def update(body):
        other = DeliverableStore(registry=RegistryStore(), authorize=store.authorize, service=store.service)
        barrier.wait(timeout=10)
        try:
            return other.update(RID, {'expected_revision': 1, 'body': body}, AUTHOR)['revision']
        except ResourceVersionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['left', 'right']))
    assert set(results) == {2, 'conflict'}
    assert len(store.registry.documents(REVISIONS)) == 2


@pytest.mark.parametrize('fields', [
    {'author_id': 'forged'}, {'created_by': 'forged'}, {'created_for': 'forged'},
    {'documents': [{'name': '../escape', 'content': ''}]},
    {'documents': [{'name': 'doc', 'content': '\ud800'}]},
    {'body': 'a' * (1024 * 1024 + 1)},
    {'status': 'withdrawn'},
])
def test_invalid_creation_is_not_persisted(owner, fields):
    store, _ = owner
    with pytest.raises(ValueError):
        store.create(RID, {'title': 'test', 'project_id': 'P', **fields}, AUTHOR)
    assert store.registry.get(COLLECTION, RID) is None


def test_project_fixed_creation_reauthorized_and_pagination(owner):
    store, denied = owner
    denied.add(AUTHOR)
    with pytest.raises(PermissionError):
        create(store)
    denied.clear()
    create(store, status='published')
    with pytest.raises(ValueError):
        store.update(RID, {'expected_revision': 1, 'project_id': 'Elsewhere'}, AUTHOR)
    assert store.list('reader', project_id='Elsewhere')['total'] == 0
    assert store.list('reader', offset=1, limit=1) == dict(items=[], total=1, offset=1, limit=1)


def test_reference_acl_rechecked_without_metadata_leaks(owner):
    store, _ = owner
    calls = []
    allowed = True
    def resolve(address, *, actor):
        calls.append((address, actor))
        if not allowed:
            raise PermissionError('secret source title')
        return {'title': 'secret source title', 'version': '7'}
    store.service.resolve = resolve
    create(store, status='published', references=[{'address': 'article_000000000001', 'version': '7'}])
    assert store.get(RID, 'reader')['snapshot']['references'][0]['version'] == '7'
    allowed = False
    assert store.get(RID, 'reader')['snapshot']['references'] == [{'status': 'restricted'}]
    assert calls[-1] == ('article_000000000001', 'reader')
    assert store.registry.get(REVISIONS, RID + '/1')['snapshot']['references'][0]['address'] == 'article_000000000001'


def test_formal_provider_registration_fixed_document_and_read_reauthorization(monkeypatch):
    from management_portal.resources.service import ResourceService
    from management_portal.resources.providers import deliverable
    from management_portal.resources import service_mutations
    # Only the post-commit index side effect is isolated; the formal allocation,
    # registration ledger, provider dispatch and PG owner all execute normally.
    def complete(service, result, **kwargs):
        if kwargs['kind'] == 'create':
            service._registrations.complete(result['resource_id'])
        return result
    monkeypatch.setattr(service_mutations, 'complete_mutation', complete)
    from management_portal.resources.providers import install_mutation_providers
    from tests.cases.directory_fixture_support import install_directory_owners
    from tests.cases.registry_support import write_projects
    install_directory_owners()
    write_projects({'projects': [{'id': 'P', 'status': 'active'}], 'status_vocab': ['active']})
    service = ResourceService(mutation_provider_loader=install_mutation_providers)
    record = service.register('deliverable', {'project_id': 'P', 'title': 'Provider', 'status': 'published',
        'documents': [{'name': 'data.txt', 'content': 'fixed'}]}, actor=AUTHOR, operation_id='test-deliverable')
    rid = record['resource_id']
    assert rid.startswith('deliverable_')
    repeated = service.register('deliverable', {'project_id': 'P', 'title': 'Provider', 'status': 'published',
        'documents': [{'name': 'data.txt', 'content': 'fixed'}]}, actor=AUTHOR, operation_id='test-deliverable')
    assert repeated['resource_id'] == rid
    # Capture and exercise the actual resolver/provider contracts without index bootstrapping.
    resolvers = {}
    deliverable.install_resolver(SimpleNamespace(register_resolver=lambda kind, resolve, **kw: resolvers.update(resolve=resolve, **kw)))
    resolved = resolvers['resolve'](rid + '/revision/1/file/data.txt', actor=AUTHOR)
    data, _, _ = resolvers['read_content'](resolved, actor=AUTHOR)
    assert data == b'fixed'
    with pytest.raises(ValueError):
        resolvers['read_content'](resolved, actor=AUTHOR, max_bytes=1)
    monkeypatch.setattr(permission_system, 'allows', lambda *a, **kw: False)
    with pytest.raises(PermissionError):
        resolvers['read_content'](resolved, actor=AUTHOR)


def test_draft_index_has_no_draft_text(owner):
    from management_portal.deliverable_index import REGISTRY_PROJECTION
    store, _ = owner
    record = create(store)
    projected = REGISTRY_PROJECTION.project(record, adapter='resource_registry')
    assert projected['title'] == RID
    assert projected['summary'] == ''
    assert '原文' not in json.dumps(projected, ensure_ascii=False)


def test_project_manager_cannot_read_historical_draft_with_real_par(monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    permissions = enable(monkeypatch)
    try:
        permissions.users('alice', 'charlie')
        permissions.place(Fact('project', 'P', {}, creator='alice'))
        service = SimpleNamespace(get=lambda project, actor: dict(resource_id=project, resource_type='project'))
        store = DeliverableStore(service=service)
        store.create(RID, {'project_id': 'P', 'title': 'Private'}, 'alice')
        store.update(RID, {'expected_revision': 1, 'status': 'published'}, 'alice')
        assert permission_system.allows('charlie', 'UnregisterResource', {'resource_id': RID})
        assert store.get(RID, 'charlie', '2')['revision'] == 2
        with pytest.raises(PermissionError):
            store.get(RID, 'charlie', '1')
    finally:
        permission_system.forget_implementation()


@pytest.mark.parametrize('revision', ['01', '+1', ' 1', '1.0', '0', '-1', True, 1.0])
def test_revision_parameter_is_strict(owner, revision):
    store, _ = owner
    create(store)
    with pytest.raises(ValueError):
        store.get(RID, AUTHOR, revision)
    assert store.get(RID, AUTHOR, '1')['revision'] == 1


@pytest.mark.parametrize('status', ['published', 'withdrawn'])
def test_shared_result_cannot_return_to_draft(owner, status):
    store, _ = owner
    create(store, status=status, withdrawal_reason='obsolete')
    with pytest.raises(ValueError, match='cannot become drafts'):
        store.update(RID, {'expected_revision': 1, 'status': 'draft'}, AUTHOR)


def test_project_existence_and_read_permission_required(owner):
    store, _ = owner
    def absent(*args, **kwargs):
        raise KeyError('missing project')
    store.service.get = absent
    with pytest.raises(KeyError):
        create(store)
    assert store.registry.get(COLLECTION, RID) is None


def test_changed_source_version_and_digest_are_explicit(owner):
    store, _ = owner
    current = {'version': '1', 'bytes': b'evidence'}
    store.service.resolve = lambda *args, **kwargs: {'version': current['version']}
    store.service.read_content = lambda *args, **kwargs: (current['bytes'], 'text/plain', {})
    reference = dict(address='article_000000000001', version='1', sha256=hashlib.sha256(b'evidence').hexdigest())
    create(store, status='published', references=[reference])
    current['bytes'] = b'replaced'
    assert store.get(RID, 'reader')['snapshot']['references'] == [{**reference, 'status': 'version_changed'}]
    with pytest.raises(ResourceVersionConflict):
        store.update(RID, {'expected_revision': 1, 'references': [reference]}, AUTHOR)
    current['bytes'], current['version'] = b'evidence', '2'
    assert store.get(RID, 'reader')['snapshot']['references'][0]['status'] == 'version_changed'
