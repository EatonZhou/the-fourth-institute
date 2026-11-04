"""TAU Project identity exceptions use the Project lifecycle owner."""
import json

import pytest

from management_common.resource_lifecycle import LifecycleStore
from management_portal import isa_client
from management_portal.hatg.contracts import InstructionError
from management_portal.test_framework import tau_fixtures
from tests.cases.test_tau_fixture_cleanup import allocate


@pytest.mark.parametrize('project_id', ['TAU-run-home', '验收项目', 'project-named', 'project_123456789abc'])
def test_named_project_uses_authoritative_deletion(monkeypatch, tmp_path, project_id):
    """自然名和看似前缀的 Project 均经唯一 owner 删除，重试不重复提交。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    calls = []
    def call(actor, name, arguments, **options):
        assert actor == 'test-actor'
        calls.append((name, arguments, options))
        assert name == 'DeleteProject' and arguments == {'project_id': project_id}
        return {'project_id': project_id, 'registration_status': 'unregistered',
                'effects': {'status': 'complete'}}
    monkeypatch.setattr(isa_client, 'call', call)
    tau_fixtures._release_resource(project_id, 'project-cleanup')
    tau_fixtures._release_resource(project_id, 'project-cleanup')
    assert [name for name, _, _ in calls] == ['DeleteProject']
    assert calls[-1][2]['key'].startswith('project-cleanup:')


@pytest.mark.parametrize('outcome', ['missing', 'denied', 'no_permit', 'unavailable', 'pending', 'wrong_identity'])
def test_project_cleanup_failure_is_not_absence(monkeypatch, outcome):
    """缺失可收口；拒绝、服务异常、身份错误和未完副作用保留待办。

    :module: operations.test-framework
    """
    from management_portal import wall_backend
    pid = 'TAU-project-cleanup'
    calls = []
    monkeypatch.setattr(wall_backend, 'projects', lambda actor: {'projects': []})
    def call(actor, name, arguments, **options):
        calls.append(name)
        assert name == 'DeleteProject'
        codes = {'missing': 'instruction.not_found', 'denied': 'instruction.forbidden',
                 'no_permit': 'instruction.not_found', 'unavailable': 'instruction.owner_unavailable'}
        if outcome in codes:
            raise InstructionError(codes[outcome], outcome,
                                   details={'reason': 'no_permit'} if outcome == 'no_permit' else {})
        return {'project_id': 'other' if outcome == 'wrong_identity' else pid,
                'registration_status': 'unregistered', 'effects': {'status': 'pending'}}
    monkeypatch.setattr(isa_client, 'call', call)
    if outcome == 'missing':
        tau_fixtures._release_resource(pid, 'project-failure')
    else:
        with pytest.raises((InstructionError, RuntimeError, ValueError)):
            tau_fixtures._release_resource(pid, 'project-failure')
    assert 'UnregisterResource' not in calls


def test_case_and_run_cleanup_share_project_owner(tmp_path, monkeypatch):
    """逐例及整轮清理处理已分配自然 Project，未分配对象保持不动。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    from management_portal import portal_features, wall_backend
    from management_portal.test_framework.run_cleanup import finish_run
    projects = {'TAU-case-project', 'TAU-run-project', 'unrelated'}
    released = []
    def call(actor, name, arguments, **options):
        pid = arguments['project_id']
        assert pid != 'unrelated'
        assert name == 'DeleteProject'
        released.append(pid)
        projects.remove(pid)
        return {'project_id': pid, 'registration_status': 'unregistered', 'effects': {'status': 'complete'}}
    monkeypatch.setattr(isa_client, 'call', call)
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [])
    monkeypatch.setattr(wall_backend, 'projects', lambda actor: {'projects': []})
    root = tmp_path / 'case'; root.mkdir()
    scope = allocate(root, 'TAU-case-project')
    (root / 'test-fixtures.json').write_text(json.dumps({'resources': ['TAU-case-project']}))
    tau_fixtures.cleanup(root)
    assert not LifecycleStore().pending(scope)
    other_scope = allocate(tmp_path / 'run', 'TAU-run-project')
    receipt = finish_run('fixture-test')
    assert receipt['status'] == 'complete'
    assert not LifecycleStore().pending(other_scope)
    assert released == ['TAU-case-project', 'TAU-run-project'] and projects == {'unrelated'}


def test_pending_project_effects_retry_same_deletion_receipt(monkeypatch):
    """项目身份已删但副作用未完时，不以读不到身份冒称清理完成。

    :module: operations.test-framework
    """
    keys = []
    def call(actor, name, arguments, **options):
        assert name == 'DeleteProject'
        keys.append(options['key'])
        return {'project_id': arguments['project_id'], 'registration_status': 'unregistered',
                'effects': {'status': 'pending'}}
    monkeypatch.setattr(isa_client, 'call', call)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='unfinished owner effects'):
            tau_fixtures._release_resource('TAU-pending-effects', 'pending-effects')
    assert len(keys) == 2 and keys[0] == keys[1]


