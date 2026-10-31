"""API registration makes every compatible harness selectable without inference calls."""
import pytest

pytestmark = pytest.mark.empty_execution_registrations

from management_portal.execution import account_registrations as accounts, isa
from management_portal.execution.accounts import default_execution_accounts
from tests.cases.test_model_api_registry_postgres import _call


def test_api_registration_exposes_all_compatible_harnesses_and_preserves_existing_routes():
    created = _call(isa.create_model_api, api_id='gateway', base_url='https://example.invalid/v1',
                    default_model='chat', protocols={'anthropic_messages': {}, 'openai_responses': {}})
    rows = {r['provider']: r for r in created['accounts']}
    assert set(rows) == {'provider_api', 'kimi', 'opencode', 'dsh', 'claude', 'codex'}
    catalog = default_execution_accounts(tolerate_unavailable=True).runtime_catalog(include_backends=True)
    for backend in catalog['backends']:
        if backend['backend'] in rows:
            assert any(r['account_id'] == rows[backend['backend']]['account_id'] for r in backend['routes'])
    old = rows['dsh']
    accounts.update_registration(old['account_id'], {'enabled': False, 'max_concurrency': 7}, old['version'], 'test')
    _call(isa.update_model_api, api_id='gateway', changes={})
    assert len(accounts.registrations()) == 6
    updated = accounts.registration(old['account_id'])
    assert not updated['enabled'] and updated['max_concurrency'] == 7


def test_protocols_limit_harnesses_and_jev_is_direct_only():
    chat = _call(isa.create_model_api, api_id='chat', base_url='https://example.invalid/v1', default_model='chat')
    assert {r['provider'] for r in chat['accounts']} == {'provider_api', 'kimi', 'opencode'}
    jev = _call(isa.create_model_api, api_id='jev', kind='typesafe_system_one', base_url='https://example.invalid', default_model='jev')
    assert {r['provider'] for r in jev['accounts']} == {'provider_api'}
    changed = _call(isa.update_model_api, api_id='chat', changes={'protocols': {'anthropic_messages': {}}})
    assert {r['provider'] for r in changed['accounts']} == {'provider_api', 'kimi', 'opencode', 'claude', 'dsh'}


def test_existing_api_routes_are_reused():
    from management_portal import model_api_store
    entry = {'id': 'deepseek', 'kind': 'openai_compatible_chat', 'enabled': True, 'provider': 'deepseek',
             'base_url': 'https://example.invalid', 'default_model': 'chat'}
    model_api_store.create_api(entry, 'test')
    for provider, name in [('claude', 'deepseek'), ('dsh', 'dsh_deepseek')]:
        accounts.create_registration({'account_id': name, 'provider': provider, 'kind': 'api_profile',
                                      'config': {'api_id': 'deepseek'}, 'max_concurrency': 5}, 'test')
    saved = _call(isa.update_model_api, api_id='deepseek', changes={})
    assert {r['account_id'] for r in saved['accounts']} == {'deepseek', 'dsh_deepseek', 'deepseek-kimi', 'deepseek-opencode'}
    assert len(saved['accounts']) == 4, 'native and direct reuse must not duplicate the returned identity'
    assert accounts.registration('deepseek')['max_concurrency'] == 5


def test_route_registration_failure_can_be_repaired_without_recreating_api(monkeypatch):
    from management_portal import model_api_store
    from management_portal.hatg.contracts import InstructionError
    original = accounts.create_registration
    def fail_second(data, actor):
        if data['provider'] == 'kimi':
            raise RuntimeError('simulated storage failure')
        return original(data, actor)
    monkeypatch.setattr(accounts, 'create_registration', fail_second)
    with pytest.raises(InstructionError) as caught:
        _call(isa.create_model_api, api_id='retry', base_url='https://example.invalid', default_model='chat')
    assert caught.value.details == {'reason':'model_api.routes_incomplete', 'api_id':'retry', 'api_saved':True}
    assert model_api_store.api_version('retry') == 1
    assert accounts.registrations() == [], 'route batch rolls back on a partial failure'
    monkeypatch.setattr(accounts, 'create_registration', original)
    repaired = _call(isa.update_model_api, api_id='retry', changes={})
    assert len(repaired['accounts']) == 3


def test_api_identity_collision_does_not_save_an_unusable_interface():
    from management_portal import model_api_store
    from management_portal.hatg.contracts import InstructionError
    accounts.create_registration({'account_id':'taken','provider':'opencode','kind':'service'}, 'test')
    with pytest.raises(InstructionError):
        _call(isa.create_model_api, api_id='taken', base_url='https://example.invalid', default_model='chat')
    assert model_api_store.api_version('taken') is None
