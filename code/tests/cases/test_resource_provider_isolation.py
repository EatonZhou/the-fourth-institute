from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.resources import providers
from management_portal.resources.service import ResourceService


def test_provider_loading_is_lazy_atomic_and_recovers_per_type(monkeypatch, subtests):
    """资源解析、变更和动作 provider 按类型懒加载，某类型注册失败不留残余且之后可重试"""
    for capability, attr in [('resolver', '_resolvers'), ('mutation', '_mutation_providers'), ('action', '_action_providers')]:
        with subtests.test(capability=capability):
            attempts = []
            failing = True
            def install(service):
                attempts.append('device')
                if capability == 'resolver':
                    service.register_resolver('device', Mock())
                elif capability == 'mutation':
                    service.register_mutation_provider('device', update=Mock())
                else:
                    service.register_action_provider('device', {'connect': Mock()})
                if failing:
                    raise RuntimeError('isolated device registration failure')
            method = {'resolver': 'install_resolver', 'mutation': 'install_mutation_provider', 'action': 'install_action_provider'}[capability]
            def module(name):
                if name.endswith('.device'):
                    return SimpleNamespace(**{method: install})
                return SimpleNamespace(**{method: lambda service: attempts.append('paper')})
            monkeypatch.setattr(providers, 'provider_plugins', lambda *args: (
                providers.ProviderPlugin('device', {capability: frozenset({'device'})}),
                providers.ProviderPlugin('paper', {capability: frozenset({'paper'})}),
            ))
            monkeypatch.setattr(providers, 'import_module', module)
            loader = providers.ProviderLoader(capability)
            keyword = {'resolver': 'resolver_loader', 'mutation': 'mutation_provider_loader', 'action': 'action_provider_loader'}[capability]
            service = ResourceService(**{keyword: loader})
            service._ensure_provider(capability, 'paper')
            assert attempts == ['paper']
            with pytest.raises(RuntimeError, match='isolated device'):
                service._ensure_provider(capability, 'device')
            registry = getattr(service, attr)
            assert ('device' not in registry.registered_types()) if capability != 'action' else not registry.actions_for('device')
            service._ensure_provider(capability, 'paper')
            assert attempts == ['paper', 'device']
            failing = False
            service._ensure_provider(capability, 'device')
            service._ensure_provider(capability, 'device')
            assert attempts == ['paper', 'device', 'device']


def test_failed_domain_import_never_falls_back_to_index(monkeypatch):
    """领域 provider 导入失败时资源解析直接报错，不回退到索引"""
    loaded = []
    def module(name):
        loaded.append(name)
        raise ImportError('isolated domain import failure')
    monkeypatch.setattr(providers, 'import_module', module)
    service = ResourceService(resolver_loader=providers.install_resolvers)
    with pytest.raises(ImportError, match='isolated domain'):
        service.resolve('paper_0123456789ab')
    assert loaded == ['management_portal.resources.providers.paper']
    assert service._resolvers.registered_types() == frozenset()


def test_index_fallback_reserves_domain_owned_types():
    """索引兜底解析器只接管没有领域 provider 的资源类型"""
    index = Mock()
    index.indexed_types.return_value = {'paper', 'todo'}
    service = ResourceService(index_provider=index, resolver_loader=providers.install_resolvers)
    service.register_index_backed_resolver(Mock())
    assert service._resolvers.registered_types() == frozenset({'todo'})
