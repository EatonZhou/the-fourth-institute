"""Native process environment migration; no socket, database or model connection."""
import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from management_portal import ai_socket_relay as relay
from management_portal import resident_shell_environment as shell
from tests.cases.claude_sdk_support import load_claude_module

daemon = load_claude_module('claude_code_daemon')
RESIDENT = 'resident_123456789abc'
LINEAGE = 'lineage_abcdef123456'
OLD = {'ARCHIVE_RESIDENT_ID': RESIDENT, 'ARCHIVE_LINEAGE_ID': LINEAGE}
NEW = {'PORTAL_RESIDENT_ID': RESIDENT, 'PORTAL_LINEAGE_ID': LINEAGE}
DUAL = {**OLD, **NEW}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Never let this suite read real credentials or saved network settings.
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PORTAL_SITE_CONFIG", site_environ({})["PORTAL_SITE_CONFIG"])
    monkeypatch.setattr(shell.portal_network, 'saved_proxy_environment', lambda: None)


@pytest.mark.parametrize('identity', [NEW])
def test_shell_mirrors_identity_and_keeps_allowlist_proxy_boundary(monkeypatch, identity):
    monkeypatch.setenv('PATH', '/test/bin')
    monkeypatch.setenv('HTTP_PROXY', 'http://worker.invalid')
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only')
    monkeypatch.setenv('PORTAL_RESIDENT_ID', 'resident_999999999999')
    monkeypatch.setenv('ARCHIVE_LINEAGE_ID', 'lineage_999999999999')
    monkeypatch.setenv('UNRELATED_SECRET', 'fixture-only')
    monkeypatch.setattr(shell.portal_network, 'saved_proxy_environment',
                        lambda: {'HTTP_PROXY': 'http://managed.invalid'})
    env, actual = shell.resident_process_environment({'resident_environment': identity})
    assert actual == NEW
    assert env == {'PATH': '/test/bin', 'HTTP_PROXY': 'http://managed.invalid', **NEW}


INVALID = [
    {**OLD, 'PORTAL_RESIDENT_ID': 'resident_999999999999'},
    {**OLD, 'PORTAL_LINEAGE_ID': 'lineage_999999999999'},
    {**OLD, 'PORTAL_RESIDENT_ID': ''},
    {'PORTAL_RESIDENT_ID': RESIDENT},
    {'PORTAL_LINEAGE_ID': LINEAGE},
    {**NEW, 'PORTAL_RESIDENT_ID': 'invalid-identity'},
    {**NEW, 'UNEXPECTED_FIELD': 'fixture-only'},
]


@pytest.mark.parametrize('identity', INVALID)
def test_shell_rejects_bad_identity_without_database_lookup(monkeypatch, identity):
    lookup = Mock(side_effect=AssertionError('database lookup must not occur'))
    monkeypatch.setattr(shell, 'portal_database_environment', lookup)
    with pytest.raises(ValueError) as error:
        shell.resident_process_environment({'resident_environment': identity,
                                            'inherit_portal_database': True})
    lookup.assert_not_called()
    assert 'fixture-only' not in str(error.value)


def test_shell_human_does_not_inherit_identity_or_database(monkeypatch):
    for key, value in DUAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only')
    assert shell.resident_process_environment({}) == ({}, {})
    with pytest.raises(ValueError, match='requires Resident identity'):
        shell.resident_process_environment({'inherit_portal_database': True})


def test_native_child_preserves_windows_profile_without_ambient_credentials(monkeypatch):
    # The real Windows OpenCode bridge resolves its authenticated IPC directory
    # from LOCALAPPDATA, and pathlib.home() needs USERPROFILE (not POSIX HOME).
    profile = r'C:\Users\测试 用户'
    values = {'USERPROFILE': profile, 'LOCALAPPDATA': profile + r'\AppData\Local',
              'APPDATA': profile + r'\AppData\Roaming', 'HOMEDRIVE': 'C:',
              'HOMEPATH': r'\Users\测试 用户'}
    for key, value in {**values, 'OPENAI_API_KEY': 'unrelated-secret',
                       'PORTAL_DATABASE_URL': 'postgresql://not-authorized'}.items():
        monkeypatch.setenv(key, value)
    env, identity = shell.resident_process_environment({})
    child = subprocess.run([sys.executable, '-I', '-c',
        'import json,ntpath,os; print(json.dumps({"home":ntpath.expanduser("~"),"env":dict(os.environ)}))'],
        env=env, capture_output=True, text=True, check=True, timeout=10)
    result = json.loads(child.stdout)
    assert result['home'] == profile
    assert {key: result['env'].get(key) for key in values} == values
    assert 'OPENAI_API_KEY' not in result['env']
    assert 'PORTAL_DATABASE_URL' not in result['env']
    assert identity == {}


