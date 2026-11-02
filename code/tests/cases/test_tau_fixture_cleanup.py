import json
from types import SimpleNamespace

import pytest
from management_portal import wall_backend as wall
from management_portal.test_framework import tau_fixtures
from management_portal.test_framework.runner_adapters import TauAdapter


def test_workspace_cleanup_uses_unconditional_domain_deletion(monkeypatch):
    """引用与绑定不再阻止注销；目录删除由对象 owner 的队列负责。"""
    from management_portal import isa_client
    wid = 'workspace_bbbbbbbbbbbb'
    calls = []
    def call(actor, name, args, **kw):
        calls.append((name, args, kw['key']))
        assert actor == 'test-actor'
    monkeypatch.setattr(isa_client, 'call', call)
    tau_fixtures._release_resource(wid, 'cleanup')
    assert [c[0] for c in calls] == ['UnregisterResource']


def test_content_fixture_is_in_area_and_cleanup_reaches_wall_owner(tmp_path, monkeypatch):
    """WALL 夹具落在 features 区域，先注销产品再删文件，证据仍在 runtime"""
    from management_common.instance_locations import current_instance
    from management_portal.test_framework import tau_fixture_paths
    from management_portal import portal_features
    root = tmp_path / 'run'; root.mkdir()
    content = tau_fixture_paths.prepare_content(root)
    assert content.is_relative_to(current_instance().area('features'))
    assert current_instance().reference(content).startswith('features:')
    assert tau_fixture_paths.prepare_content(root) == content
    repo = content / 'wall-test-repo'; repo.mkdir()
    (root / 'evidence.png').write_bytes(b'png')
    calls = []
    def release(path, manifest, actor, **kwargs):
        assert repo.is_dir()
        calls.append(path)
        return {'projects': ['sample'] if path == content else [], 'items': 0}, []
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [('wall', release)])
    result = tau_fixtures.cleanup(root)
    assert calls == [root.resolve(), content]
    assert result['projects'] == ['sample']
    assert not content.exists() and (root / 'evidence.png').exists()


def test_content_fixture_replacement_is_refused_before_product_cleanup(tmp_path, monkeypatch):
    """夹具根被替换时不得注销或删除替代目录"""
    from management_portal.test_framework import tau_fixture_paths
    from management_portal import portal_features
    root = tmp_path / 'run'; root.mkdir()
    content = tau_fixture_paths.prepare_content(root)
    content.rmdir()
    content.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(portal_features, 'contributions', lambda name: pytest.fail('cleanup reached product'))
    with pytest.raises(ValueError, match='escape'):
        tau_fixtures.cleanup(root)
    content.unlink()


@pytest.mark.parametrize('directory_missing', [False, True])
def test_async_wall_workspace_unregistered_without_allocation_context(tmp_path, monkeypatch, directory_missing):
    """后台 WALL 分配未继承请求上下文时仍注销夹具登记，不碰目录外已有资源"""
    from management_common.resource_identity import get_resource_type_registry
    from management_portal.resources.workspace_store import get_workspace_store, now
    from management_portal.test_framework import tau_fixture_paths
    from management_portal import portal_features
    root = tmp_path / 'case'; root.mkdir()
    content = tau_fixture_paths.prepare_content(root)
    repo = content / 'repo'; repo.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    store = get_workspace_store()
    new_id = lambda: get_resource_type_registry().new_id('workspace')
    wid = store.for_path(str(repo), new_id=new_id)
    keep = store.for_path(str(outside), new_id=new_id)
    if directory_missing:
        repo.rmdir()
        content.rmdir()
    # No lifecycle_scope: this mirrors a WALL background job. Its own release
    # hook has detached its workers before the framework handles the directory.
    calls = []
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [])
    def release(rid, key):
        assert rid == wid and repo.is_dir() is (not directory_missing)
        calls.append(rid)
        store.database.write(lambda conn: store.mark_deleted(rid, deleted_at=now(), deleted_by='test-actor', connection=conn))
    monkeypatch.setattr(tau_fixtures, '_release_resource', release)
    result = tau_fixtures.cleanup(root)
    assert calls == [wid] and wid in result['resources']
    assert not store.rows([wid]) and store.rows([keep]) and outside.is_dir()
    assert not content.exists()


