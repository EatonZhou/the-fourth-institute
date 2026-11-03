from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from management_common.resource_identity import get_resource_type_registry
from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope, current_lifecycle
from management_portal.hatg.contracts import InstructionContext
from management_portal.hatg.kernel import InstructionKernel
from management_portal.test_framework import tau_fixtures


def open_scope(root):
    return LifecycleStore().open(str(root.resolve()), actor='test-actor', run_id=root.name, case_id='case')


@pytest.mark.parametrize('mode', ['finite', 'stream', 'foreign', 'closed', 'ordinary'])
def test_asgi_instruction_carries_lifecycle_to_owner_thread(tmp_path, monkeypatch, mode):
    """真实 ASGI 入口跨线程传递用例归属，owner 拒绝越权与迟到请求，普通调用不被接管。"""
    import asyncio
    import json
    from management_portal import asgi
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionResult, failed_result, normalize_error
    from tests.cases.test_portal_asgi import _request, _response_body

    store = LifecycleStore()
    scope = open_scope(tmp_path)
    if mode == 'closed':
        store.seal(scope)
    monkeypatch.setattr(asgi, '_instruction_principal', lambda _request: (
        'unrelated' if mode == 'foreign' else 'test-actor', 'web'))
    monkeypatch.setattr(asgi, '_same_origin', lambda _request: True)
    monkeypatch.setattr(routing, 'declaration_for', lambda _name: SimpleNamespace(streaming=mode == 'stream'))
    created = []

    def allocate(context):
        # The same boundary used by local and remote kernels, on the actual
        # executor thread rather than the request's asyncio context.
        with InstructionKernel._identity_scope(InstructionContext.from_payload(context.to_payload())):
            created.append(get_resource_type_registry().new_id('workspace'))
            return {'resource_id': created[-1], 'scope_id': current_lifecycle()}

    def execute(call, context):
        try:
            return InstructionResult('inst_web_test', call.instruction, 'committed', result=allocate(context))
        except Exception as exc:
            return failed_result('inst_web_test', call.instruction, normalize_error(exc))

    def stream(call, context, **_options):
        yield [allocate(context)]

    monkeypatch.setattr(routing, 'execute', execute)
    monkeypatch.setattr(routing, 'stream', stream)

    async def request():
        app = SimpleNamespace(state=SimpleNamespace())
        response = await asgi.instruction_execute(_request(
            '/api/instructions/execute', method='POST', app=app,
            body=json.dumps({'instruction': 'CreateUpload', 'arguments': {}}).encode(),
            headers={} if mode == 'ordinary' else {'X-Portal-Resource-Lifecycle': scope},
        ))
        if mode == 'stream':
            assert scope.encode() in await _response_body(response)
        elif mode in {'foreign', 'closed'}:
            assert response.status_code != 200
        else:
            assert response.status_code == 200

    asyncio.run(request())
    if mode in {'foreign', 'closed'}:
        assert created == []
        assert store.pending(scope) == []
    else:
        assert len(created) == 1
        assert store.pending(scope) == ([] if mode == 'ordinary' else created)
    assert current_lifecycle() == ''


def test_workspaces_are_tracked_and_deleted_after_their_resident(tmp_path, monkeypatch):
    """额外 Workspace 自动跟踪，Resident 先删除，已有 Workspace 不被接管"""
    store = LifecycleStore()
    scope = open_scope(tmp_path)
    registry = get_resource_type_registry()
    existing = registry.new_id('workspace')
    with lifecycle_scope(scope, actor='test-actor'):
        resident = registry.new_id('resident')
        workspace = registry.new_id('workspace')
        registry.validate(existing, 'workspace')
    assert set(store.pending(scope)) == {resident, workspace}
    assert resident in store.actors(scope)
    released = []
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, key: released.append(rid))
    tau_fixtures.cleanup(tmp_path)
    assert released == [resident, workspace]
    assert store.pending(scope) == []


