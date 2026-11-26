"""Named domain operations retain their author, version and authorization boundaries."""
from unittest.mock import Mock

import pytest

from management_portal import permission_system
from management_portal.resources.service import ResourceService
from management_portal.resources.transport import ResourceServiceClient
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401


OPERATIONS = (
    'create_knowledge_file', 'update_knowledge_file_name', 'move_knowledge_items',
    'update_device_ssh_credential', 'delete_device_ssh_credential', 'check_device_ssh',
    'run_device_command', 'update_workbench_page', 'update_workbench_reverse_plan',
)
ACTOR = 'resident_0123456789ab'
DEVICE = 'device_0123456789ab'
WORKBENCH = 'workbench_0123456789ab'


def test_named_methods_replace_action_dispatch():
    for cls in (ResourceService, ResourceServiceClient):
        assert all(callable(getattr(cls, name, None)) for name in OPERATIONS)
        assert not hasattr(cls, 'action')
    assert not hasattr(ResourceService, 'register_action_provider')


def test_workbench_named_calls_use_installed_provider_only(named_service, monkeypatch):
    from management_portal.resources.providers import workbench
    from management_portal.resources.mutation import ResourceMutationProvider
    service, checks, allowed = named_service
    # An installed provider owns these callbacks; the service must not import a
    # different application implementation or bypass the provider registry.
    monkeypatch.setattr(workbench, 'update_workbench_page', Mock(side_effect=AssertionError('direct import')))
    monkeypatch.setattr(workbench, 'update_workbench_reverse_plan', Mock(side_effect=AssertionError('direct import')))
    page = Mock(return_value={'revision': 4})
    reverse = Mock(return_value={'revision': 5})
    service.register_mutation_provider('workbench', update_workbench_page=page,
                                      update_workbench_reverse_plan=reverse)
    assert service.update_workbench_page(WORKBENCH, 'job-p', 3, '<h1>ok</h1>',
                                         actor=ACTOR, title='Title') == {'revision': 4}
    assert service.update_workbench_reverse_plan(WORKBENCH, 'job-r', {'operations': []},
                                                 actor=ACTOR) == {'revision': 5}
    page.assert_called_once_with(WORKBENCH, 'job-p', 3, '<h1>ok</h1>', actor=ACTOR,
                                 title='Title', description=None)
    reverse.assert_called_once_with(WORKBENCH, 'job-r', {'operations': []}, actor=ACTOR)
    assert [c[1] for c in checks] == ['UpdateWorkbenchPage', 'UpdateWorkbenchReversePlan']
    allowed['value'] = False
    for method, args in ((service.update_workbench_page, (WORKBENCH, 'job-p', 3, 'blocked')),
                         (service.update_workbench_reverse_plan, (WORKBENCH, 'job-r', {}))):
        with pytest.raises(PermissionError):
            method(*args, actor=ACTOR)
    assert page.call_count == reverse.call_count == 1
    allowed['value'] = True
    # A generic update provider is not an implicit submission callback.
    service._mutation_providers.register('workbench', ResourceMutationProvider(update=Mock()), replace=True)
    with pytest.raises(ValueError, match='UpdateWorkbenchPage.*not supported'):
        service.update_workbench_page(WORKBENCH, 'job-p', 3, 'blocked', actor=ACTOR)
    with pytest.raises(ValueError, match='UpdateWorkbenchReversePlan.*not supported'):
        service.update_workbench_reverse_plan(WORKBENCH, 'job-r', {}, actor=ACTOR)


def test_device_command_rpc_needs_no_optional_application(monkeypatch, tmp_path):
    import builtins
    original_import = builtins.__import__
    def core_only(name, *args, **kwargs):
        if name.startswith('management_portal.google_cloud'):
            raise AssertionError('RPC client must not import optional device application')
        return original_import(name, *args, **kwargs)
    client = ResourceServiceClient(tmp_path / 'unused.sock')
    monkeypatch.setattr(builtins, '__import__', core_only)
    exchange = Mock(return_value={'exit_code': 0})
    monkeypatch.setattr(client, '_exchange', exchange)
    assert client.run_device_command(DEVICE, 'true', actor=ACTOR) == {'exit_code': 0}
    exchange.assert_called_once_with('resource.run_device_command',
        {'resource_id': DEVICE, 'command': 'true', 'actor': ACTOR}, timeout=135.0)
    client.timeout = 200
    client.run_device_command(DEVICE, 'true', actor=ACTOR)
    assert exchange.call_args.kwargs['timeout'] == 200