@pytest.mark.parametrize('resources', [
    [{'resource_id': 'resident_123456789abc', 'deleted': True}],
    'resident_123456789abc', ['not-a-resource'], [None],
])
def test_invalid_resource_manifest_keeps_files_without_business_mutations(tmp_path, monkeypatch, resources):
    """资源清单格式非法时清理报错，保留文件且不做业务删除"""
    manifest = {'resources': resources}
    (tmp_path / 'test-fixtures.json').write_text(json.dumps(manifest))
    sample = tmp_path / 'fixtures'
    sample.mkdir()
    (sample / 'data.txt').write_text('retain for cleanup')
    monkeypatch.setattr(wall, 'projects', lambda actor: pytest.fail('Invalid manifest reached business cleanup'))
    with pytest.raises(ValueError, match='正式资源 ID 字符串数组'):
        tau_fixtures.cleanup(tmp_path)
    assert (sample / 'data.txt').exists()
    assert json.loads((tmp_path / 'test-fixtures.json').read_text()) == manifest


def test_cleanup_unregisters_only_case_projects_before_deleting_files(tmp_path, monkeypatch):
    """清理先注销用例目录内的 Project 再删文件，不动目录外项目和截图

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    root = tmp_path / 'case'
    sample = root / 'fixtures' / 'repo'
    sample.mkdir(parents=True)
    (sample / 'SPEC.md').write_text('# Sample')
    (root / 'helpers').mkdir()
    (root / 'helpers' / 'run.js').write_text('sample')
    (root / 'evidence.png').write_bytes(b'png')
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'fixtures' / 'link').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': [
        {'id': 'sample', 'root': str(sample)}, {'id': 'other', 'root': str(outside)}]})
    monkeypatch.setattr(wall, '_read_state', lambda: {'jobs': {}})
    calls = []
    def unregister(payload, actor):
        assert (sample / 'SPEC.md').exists()
        calls.append(payload['project'])
    monkeypatch.setattr(wall, 'unregister', unregister)
    tau_fixtures.cleanup(root)
    assert calls == ['sample']
    assert not sample.exists() and not (root / 'helpers').exists()
    assert outside.exists() and (root / 'evidence.png').exists()


def test_failed_business_cleanup_keeps_fixture_and_can_retry(tmp_path, monkeypatch):
    """注销 Project 失败时保留夹具文件，之后可以重试清理

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    sample = tmp_path / 'fixtures' / 'repo'; sample.mkdir(parents=True)
    (sample / 'SPEC.md').write_text('# sample')
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': [{'id': 'sample', 'root': str(sample)}]})
    monkeypatch.setattr(wall, '_read_state', lambda: {'jobs': {}})
    monkeypatch.setattr(wall, 'unregister', lambda *a: (_ for _ in ()).throw(RuntimeError('Resident running')))
    with pytest.raises(RuntimeError, match='Resident running'):
        tau_fixtures.cleanup(tmp_path)
    assert (sample / 'SPEC.md').exists()
    monkeypatch.setattr(wall, 'unregister', lambda *a: None)
    tau_fixtures.cleanup(tmp_path)
    assert not sample.exists()


