"""资源类型声明、启停与指令契约。

:module: resource.service
:design: decision_355ed742bca5
"""
from importlib import import_module
from unittest.mock import Mock

import pytest

from management_common.resource_identity import get_resource_type_registry
from management_portal.resources import ResourceService
from management_portal.resources.access import ResourceAccessPolicy


def test_declarations_are_complete_and_bind_real_providers(isolated_portal_case):
    """声明覆盖统一类型并绑定实际提供者，目录位置保持既有顺序。"""
    registry = get_resource_type_registry()
    declarations = registry.declarations
    assert set(declarations) == registry.types | registry.id_exception_types
    for kind, declaration in declarations.items():
        assert declaration['type'] == kind
        assert declaration['catalog_section'] in {'', 'knowledge', 'skill', 'repo', 'article', 'experiment', 'device'}
        if declaration['provider']:
            module, attribute = declaration['provider'].split(':')
            assert callable(getattr(import_module(module), attribute))
    assert declarations['repo']['catalog_order'] < declarations['article']['catalog_order']
    from management_portal.resources.providers import install_mutation_providers
    with isolated_portal_case():
        service = ResourceService(mutation_provider_loader=install_mutation_providers)
        for kind, declaration in declarations.items():
            service._ensure_mutation_providers(kind)
            if declaration['registerable']:
                assert kind in service._mutation_providers.registered_types(), kind
                provider = service._mutation_providers.provider_for(kind)
                assert callable(provider.register), f'{kind}: declaration allows registration without a provider register'


@pytest.mark.parametrize(('kind', 'has_register', 'expected'), [
    ('device', True, True),
    ('device', False, False),
    ('chatroom', True, False),
])
def test_registration_policy_and_lazy_provider_capabilities(
    isolated_portal_case, monkeypatch, kind, has_register, expected,
):
    """冷启动先装载登记机制，再结合政策；禁用政策保留原文与其他能力并拒绝登记。"""
    from types import SimpleNamespace
    from management_portal.resources import isa
    from management_portal.resources.resource_types import ResourceTypeError
    with isolated_portal_case():
        register = Mock()
        loaded = []

        def install(service):
            loaded.append(kind)
            service.register_mutation_provider(
                kind, register=register if has_register else None, unregister=Mock(),
            )

        service = ResourceService(
            mutation_provider_loader=install,
            index_provider=SimpleNamespace(supports_type=lambda _: False, type_counts=lambda: {}),
        )
        monkeypatch.setattr(isa, '_service', lambda: service)
        assert service._mutation_providers.registered_types() == frozenset()
        detail = isa.get_resource_type(SimpleNamespace(argument={'resource_type': kind}.get))
        assert loaded == [kind]
        assert detail['registerable'] is expected
        assert detail['unregisterable'] is True
        declaration = get_resource_type_registry().declaration(kind)
        assert detail['declaration'] == declaration
        listed = next(row for row in isa.list_resource_types(None)['types'] if row['type'] == kind)
        assert listed['registerable'] is expected
        assert listed['declaration'] == declaration
        assert loaded == [kind]
        if not declaration['registerable']:
            with pytest.raises(ResourceTypeError) as error:
                service.register(kind, {}, actor='test-actor')
            assert error.value.code == 'resource_type.not_registerable'
            register.assert_not_called()


def test_disabled_type_preserves_identity_and_existing_resolution(isolated_portal_case):
    """停用拒绝登记和精确搜索，但已有地址继续解析；状态有并发版本。"""
    from management_portal.resources.resource_types import ResourceTypes, ResourceTypeError
    with isolated_portal_case():
        types = ResourceTypes()
        service = ResourceService(type_registry=types, access_policy=Mock(spec=ResourceAccessPolicy))
        service.register_resolver('device', lambda address, **kw: {
            'root_id': address, 'resource_type': 'device', 'target_kind': 'device',
            'record': {'resource_id': address},
        })
        assert types.get('device')['version'] == 0
        assert types.update('device', False, expected_version=0, actor='test')['state'] == 'disabled'
        with pytest.raises(ResourceTypeError) as conflict:
            types.update('device', True, expected_version=0, actor='test')
        assert conflict.value.code == 'instruction.version_conflict'
        with pytest.raises(ResourceTypeError) as disabled:
            service.register('device', {}, actor='test')
        assert disabled.value.code == 'resource_type.disabled'
        assert service.query(q='device_0123456789ab')['total'] == 0
        assert service.resolve('device_0123456789ab')['resource_id'] == 'device_0123456789ab'
        assert types.update('device', True, expected_version=1, actor='test')['version'] == 2


def test_not_installed_type_cannot_be_enabled(isolated_portal_case):
    """未部署单元的类型无法启用，ID 校验仍有效。"""
    from management_portal.resources.resource_types import ResourceTypes, ResourceTypeError
    with isolated_portal_case():
        types = ResourceTypes(units=frozenset({'core'}))
        assert types.get('paper')['state'] == 'not_installed'
        with pytest.raises(ResourceTypeError) as error:
            types.update('paper', True, expected_version=0, actor='test')
        assert error.value.code == 'resource_type.not_installed'
        assert get_resource_type_registry().parse('paper_0123456789ab').resource_type == 'paper'