def test_workbench_disabled_or_uninstalled_never_loads_application(named_service, monkeypatch):
    from management_portal.resources import providers
    service, _, _ = named_service
    install = Mock()
    service._mutation_provider_loader = install
    service.resource_types.require.side_effect = ValueError('workbenches disabled')
    with pytest.raises(ValueError, match='disabled'):
        service.update_workbench_page(WORKBENCH, 'job', 3, 'blocked', actor=ACTOR)
    install.assert_not_called()
    service.resource_types.require.side_effect = None
    service._mutation_provider_loader = providers.install_mutation_providers
    monkeypatch.setattr(providers, 'provider_plugins', lambda resource_type='': ())
    with pytest.raises(ValueError, match='no mutation provider registered'):
        service.update_workbench_reverse_plan(WORKBENCH, 'job', {}, actor=ACTOR)
    assert service._mutation_providers.registered_types() == frozenset()
    with pytest.raises(TypeError, match='must be callable'):
        service.register_mutation_provider('workbench', update_workbench_page='not-callable')
    assert service._mutation_providers.registered_types() == frozenset()


def test_capabilities_do_not_install_or_expose_action_providers():
    from management_portal.resources.providers import device
    service = ResourceService(index_provider=Mock(), type_registry=Mock(),
                              resolver_loader=device.install_resolver,
                              mutation_provider_loader=device.install_mutation_provider)
    capabilities = service.capabilities('device')
    assert 'actions' not in capabilities
    assert capabilities['resolvable'] and capabilities['updatable']
    assert set(service._loaded_provider_types) == {'resolver', 'mutation'}


@pytest.fixture
def named_service(monkeypatch):
    from management_common.resource_identity import get_resource_type_registry
    registry = get_resource_type_registry()
    service = ResourceService(type_registry=Mock())
    monkeypatch.setattr(service, '_existing_identity', lambda rid, kind='', **kw: registry.parse(rid))
    checks = []
    allowed = {'value': True}
    def allows(actor, instruction, arguments):
        checks.append((actor, instruction, arguments))
        return allowed['value']
    monkeypatch.setattr(permission_system, 'allows', allows)
    return service, checks, allowed


def test_ssh_checks_use_named_permissions_and_recheck_revocation(named_service, monkeypatch):
    from management_portal import device_identity_backend as backend
    service, checks, allowed = named_service
    ssh_test = Mock(return_value={'reachable': True, 'message': 'connected', 'latency_ms': 10})
    save = Mock(return_value={'credential_saved': True})
    delete = Mock(return_value={'credential_saved': False, 'deleted': True})
    run = Mock(return_value={'exit_code': 0, 'output': 'ok', 'truncated': False})
    monkeypatch.setattr(backend, 'test_ssh_connection', ssh_test)
    monkeypatch.setattr(backend, 'effective_ssh_access', lambda rid: {'via_resource_id': rid})
    monkeypatch.setattr(backend, 'save_device_ssh_password', save)
    monkeypatch.setattr(backend, 'delete_device_ssh_password', delete)
    monkeypatch.setattr(backend, 'run_device_ssh', run)
    assert service.check_device_ssh(DEVICE, timeout_seconds=7, actor=ACTOR) == {'ok': True}
    assert service.update_device_ssh_credential(DEVICE, 'test-only-password', actor=ACTOR)['credential_saved']
    assert service.delete_device_ssh_credential(DEVICE, actor=ACTOR)['deleted']
    assert service.run_device_command(DEVICE, 'true', actor=ACTOR)['exit_code'] == 0
    assert [c[1] for c in checks] == ['CheckDeviceSsh', 'UpdateDeviceSshCredential',
                                     'DeleteDeviceSshCredential', 'RunDeviceCommand']
    ssh_test.assert_called_once_with(DEVICE, timeout_seconds=7)
    run.assert_called_once_with(DEVICE, 'true', actor=ACTOR)
    allowed['value'] = False
    for method, args in ((service.check_device_ssh, (DEVICE,)),
                         (service.update_device_ssh_credential, (DEVICE, 'blocked')),
                         (service.delete_device_ssh_credential, (DEVICE,)),
                         (service.run_device_command, (DEVICE, 'blocked'))):
        with pytest.raises(PermissionError):
            method(*args, actor=ACTOR)
    assert all(c[0] == ACTOR for c in checks)
    assert save.call_count == delete.call_count == run.call_count == ssh_test.call_count == 1


