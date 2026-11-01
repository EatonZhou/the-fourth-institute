"""Code repair rebuilds engineering bytes with new code and the same frozen data."""
import contextlib
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from management_portal.distribution import engineering, service
from scripts import generate_tfi_distribution as cli
from scripts import tfi_release_install as installer


@pytest.fixture
def repair(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'scripts/install_tfi_release',
                 'scripts/tfi_release_install.py'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# fixture\n')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    # Core skill selection has independent tests; do not read live skill trees.
    monkeypatch.setattr('management_portal.distribution.core_skills.export_core_skills', lambda *a, **k: {})
    snapshot = {'source': str(source), 'destination': str(tmp_path / 'exports')}
    previous_root = tmp_path / 'old'
    previous = engineering.generate(source_root=source, destination=previous_root, snapshot=snapshot,
        resolved={'generated_files': {'data/projects/demo/Documents/current.txt': '原始资料\n'}},
        database_sql='-- exact frozen SQL\nSELECT 1;\n', run_id='repair-run')
    path = previous_root / 'manifest.json'
    origin = engineering.checksum(path)
    state = {'run_id': 'repair-run', 'snapshot': snapshot, 'state': 'checking', 'revision': 1,
             'result': {}, 'supervisor_resident_id': 'resident_fixture'}
    reports = []

    def report(run_id, **kwargs):
        reports.append(copy.deepcopy(kwargs))
        state.update(state=kwargs['state'], revision=state['revision'] + 1, result=kwargs['evidence'])
        return copy.deepcopy(state)

    store = SimpleNamespace(get_run=lambda _: copy.deepcopy(state), report=report)
    monkeypatch.setattr(service, 'ReleaseStore', lambda: store)
    monkeypatch.setattr('management_portal.distribution.store.ReleaseStore', lambda: store)
    monkeypatch.setattr(service, 'store_database_run_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(service, 'source_root', lambda _: source)
    return SimpleNamespace(source=source, path=path, previous=previous, origin=origin, reports=reports,
        args={'data_manifest': path, 'expected_manifest_sha256': origin, 'attempt': 2}, tmp=tmp_path)


def test_cli_code_repair_preserves_sql_and_data_and_meets_installer_contract(repair, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['generate_tfi_distribution', 'generate', '--run-id', 'repair-run',
        '--attempt', '2', '--data-manifest', str(repair.path), '--data-manifest-sha256', repair.origin])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    new_path = Path(result['result']['manifest'])
    new = json.loads(new_path.read_text())
    assert 'upgrade' not in new
    assert (new_path.parent / new['database']['path']).read_bytes() == (repair.path.parent / repair.previous['database']['path']).read_bytes()
    assert [r for r in new['inventory'] if r['path'].startswith('data/')] == [r for r in repair.previous['inventory'] if r['path'].startswith('data/')]
    assert engineering.checksum(repair.path) == repair.origin
    manifest, bundle = installer.validate_engineering(new_path)
    assert manifest['files'] == {'projects': 'data/projects'} and bundle == new_path.parent


def test_code_repair_requires_a_new_attempt_and_the_reviewed_manifest(repair):
    with pytest.raises(ValueError, match='new attempt'):
        service.rebuild_code_run('repair-run', **{**repair.args, 'attempt': 1})
    with pytest.raises(ValueError, match='digest changed'):
        service.rebuild_code_run('repair-run', **{**repair.args, 'expected_manifest_sha256': 'f' * 64})
    assert repair.reports == []


def test_code_repair_refreshes_code_owned_core_rules(repair, monkeypatch):
    relative = 'code/core_skills/tfi-agent-rule/SKILL.md'
    path = repair.path.parent / relative
    path.parent.mkdir(parents=True)
    path.write_text('old rules\n')
    repair.previous['core_skills'] = {'files': [{'path': relative, 'sha256': engineering.checksum(path), 'size': path.stat().st_size}], 'roles': ['general']}
    repair.previous['inventory'] = engineering.inventory(repair.path.parent)
    repair.path.write_text(json.dumps(repair.previous))
    repair.args['expected_manifest_sha256'] = engineering.checksum(repair.path)

    def export(destination, **kwargs):
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('current code rules\n')
        return {'files': [{'path': relative, 'sha256': engineering.checksum(output), 'size': output.stat().st_size}], 'roles': ['general']}

    monkeypatch.setattr('management_portal.distribution.core_skills.export_core_skills', export)
    result = service.rebuild_code_run('repair-run', **repair.args)
    new_path = Path(result['result']['manifest'])
    manifest = json.loads(new_path.read_text())
    assert manifest['core_skills'] != repair.previous['core_skills']
    assert (new_path.parent / relative).read_text() == 'current code rules\n'


@pytest.mark.parametrize('kind', ['database', 'data'])
def test_repair_rejects_generated_sql_or_data_drift(repair, monkeypatch, kind):
    generate = engineering.generate

    def drifting(**kwargs):
        manifest = generate(**kwargs)
        if kind == 'database':
            manifest['database']['sha256'] = 'f' * 64
        else:
            next(r for r in manifest['inventory'] if r['path'].startswith('data/'))['sha256'] = 'f' * 64
        return manifest
    monkeypatch.setattr(engineering, 'generate', drifting)
    with pytest.raises(ValueError, match='reviewed data bytes'):
        service.rebuild_code_run('repair-run', **repair.args)
    assert repair.reports[-1]['state'] == 'failed'


@pytest.mark.parametrize('include_test', [False, True])
def test_distribution_keeps_selected_test_catalog_and_fixtures(repair, include_test):
    """打包 Test 时保留可执行用例和夹具，且不带旧 UI 套件与阶段运行历史"""
    contents = {
        'tests/__init__.py': '',
        'tests/cases/conftest.py': '# isolated fixture\n',
        'tests/cases/test_sample.py': 'def test_sample(): assert True\n',
        'tests/cases/tau_cases.json': '{"cases": []}\n',
        'tests/cases/fixtures/sample.json': '{"synthetic": true}\n',
        'tests/ui/deleted.spec.js': '// deleted\n',
        '.wall/history.json': '{"private_history": true}\n',
    }
    for relative, content in contents.items():
        path = repair.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(['git', '-C', str(repair.source), 'add', '.'], check=True)
    destination = repair.tmp / 'test-export'
    engineering.generate(source_root=repair.source, destination=destination, snapshot={},
        resolved={'features': ['test_framework'] if include_test else []}, database_sql='SELECT 1;\n')
    for relative, content in contents.items():
        path = destination / 'code' / relative
        expected = include_test and (relative == 'tests/__init__.py' or relative.startswith('tests/cases/'))
        assert path.exists() == expected, relative
        if expected:
            if path.suffix == '.json':
                assert json.loads(path.read_text()) == json.loads(content)
            else:
                assert path.read_text() == content