def test_provider_groups_install_once_and_experiment_owns_its_resolver(isolated_portal_case):
    """共享提供者按整组安装一次，实验解析器不会重复注册其他知识类型。"""
    from management_portal.resources.runtime import create_local_resource_service
    with isolated_portal_case():
        service = create_local_resource_service()
        for kind in ('decision', 'concept', 'experiment', 'thinking', 'skill'):
            assert service.capabilities(kind)['resolvable']


def test_index_filters_before_paging_and_retains_disabled_instances(isolated_portal_case):
    """停用类型退出分页、计数与建议，索引仍保留已有实例用于直接读取。"""
    from management_portal.resource_index_backend import IndexStore
    from management_portal.resource_index_records import ChangeSet
    from management_portal.resources.resource_types import ResourceTypes
    with isolated_portal_case():
        store = IndexStore()
        def record(kind, code):
            return {'resource_id': f'{kind}_{code}', 'resource_type': kind, 'title': 'Type sample',
                    'summary': '', 'facets': [kind], 'projects': [], 'status': 'active', 'attributes': {},
                    'source': {'adapter': 'test'}}
        device = record('device', '0123456789ab')
        model = record('model', '0123456789ab')
        store.apply(ChangeSet(adapter='test', upserts=(device, model)))
        assert store.type_counts() == {'device': 1, 'model': 1}
        types = ResourceTypes()
        types.update('device', False, expected_version=0, actor='test')
        assert store.query(limit=1, actor='test-actor')['total'] == 1
        assert store.query(limit=1, actor='test-actor')['resources'][0]['resource_type'] == 'model'
        assert all(r['resource_type'] != 'device' for r in store.suggest(q='Type', actor='test-actor')['resources'])
        assert store.type_counts()['device'] == 1
        store.apply(ChangeSet(adapter='test', upserts=(record('device', 'fedcba987654'),)))
        assert store.type_counts()['device'] == 1


def test_resource_type_instruction_contracts(isolated_portal_case, monkeypatch):
    """三条类型指令共享状态与动作定义，类型参数唯一且信封前提保留冲突详情。"""
    from types import SimpleNamespace
    from management_portal.resources import isa
    from management_portal.resources.runtime import create_local_resource_service
    from management_portal.hatg.contracts import InstructionError
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.hatg.catalog import declaration
    from management_portal.hatg.kernel import Invocation
    with isolated_portal_case():
        service = create_local_resource_service()
        monkeypatch.setattr(isa, '_service', lambda: service)
        def invocation(instruction='GetResourceType', *, expected_version=None, **kwargs):
            definition = declaration(instruction)
            definition.validate_arguments(kwargs)
            return Invocation(definition, InstructionCall.from_payload({
                'instruction': instruction, 'arguments': kwargs, 'expected_version': expected_version,
            }), SimpleNamespace(caller='test-actor'), 'test-resource-types')
        result = isa.list_resource_types(invocation('ListResourceTypes'))
        assert {row['type'] for row in result['types']} == set(service.resource_types.identity.declarations)
        for row in result['types']:
            assert row['registerable'] == service.resource_types.identity.declaration(row['type'])['registerable']
        device = isa.get_resource_type(invocation(resource_type='device'))
        assert device['type'] == 'device'
        assert device['actions'] and device['registerable'] and device['installed']
        disabled = isa.update_resource_type(invocation('UpdateResourceType', resource_type='device', enabled=False, expected_version=0))
        assert disabled['version'] == 1
        with pytest.raises(InstructionError) as conflict:
            isa.update_resource_type(invocation('UpdateResourceType', resource_type='device', enabled=True, expected_version=0))
        assert conflict.value.code == 'instruction.version_conflict'
        assert conflict.value.details == {'object_id': 'device', 'expected_version': 0, 'current_version': 1}
        assert service.resource_types.get('device')['enabled'] is False
        assert service.resource_types.get('device')['version'] == 1
        mapped = isa.update_resource_type(invocation(
            'UpdateResourceType', resource_type='device', enabled=False, expected_version={'device': 1},
        ))
        assert mapped['version'] == 2
        unconditional = isa.update_resource_type(invocation('UpdateResourceType', resource_type='device', enabled=True))
        assert unconditional['version'] == 3 and unconditional['enabled'] is True
        with pytest.raises(InstructionError):
            declaration('UpdateResourceType').validate_arguments({'resource_type': 'device', 'enabled': False, 'expected_version': 3})
        assert declaration('UpdateResourceType').schema()['target'] == 'resource_type'
        for instruction, arguments in (
            ('GetResourceType', {'type': 'device'}),
            ('UpdateResourceType', {'type': 'device', 'enabled': False}),
        ):
            with pytest.raises(InstructionError):
                declaration(instruction).validate_arguments(arguments)


def test_deployed_provider_import_failure_is_fatal(monkeypatch):
    """已部署提供者缺失必须报错，未部署单元不导入提供者。"""
    from types import SimpleNamespace
    from management_portal.resources import providers, resource_types
    declaration = get_resource_type_registry().declaration('device')
    declaration['provider'] = 'management_portal.resources.providers.missing_for_test:install_resolver'
    monkeypatch.setattr(providers, 'get_resource_type_registry', lambda: SimpleNamespace(declarations={'device': declaration}))
    monkeypatch.setattr(resource_types, 'deployed_units', lambda: frozenset({'core'}))
    with pytest.raises(ModuleNotFoundError):
        providers.provider_plugins()
    monkeypatch.setattr(resource_types, 'deployed_units', lambda: frozenset())
    assert providers.provider_plugins() == ()