def test_workbench_named_methods_preserve_job_and_version(named_service, monkeypatch):
    from management_portal.resources.providers import install_mutation_providers
    from management_portal.workbench_projections.service import ProjectionService
    service, checks, _ = named_service
    service._mutation_provider_loader = install_mutation_providers
    save = Mock(return_value={'revision': 4})
    reverse = Mock(return_value={'revision': 5})
    monkeypatch.setattr(ProjectionService, 'save_generation_result', save)
    monkeypatch.setattr(ProjectionService, 'save_reverse_plan', reverse)
    monkeypatch.setattr(service, 'notify_source_changed', Mock())
    assert service.update_workbench_page(WORKBENCH, 'job-existing', 3, '<h1>new</h1>',
                                         actor=ACTOR, title='Title') == {'revision': 4}
    assert save.call_args.args == (WORKBENCH, {'job_id': 'job-existing', 'base_revision': 3,
                                             'title': 'Title', 'html': '<h1>new</h1>'}, ACTOR)
    assert service.update_workbench_reverse_plan(WORKBENCH, 'job-reverse', {'operations': []},
                                                 actor=ACTOR) == {'revision': 5}
    assert reverse.call_args.args == (WORKBENCH, {'job_id': 'job-reverse', 'plan': {'operations': []}}, ACTOR)
    assert [c[1] for c in checks] == ['UpdateWorkbenchPage', 'UpdateWorkbenchReversePlan']


def test_wrong_type_and_anonymous_actor_fail_before_domain_calls(named_service):
    service, _, _ = named_service
    with pytest.raises(ValueError):
        service.check_device_ssh(WORKBENCH, actor=ACTOR)
    with pytest.raises(PermissionError):
        service.check_device_ssh(DEVICE, actor='')


def test_ssh_failure_uses_public_error_contract(named_service, monkeypatch):
    from management_portal import device_identity_backend as backend
    service, _, _ = named_service
    monkeypatch.setattr(backend, 'test_ssh_connection', lambda *a, **kw: {
        'reachable': False, 'message': 'connection refused', 'latency_ms': 12})
    assert service.check_device_ssh(DEVICE, actor=ACTOR) == {'ok': False, 'error': 'connection refused'}