def test_cleanup_failure_receipt_is_not_complete(tmp_path, monkeypatch):
    """TAU 夹具清理失败时，清理回执标记为未完成并记录错误

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    from management_portal.test_framework import runner_adapters
    monkeypatch.setattr(runner_adapters, '_terminate_artifact_helpers', lambda p: [])
    monkeypatch.setattr(runner_adapters, '_artifact_helper_targets', lambda p: [])
    monkeypatch.setattr(tau_fixtures, 'cleanup', lambda p: (_ for _ in ()).throw(RuntimeError('fixture remains')))
    with pytest.raises(RuntimeError, match='fixture remains'):
        object.__new__(TauAdapter).cleanup(run_dir=tmp_path, emit=lambda text: None)
    receipt = json.loads((tmp_path / 'framework-helper-cleanup.json').read_text())
    assert receipt['complete'] is False and receipt['error'] == 'fixture remains'


@pytest.mark.parametrize('outcome', ['deleted', 'missing', 'failed'])
def test_upload_handles_use_file_cleanup_and_preserve_failed_cleanup(tmp_path, monkeypatch, outcome):
    """上传文件直接经内部文件清理，I/O失败时保留清单和夹具。"""
    from management_portal.resources import upload_files

    upload_id = 'upload_' + 'a' * 24
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': [], 'uploads': [upload_id]}))
    sample = tmp_path / 'fixtures'; sample.mkdir()
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    def remove(actual_id):
        assert actual_id == upload_id
        if outcome == 'failed':
            raise OSError('file cleanup failed')
        return {'upload_id': upload_id, 'deleted': outcome == 'deleted'}
    monkeypatch.setattr(upload_files, 'remove_upload', remove)
    if outcome == 'failed':
        with pytest.raises(RuntimeError, match='file cleanup failed'):
            tau_fixtures.cleanup(tmp_path)
        assert sample.exists()
        assert json.loads(manifest.read_text())['uploads'] == [upload_id]
    else:
        assert tau_fixtures.cleanup(tmp_path)['uploads'] == [upload_id]
        assert json.loads(manifest.read_text())['uploads'] == []
        assert not sample.exists()


def test_run_cleanup_removes_allocated_upload_without_instruction(monkeypatch):
    from datetime import datetime, timezone
    from management_common.portal_paths import path
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from management_common.upload_retention import created
    from management_portal import isa_client
    from management_portal.test_framework.run_cleanup import finish_run

    upload_id = 'upload_' + 'b' * 24
    directory = path('uploads') / upload_id
    store = LifecycleStore()
    scope = store.open('upload-test', actor='test-actor', run_id='upload-run', case_id='cleanup')
    with lifecycle_scope(scope, actor='test-actor'):
        created(directory, upload_id=upload_id,
                created_at=datetime.now(timezone.utc).isoformat())
        (directory / 'meta.json').write_text(json.dumps({'upload_id': upload_id, 'ready': True,
            'file_name': 'sample.txt'}))
        (directory / 'sample.txt').write_text('fixture')
    def unexpected_instruction(*args, **kwargs):
        pytest.fail('Upload file cleanup must not call HATG')
    monkeypatch.setattr(isa_client, 'call', unexpected_instruction)
    result = finish_run('upload-run')
    assert result['status'] == 'complete', result
    assert result['resources'] == [upload_id]
    assert not directory.exists() and not store.pending(scope)
    assert store.scope(scope)['state'] == 'complete'


def test_legacy_scripts_removed_even_when_report_is_missing(tmp_path):
    """没有夹具清单时仍删除遗留脚本，保留截图

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    (tmp_path / 'helper.py').write_text('pass')
    (tmp_path / 'screenshot.png').write_bytes(b'png')
    tau_fixtures.cleanup(tmp_path)
    assert not (tmp_path / 'helper.py').exists() and (tmp_path / 'screenshot.png').exists()


@pytest.mark.parametrize('kind', ['resident', 'lineage'])
def test_collaboration_fixtures_use_their_owner_and_verify_deletion(tmp_path, monkeypatch, kind):
    """测试样本通过 Resident 与 Lineage 生命周期回收

    清理精确样本身份，调用所属删除入口并确认删除完成，不向资源登记表请求替代删除。

    :module: operations.test-workbench
    """
    from management_portal import isa_client, resident_handoff
    from management_portal.resources.providers import collaboration
    providers = {}
    collaboration.install_mutation_provider(SimpleNamespace(register_mutation_provider=lambda types, **kwargs: providers.update(kwargs)))
    resource_id = f'{kind}_123456789abc'
    lineage_id = 'lineage_123456789abc'
    path = tmp_path / 'test-fixtures.json'
    path.write_text(json.dumps({'resources': [resource_id]}))
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    calls = []
    deleted = False
    def release(**kwargs):
        nonlocal deleted
        assert kwargs == {'actor': 'test-actor', 'resident_id': resource_id, 'lineage_id': lineage_id}
        calls.append('release_resident')
        deleted = True
    def instruction(actor, name, arguments, **kwargs):
        nonlocal deleted
        if name == 'UnregisterResource':
            assert arguments == {'resource_id': resource_id}
            return providers['unregister'](resource_id, actor=actor)
        assert actor == 'test-actor' and arguments == {f'{kind}_id': resource_id}
        calls.append(name)
        if name == 'DeleteLineage':
            deleted = True
            return {}
        assert name == ('GetResident' if kind == 'resident' else 'GetLineage')
        return {kind: {'lineage_id': lineage_id, 'status': 'deleted' if deleted else 'active'}}
    monkeypatch.setattr(isa_client, 'call', instruction)
    monkeypatch.setattr(resident_handoff, 'release_resident', release)
    tau_fixtures.cleanup(tmp_path)
    assert calls == (['GetResident', 'release_resident', 'GetResident'] if kind == 'resident'
                     else ['GetLineage', 'DeleteLineage', 'GetLineage'])
    assert json.loads(path.read_text())['resources'] == []