def test_parallel_lifecycles_capture_only_new_ids_and_survive_failed_request(tmp_path):
    """并行的资源生命周期各自只记录新分配的 ID，请求失败后记录仍保留"""
    store = LifecycleStore()
    left, right = open_scope(tmp_path / 'left'), open_scope(tmp_path / 'right')
    registry = get_resource_type_registry()
    existing = registry.new_id('paper')
    def create(scope):
        with lifecycle_scope(scope, actor='test-actor'):
            registry.validate(existing, 'paper')  # reading/reusing an input is not ownership
            return registry.new_id('search')
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(create, [left, right]))
    with pytest.raises(RuntimeError, match='response lost'):
        with lifecycle_scope(left, actor='test-actor'):
            c = registry.new_id('paper')
            raise RuntimeError('response lost')
    assert store.pending(left) == [c, a]
    assert store.pending(right) == [b]
    assert current_lifecycle() == ''
    with pytest.raises(PermissionError):
        with lifecycle_scope(left, actor='unrelated'):
            pytest.fail('unrelated caller entered scope')


def test_cleanup_waits_for_inflight_creations_and_rejects_late_requests(tmp_path):
    """封存生命周期时等待进行中的资源创建完成，并拒绝封存后到达的请求"""
    store = LifecycleStore()
    scope = open_scope(tmp_path)
    started, finish = Event(), Event()
    def create():
        with lifecycle_scope(scope, actor='test-actor'):
            started.set()
            assert finish.wait(5)
            return get_resource_type_registry().new_id('search')
    with ThreadPoolExecutor(2) as pool:
        request = pool.submit(create)
        assert started.wait(5)
        closing = pool.submit(store.seal, scope)
        try:
            assert not closing.done()
        finally:
            finish.set()
        rid = request.result(5)
        closing.result(5)
    assert store.pending(scope) == [rid]
    with pytest.raises(ValueError, match='closed'):
        with lifecycle_scope(scope, actor='test-actor'):
            pytest.fail('late request entered scope')


def test_signed_resource_rpc_carries_scope_across_request_threads(tmp_path, monkeypatch):
    """签名的 ResourceService RPC 把生命周期范围带到服务端线程，描述符投影不继承该范围"""
    from contextlib import contextmanager
    from threading import Thread
    from management_common import resource_lifecycle
    from management_common.portal_database import portal_database
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from tests.cases.test_resource_service_process import FakePublisher, FakeResourceService

    # Even a one-connection data pool must serve a nested RPC while its parent
    # holds a lease and cleanup is waiting for that parent to finish.
    db = portal_database(schema='resource_lifecycle', max_size=1)
    monkeypatch.setattr(resource_lifecycle, 'portal_database', lambda **_options: db)
    scope = open_scope(tmp_path)
    db._connection_pool().timeout = 0.5
    cleanup_waiting = Event()
    advisory_lock = db.advisory_lock

    @contextmanager
    def observed_lock(key, *, blocking=True, shared=False):
        with advisory_lock(key, blocking=blocking, shared=shared) as acquired:
            if not blocking and not acquired:
                cleanup_waiting.set()
            yield acquired

    monkeypatch.setattr(db, 'advisory_lock', observed_lock)

    class AllocatingService(FakeResourceService):
        def query(self, **params):
            # Exercise the actual signed transport without domain storage mocks.
            return {'resource_id': get_resource_type_registry().new_id('search'),
                    'scope_id': current_lifecycle()}

        def sync_collaboration_resources(self, resources, *, domain_identity=""):
            # A descriptor projection executes as the owning service. It must
            # not inherit a User/Resident's allocation lifecycle over RPC.
            assert current_lifecycle() == ''
            return super().sync_collaboration_resources(resources, domain_identity=domain_identity)

    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    key = tmp_path / 'rpc-key'
    key.write_bytes(b'test-resource-lifecycle-rpc-key-32-bytes')
    key.chmod(0o600)
    monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(key))
    socket = tmp_path / 'resources.sock'
    server = ResourceServiceServer(socket, service=AllocatingService(), publisher=FakePublisher())
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    try:
        with ThreadPoolExecutor(1) as closer:
            with lifecycle_scope(scope, actor='test-actor'):
                closing = closer.submit(LifecycleStore().seal, scope, timeout=5)
                assert cleanup_waiting.wait(2)
                result = ResourceServiceClient(socket, timeout=3).query(actor='test-actor')
                bundle = ResourceServiceClient(socket, timeout=3).sync_collaboration_resources([
                    {'descriptor': {'resource_id': 'resident_0123456789ab'}}])
                assert bundle['resources'] == [{'resource_id': 'resident_0123456789ab'}]
            closing.result(2)
        assert result['scope_id'] == scope
        assert LifecycleStore().pending(scope) == [result['resource_id']]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resident_and_remote_instruction_context_keep_case_identity(tmp_path):
    """绑定用例的 Resident 与远端指令上下文所分配资源归入该用例，用例完成后解除绑定"""
    store = LifecycleStore()
    scope = open_scope(tmp_path)
    resident = 'resident_abcdefabcdef'
    store.bind(scope, resident)
    context = InstructionContext(caller=resident, entry='web')
    with InstructionKernel._identity_scope(context):
        rid = get_resource_type_registry().new_id('skill')
        assert current_lifecycle() == scope
    wire = InstructionContext(caller='test-actor', entry='web', resource_lifecycle_id=scope)
    with InstructionKernel._identity_scope(InstructionContext.from_payload(wire.to_payload())):
        second = get_resource_type_registry().new_id('workbench')
    assert store.pending(scope) == [second, rid]
    with pytest.raises(ValueError, match='unfinished'):
        store.bind(open_scope(tmp_path / 'other'), resident)
    store.seal(scope)
    for allocated in store.pending(scope):
        store.released(scope, allocated)
    store.complete(scope)
    assert store.for_actor(resident) == ''
    with InstructionKernel._identity_scope(context):
        assert current_lifecycle() == ''  # Ordinary later work is no longer a test.
    with pytest.raises(ValueError, match='closed'):
        with lifecycle_scope(scope, actor='test-actor'):
            pytest.fail('an explicitly late test request was accepted')
    next_scope = open_scope(tmp_path / 'next')
    store.bind(next_scope, resident)
    with InstructionKernel._identity_scope(context):
        assert current_lifecycle() == next_scope


