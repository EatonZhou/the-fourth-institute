"""Code repair uses real engineering bytes and the existing upgrade preflight."""
import contextlib
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.distribution import engineering, service
from scripts import generate_tfi_distribution as cli
from scripts import tfi_release_install as installer, tfi_release_upgrade as upgrade


@pytest.fixture
def repair(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'scripts/install_tfi_release',
                 'scripts/tfi_release_install.py', 'scripts/tfi_release_upgrade.py'):
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
        resolved={'generated_files': {'data/share_root/Documents/current.txt': '原始资料\n'}},
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
        args={'data_manifest': path, 'expected_manifest_sha256': origin, 'attempt': 2},
        declaration={'from_manifest_sha256': [origin], 'migrations': []}, tmp=tmp_path)


def consumer(repair, manifest_path, origin=None):
    root = repair.tmp / 'installed'
    (root / 'runtime/pg').mkdir(parents=True, exist_ok=True)
    (root / 'runtime/pg/PG_VERSION').write_text('16')
    api = SimpleNamespace(read_json=installer.read_json, digest=installer.digest,
        engineering_path=installer.engineering_path, validate_engineering=installer.validate_engineering,
        platform_preflight=lambda _: {}, dependency_preflight=Mock(return_value=[
            {'dependency': 'PostgreSQL', 'version': 'PostgreSQL 16.1'}]))
    target = {'run_id': 'repair-run', 'supervisor_resident_id': 'resident_fixture', 'install_root': str(root)}
    return upgrade.preflight(api, root, {'target': target, 'manifest_sha256': origin or repair.origin}, manifest_path)


@pytest.mark.parametrize('migration', [False, True])
def test_cli_to_manifest_to_upgrade_preserves_sql_and_data(repair, monkeypatch, capsys, migration):
    declaration = repair.declaration
    if migration:
        sql = repair.source / 'migrations/compatible.sql'
        sql.parent.mkdir()
        sql.write_text('-- explicit dedicated migration\nSELECT 1;\n')
        declaration['migrations'] = [{'id': 'compatible-v1', 'path': 'code/migrations/compatible.sql',
                                      'sha256': engineering.checksum(sql)}]
    declaration_path = repair.tmp / 'upgrade.json'
    declaration_path.write_text(json.dumps(declaration))
    monkeypatch.setattr(sys, 'argv', ['generate_tfi_distribution', 'generate', '--run-id', 'repair-run',
        '--attempt', '2', '--data-manifest', str(repair.path), '--data-manifest-sha256', repair.origin,
        '--upgrade-declaration', str(declaration_path)])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    new_path = Path(result['result']['manifest'])
    new = json.loads(new_path.read_text())
    assert new['upgrade'] == declaration
    assert (new_path.parent / new['database']['path']).read_bytes() == (repair.path.parent / repair.previous['database']['path']).read_bytes()
    assert [r for r in new['inventory'] if r['path'].startswith('data/')] == [r for r in repair.previous['inventory'] if r['path'].startswith('data/')]
    assert engineering.checksum(repair.path) == repair.origin
    assert consumer(repair, new_path)[3] == declaration['migrations']
    with pytest.raises(ValueError, match='supported upgrade origin'):
        consumer(repair, new_path, 'f' * 64)


@pytest.mark.parametrize('invalid', [None, {}, {'migrations': []},
    {'from_manifest_sha256': ['f' * 64], 'migrations': []},
    {'from_manifest_sha256': ['f' * 64]},
    {'from_manifest_sha256': ['f' * 64], 'migrations': 'infer'}])
def test_invalid_declaration_rejected_before_run_mutation(repair, invalid):
    with pytest.raises(ValueError):
        service.rebuild_code_run('repair-run', **repair.args, upgrade_declaration=invalid)
    assert repair.reports == []
    assert not (repair.tmp / 'exports').exists()


@pytest.mark.parametrize('change', [
    {'id': 'space forbidden'}, {'id': 'x' * 121}, {'path': '../outside.sql'},
    {'path': 'database/distribution.sql'}, {'path': 'data/share_root/migration.sql'},
    {'sha256': '0' * 64}, {'path': ['bad']}, {'id': 42}])
def test_illegal_migration_rejected_before_run_mutation(repair, change):
    sql = repair.source / 'migration.sql'
    sql.write_text('SELECT 1;\n')
    item = {'id': 'valid', 'path': 'code/migration.sql', 'sha256': engineering.checksum(sql), **change}
    repair.declaration['migrations'] = [item]
    with pytest.raises(ValueError):
        service.rebuild_code_run('repair-run', **repair.args, upgrade_declaration=repair.declaration)
    assert repair.reports == []


def test_omitted_declaration_keeps_nonupgrade_behavior(repair):
    result = service.rebuild_code_run('repair-run', **repair.args)
    path = Path(result['result']['manifest'])
    assert 'upgrade' not in json.loads(path.read_text())
    with pytest.raises(ValueError, match='supported upgrade origin'):
        consumer(repair, path)