@pytest.mark.parametrize('code', ['instruction.not_found', 'instruction.forbidden'])
def test_missing_fixture_is_complete_but_denied_fixture_is_preserved(tmp_path, monkeypatch, code):
    """已删除测试样本可重复清理，拒绝访问保留待办

    仅正式不存在回执完成清理；权限错误保留样本清单供检查与重试。

    :module: operations.test-workbench
    """
    from management_portal import isa_client
    from management_portal.hatg.contracts import InstructionError
    path = tmp_path / 'test-fixtures.json'
    manifest = {'resources': ['resident_123456789abc']}
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    def instruction(*args, **kwargs):
        raise InstructionError(code, 'fixture unavailable')
    monkeypatch.setattr(isa_client, 'call', instruction)
    if code == 'instruction.not_found':
        tau_fixtures.cleanup(tmp_path)
        assert json.loads(path.read_text())['resources'] == []
    else:
        with pytest.raises(RuntimeError, match='fixture unavailable'):
            tau_fixtures.cleanup(tmp_path)
        assert json.loads(path.read_text())['resources'] == manifest['resources']


@pytest.mark.parametrize('resolution', ['instruction.not_found', 'instruction.forbidden',
                                        'instruction.owner_unavailable', 'existing'])
def test_deleted_fixture_permission_facts_do_not_prevent_confirming_absence(monkeypatch, resolution):
    """注销删除权限事实后，框架用同一主体的正式解析确认缺失。

    :module: operations.test-workbench
    :design: decision_c953eab654cd
    """
    from management_portal import isa_client
    from management_portal.hatg.contracts import InstructionError
    rid = 'information_123456789abc'
    calls = []
    def instruction(actor, name, arguments, **kwargs):
        assert actor == 'test-actor'
        calls.append(name)
        if name == 'UnregisterResource':
            assert arguments == {'resource_id': rid}
            raise InstructionError('instruction.forbidden', 'permission facts removed or denied')
        assert name == 'ResolveResource' and arguments == {'address': rid}
        if resolution != 'existing':
            raise InstructionError(resolution, 'resolution result')
        return {'resource': {'resource_id': rid}}
    monkeypatch.setattr(isa_client, 'call', instruction)
    if resolution == 'instruction.not_found':
        tau_fixtures._release_resource(rid, 'cleanup')
    else:
        with pytest.raises(InstructionError):
            tau_fixtures._release_resource(rid, 'cleanup')
    assert calls == ['UnregisterResource', 'ResolveResource']


@pytest.mark.parametrize('outcome', ['deleted', 'missing', 'raced', 'busy', 'denied', 'incomplete'])
def test_paper_fixture_cleanup_uses_product_deletion_and_preserves_failures(tmp_path, monkeypatch, outcome):
    """Paper 测试样本经产品生命周期清理并核验删除

    验证正常删除、不存在和并发删除可幂等完成；忙碌、权限拒绝或删除未完成时保留精确资源清单与样例文件，避免通用注销绕过 Paper 绑定和评审的回收。

    :importance: P1
    :design: decision_c953eab654cd
    """
    from management_portal import isa_client, paper_resources, paper_store
    from management_portal.resources.providers import paper as paper_provider
    providers = {}
    paper_provider.install_mutation_provider(SimpleNamespace(register_mutation_provider=lambda kind, **kwargs: providers.update(kwargs)))

    paper_id = 'paper_123456789abc'
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': [paper_id]}))
    sample = tmp_path / 'fixtures'; sample.mkdir()
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    def unregister(actor, name, arguments, **kwargs):
        assert name == 'UnregisterResource' and arguments == {'resource_id': paper_id}
        return providers['unregister'](paper_id, actor=actor)
    monkeypatch.setattr(isa_client, 'call', unregister)
    exists = outcome != 'missing'
    calls = []
    monkeypatch.setattr(paper_store, '_paper_by_id', lambda rid: {'id': rid} if exists else None)
    def delete(payload, actor):
        nonlocal exists
        assert payload == {'paper_id': paper_id} and actor == 'test-actor'
        calls.append(paper_id)
        if outcome == 'busy': raise RuntimeError('review busy')
        if outcome == 'denied': raise PermissionError('permission denied')
        if outcome != 'incomplete': exists = False
        if outcome == 'raced': raise KeyError('paper not found')
        return {'deleted': True}
    monkeypatch.setattr(paper_resources, 'delete_paper', delete)
    if outcome in {'busy', 'denied', 'incomplete'}:
        with pytest.raises(RuntimeError, match='review busy|permission denied|删除尚未完成'):
            tau_fixtures.cleanup(tmp_path)
        assert sample.exists()
        assert json.loads(manifest.read_text())['resources'] == [paper_id]
    else:
        tau_fixtures.cleanup(tmp_path)
        assert not sample.exists()
        assert json.loads(manifest.read_text())['resources'] == []
    assert calls == ([] if outcome == 'missing' else [paper_id])


