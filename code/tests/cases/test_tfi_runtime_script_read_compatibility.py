"""Script reader naming compatibility without restart, review writes or smoke work.

:design: decision_15aab18c0ab3
:importance: P2
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import dev_resident_lifecycle_smoke as smoke
from scripts import harness_lineage as harness
from scripts import portal_dev_restart_requests as restart
from scripts import portal_dev_source_reloader as reloader


REPO = Path(__file__).resolve().parents[2]
PREFIXES = ['archive.', 'tfi.']


def stored_payload(tmp_path, payload):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(payload))
    return path, (path.read_bytes(), path.stat().st_mtime_ns)


def assert_untouched(path, original):
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('version', ['v2', 'v99'])
def test_restart_request_reads_same_version_without_rewriting(tmp_path, prefix, version):
    payload = {'schema': prefix + 'portal-dev-restart-request.' + version,
               'request_id': 'request-fixture', 'revision': 7,
               'requested_at': '2026-10-19T00:00:00Z',
               'components': ['web', 'execution'], 'auto': False}
    path, original = stored_payload(tmp_path, payload)
    result = restart.read_restart_request(path)
    if version == 'v2' and prefix == 'tfi.':
        assert result['request_id'] == payload['request_id']
        assert result['revision'] == 7
        assert result['components'] == ['execution', 'web']
        assert result['auto'] is False
        assert result['schema'] == restart.REQUEST_SCHEMA == 'tfi.portal-dev-restart-request.v2'
    else:
        assert result == {}
    assert_untouched(path, original)


@pytest.mark.parametrize('prefix', PREFIXES)
def test_restart_request_still_rejects_invalid_revision(tmp_path, prefix):
    path, original = stored_payload(tmp_path, {
        'schema': prefix + 'portal-dev-restart-request.v2',
        'request_id': 'request-fixture', 'revision': 0, 'components': ['web'],
    })
    assert restart.read_restart_request(path) == {}
    assert_untouched(path, original)


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('version', ['v1', 'v99'])
def test_deployed_state_reads_same_version_without_rewriting(tmp_path, prefix, version):
    current = {name: (('current.py', 100, 200),) for name in reloader.ALL_COMPONENTS}
    stored = {name: (('deployed.py', 10, 20),) for name in reloader.ALL_COMPONENTS}
    path, original = stored_payload(tmp_path, {
        'schema': prefix + 'portal-deployed-component-source-state.' + version,
        'components': stored,
    })
    assert reloader.read_deployed_source_states(current, path) == (stored if version == 'v1' and prefix == 'tfi.' else current)
    assert reloader.DEPLOYED_SOURCE_STATE_SCHEMA == 'tfi.portal-deployed-component-source-state.v1'
    assert_untouched(path, original)


@pytest.mark.parametrize('prefix', PREFIXES)
def test_deployed_state_still_rejects_incomplete_components(tmp_path, prefix):
    current = {name: (('current.py', 100, 200),) for name in reloader.ALL_COMPONENTS}
    path, original = stored_payload(tmp_path, {
        'schema': prefix + 'portal-deployed-component-source-state.v1', 'components': {},
    })
    assert reloader.read_deployed_source_states(current, path) == current
    assert_untouched(path, original)


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('invalid', ['', 'version', 'batch', 'score'])
def test_harness_reviews_preserve_identity_and_validation(prefix, invalid):
    batch = {'batch_id': 'batch-fixture', 'samples': [{'execution_id': 'execution-fixture'}]}
    results = {'schema': prefix + 'harness-review-results.v1', 'batch_id': 'batch-fixture',
               'reviews': [{'execution_id': 'execution-fixture', 'result': 'pass',
                            'reason': 'synthetic fixture', 'score': 0.5}]}
    if invalid == 'version':
        results['schema'] = prefix + 'harness-review-results.v99'
    elif invalid == 'batch':
        results['batch_id'] = 'other-batch'
    elif invalid == 'score':
        results['reviews'][0]['score'] = 2
    original = deepcopy((batch, results))
    if invalid or prefix != "tfi.":
        with pytest.raises(ValueError):
            harness.validated_reviews(batch, results)
    else:
        assert harness.validated_reviews(batch, results) is results['reviews']
    assert (batch, results) == original
    assert harness.RESULT_SCHEMA == 'tfi.harness-review-results.v1'


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('invalid', ['', 'version', 'service'])
def test_smoke_proof_reads_retained_request_without_business_or_writes(monkeypatch, prefix, invalid):
    request = {'schema': prefix + 'execution-request.v8', 'service_type': 'agent_session',
               'provider': 'codex', 'operation': 'turn',
               'system_instructions': {'content': '<portal-system-prompt role="resident">fixture</portal-system-prompt>'},
               'metadata': {'business_type': 'resident'}}
    if invalid == 'version':
        request['schema'] = prefix + 'execution-request.v99'
    elif invalid == 'service':
        request['service_type'] = 'generation'
    row = {'execution_id': 'execution-fixture', 'status': 'completed',
           'request_json': json.dumps(request), 'checkpoint_json': '{}',
           'result_json': '{"status":"completed"}'}
    original = deepcopy(row)
    queries, closed = [], []

    def connect(schema):
        def execute(sql, parameters):
            assert sql.lstrip().startswith('SELECT ')
            queries.append((schema, parameters))
            if schema == 'lineage':
                assert 'conversation_input_states' in sql
                value = {'completed': True}
            elif schema == 'agent':
                assert 'chat_message_positions reply' in sql
                value = {'execution_id': 'execution-fixture'}
            else:
                assert schema == 'execution_runtime' and 'FROM executions' in sql
                value = row
            return SimpleNamespace(fetchone=lambda: value)
        return SimpleNamespace(execute=execute, close=lambda: closed.append(schema))

    monkeypatch.setattr(smoke, '_read_connection', connect)
    if invalid or prefix != 'tfi.':
        with pytest.raises(AssertionError, match='wrong request contract' if invalid == 'version' or prefix != 'tfi.' else 'wrong execution service'):
            smoke.execution_proof(['message-fixture'])
    else:
        proof, = smoke.execution_proof(['message-fixture'])
        assert proof['request_schema'] == request['schema']
        assert proof['storage_state'] == 'terminal_request_retained'
        assert proof['operation'] == 'turn'
        assert closed == ['lineage', 'agent', 'execution_runtime']
    assert queries == [('lineage', ('message-fixture',)), ('agent', ('message-fixture',)),
                       ('execution_runtime', ('execution-fixture',))]
    assert row == original


@pytest.mark.parametrize('script', [
    'portal_dev_restart_requests.py', 'portal_dev_source_reloader.py', 'dev_resident_lifecycle_smoke.py',
])
def test_script_imports_outside_repository_without_pythonpath(tmp_path, script):
    command = [sys.executable, '-I', '-c',
               'import runpy, sys; runpy.run_path(sys.argv[1], run_name="read_only_import"); '
               'from management_common.format_tags import schema_matches; '
               'assert schema_matches("tfi.execution-request.v8", "tfi.execution-request.v8")',
               str(REPO / 'scripts' / script)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_harness_loads_helper_after_release_path_is_selected(tmp_path):
    import os

    # Copy the real helper dependency closure; no repository or host PYTHONPATH
    # may rescue the captured script. Only the RunScript host/writer is replaced.
    release = tmp_path / 'captured' / 'artifacts' / 'release'
    package = release / 'management_common'
    package.mkdir(parents=True)
    for name in ('__init__', 'format_tags', 'site_compatibility', 'site_config',
                 'portal_paths', 'product_environment', 'file_lock'):
        shutil.copyfile(REPO / f'management_common/{name}.py', package / f'{name}.py')
    captured = tmp_path / 'captured' / 'scripts' / 'harness_lineage.py'
    captured.parent.mkdir(parents=True)
    shutil.copyfile(REPO / 'scripts/harness_lineage.py', captured)
    site = tmp_path / 'site.json'
    envfile = tmp_path / 'database.env'
    envfile.write_text("export PORTAL_DATABASE_URL='fixture-never-connected'\n")
    payload = tmp_path / 'reviews.json'
    probe = '''
import json, os, pathlib, runpy, sys, types
forbidden = []
def no_business(event, args):
    if event in ('socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'):
        forbidden.append(event)
        raise AssertionError('no network or business process allowed')
    if event == 'import' and args[0].split('.')[0] in ('psycopg', 'management_portal'):
        forbidden.append(args[0])
        raise AssertionError('no database or business module allowed')
sys.addaudithook(no_business)
flow = runpy.run_path(sys.argv[1], run_name='read_only_import')
assert not any(name.startswith('management_common') for name in sys.modules)
main = flow['main']
scope = main.__globals__
release, payload = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
mode, expected = sys.argv[4], int(sys.argv[5])
batch = {'batch_id': 'fixture', 'samples': [{'execution_id': 'e'}]}
results = json.loads(payload.read_text())
original = json.dumps(results, sort_keys=True)
outputs, calls = [], []
def writer(identifier):
    calls.append(identifier)
    assert identifier == 'fixture'
    assert os.environ['PORTAL_DATABASE_URL'] == 'fixture-never-connected'
    assert scope['_deployment_path']('harness_state_dir') == release / 'review-state'
    with scope['locked'](release / 'reader-probe.lock'):
        reviews = scope['validated_reviews'](batch, results)
    return {'reviews': reviews}
scope['record_batch'] = writer
sys.modules['portal_script'] = types.SimpleNamespace(
    inputs=lambda: {'lineage_id': 'lineage_222222222222' if mode == 'foreign' else 'lineage_111111111111',
                    'arguments': {'prompt': 'fixture'}},
    set_output=outputs.append)
sys.argv = [sys.argv[1], 'record']
assert main() == expected
assert json.dumps(results, sort_keys=True) == original
if expected == 0:
    assert outputs == [{'operation': 'record', 'reviews': results['reviews']}]
    assert sys.path[0] == str(release)
else:
    assert outputs == []
if mode in ('canonical', 'legacy', 'relative_release', 'version', 'unconfigured_namespace'):
    assert calls == ['fixture']
    for name in ('format_tags', 'site_compatibility', 'site_config', 'portal_paths', 'file_lock'):
        module = sys.modules['management_common.' + name]
        assert pathlib.Path(module.__file__).resolve() == release / 'management_common' / (name + '.py')
else:
    assert calls == []
assert flow['RESULT_SCHEMA'] == 'tfi.harness-review-results.v1'
assert forbidden == []
print(json.dumps({'scenario': mode, 'main_rc': expected, 'writer_called': bool(calls)}))
'''
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PORTAL_', 'ARCHIVE_', 'PYTHON'))}
    env['PORTAL_SITE_CONFIG'] = str(site)
    for mode in ('canonical', 'legacy', 'relative_release', 'version', 'unconfigured_namespace',
                 'foreign', 'invalid_lineage', 'missing_release', 'invalid_path', 'missing_database'):
        settings = {'harness_recording_lineage_id': 'lineage_111111111111',
                    'harness_release_tree': str(release), 'harness_state_dir': 'review-state',
                    'harness_database_env_file': str(envfile)}
        if mode == 'invalid_lineage':
            settings['harness_recording_lineage_id'] = 'invalid'
        elif mode == 'missing_release':
            del settings['harness_release_tree']
        elif mode == 'invalid_path':
            settings['harness_release_tree'] = 'bad\npath'
        elif mode == 'missing_database':
            del settings['harness_database_env_file']
        elif mode == 'relative_release':
            settings['harness_release_tree'] = 'artifacts/release'
        site.write_text(json.dumps({'deployment': {'system_workflow': settings,
                        'compatibility': {'format_namespaces': [] if mode == 'unconfigured_namespace' else ['archive.']}}}))
        tag = 'tfi.' if mode in ('canonical', 'relative_release') else 'archive.'
        payload.write_text(json.dumps({'schema': tag + 'harness-review-results.' + ('v99' if mode == 'version' else 'v1'),
            'batch_id': 'fixture', 'reviews': [{'execution_id': 'e', 'result': 'pass', 'reason': 'fixture'}]}))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (site, envfile, payload)}
        expected = 0 if mode in ('canonical', 'relative_release') else 1
        command = [sys.executable, '-I', '-c', probe, str(captured), str(release), str(payload), mode, str(expected)]
        result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, (mode, result.stdout, result.stderr)
        assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
        print(result.stdout, end='')


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