def test_code_repair_keeps_frozen_core_rules_when_source_rules_change(repair, monkeypatch):
    relative = 'data/share_root/Skills/tfi-agent-rule/SKILL.md'
    path = repair.path.parent / relative
    path.parent.mkdir(parents=True)
    path.write_text('immutable original rules\n')
    repair.previous['core_skills'] = {'files': [{'path': relative, 'sha256': engineering.checksum(path), 'size': path.stat().st_size}], 'roles': ['general']}
    repair.previous['inventory'] = engineering.inventory(repair.path.parent)
    repair.path.write_text(json.dumps(repair.previous))
    repair.args['expected_manifest_sha256'] = engineering.checksum(repair.path)
    monkeypatch.setattr('management_portal.distribution.core_skills.export_core_skills',
                        lambda *a, **k: pytest.fail('Code repair read mutable source rules'))
    result = service.rebuild_code_run('repair-run', **repair.args)
    new_path = Path(result['result']['manifest'])
    manifest = json.loads(new_path.read_text())
    assert manifest['core_skills'] == repair.previous['core_skills']
    assert (new_path.parent / relative).read_bytes() == path.read_bytes()


def test_existing_declaration_is_inherited_when_omitted(repair):
    repair.previous['upgrade'] = {'from_manifest_sha256': ['a' * 64], 'migrations': []}
    repair.path.write_text(json.dumps(repair.previous))
    repair.args['expected_manifest_sha256'] = engineering.checksum(repair.path)
    result = service.rebuild_code_run('repair-run', **repair.args)
    assert json.loads(Path(result['result']['manifest']).read_text())['upgrade'] == repair.previous['upgrade']


@pytest.mark.parametrize('kind', ['missing_migrations', 'extra_origin', 'duplicate_migration', 'outside_symlink'])
def test_explicit_contract_does_not_infer_or_expand_compatibility(repair, kind):
    declaration = repair.declaration
    if kind == 'missing_migrations':
        del declaration['migrations']
    elif kind == 'extra_origin':
        declaration['from_manifest_sha256'].append('f' * 64)
    else:
        sql = repair.source / 'migration.sql'
        if kind == 'outside_symlink':
            outside = repair.tmp / 'outside.sql'
            outside.write_text('SELECT 1;\n')
            sql.symlink_to(outside)
        else:
            sql.write_text('SELECT 1;\n')
        item = {'id': 'v1', 'path': 'code/migration.sql', 'sha256': engineering.checksum(sql)}
        declaration['migrations'] = [item] if kind == 'outside_symlink' else [item, item]
    with pytest.raises(ValueError):
        service.rebuild_code_run('repair-run', **repair.args, upgrade_declaration=declaration)
    assert repair.reports == []


def test_generator_rechecks_actual_migration_bytes_after_copy(repair, monkeypatch):
    sql = repair.source / 'migration.sql'
    sql.write_text('SELECT 1;\n')
    repair.declaration['migrations'] = [{'id': 'v1', 'path': 'code/migration.sql', 'sha256': engineering.checksum(sql)}]
    original = engineering.copy_content
    def changed_copy(source, target, **kwargs):
        original(source, target, **kwargs)
        if Path(source) == sql:
            Path(target).write_text('SELECT 2;\n')
    monkeypatch.setattr(engineering, 'copy_content', changed_copy)
    with pytest.raises(ValueError, match='digest must match'):
        service.rebuild_code_run('repair-run', **repair.args, upgrade_declaration=repair.declaration)
    assert repair.reports[-1]['state'] == 'failed'
    destination = Path(repair.reports[-1]['evidence']['directory'])
    assert (destination / 'GENERATION_FAILED').exists()
    assert not (destination / 'manifest.json').exists()


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
        service.rebuild_code_run('repair-run', **repair.args, upgrade_declaration=repair.declaration)
    assert repair.reports[-1]['state'] == 'failed'


def test_cli_rejects_declaration_outside_code_repair(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['generate_tfi_distribution', 'generate', '--run-id', 'r',
                                    '--upgrade-declaration', 'unused.json'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('include_test', [False, True])
def test_distribution_keeps_selected_test_catalog_and_fixtures(repair, include_test):
    """打包 Test 时保留可执行用例和夹具，且不带旧 UI 套件与阶段运行历史"""
    contents = {
        'tests/__init__.py': '',
        'tests/cases/conftest.py': '# isolated fixture\n',
        'tests/cases/test_sample.py': 'def test_sample(): assert True\n',
        'tests/cases/tau_cases.json': '{"cases": []}\n',
        'tests/cases/fixtures/sample.json': '{"synthetic": true}\n',
        'tests/ui/retired.spec.js': '// retired\n',
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
