"""Standalone Git samples retain ownership and domain cleanup boundaries."""
import json
import subprocess

import pytest

from management_portal.test_framework import tau_fixture_paths, tau_fixtures
from management_portal.test_framework.models import CatalogError, Implementation


@pytest.fixture
def locations(tmp_path, monkeypatch):
    checkout = tmp_path / 'checkout'
    (checkout / '.git').mkdir(parents=True)
    evidence = checkout / 'case'
    evidence.mkdir()
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setattr(tau_fixture_paths, '_bases', lambda: [checkout, scratch])
    return evidence, scratch


def test_standalone_fixture_is_outside_checkout_and_reused_on_resume(locations):
    """证据在代码树内时仍可准备未初始化 Git 夹具，恢复复用原目录"""
    evidence, scratch = locations
    path = tau_fixture_paths.prepare(evidence)
    assert path.parent == scratch
    probe = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True)
    assert probe.returncode != 0
    (path / 'proof.txt').write_text('retained')
    assert tau_fixture_paths.prepare(evidence) == path
    assert (path / 'proof.txt').read_text() == 'retained'
    tau_fixture_paths.cleanup(evidence)
    tau_fixture_paths.cleanup(evidence)
    assert not path.exists() and evidence.exists()


def test_standalone_cleanup_waits_for_business_owner(locations, monkeypatch):
    """业务注销失败保留外部夹具，成功后才删除本用例文件"""
    evidence, _ = locations
    path = tau_fixture_paths.prepare(evidence)
    (path / '.git').mkdir()
    (path / 'sample.txt').write_text('sample')
    resource = 'repo_123456789abc'
    (evidence / 'test-fixtures.json').write_text(json.dumps({'resources': [resource]}))
    from management_portal import portal_features
    monkeypatch.setattr(portal_features, 'contributions', lambda name: [])
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda *a: (_ for _ in ()).throw(RuntimeError('busy')))
    with pytest.raises(RuntimeError, match='busy'):
        tau_fixtures.cleanup(evidence)
    assert (path / 'sample.txt').exists()
    released = []
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, key: released.append(rid))
    result = tau_fixtures.cleanup(evidence)
    assert released == [resource]
    assert str(path) in result['paths'] and not path.exists()
    assert (evidence / 'standalone-fixture.json').exists()


def test_replaced_fixture_cannot_delete_another_directory(locations):
    """夹具被替换为符号链接时拒绝清理并保留外部目标"""
    evidence, scratch = locations
    path = tau_fixture_paths.prepare(evidence)
    path.rmdir()
    outside = scratch / 'keep'
    outside.mkdir()
    (outside / 'sentinel').write_text('keep')
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='not owned'):
        tau_fixture_paths.cleanup(evidence)
    assert (outside / 'sentinel').read_text() == 'keep'


def test_standalone_fixture_declaration_survives_planning_and_requires_scope():
    """独立文件目录声明随测试实现传递，拒绝缺少理由或副作用声明"""
    raw = {'id': 'impl-tau-standalone', 'runner': 'tau', 'entrypoint': 'tau:standalone',
           'source': 'tests/cases/tau_cases.json', 'risk': 'external-side-effects',
           'standalone_git_fixture': 'Need a directory outside all Git checkouts'}
    assert Implementation.parse(raw, 'test_123456789abc').public()['standalone_git_fixture'] == raw['standalone_git_fixture']
    for invalid in ['', True, None]:
        with pytest.raises(CatalogError):
            Implementation.parse({**raw, 'standalone_git_fixture': invalid}, 'test_123456789abc')
    with pytest.raises(CatalogError):
        Implementation.parse({**raw, 'risk': 'read-only'}, 'test_123456789abc')
