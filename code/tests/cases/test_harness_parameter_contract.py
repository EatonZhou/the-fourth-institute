"""Declared transport hints preserve complete native Chat business payloads.

:module: execution.providers
"""
import json
import urllib.request
from unittest.mock import Mock

import pytest

from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal import model_api_bridge, model_api_wire as wire
from tests.cases.test_model_api_bridge import endpoint


@pytest.mark.parametrize('streaming', [True, False])
def test_chat_projection_preserves_business_fields_and_native_response(endpoint, streaming):
    server, entry = endpoint
    server.protocol, server.json = wire.CHAT, not streaming
    entry.update(kind='openai_compatible_chat', provider='google')
    payload = {'model': 'test-model', 'stream': streaming, 'prompt_cache_key': 'routing-only',
               'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'retained'}]}],
               'tools': [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}],
               'tool_choice': 'required', 'temperature': .4, 'response_format': {'type': 'json_object'},
               'vendor_business_extension': {'nested': ['must', 'survive']}}
    with model_api_bridge.Bridge(lambda: entry, wire.CHAT) as bridge:
        request = urllib.request.Request(
            bridge.base_url + '/chat/completions', data=json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + bridge.token, 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            assert response.headers['X-TFI-Omitted-Advisory-Fields'] == 'prompt_cache_key'
            body = response.read().decode()
    assert server.requests[0][1] == {k: v for k, v in payload.items() if k != 'prompt_cache_key'}
    assert 'call-1' in body and 'read_file' in body
    assert 'prompt_tokens' in body


def test_cache_hint_contract_has_explicit_gateway_override_and_no_business_drop_policy():
    entry = {'kind': 'openai_compatible_chat', 'provider': 'google'}
    assert AIProviderRegistry.harness_parameter_contracts(entry)[wire.CHAT]['omitted_advisory_fields'] == ['prompt_cache_key']
    entry['protocols'] = {wire.CHAT: {'supports_prompt_cache_key': True}}
    assert AIProviderRegistry.harness_parameter_contracts(entry)[wire.CHAT]['omitted_advisory_fields'] == []
    from management_portal.execution.isa import _validate_protocols
    for value in [None, 0, 'false', []]:
        with pytest.raises(Exception, match='布尔'):
            _validate_protocols({wire.CHAT: {'supports_prompt_cache_key': value}})
    with pytest.raises(Exception, match='只接受'):
        _validate_protocols({wire.CHAT: {'drop_messages': True}})


def test_rejection_diagnostics_cannot_echo_private_upstream_values():
    assert model_api_bridge.rejection_terms(
        b'{"error":"tools function_declarations parameters properties should be non-empty for OBJECT type; '
        b'sk-private-value https://private.invalid/?token=private private@example.com"}') == [
            'tools', 'function_declarations', 'parameters', 'properties', 'should', 'non-empty', 'object', 'type', 'invalid']


def test_kimi_account_host_filters_native_environment_and_closes_transient_adapter(tmp_path, monkeypatch):
    from management_portal import kimi_code_server as host, kimi_account_runtime as accounts, kimi_provider_catalog as catalog
    from management_portal import provider_daemon_host
    from management_portal import resident_shell_environment
    row = {'account_id': 'api-kimi', 'kind': 'api_profile'}
    monkeypatch.setattr(accounts, 'account_for_route', lambda _: row)
    monkeypatch.setattr(accounts, 'home', lambda _: tmp_path)
    write = Mock()
    monkeypatch.setattr(catalog, 'write_config', write)
    monkeypatch.setattr(host.kimi_windows_runtime, 'environment', lambda: {})
    monkeypatch.setattr(host, 'close_all_database_pools', lambda: None)
    close = Mock()
    monkeypatch.setattr(model_api_bridge, 'close_bridges', close)
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'private-host-only')
    monkeypatch.setattr(resident_shell_environment, 'resident_process_environment', lambda _: ({'HOME': str(tmp_path)}, {}))
    def run(command, **options):
        assert 'PORTAL_DATABASE_URL' not in options['env']
        assert options['env']['KIMI_CODE_HOME'] == str(tmp_path)
        assert '127.0.0.1' in options['env']['NO_PROXY']
        assert options['cwd'] == tmp_path
        raise RuntimeError('native child stopped')
    monkeypatch.setattr(provider_daemon_host, 'run', run)
    with pytest.raises(RuntimeError, match='native child stopped'):
        host.account_main('api-kimi', tmp_path / 'kimi', 12345)
    write.assert_called_once_with(row, tmp_path)
    close.assert_called_once()
