"""Detached RunScript must retain explicit site paths without service secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from management_portal.society_service.script_runs import runtime, service


@pytest.fixture
def launch_case(tmp_path, monkeypatch):
    paths = {key: str(tmp_path / ('data ' + key)) for key in service.portal_paths.KEYS}
    paths_file = tmp_path / 'paths with spaces.json'
    paths_file.write_text(json.dumps(paths))
    site_file = tmp_path / 'site with spaces.json'
    site_file.write_text(json.dumps({'deployment': {'compatibility': {'domain_values': {
        'script_run_private_root': str(tmp_path / 'private'),
    }}}}))
    names = {'PORTAL_SITE_CONFIG': str(site_file), 'PORTAL_PATHS_CONFIG': str(paths_file),
             'PORTAL_RELEASE_ROOT': str(tmp_path / 'release'),
             **{value: paths[key] for key, value in service.portal_paths.ENV_KEYS.items()}}
    for key, value in names.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'postgresql://fixture-secret')
    monkeypatch.setenv('PORTAL_AGENT_TOKEN', 'fixture-secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-secret')
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path / 'runs')
    monkeypatch.setattr(runtime, 'PRIVATE_ROOT', tmp_path / 'private')
    monkeypatch.setattr(service, '_issue_token', lambda *args: 'fixture-lease')
    payload = tmp_path / 'probe.py'
    payload.write_text("""import json, os
from pathlib import Path
from management_common import portal_paths
from management_portal.society_service.script_runs import runtime
out = {'paths': {k: str(v) for k, v in portal_paths.paths().items()},
       'private_root': str(runtime.PRIVATE_ROOT),
       'secret_keys': [k for k in ('PORTAL_DATABASE_URL', 'PORTAL_AGENT_TOKEN', 'OPENAI_API_KEY') if k in os.environ]}
Path(os.environ['PORTAL_SCRIPT_OUTPUT']).write_text(json.dumps(out))
""")
    record = {'script_run_id': 'script_run_environment_fixture', 'unit': 'portal-script-fixture',
              'arguments': {}, 'caller_id': 'resident_fixture', 'lineage_id': 'lineage_fixture',
              'trigger_path_id': '', 'effect_id': ''}
    config = {'path': str(payload), 'cwd': str(tmp_path), 'runtime': 'python',
              'argv': [], 'timeout_seconds': 10}
    return record, config, names, paths


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_detached_launch_carries_only_site_path_configuration(platform, launch_case, monkeypatch):
    record, config, names, _ = launch_case
    captured = {}
    monkeypatch.setattr(service.sys, 'platform', platform)
    def run(command, **kwargs):
        captured['command'] = command
        return subprocess.CompletedProcess(command, 0, '', '')
    def job(unit, command, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(service.process_runtime, 'run_process', run)
    monkeypatch.setattr(service, 'launch_job', job)
    service.launch(record, config)
    if platform == 'linux':
        environment = dict(arg[len('--setenv='):].split('=', 1)
                           for arg in captured['command'] if arg.startswith('--setenv='))
        assert '--property=RuntimeMaxSec=20' in captured['command']
    else:
        environment = captured['environment']
        assert captured['inherit_environment'] is False
    assert {key: environment.get(key) for key in names} == names
    assert not {'PORTAL_DATABASE_URL', 'PORTAL_AGENT_TOKEN', 'OPENAI_API_KEY'} & environment.keys()


@pytest.mark.skipif(sys.platform != 'linux', reason='isolated Linux runner')
def test_runner_and_payload_read_the_launching_site(launch_case, monkeypatch, tmp_path):
    record, config, names, paths = launch_case
    captured = {}
    def capture(command, **kwargs):
        captured['command'] = command
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(service.process_runtime, 'run_process', capture)
    service.launch(record, config)
    command = captured['command']
    environment = {'PATH': os.defpath, 'HOME': str(tmp_path), 'LANG': 'C.UTF-8',
                   'PYTHONPATH': str(runtime.REPOSITORY), 'PYTHONDONTWRITEBYTECODE': '1'}
    environment.update(arg[len('--setenv='):].split('=', 1)
                       for arg in command if arg.startswith('--setenv='))
    # Execute the actual runner argv with a clean manager environment. No unit
    # registration or live Portal request is needed for this harmless payload.
    child = subprocess.run(command[command.index(sys.executable):],
                           cwd=runtime.REPOSITORY, env=environment,
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    run_dir = runtime.RUN_ROOT / record['script_run_id']
    result = json.loads((run_dir / 'result.json').read_text())
    assert result['status'] == 'succeeded', result
    output = json.loads((run_dir / 'io/output.json').read_text())
    assert output == {'paths': paths, 'private_root': str(tmp_path / 'private'), 'secret_keys': []}
    assert not (runtime.PRIVATE_ROOT / (record['script_run_id'] + '.json')).exists()


def test_absent_optional_configuration_is_not_invented(launch_case, monkeypatch):
    record, config, names, _ = launch_case
    monkeypatch.setattr(service.sys, 'platform', 'linux')
    for name in names:
        monkeypatch.delenv(name)
    captured = {}
    def capture(command, **kwargs):
        captured['command'] = command
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(service.process_runtime, 'run_process', capture)
    service.launch(record, config)
    assert not any(arg.startswith('--setenv=') for arg in captured['command'])
