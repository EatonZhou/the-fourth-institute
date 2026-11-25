"""Retired result workflow: no creation/UI/ISA, historical bytes keep their ACLs."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from management_portal import permission_system
from management_portal.resources.deliverables import COLLECTION, REVISIONS, DeliverableStore
from management_portal.resources.registry_store import get_registry_store
from tests.cases.permission_test_support import Fact, enable
from tests.cases.registry_support import write_projects

RID = 'deliverable_000000000001'
PROJECT = 'RetainedResults'


@pytest.fixture
def archive(monkeypatch):
    from management_portal.resource_index_backend import IndexStore, ResourceIndexService
    from management_portal.resource_index_registries import ProjectRegistryAdapter, ResourceRegistryAdapter
    from management_portal.resources.providers import install_resolvers, install_mutation_providers
    from management_portal.resources.service import ResourceService

    write_projects({'projects': [{'id': PROJECT, 'status': 'active', 'created_by': 'alice'}]})
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('project', PROJECT, {}, creator='alice'))
    # Historical schema fixture, not a production migration or new result writer.
    record = dict(resource_id=RID, resource_type='deliverable', author_id='alice',
                  created_by='alice', created_for='', project_id=PROJECT, projects=[PROJECT],
                  title='Existing result', summary='Published summary', status='withdrawn',
                  withdrawal_reason='Superseded', revision=3, created_at='2026-11-22T00:00:00Z',
                  updated_at='2026-11-23T00:00:00Z', portal_url=f'/results?id={RID}')
    registry = get_registry_store()
    with registry.writer(COLLECTION, REVISIONS) as writer:
        writer.put(COLLECTION, RID, record)
        for revision, status in enumerate(('draft', 'published', 'withdrawn'), 1):
            content = 'private draft' if revision == 1 else '保留的原始文档\n'
            snapshot = dict(title='Existing result', summary='', body=content,
                            body_sha256=hashlib.sha256(content.encode()).hexdigest(), validation='author claim',
                            limitations='', references=[], documents=[dict(name='raw.txt', content=content,
                            sha256=hashlib.sha256(content.encode()).hexdigest())])
            writer.put(REVISIONS, f'{RID}/{revision}', dict(status=status, snapshot=snapshot))
        permission_system.sync_resource(record, resource_type='deliverable', connection=writer.connection)
    index = ResourceIndexService(store=IndexStore(), adapters=[ProjectRegistryAdapter(), ResourceRegistryAdapter()],
                                 start_worker=False)
    service = ResourceService(index_provider=index, resolver_loader=install_resolvers,
                              mutation_provider_loader=install_mutation_providers)
    yield service, permissions, registry
    permission_system.forget_implementation()


def test_formal_old_addresses_read_identical_documents_without_mutation(archive):
    service, _, registry = archive
    before = (deepcopy(registry.documents(COLLECTION)), deepcopy(registry.documents(REVISIONS)))
    for revision in (2, 3):
        data, _, _ = service.read_content(f'{RID}/revision/{revision}/file/raw.txt', actor='bob')
        assert data == '保留的原始文档\n'.encode()
        detail, _, _ = service.read_content(f'{RID}/revision/{revision}', actor='bob')
        result = json.loads(detail)
        assert result['revision'] == revision
        assert result['record']['status'] == 'withdrawn'
        assert result['can_edit'] is False
    assert service.resolve(RID, actor='bob')['portal_url'].startswith('/api/resources/content?address=')
    assert before == (registry.documents(COLLECTION), registry.documents(REVISIONS))


def test_draft_and_revoked_access_remain_private(archive):
    service, permissions, _ = archive
    with pytest.raises(PermissionError):
        service.read_content(f'{RID}/revision/1/file/raw.txt', actor='bob')
    data, _, _ = service.read_content(f'{RID}/revision/1/file/raw.txt', actor='alice')
    assert data == b'private draft'
    permissions.deny('bob', PROJECT)
    with pytest.raises(PermissionError):
        service.read_content(f'{RID}/revision/2/file/raw.txt', actor='bob')


def test_registration_and_update_cannot_resume_retired_workflow(archive):
    from management_portal.resources.resource_types import ResourceTypeError
    service, _, registry = archive
    before = (deepcopy(registry.documents(COLLECTION)), deepcopy(registry.documents(REVISIONS)))
    with pytest.raises(ResourceTypeError, match='registration is disabled'):
        service.register('deliverable', {'project_id': PROJECT, 'title': 'new'}, actor='alice')
    with pytest.raises(ValueError, match='no mutation provider registered for deliverable'):
        service.update(RID, {'expected_revision': 3, 'body': 'changed'}, actor='alice')
    assert not hasattr(DeliverableStore, 'create')
    assert not hasattr(DeliverableStore, 'update')
    assert before == (registry.documents(COLLECTION), registry.documents(REVISIONS))


def test_unknown_sources_stay_redacted(archive):
    service, _, registry = archive
    with registry.writer(REVISIONS) as writer:
        version = writer.get(REVISIONS, f'{RID}/2')
        version['snapshot']['references'] = [{'address': 'article_ffffffffffff', 'version': 'secret'}]
        writer.put(REVISIONS, f'{RID}/2', version)
    detail, _, _ = service.read_content(f'{RID}/revision/2', actor='bob')
    assert json.loads(detail)['snapshot']['references'] == [{'status': 'restricted'}]


def test_removed_isa_routes_and_prompt_guidance():
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal import portal_features, serve_management_portal
    from management_portal.permission_system.scheme.repository import Schemes
    from management_portal.resources import isa
    assert 'ListDeliverables' not in instruction_catalog()
    assert 'ListDeliverables' not in isa.HANDLERS
    assert not {'resident_results', 'deliverable_archive'} & {key for key, _ in portal_features.contributions('web.routes')}
    routes = serve_management_portal._portal_route_definitions()
    assert not any(r.path == '/results' or r.path.startswith('/api/results') for r in routes)
    root = Path(__file__).resolve().parents[2]
    prompt = (root / 'core_skills/tfi-agent-rule/portal-resident.md').read_text()
    assert 'ListDeliverables' not in prompt and 'resident-results.md' not in prompt and '`deliverable`' not in prompt
    assert not (root / 'core_skills/tfi-agent-rule/resident-results.md').exists()
    # The existing owner filters retired instruction classifications when
    # loading saved schemes; no live SQL or permission relaxation is needed.
    schemes = Schemes(None)
    rules = schemes.get(schemes.builtin_ids()[0])['rules']
    rules['instructions']['ListDeliverables'] = {'a': '@viewer', 'r': '@shared'}
    compiled = schemes.compile(rules, saved=True)
    assert not compiled.unmapped()
    assert not compiled.slots('ListDeliverables')