def test_real_rpc_routes_preserve_actor_and_remove_generic_action(named_service, monkeypatch, tmp_path):
    import threading
    from management_portal.resources.server import ResourceServiceServer
    from management_portal import device_identity_backend as backend
    service, checks, allowed = named_service
    key = tmp_path / 'rpc.key'
    key.write_bytes(b'test-only-named-operation-rpc-signing-key')
    key.chmod(0o600)
    monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(key))
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    monkeypatch.setattr(backend, 'test_ssh_connection', lambda *a, **kw: {'reachable': True})
    # This case owns the RPC/domain boundary; ISA assembly and maintenance are
    # integrated separately while other plan owners update their catalogs.
    from management_portal.resources import isa
    monkeypatch.setattr(isa, 'build_kernel', lambda: Mock(owner='resource'))
    monkeypatch.setattr(ResourceServiceServer, '_start_warmup', lambda self: None)
    monkeypatch.setattr(ResourceServiceServer, '_start_background_maintenance', lambda self: None)
    server = ResourceServiceServer(tmp_path / 'resource.sock', service=service, publisher=Mock())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    client = ResourceServiceClient(server.socket_path, timeout=2)
    try:
        assert client.check_device_ssh(DEVICE, actor=ACTOR) == {'ok': True}
        assert checks == [(ACTOR, 'CheckDeviceSsh', {'resource_id': DEVICE})]
        allowed['value'] = False
        with pytest.raises(PermissionError):
            client.check_device_ssh(DEVICE, actor=ACTOR)
        with pytest.raises(ValueError, match='unsupported ResourceService method'):
            client._exchange('resource.action', {'resource_id': DEVICE, 'action': 'ssh.test', 'actor': ACTOR})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rpc_all_named_payloads_are_explicit_and_keep_versions(monkeypatch, tmp_path):
    client = ResourceServiceClient(tmp_path / 'unused.sock')
    exchange = Mock(return_value={'revision': 4})
    monkeypatch.setattr(client, '_exchange', exchange)
    client.create_knowledge_file('thinking_0123456789ab', 'usage.md', 'content', 3, actor=ACTOR)
    client.update_knowledge_file_name('thinking_0123456789ab', 'new.md', 4, actor=ACTOR, operation_id='rename-1')
    client.move_knowledge_items('Destination', [{'resource_id': 'thinking_0123456789ab', 'expected_version': 5}],
                                actor=ACTOR, operation_id='move-1')
    client.update_device_ssh_credential(DEVICE, 'test-only-password', actor=ACTOR)
    client.delete_device_ssh_credential(DEVICE, actor=ACTOR)
    client.check_device_ssh(DEVICE, actor=ACTOR, timeout_seconds=40)
    client.run_device_command(DEVICE, 'true', actor=ACTOR)
    client.update_workbench_page(WORKBENCH, 'job', 3, '<h1>ok</h1>', actor=ACTOR)
    client.update_workbench_reverse_plan(WORKBENCH, 'job-reverse', {}, actor=ACTOR)
    calls = exchange.call_args_list
    assert {c.args[0] for c in calls} == {'resource.'+n for n in OPERATIONS}
    assert all(c.args[1]['actor'] == ACTOR for c in calls)
    assert all('action' not in c.args[1] and 'payload' not in c.args[1] for c in calls)
    assert calls[0].args[1]['expected_version'] == 3
    assert calls[1].args[1]['operation_id'] == 'rename-1'
    assert calls[2].args[1]['items'][0]['expected_version'] == 5
    assert calls[7].args[1]['job_id'] == 'job' and calls[7].args[1]['base_revision'] == 3