def test_named_project_cleanup_reaches_real_owner(tmp_path, monkeypatch):
    """隔离数据库中真实 Project 删除同时注销登记并交原生目录队列。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    from tests.cases.test_project_deletion import _configure_project
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal import project_registry
    project_root, _, _, _ = _configure_project(monkeypatch, tmp_path)
    tau_fixtures._release_resource('Example', 'real-project-owner')
    assert project_registry.project('Example') is None
    get_directory_cleanup_store().run_pending()
    assert not project_root.exists()


def test_unallocated_natural_project_is_not_deletion_authority(tmp_path, monkeypatch):
    """手写自然项目名不授予清理权，文件和清单保留。

    :module: operations.test-framework
    """
    sample = tmp_path / 'fixtures'; sample.mkdir()
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': ['TAU-unowned']}))
    monkeypatch.setattr(isa_client, 'call', lambda *a, **kw: pytest.fail('unowned mutation'))
    with pytest.raises(ValueError, match='缺少本轮分配记录'):
        tau_fixtures.cleanup(tmp_path)
    assert sample.exists() and json.loads(manifest.read_text())['resources'] == ['TAU-unowned']


def test_workspace_migration_guard_does_not_erase_fixture(tmp_path, monkeypatch):
    """登记清单不可用时保留目录，不能以权限过滤发现替代完整阻挡检查。

    :module: operations.test-framework
    """
    from management_portal import portal_features
    from management_portal.resources import workspace_store
    sample = tmp_path / 'fixtures'; sample.mkdir()
    (sample / 'keep.txt').write_text('retained')
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [])
    def unavailable():
        raise RuntimeError('Workspace directory protocol migration requires the offline maintenance window')
    monkeypatch.setattr(workspace_store, 'get_workspace_store', unavailable)
    with pytest.raises(RuntimeError, match='offline maintenance window'):
        tau_fixtures.cleanup(tmp_path)
    assert (sample / 'keep.txt').read_text() == 'retained'


def test_hidden_workspace_registration_still_blocks_fixture_removal(tmp_path, monkeypatch):
    """同主体发现看不到登记时，完整登记阻挡仍保留目录和证据。

    实际权限引擎过滤发现结果；实际 Workspace 登记独立于发现可见性。
    实际显式拒绝读取后，不能把空列表当作全局无登记。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    from types import SimpleNamespace
    from tests.cases.permission_test_support import enable, Fact
    from management_portal import portal_features, resources
    from management_portal.resources import ResourceService
    from management_portal.resources.contracts import ResourceQuery
    from management_portal.resources.query import query_records
    from management_portal.resources.collaboration_registry import descriptor_resource_record
    from management_portal.resources.workspace_store import get_workspace_store

    sample = tmp_path / 'fixtures' / 'private-workspace'; sample.mkdir(parents=True)
    (sample / 'keep.txt').write_text('private registered business content')
    evidence = tmp_path / 'evidence.txt'; evidence.write_text('keep evidence')
    wid = 'workspace_123456789abc'
    store = get_workspace_store()
    store.ensure(wid, str(sample), title='Hidden registration')
    permissions = enable(monkeypatch)
    permissions.users('test-actor', 'workspace-owner')
    permissions.place(Fact('workspace', wid, creator='workspace-owner'))
    observer = 'test-actor'
    permissions.deny(observer, wid)
    assert not permissions.holds(observer, '@viewer', wid)
    record = descriptor_resource_record(store.descriptors([wid])[wid])
    # The inventory is indexed in both requests; only its actual ACL differs.
    index = SimpleNamespace(query=lambda **kwargs: query_records([record], ResourceQuery.from_kwargs(kwargs)))
    service = ResourceService(index_provider=index)
    assert service.query(actor='workspace-owner', resource_types=['workspace'])['total'] == 1
    hidden = service.query(actor=observer, resource_types=['workspace'])
    assert hidden['resources'] == [] and hidden['total'] == 0
    monkeypatch.setattr(resources, 'get_resource_service', lambda: service)
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [])
    monkeypatch.setattr(isa_client, 'call', lambda *a, **kw: pytest.fail('hidden object must not be mutated'))
    with pytest.raises(RuntimeError, match=wid):
        tau_fixtures.cleanup(tmp_path)
    assert store.rows([wid])[wid]['deleted_at'] is None
    assert (sample / 'keep.txt').read_text() == 'private registered business content'
    assert evidence.read_text() == 'keep evidence'