def test_database_permission_gate_and_file_fallback_are_unchanged(monkeypatch, tmp_path):
    request = SimpleNamespace(agent_id=RESIDENT, permission_profile='owner', metadata={
        'business_type': 'resident', 'resident_id': RESIDENT, 'filesystem_scope': {'mode': 'A'}})
    assert shell.may_inherit_portal_database(request)
    for field, value in [('business_type', 'human'), ('resident_id', 'resident_999999999999'),
                         ('filesystem_scope', {'mode': 'B'})]:
        old = request.metadata[field]
        request.metadata[field] = value
        assert not shell.may_inherit_portal_database(request)
        request.metadata[field] = old
    request.permission_profile = 'restricted'
    assert not shell.may_inherit_portal_database(request)
    config = tmp_path / 'postgres.env'
    config.write_text('PORTAL_DATABASE_URL="postgresql://fixture-only"\n')
    monkeypatch.setenv('PORTAL_POSTGRES_ENV_FILE', str(config))
    assert shell.portal_database_environment(False) == {}
    env, _ = shell.resident_process_environment({
        'resident_environment': NEW, 'inherit_portal_database': True})
    assert env == {**NEW, 'PORTAL_DATABASE_URL': 'postgresql://fixture-only'}
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://direct-fixture')
    assert shell.portal_database_environment(True) == {'PORTAL_DATABASE_URL': 'postgresql://direct-fixture'}
    monkeypatch.delenv('PORTAL_DATABASE_URL')
    config.unlink()
    with pytest.raises(RuntimeError, match='unavailable'):
        shell.portal_database_environment(True)


@pytest.fixture
def sdk_sink(monkeypatch, tmp_path):
    profile = {'id': 'test-profile', 'default_model': 'test-model',
               'models': [{'id': 'test-model', 'enabled': True}]}
    monkeypatch.setattr(daemon, '_provider_environment', lambda _: ({'HTTP_PROXY': 'http://provider.invalid'}, profile))
    monkeypatch.setattr(daemon, '_provider_profile', lambda _: profile)
    monkeypatch.setattr(daemon, '_validated_model', lambda *_: 'test-model')
    client = SimpleNamespace(connect=AsyncMock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(daemon, 'ClaudeSDKClient', factory)
    params = {'session_key': 'resident:test', 'cwd': str(tmp_path),
              'config_dir': str(tmp_path / 'config'), 'provider_profile': 'test-profile',
              'model': 'test-model', 'is_owner': True}
    return daemon.ClaudeCodeDaemon(), params, factory, client


@pytest.mark.parametrize('identity', [NEW])
@pytest.mark.parametrize('is_owner', [True, False])
@pytest.mark.parametrize('allow_subagents', [None, False])
def test_daemon_actual_sdk_options_mirror_identity_without_widening_permissions(sdk_sink, identity, is_owner, allow_subagents):
    instance, params, factory, client = sdk_sink
    asyncio.run(instance._create_handle({**params, 'resident_environment': identity, 'is_owner': is_owner,
                                       'allow_subagents': allow_subagents}))
    options = factory.call_args.args[0]
    assert {key: options.env[key] for key in NEW} == NEW
    assert options.env['PORTAL_DATABASE_URL'] == ''
    assert options.env['HTTP_PROXY'] == 'http://provider.invalid'
    assert options.allowed_tools == (daemon.OWNER_TOOLS if is_owner else daemon.NON_OWNER_TOOLS)
    assert options.permission_mode == 'dontAsk'
    expected_disallowed = [*([] if is_owner else daemon.NON_OWNER_DISALLOWED_TOOLS),
                           *(daemon.SUBAGENT_TOOLS if allow_subagents is False else [])]
    assert options.disallowed_tools == expected_disallowed
    client.connect.assert_awaited_once()


@pytest.mark.parametrize('identity', INVALID)
def test_daemon_rejects_bad_identity_before_sdk_connect(sdk_sink, identity):
    instance, params, factory, _ = sdk_sink
    with pytest.raises(ValueError):
        asyncio.run(instance._create_handle({**params, 'resident_environment': identity}))
    factory.assert_not_called()


def test_daemon_human_masks_ambient_identity_and_database(sdk_sink, monkeypatch):
    instance, params, factory, _ = sdk_sink
    for key, value in DUAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only')
    asyncio.run(instance._create_handle(params))
    env = factory.call_args.args[0].env
    assert all(env[key] == '' for key in NEW)
    assert env['PORTAL_DATABASE_URL'] == ''
    factory.reset_mock()
    with pytest.raises(ValueError, match='requires Resident identity'):
        asyncio.run(instance._create_handle({**params, 'inherit_portal_database': True}))
    factory.assert_not_called()


def test_daemon_authorized_identity_receives_existing_database_contract(sdk_sink, monkeypatch):
    instance, params, factory, _ = sdk_sink
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only')
    asyncio.run(instance._create_handle({**params, 'resident_environment': NEW, 'inherit_portal_database': True}))
    env = factory.call_args.args[0].env
    assert env['PORTAL_DATABASE_URL'] == 'postgresql://fixture-only'
    assert {key: env[key] for key in NEW} == NEW


def test_daemon_signature_uses_only_canonical_identity(sdk_sink):
    instance, params, _, _ = sdk_sink
    original = instance._signature({**params, 'resident_environment': NEW}, 'test-model')
    assert json.dumps(NEW, sort_keys=True) in original
    changed = {**NEW, 'PORTAL_RESIDENT_ID': 'resident_999999999999'}
    assert instance._signature({**params, 'resident_environment': changed}, 'test-model') != original
    for old in (OLD, DUAL):
        with pytest.raises(ValueError):
            instance._signature({**params, 'resident_environment': old}, 'test-model')


def test_fork_still_excludes_database_from_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-only')
    monkeypatch.setenv('PATH', '/test/bin')
    child = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"session_id":"fork-test"}', b'')))
    launch = AsyncMock(return_value=child)
    monkeypatch.setattr(daemon.asyncio, 'create_subprocess_exec', launch)
    monkeypatch.setattr(daemon.ClaudeCodeDaemon, '_relocate_forked_session_file', Mock())
    assert asyncio.run(daemon.ClaudeCodeDaemon._fork_session_file('source', tmp_path, tmp_path, 'title')) == 'fork-test'
    env = launch.call_args.kwargs['env']
    assert 'PORTAL_DATABASE_URL' not in env
    assert env['PATH'] == '/test/bin'