@pytest.mark.parametrize('outcome', ['deleted', 'missing', 'raced', 'busy', 'denied', 'incomplete', 'generating'])
def test_review_cleanup_uses_paper_owner_and_retains_failed_allocations(tmp_path, monkeypatch, outcome):
    """评审资源经论文后端删除，删除失败、未完成或仍在生成时留在清单中"""
    from management_portal import isa_client, paper_reviews, paper_state_store
    from management_portal.resources.providers import install_mutation_providers
    providers = {}
    install_mutation_providers.for_type(SimpleNamespace(
        register_mutation_provider=lambda kind, **handlers: providers.update({kind: handlers})
    ), 'review')
    review_id = 'review_123456789abc'
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': [review_id]}))
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    exists = outcome != 'missing'
    monkeypatch.setattr(paper_state_store, 'get', lambda kind, rid: {'review_id': rid} if exists else None)
    monkeypatch.setattr(paper_state_store, 'records', lambda kind: [
        {'review_id': review_id, 'status': 'running'}] if outcome == 'generating' else [])
    calls = []

    def delete(payload, actor):
        nonlocal exists
        assert payload == {'review_id': review_id} and actor == 'test-actor'
        calls.append(review_id)
        if outcome == 'busy': raise RuntimeError('review busy')
        if outcome == 'denied': raise PermissionError('permission denied')
        if outcome != 'incomplete': exists = False
        if outcome == 'raced': raise KeyError('review not found')

    monkeypatch.setattr(paper_reviews, 'delete_review', delete)
    monkeypatch.setattr(isa_client, 'call', lambda actor, name, arguments, **kwargs:
                        providers['review']['unregister'](arguments['resource_id'], actor=actor))
    if outcome in {'busy', 'denied', 'incomplete', 'generating'}:
        with pytest.raises(RuntimeError, match='review busy|permission denied|删除尚未完成|评审生成尚未结束'):
            tau_fixtures.cleanup(tmp_path)
        assert json.loads(manifest.read_text())['resources'] == [review_id]
    else:
        tau_fixtures.cleanup(tmp_path)
        assert json.loads(manifest.read_text())['resources'] == []
    assert calls == ([] if outcome in {'missing', 'generating'} else [review_id])


def test_repaired_cleanup_owner_is_not_blocked_by_a_cached_failed_instruction(tmp_path, monkeypatch):
    """资源注销方修复后重试清理换用新幂等键，不被缓存的失败结果阻挡"""
    from management_portal import isa_client
    from management_portal.hatg.contracts import InstructionError
    resource_id = 'review_123456789abc'
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': [resource_id]}))
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    failed_keys = set()
    calls = []
    repaired = False

    def unregister(actor, name, arguments, *, key):
        calls.append(key)
        if not repaired or key in failed_keys:
            failed_keys.add(key)
            raise InstructionError('instruction.failed', 'owner not installed')
        return {'resource_id': resource_id}

    monkeypatch.setattr(isa_client, 'call', unregister)
    with pytest.raises(RuntimeError, match='owner not installed'):
        tau_fixtures.cleanup(tmp_path)
    assert json.loads(manifest.read_text())['resources'] == [resource_id]
    repaired = True
    assert tau_fixtures.cleanup(tmp_path)['resources'] == [resource_id]
    assert len(set(calls)) == 2