def test_automatic_cleanup_without_manifest_retries_failures_and_preserves_other_case(tmp_path, monkeypatch):
    """无清单的自动清理在注销失败后换新幂等键重试，且不动其他用例的资源"""
    from management_portal import isa_client
    store = LifecycleStore()
    root = tmp_path / 'case'
    (root / 'helpers').mkdir(parents=True)
    (root / 'helpers' / 'probe.js').write_text('probe')
    scope, other = open_scope(root), open_scope(tmp_path / 'other')
    with lifecycle_scope(scope, actor='test-actor'):
        rid = get_resource_type_registry().new_id('search')
    with lifecycle_scope(other, actor='test-actor'):
        unrelated = get_resource_type_registry().new_id('search')
    monkeypatch.setattr('management_portal.wall_backend.projects', lambda actor: {'projects': []})
    calls = []
    def release(actor, instruction, arguments, **options):
        assert actor == 'test-actor' and instruction == 'UnregisterResource'
        assert arguments == {'resource_id': rid}
        calls.append(options['key'])
        if len(calls) == 1:
            from management_portal.hatg.contracts import InstructionError
            raise InstructionError('instruction.owner_unavailable', 'owner unavailable')
        return {'deleted': True}
    monkeypatch.setattr(isa_client, 'call', release)
    with pytest.raises(RuntimeError, match='owner unavailable'):
        tau_fixtures.cleanup(root)
    assert (root / 'helpers' / 'probe.js').exists()
    assert store.pending(scope) == [rid]
    receipt = tau_fixtures.cleanup(root)
    assert receipt['resources'] == [rid] and receipt['resource_lifecycle_id'] == scope
    assert calls[0] != calls[1]  # A repaired owner must not replay its old failed instruction.
    assert all(key.startswith(f'test-release:{scope}:{rid}:') for key in calls)
    assert not (root / 'helpers').exists()
    assert store.pending(scope) == [] and store.pending(other) == [unrelated]


