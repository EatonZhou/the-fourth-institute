import json
from types import SimpleNamespace

import pytest
from management_portal import wall_backend as wall
from management_portal.test_framework import tau_fixtures
from management_portal.test_framework.runner_adapters import TauAdapter


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


@pytest.mark.parametrize('outcome', ['deleted', 'missing', 'denied'])
def test_upload_handles_use_delete_upload_and_preserve_denied_cleanup(tmp_path, monkeypatch, outcome):
    """上传文件经 DeleteUpload 清理，权限被拒时保留清单和夹具"""
    from management_portal import isa_client
    from management_portal.hatg.contracts import InstructionError

    upload_id = 'upload_' + 'a' * 24
    manifest = tmp_path / 'test-fixtures.json'
    manifest.write_text(json.dumps({'resources': [], 'uploads': [upload_id]}))
    sample = tmp_path / 'fixtures'; sample.mkdir()
    monkeypatch.setattr(wall, 'projects', lambda actor: {'projects': []})
    def call(actor, name, arguments, **kwargs):
        assert actor == 'test-actor'
        assert name == 'DeleteUpload' and arguments == {'upload_id': upload_id}
        assert kwargs['key'].startswith(f'tau-upload:{tmp_path.name}:{upload_id}:')
        if outcome != 'deleted':
            raise InstructionError('instruction.not_found' if outcome == 'missing' else 'instruction.permission_denied', outcome)
    monkeypatch.setattr(isa_client, 'call', call)
    if outcome == 'denied':
        with pytest.raises(RuntimeError, match='denied'):
            tau_fixtures.cleanup(tmp_path)
        assert sample.exists()
        assert json.loads(manifest.read_text())['uploads'] == [upload_id]
    else:
        assert tau_fixtures.cleanup(tmp_path)['uploads'] == [upload_id]
        assert json.loads(manifest.read_text())['uploads'] == []
        assert not sample.exists()


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