@pytest.mark.parametrize('prefix', ['PORTAL_'])
def test_relay_reads_both_name_generations_with_same_numeric_constraints(monkeypatch, prefix):
    values = {'HOST': '127.0.0.2', 'PORT': '19001', 'ALLOWED_IPS': '127.0.0.0/8',
              'MAX_BYTES': '2048', 'MAX_CONCURRENT': '3'}
    for suffix, value in values.items():
        monkeypatch.setenv(prefix + 'AI_RELAY_' + suffix, value)
    args = relay._build_arg_parser().parse_args([])
    assert (args.host, args.port, args.allowed_ips, args.max_bytes, args.max_concurrent) == (
        '127.0.0.2', 19001, '127.0.0.0/8', 2048, 3)
    monkeypatch.setenv(prefix + 'AI_RELAY_PORT', 'invalid')
    with pytest.raises(ValueError):
        relay._build_arg_parser()


def test_relay_new_name_presence_wins_and_cli_overrides_environment(monkeypatch):
    monkeypatch.setenv('ARCHIVE_AI_RELAY_HOST', '127.0.0.2')
    monkeypatch.setenv('PORTAL_AI_RELAY_HOST', '')
    monkeypatch.setenv('ARCHIVE_AI_RELAY_PORT', '19001')
    monkeypatch.setenv('PORTAL_AI_RELAY_PORT', '19002')
    assert relay._build_arg_parser().parse_args([]).host == ''
    assert relay._build_arg_parser().parse_args([]).port == 19002
    assert relay._build_arg_parser().parse_args(['--port', '19003']).port == 19003


def test_relay_default_and_peer_filter_are_preserved():
    args = relay._build_arg_parser().parse_args([])
    assert args.host == relay.DEFAULT_HOST
    assert args.port == relay.DEFAULT_PORT
    assert args.max_bytes == relay.DEFAULT_MAX_BYTES
    assert args.max_concurrent == relay.DEFAULT_MAX_CONCURRENT
    networks = relay._parse_allowed_networks('127.0.0.0/8')
    assert relay._peer_allowed('127.0.0.1', networks)
    assert not relay._peer_allowed('192.0.2.1', networks)


@pytest.mark.parametrize('identity', [NEW, dict(reversed(list(NEW.items()))), {},
                                      {'PORTAL_RESIDENT_ID': '', 'PORTAL_LINEAGE_ID': ''}])
def test_shell_identity_fingerprint_uses_canonical_fields(identity):
    _, fingerprint_input = shell.resident_process_environment({'resident_environment': identity})
    assert fingerprint_input == shell.resident_identity_environment(identity)


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