@pytest.mark.parametrize('surface', ['paper', 'knowledge', 'skill', 'wall'])
def test_search_unregister_uses_page_deletion_and_is_idempotent(monkeypatch, surface):
    """各页面的检索资源注销走页面删除流程，重复注销只删除一次"""
    from management_portal import search_identity_backend, semantic_search_jobs
    from management_portal.resources.providers import search
    rid = 'search_123456abcdef'
    record = {'id': rid, 'surface': surface, 'project': 'sample', 'deleted': False}
    def resolve(resource_id, *, actor):
        assert resource_id == rid and actor == 'test-actor'
        return {'record': record, 'exists': not record['deleted']}
    monkeypatch.setattr(search_identity_backend, 'resolve_search_address', resolve)
    calls = []
    def delete(surface_name, payload, actor):
        calls.append((surface_name, payload, actor))
        record['deleted'] = True
    monkeypatch.setattr(semantic_search_jobs, 'delete_search', delete)
    providers = {}
    search.install_mutation_provider(SimpleNamespace(register_mutation_provider=lambda kind, **kwargs: providers.update(kwargs)))
    for _ in range(2):
        result = providers['unregister'](rid, actor='test-actor')
        assert result['resource_id'] == rid and result['deleted']
    assert calls == [(surface, {'search_id': rid, 'project': 'sample'}, 'test-actor')]


def test_page_search_creation_is_automatically_cleaned_via_resource_service(tmp_path, monkeypatch):
    """用例范围内新建的页面检索由清理自动删除，刷新已有检索不计入"""
    from management_portal import semantic_search_jobs as searches, shared_search_adapter
    monkeypatch.setattr(searches, 'start_task', lambda job: job)
    cancelled = []
    monkeypatch.setattr(shared_search_adapter, 'cancel_run', lambda job, **options: cancelled.append(job['search_id']))
    existing = searches.start_search('paper', {'query': 'existing input'}, 'test-actor')['search_id']
    scope = open_scope(tmp_path)
    with lifecycle_scope(scope, actor='test-actor'):
        searches.refresh_search('paper', {'search_id': existing}, 'test-actor')
        created = searches.start_search('paper', {'query': 'new fixture'}, 'test-actor')['search_id']
    assert LifecycleStore().pending(scope) == [created]
    tau_fixtures.cleanup(tmp_path)
    assert LifecycleStore().pending(scope) == []
    assert searches.search_by_id('paper', created)['deleted']
    assert not searches.search_by_id('paper', existing).get('deleted')
    assert cancelled == [created]


def test_workspace_allocation_rpc_preserves_authenticated_lifecycle(tmp_path, monkeypatch):
    """真实跨线程 RPC 分配 Workspace 归入用例，复用已有路径不抢占归属"""
    from threading import Thread
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from management_portal.resources.workspace_store import get_workspace_store
    from tests.cases.test_resource_service_process import FakePublisher, FakeResourceService

    scope = open_scope(tmp_path / 'case')
    (tmp_path / 'new').mkdir()
    owner = 'resident_abcdef123456'
    LifecycleStore().bind(scope, owner)
    registry = get_resource_type_registry()
    existing_path = tmp_path / 'existing'; existing_path.mkdir()
    existing = get_workspace_store().for_path(str(existing_path), new_id=lambda: registry.new_id('workspace'))
    class AllocatingService(FakeResourceService):
        def workspace_id_for_path(self, path, *, preferred_id=''):
            assert current_lifecycle() == scope
            return get_workspace_store().for_path(path, preferred_id=preferred_id,
                                                  new_id=lambda: registry.new_id('workspace'))
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    key = tmp_path / 'rpc-key'; key.write_bytes(b'workspace-lifecycle-signing-key-32'); key.chmod(0o600)
    monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(key))
    socket = tmp_path / 'resources.sock'
    server = ResourceServiceServer(socket, service=AllocatingService(), publisher=FakePublisher())
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    assert server.started.wait(2)
    try:
        client = ResourceServiceClient(socket, timeout=3)
        with lifecycle_scope(scope, actor='test-actor'):
            with pytest.raises(PermissionError, match='authenticated actor'):
                client.workspace_id_for_path(tmp_path / 'unattributed')
        context = InstructionContext(caller=owner, entry='web')
        with InstructionKernel._identity_scope(context):
            created = client.workspace_id_for_path(tmp_path / 'new')
            assert client.workspace_id_for_path(tmp_path / 'new') == created
            assert client.workspace_id_for_path(existing_path) == existing
        assert LifecycleStore().pending(scope) == [created]
        assert get_workspace_store().paths([created])[created] == str(tmp_path / 'new')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
