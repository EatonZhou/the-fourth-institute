from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.execution import account_configuration as owner
from management_portal.hatg.contracts import InstructionError


class NativeConfig:
    path = Path('/accounts/codex_test/config.toml')

    def __init__(self, provider='openai_http'):
        self.provider = provider
        self.version = 'native-v1'
        self.calls = []
        self.conflict_at_write = False
        self.fail_read_after_write = False

    def request(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == 'config/read':
            if self.fail_read_after_write and self.version == 'native-v2':
                raise OSError('not connected')
            return {
                'config': {'model_provider': self.provider, 'secret': 'not-public'},
                'layers': [{'name': {'type': 'user', 'file': str(self.path)},
                            'version': self.version,
                            'config': {'model_provider': self.provider,
                                       'model_providers': {'openai_http': {'secret': 'not-public'}},
                                       'secret': 'not-public'}}],
            }
        assert method == 'config/value/write'
        assert params == {'keyPath': 'model_provider', 'value': 'openai',
                          'mergeStrategy': 'replace', 'filePath': str(self.path),
                          'expectedVersion': 'native-v1'}
        if self.conflict_at_write:
            raise RuntimeError('concurrent native edit')
        self.provider, self.version = params['value'], 'native-v2'
        return {'status': 'ok', 'filePath': str(self.path), 'version': self.version}


def invocation(version='native-v1', provider='openai'):
    args = {'account_id': 'codex_test', 'model_provider': provider}
    return SimpleNamespace(argument=args.get, expected_version_for=lambda _: version)


@pytest.fixture
def native(monkeypatch):
    rpc = NativeConfig()
    monkeypatch.setattr(owner, '_connection', lambda _: (rpc, rpc.path))
    return rpc


def test_native_read_is_bounded_and_does_not_expose_config(native):
    result = owner.get_configuration(invocation())['configuration']
    assert result == {'account_id': 'codex_test', 'model_provider': 'openai_http',
                      'effective_model_provider': 'openai_http',
                      'model_providers': ['openai_http'], 'version': 'native-v1'}
    assert native.calls == [('config/read', {'includeLayers': True})]


def test_reference_check_is_opt_in_bounded_and_never_returns_conversation(native):
    read = native.request
    calls = []
    def request(method, params):
        if method != 'thread/list':
            return read(method, params)
        calls.append(params)
        return {'data': [{'id': 'private-thread', 'preview': 'private-conversation'}]}
    native.request = request
    args = {'account_id': 'codex_test', 'include_provider_references': True}
    result = owner.get_configuration(SimpleNamespace(argument=args.get))
    assert result['configuration']['provider_references'] == {
        'openai_http': {'persisted_reference_found': True}}
    assert len(calls) == 1
    assert calls[0]['limit'] == 1 and calls[0]['modelProviders'] == ['openai_http']
    assert 'subAgent' in calls[0]['sourceKinds']
    assert 'private' not in str(result)


def test_update_uses_native_cas_then_verifies_without_host_restart_or_turn(native):
    result = owner.update_configuration(invocation())
    assert result['changed'] is True
    assert result['configuration']['model_provider'] == 'openai'
    assert result['configuration']['model_providers'] == ['openai_http']
    assert [call[0] for call in native.calls] == ['config/read', 'config/value/write', 'config/read']


def test_already_effective_default_does_not_write(native):
    native.provider = 'openai'
    assert owner.update_configuration(invocation())['changed'] is False
    assert len(native.calls) == 1


@pytest.mark.parametrize('version,provider,code', [
    (None, 'openai', 'instruction.invalid_arguments'),
    (1, 'openai', 'instruction.invalid_arguments'),
    ('stale', 'openai', 'instruction.version_conflict'),
    ('native-v1', 'missing', 'instruction.invalid_arguments'),
    ('native-v1', 'bad.provider', 'instruction.invalid_arguments'),
])
def test_bad_versions_and_provider_names_never_write(native, version, provider, code):
    with pytest.raises(InstructionError) as error:
        owner.update_configuration(invocation(version, provider))
    assert error.value.code == code
    assert all(method == 'config/read' for method, _ in native.calls)


def test_native_race_is_not_retried(native):
    native.conflict_at_write = True
    with pytest.raises(InstructionError) as error:
        owner.update_configuration(invocation())
    assert error.value.commit_state == 'unknown'
    assert native.provider == 'openai_http'
    assert [call[0] for call in native.calls] == ['config/read', 'config/value/write']


def test_readback_failure_preserves_committed_result(native):
    native.fail_read_after_write = True
    with pytest.raises(InstructionError) as error:
        owner.update_configuration(invocation())
    assert error.value.code == 'instruction.projection_pending'
    assert error.value.commit_state == 'committed'
    assert native.provider == 'openai'


def test_missing_or_mismatched_user_layer_fails_closed(native):
    native.path = Path('/different-account/config.toml')
    with pytest.raises(InstructionError):
        owner._read(native, Path('/accounts/codex_test/config.toml'), 'codex_test')


@pytest.mark.parametrize('provider,kind', [('claude', 'subscription_login'), ('codex', 'api_profile')])
def test_only_registered_codex_subscriptions_can_be_configured(monkeypatch, provider, kind):
    from management_portal.execution import account_registrations
    monkeypatch.setattr(account_registrations, 'registration', lambda _: {'provider': provider, 'kind': kind})
    with pytest.raises(InstructionError) as error:
        owner._connection('codex_test')
    assert error.value.code == 'instruction.invalid_arguments'


def test_configuration_instructions_require_shared_admin():
    import json
    from management_portal.hatg.catalog.environment import DECLARATIONS
    root = Path(__file__).resolve().parents[2]
    default = json.loads((root / 'management_portal/permission_system/scheme/builtin/default.json').read_text())
    declarations = {d.name: d for d in DECLARATIONS}
    for name in owner.HANDLERS:
        assert default['instructions'][name] == {'a': '@admin', 'r': '@shared'}
        assert declarations[name].owner == 'execution'