def test_workbench_domain_job_binding_replay_and_revocation(named_service, monkeypatch):
    import hashlib
    import json
    from contextlib import nullcontext
    from management_portal.workbench_projections import service as domain
    from management_portal.workbench_projections.store import ProjectionError
    from management_portal.resources.providers import install_mutation_providers
    service, _, _ = named_service
    service._mutation_provider_loader = install_mutation_providers
    store = Mock()
    store.lock.side_effect = lambda scope: nullcontext()
    app = {'workbench_id': WORKBENCH, 'revision': 3}
    job = {'workbench_id': WORKBENCH, 'base_revision': 3, 'created_by': 'owner'}
    store.resident_job.return_value = job
    store.get.return_value = app
    monkeypatch.setattr(domain, 'Store', lambda: store)
    monkeypatch.setattr(service, 'notify_source_changed', Mock())
    with pytest.raises(ProjectionError, match='基线'):
        service.update_workbench_page(WORKBENCH, 'job-bound', 2, '<h1>new</h1>', actor=ACTOR)
    job['workbench_id'] = 'workbench_aaaaaaaaaaaa'
    with pytest.raises(ProjectionError, match='目标'):
        service.update_workbench_page(WORKBENCH, 'job-bound', 3, '<h1>new</h1>', actor=ACTOR)
    job['workbench_id'] = WORKBENCH
    digest = hashlib.sha256(json.dumps({'html': '<h1>new</h1>'}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    job['save_receipt'] = {'revision': 4, 'payload_hash': digest, 'job_id': 'job-bound'}
    assert service.update_workbench_page(WORKBENCH, 'job-bound', 3, '<h1>new</h1>', actor=ACTOR) == job['save_receipt']
    store.resident_job.assert_called_with('job-bound', ACTOR)
    with pytest.raises(ProjectionError, match='不同页面'):
        service.update_workbench_page(WORKBENCH, 'job-bound', 3, '<h1>changed</h1>', actor=ACTOR)
    store.require_write.side_effect = PermissionError('revoked')
    with pytest.raises(PermissionError, match='revoked'):
        service.update_workbench_page(WORKBENCH, 'job-bound', 3, '<h1>new</h1>', actor=ACTOR)
    assert not store.job.called


@pytest.fixture
def knowledge_domain(tmp_path, monkeypatch, permissions):
    from pathlib import Path
    from knowledge_board_lan import knowledge_backend, knowledge_source, knowledge_details
    from management_portal.resource_index_backend import ResourceIndexService
    from management_portal.resource_index_knowledge import ProjectKnowledgeAdapter
    from management_portal.resource_index_registries import ProjectRegistryAdapter
    from management_portal.resource_index_store import IndexStore
    from management_portal.resources.directory_registrations import register_owned
    from management_portal.resources.providers import knowledge
    from tests.cases.registry_support import write_projects
    from tests.cases.test_knowledge_versioning import _markdown, KNOWLEDGE_ID

    projects = [{'id': name, 'path': str(tmp_path / name), 'status': 'active', 'tags': []}
                for name in ('AlignMe', 'Destination', 'Home')]
    for entry in projects:
        Path(entry['path']).mkdir()
    write_projects({'projects': projects})
    permissions.place(*(Fact('project', p['id']) for p in projects),
                      Fact('lineage', 'lineage_0123456789ab', {'project': 'Home'}),
                      Fact('resident', ACTOR, {'lineage': 'lineage_0123456789ab'}))
    monkeypatch.setattr(knowledge_backend, '_parse_projects_yaml', lambda: projects)
    monkeypatch.setattr(knowledge_source, '_parse_projects_yaml', lambda: projects)
    adapter, project_adapter = ProjectKnowledgeAdapter(), ProjectRegistryAdapter()
    index_store = IndexStore()
    index_store.publish_snapshot(adapter.name, [], {})
    index_store.publish_snapshot(project_adapter.name,
                                 [project_adapter.project_record('project', p) for p in projects], {})
    index = ResourceIndexService(store=index_store, adapters=[adapter, project_adapter], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    service = ResourceService(index_provider=index, resolver_loader=knowledge.install_resolver)
    from management_portal import resources
    for module in (knowledge_backend, knowledge_source, knowledge_details, resources):
        monkeypatch.setattr(module, 'get_resource_service', lambda: service)
    primary = tmp_path / 'AlignMe' / 'knowledge_base' / 'thinking' / (KNOWLEDGE_ID+'.md')
    primary.parent.mkdir(parents=True)
    primary.write_text(_markdown())
    companion = primary.parent / KNOWLEDGE_ID
    companion.mkdir()
    (companion/'image.png').write_bytes(b'original-image')
    register_owned('knowledge', KNOWLEDGE_ID, [primary, companion])
    permissions.place(Fact('thinking', KNOWLEDGE_ID, {'project': 'AlignMe'}))
    index.project_relocated([{'resource_id': KNOWLEDGE_ID, 'resource_type': 'thinking',
                              'path': str(primary), 'project': 'AlignMe'}])
    yield service, KNOWLEDGE_ID, primary, companion
    index.adapter_layer.stop()


def test_knowledge_create_rename_move_real_pg_and_projection(knowledge_domain, permissions):
    from pathlib import Path
    from management_portal.resources.errors import ResourceVersionConflict
    service, rid, primary, companion = knowledge_domain
    permissions.grant(ACTOR, 'AlignMe')
    permissions.grant(ACTOR, 'Destination')
    body = primary.read_text().split('---', 2)[2]
    inode = (companion/'image.png').stat().st_ino
    created = service.create_knowledge_file(rid, 'usage.md', '# Usage', 3, actor=ACTOR)
    assert created['version']['number'] == 4
    assert service.read_content(created['address'], actor=ACTOR)[0] == b'# Usage'
    with pytest.raises(ResourceVersionConflict):
        service.create_knowledge_file(rid, 'stale.md', 'stale', 3, actor=ACTOR)
    renamed = service.update_knowledge_file_name(rid, 'new.md', 4, actor=ACTOR, operation_id='rename-same')
    assert renamed['status'] == 'completed' and renamed['version']['number'] == 5
    assert service.update_knowledge_file_name(rid, 'new.md', 4, actor=ACTOR, operation_id='rename-same') == renamed
    with pytest.raises(ValueError, match='different arguments'):
        service.update_knowledge_file_name(rid, 'different.md', 4, actor=ACTOR, operation_id='rename-same')
    items = [{'resource_id': rid, 'expected_version': 5}]
    moved = service.move_knowledge_items('Destination', items, actor=ACTOR, operation_id='move-same')
    assert moved['status'] == 'completed' and moved['projected']
    assert moved['committed'][0]['version']['number'] == 6
    assert service.move_knowledge_items('Destination', items, actor=ACTOR, operation_id='move-same') == moved
    path = Path(service.resolve(rid, actor=ACTOR)['resolved_path'])
    assert 'Destination' in path.parts and path.name == 'new.md'
    assert path.read_text().split('---', 2)[2] == body
    assert (path.parent/rid/'image.png').stat().st_ino == inode
    assert service.read_content(f'{rid}/file/{rid}/usage.md', actor=ACTOR)[0] == b'# Usage'


def test_knowledge_permission_refusals_and_lock_recheck(knowledge_domain, permissions, monkeypatch):
    from contextlib import contextmanager
    from management_portal.resources import locking
    service, rid, primary, companion = knowledge_domain
    before = primary.read_bytes()
    with pytest.raises(PermissionError):
        service.create_knowledge_file(rid, 'denied.md', 'denied', 3, actor=ACTOR)
    permissions.grant(ACTOR, 'AlignMe')
    with pytest.raises(PermissionError):
        service.move_knowledge_items('Destination', [{'resource_id': rid, 'expected_version': 3}], actor=ACTOR)
    original = locking.mutation_projection_scope
    @contextmanager
    def revoke_while_waiting(resource_id):
        with original(resource_id):
            permissions.deny(ACTOR, rid, '@contributor')
            yield
    monkeypatch.setattr(locking, 'mutation_projection_scope', revoke_while_waiting)
    with pytest.raises(PermissionError):
        service.create_knowledge_file(rid, 'revoked.md', 'revoked', 3, actor=ACTOR)
    assert primary.read_bytes() == before
    assert list(p.name for p in companion.iterdir()) == ['image.png']


@pytest.mark.parametrize('operation', ['credential_save', 'credential_delete', 'command'])
def test_ssh_real_par_viewer_admin_and_revoke(monkeypatch, permissions, operation):
    from management_portal import device_identity_backend as backend
    permissions.place(Fact('project', 'Other'), Fact('lineage', 'lineage_0123456789ab', {'project': 'Other'}),
                      Fact('resident', ACTOR, {'lineage': 'lineage_0123456789ab'}),
                      Fact('device', DEVICE))
    service = ResourceService()
    monkeypatch.setattr(backend, 'test_ssh_connection', lambda *a, **kw: {'reachable': True})
    monkeypatch.setattr(backend, 'effective_ssh_access', lambda rid: {'via_resource_id': rid})
    save = Mock(return_value={'credential_saved': True})
    monkeypatch.setattr(backend, 'save_device_ssh_password', save)
    delete = Mock(return_value={'credential_saved': False, 'deleted': True})
    run = Mock(return_value={'exit_code': 0, 'output': '', 'truncated': False})
    monkeypatch.setattr(backend, 'delete_device_ssh_password', delete)
    monkeypatch.setattr(backend, 'run_device_ssh', run)
    invoke = {
        'credential_save': lambda: service.update_device_ssh_credential(DEVICE, 'test-only-password', actor=ACTOR),
        'credential_delete': lambda: service.delete_device_ssh_credential(DEVICE, actor=ACTOR),
        'command': lambda: service.run_device_command(DEVICE, 'true', actor=ACTOR),
    }[operation]
    permissions.grant(ACTOR, DEVICE, '@viewer')
    assert service.check_device_ssh(DEVICE, actor=ACTOR) == {'ok': True}
    with pytest.raises(PermissionError):
        invoke()
    permissions.grant(ACTOR, DEVICE, '@contributor')
    with pytest.raises(PermissionError):
        invoke()
    permissions.grant(ACTOR, DEVICE, '@admin')
    invoke()
    permissions.deny(ACTOR, DEVICE, '@admin')
    with pytest.raises(PermissionError):
        invoke()
    assert save.call_count == int(operation == 'credential_save')
    assert delete.call_count == int(operation == 'credential_delete')
    assert run.call_count == int(operation == 'command')


def test_old_action_declaration_and_discovery_are_removed(permissions):
    from management_portal.hatg import declarations, routing
    assert not hasattr(declarations, 'ResourceActionDeclaration')
    assert not hasattr(routing, '_resource_actions')
    listed = routing.list_instructions(group='all')
    assert 'RunResourceAction' not in {item['instruction'] for item in listed}
    assert all('resource_actions' not in item for item in listed)
    schema = routing.instruction_schema('CheckDeviceSsh')
    assert not schema['mutates'] and 'resource_actions' not in schema


def test_article_git_preserves_contributor_role_without_retired_instruction(permissions):
    from management_portal.article_git import require_git_write
    article = 'article_0123456789ab'
    permissions.place(Fact('project', 'Other'), Fact('project', 'Home'),
                      Fact('lineage', 'lineage_0123456789ab', {'project': 'Home'}),
                      Fact('resident', ACTOR, {'lineage': 'lineage_0123456789ab'}),
                      Fact('article', article, {'project': 'Other'}))
    permissions.grant(ACTOR, article, '@viewer')
    with pytest.raises(PermissionError):
        require_git_write(article, ACTOR)
    permissions.grant(ACTOR, article, '@contributor')
    require_git_write(article, ACTOR)
    permissions.deny(ACTOR, article, '@contributor')
    with pytest.raises(PermissionError):
        require_git_write(article, ACTOR)


def test_knowledge_detail_uses_exact_rename_permission(permissions):
    from management_portal.resource_views import knowledge_detail
    rid = 'thinking_0123456789ab'
    permissions.place(Fact('project', 'Other'), Fact('project', 'Home'),
                      Fact('lineage', 'lineage_0123456789ab', {'project': 'Home'}),
                      Fact('resident', ACTOR, {'lineage': 'lineage_0123456789ab'}),
                      Fact('thinking', rid, {'project': 'Other'}))
    resolved = {'address': rid, 'filename': 'sample.md', 'content_kind': 'markdown',
                'detail': {'id': rid, 'version': {'number': 3}}}
    service = Mock()
    service.resolve.return_value = resolved
    service.read_content.return_value = (b'# Example', 'text/markdown', resolved)
    permissions.grant(ACTOR, rid, '@viewer')
    permissions.grant(ACTOR, rid, 'CreateKnowledgeFile')
    assert knowledge_detail(rid, service=service, actor=ACTOR)['can_edit'] is False
    permissions.grant(ACTOR, rid, 'UpdateKnowledgeFileName')
    assert knowledge_detail(rid, service=service, actor=ACTOR)['can_edit'] is True
    permissions.deny(ACTOR, rid, 'UpdateKnowledgeFileName')
    assert knowledge_detail(rid, service=service, actor=ACTOR)['can_edit'] is False
