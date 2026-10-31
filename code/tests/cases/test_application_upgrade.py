"""Offline application backup and transaction boundaries, without host services."""
import json
from pathlib import Path
from unittest.mock import Mock
from contextlib import nullcontext
import pytest

from scripts import tfi_application_snapshot as snapshot
from scripts import tfi_application_upgrade as upgrade
from scripts import tfi_release_install as installer


@pytest.fixture
def application(tmp_path):
    app = tmp_path / 'Portal'
    root = app / 'instance/one'
    for name in ('code', 'dependencies/python-wheels', 'instance/one/config', 'instance/one/data/database',
                 'instance/one/data/accounts', 'instance/one/data/share'):
        (app / name).mkdir(parents=True, exist_ok=True)
    (app / 'code/version').write_text('original')
    (app / 'dependencies/python-wheels/requirements.lock').write_text('old')
    (root / 'data/database/PG_VERSION').write_text('16')
    (root / 'data/database/business').write_text('local rows')
    (root / 'data/accounts/sentinel').write_text('private fixture')
    (root / 'data/share/content').write_text('local workspace')
    (root / 'data/share/alias').symlink_to('content-directory', target_is_directory=True)
    (root / 'data/share/content-directory').mkdir()
    state = {'target': {'install_root': str(root), 'shared_code_root': str(app / 'code'),
        'service_prefix': 'tfi-one', 'windows_service_mode': 'user', 'data_roots': {
            'share_root': str(root / 'data/share'),
            'instances_root': str(root / 'data/instances')}},
        'manifest_sha256': 'a' * 64, 'status': 'installed', 'completed': ['database']}
    installer.write_json(root / 'installation.json', state)
    return app, state


def test_snapshot_rebases_internal_links_and_restores_local_bytes(application, tmp_path):
    app, state = application
    backup = tmp_path / 'backup'
    expected = snapshot.copy_tree(app, backup)
    link = backup / 'instance/one/data/share/alias'
    assert link.resolve().is_relative_to(backup)
    (app / 'code/version').write_text('new')
    (app / 'instance/one/data/database/business').write_text('migrated')
    (app / 'unexpected').write_text('failed generation')
    snapshot.restore(backup, app, expected)
    assert snapshot.inventory(app) == expected
    assert (app / 'instance/one/data/accounts/sentinel').read_text() == 'private fixture'
    assert (app / 'instance/one/data/share/alias').resolve().is_relative_to(app)


def test_external_links_and_backup_corruption_are_rejected(application, tmp_path):
    app, _ = application
    (app / 'external').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='External'):
        snapshot.copy_tree(app, tmp_path / 'unsafe')
    (app / 'external').unlink()
    expected = snapshot.copy_tree(app, tmp_path / 'good')
    (tmp_path / 'good/code/version').write_text('corrupt')
    with pytest.raises(ValueError, match='integrity'):
        snapshot.restore(tmp_path / 'good', app, expected)
    assert (app / 'code/version').read_text() == 'original'


@pytest.fixture
def transaction(application, tmp_path, monkeypatch):
    app, state = application
    owner = upgrade.ApplicationUpgrade(installer, app)
    bundle = tmp_path / 'bundle'
    (bundle / 'code').mkdir(parents=True)
    (bundle / 'code/version').write_text('new')
    (bundle / 'wheels').mkdir()
    (bundle / 'wheels/requirements.lock').write_text('new')
    manifest = {'run_id': 'new-run', 'code': {'path': 'code'}, 'upgrade': {'application': {
        'wheelhouse': 'wheels', 'package': {'source_commit': 'b' * 40}}}}
    path = bundle / 'manifest.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(owner, 'preflight', lambda _: ([state], manifest, {}))
    monkeypatch.setattr(owner, 'stop', Mock())
    monkeypatch.setattr(owner, 'start', Mock())
    monkeypatch.setattr(owner, 'schema', Mock())
    monkeypatch.setattr(owner, 'detach_codex', Mock(return_value=[]))
    monkeypatch.setattr(installer, 'prepare_dependencies', Mock(side_effect=lambda state, manifest:
        installer.save_stage(state, 'dependencies', 'prepared')))
    monkeypatch.setattr(installer, 'service_controller', lambda: Mock())
    monkeypatch.setattr(installer, 'wait_database', Mock())
    from scripts import tfi_application_work as work
    monkeypatch.setattr(work, 'inspect', Mock(return_value={'retained': 'stable'}))
    monkeypatch.setattr(upgrade.barrier, 'freeze', lambda *_: nullcontext([Mock()]))
    monkeypatch.setattr(upgrade.barrier, 'assert_no_clients', Mock())
    monkeypatch.setattr(owner, 'verify_work_in_runtime',
                        lambda records, expected: upgrade.barrier.verify(installer, records, expected))
    from scripts import tfi_application_admission as admission
    monkeypatch.setattr(admission, 'prepare', Mock(return_value=[{'test_gate': True}]))
    monkeypatch.setattr(admission, 'close', Mock())
    monkeypatch.setattr(admission, 'restore', Mock())
    monkeypatch.setattr(owner, 'restore_admission_in_runtime', Mock())
    return owner, path, state


def test_migration_failure_restores_pair_and_preserves_failed_generation(transaction):
    owner, path, state = transaction
    business = owner.application / 'instance/one/data/database/business'
    def fail(*args, **kwargs):
        business.write_text('partly migrated rows')
        raise ValueError('injected migration failure')
    owner.schema.side_effect = fail
    with pytest.raises(ValueError, match='injected'):
        owner.upgrade(path)
    assert business.read_text() == 'local rows'
    assert (owner.application / 'code/version').read_text() == 'original'
    assert upgrade.states(owner.application)[0]['manifest_sha256'] == 'a' * 64
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'restored'
    failed = Path(journal['failed_generation'])
    inventory = json.loads((failed / 'inventory.json').read_text())
    assert snapshot.backup_bytes(failed, 'instance/one/data/database/business', inventory) == b'partly migrated rows'


def test_success_keeps_identity_and_credentials_without_importing_seed(transaction):
    owner, path, state = transaction
    result = owner.upgrade(path)
    assert result['status'] == 'installed'
    current = upgrade.states(owner.application)[0]
    assert current['target'] == state['target']
    assert current['version_run_id'] == 'new-run'
    assert current['status'] == 'installed'
    assert current['stages'][-1]['phase'] == 'application_upgrade_health'
    assert (owner.application / 'instance/one/data/database/business').read_text() == 'local rows'
    assert (owner.application / 'instance/one/data/accounts/sentinel').read_text() == 'private fixture'
    assert (owner.application / 'code/version').read_text() == 'new'
    assert not upgrade.pending(owner.application)


def test_late_work_blocks_before_stop_or_snapshot(transaction, monkeypatch):
    owner, path, _ = transaction
    from scripts import tfi_application_work as work
    monkeypatch.setattr(work, 'inspect', Mock(side_effect=RuntimeError('active work')))
    with pytest.raises(RuntimeError, match='active work'):
        owner.upgrade(path)
    owner.stop.assert_not_called()
    assert not owner.journal_path.exists()


def test_retained_work_mismatch_rolls_back_before_admission(transaction, monkeypatch):
    owner, path, _ = transaction
    from scripts import tfi_application_work as work
    monkeypatch.setattr(work, 'inspect', Mock(side_effect=[
        {'retained': 'original'}, {'retained': 'original'},
        {'retained': 'original'}, {'retained': 'changed'}, {'retained': 'original'}]))
    with pytest.raises(ValueError, match='Retained waiting work changed'):
        owner.upgrade(path)
    assert (owner.application / 'code/version').read_text() == 'original'
    assert installer.read_json(owner.journal_path)['phase'] == 'restored'
    owner.start.assert_called_once()


def test_admitted_start_failure_recovers_forward_without_replaying_backup(transaction):
    owner, path, _ = transaction
    business = owner.application / 'instance/one/data/database/business'
    def started_then_failed(records):
        business.write_text('waiting input progressed after admission')
        raise RuntimeError('startup health failed')
    owner.start.side_effect = started_then_failed
    with pytest.raises(RuntimeError, match='startup health failed'):
        owner.upgrade(path)
    assert installer.read_json(owner.journal_path)['phase'] == 'resuming'
    assert upgrade.states(owner.application)[0]['status'] == 'prepared'
    assert (owner.application / 'code/version').read_text() == 'new'
    owner.start.side_effect = None
    assert owner.recover()['status'] == 'complete'
    assert upgrade.states(owner.application)[0]['status'] == 'installed'
    assert business.read_text() == 'waiting input progressed after admission'
    assert not upgrade.pending(owner.application)


@pytest.mark.parametrize('failed_write', ['installation', 'complete_journal'])
def test_completion_write_failure_recovers_forward_without_restoring_data(transaction, monkeypatch, failed_write):
    owner, path, _ = transaction
    business = owner.application / 'instance/one/data/database/business'
    owner.start.side_effect = lambda records: business.write_text('new admitted work')
    save_stage = installer.save_stage
    write_journal = upgrade.atomic_write_private_bytes

    def fail_state(state, phase, status, **details):
        if phase == 'application_upgrade_health':
            raise OSError('completion write failed')
        save_stage(state, phase, status, **details)

    def fail_journal(path, content):
        if path == owner.journal_path and json.loads(content)['phase'] == 'complete':
            raise OSError('completion write failed')
        write_journal(path, content)

    monkeypatch.setattr(installer, 'save_stage', fail_state if failed_write == 'installation' else save_stage)
    monkeypatch.setattr(upgrade, 'atomic_write_private_bytes',
                        fail_journal if failed_write == 'complete_journal' else write_journal)
    with pytest.raises(OSError, match='completion write failed'):
        owner.upgrade(path)
    assert installer.read_json(owner.journal_path)['phase'] == 'resuming'
    assert business.read_text() == 'new admitted work'
    assert (owner.application / 'code/version').read_text() == 'new'
    assert upgrade.states(owner.application)[0]['status'] == (
        'prepared' if failed_write == 'installation' else 'installed')
    monkeypatch.setattr(installer, 'save_stage', save_stage)
    monkeypatch.setattr(upgrade, 'atomic_write_private_bytes', write_journal)
    owner.start.side_effect = None
    assert owner.recover()['status'] == 'complete'
    current = upgrade.states(owner.application)[0]
    assert current['status'] == 'installed'
    assert current['version_run_id'] == 'new-run'
    assert business.read_text() == 'new admitted work'
    assert owner.recover()['status'] == 'complete'
    assert upgrade.states(owner.application)[0] == current


def test_unknown_database_client_restores_service_availability_without_backup(transaction, monkeypatch):
    owner, path, _ = transaction
    monkeypatch.setattr(upgrade.barrier, 'assert_no_clients', Mock(side_effect=RuntimeError('other client')))
    with pytest.raises(RuntimeError, match='other client'):
        owner.upgrade(path)
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'prebackup_failed'
    assert not (owner.paths(journal) / 'backup').exists()
    assert (owner.application / 'code/version').read_text() == 'original'
    assert upgrade.states(owner.application)[0]['status'] == 'installed'
    owner.start.assert_called_once()


@pytest.mark.parametrize('write_fails', [False, True])
def test_client_refusal_is_sealed_before_service_recovery_even_if_diagnostic_write_fails(transaction, monkeypatch, write_fails):
    owner, path, _ = transaction
    report = {'schema': 'tfi.application-client-proof.v1', 'stage': 'plan_disconnected',
              'clients': [{'ordinal': 1, 'reasons': ['captured'], 'checks': {'captured': False}}]}
    error = upgrade.clients.ClientProofError(report)
    clear = Mock(side_effect=error)
    monkeypatch.setattr(upgrade.clients, 'owned_clients', lambda *_: nullcontext(clear))
    original_save = owner.save
    def save(*args, **kwargs):
        if write_fails and 'client_failure' in kwargs:
            raise OSError('diagnostic disk failure')
        return original_save(*args, **kwargs)
    monkeypatch.setattr(owner, 'save', save)
    def check_on_restart(*_):
        journal = installer.read_json(owner.journal_path)
        assert journal['phase'] == 'resuming'
        if not write_fails: assert journal['client_failure'] == report
    owner.start.side_effect = check_on_restart
    with pytest.raises(OSError if write_fails else upgrade.clients.ClientProofError):
        owner.upgrade(path)
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'prebackup_failed'
    assert not (owner.paths(journal) / 'backup').exists()
    owner.start.assert_called_once()
    owner.schema.assert_not_called()


def test_work_changed_during_shutdown_refuses_before_backup(transaction, monkeypatch):
    owner, path, _ = transaction
    from scripts import tfi_application_work as work
    monkeypatch.setattr(work, 'inspect', Mock(side_effect=[{'retained': 'old'}, {'retained': 'changed'}]))
    with pytest.raises(ValueError, match='changed during service shutdown'):
        owner.upgrade(path)
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'prebackup_failed'
    assert not (owner.paths(journal) / 'backup').exists()
    assert (owner.application / 'code/version').read_text() == 'original'
    owner.start.assert_called_once()


def test_partial_switch_crash_can_recover_from_external_installer(transaction):
    owner, path, _ = transaction
    owner.schema.side_effect = KeyboardInterrupt('crash')
    with pytest.raises(KeyboardInterrupt):
        owner.upgrade(path)
    assert upgrade.pending(owner.application)
    owner.schema.side_effect = None
    assert owner.recover()['status'] == 'restored'
    assert (owner.application / 'code/version').read_text() == 'original'


def test_other_instance_or_changed_target_cannot_be_ignored(application):
    app, state = application
    other = app / 'instance/two'
    other.mkdir()
    with pytest.raises(ValueError, match='Unregistered'):
        upgrade.states(app)
    installer.write_json(other / 'installation.json', state)
    with pytest.raises(ValueError, match='belong'):
        upgrade.states(app)


def test_windows_maintenance_reexec_waits_and_returns_child_exit(transaction, monkeypatch):
    owner, manifest, state = transaction
    monkeypatch.setattr(installer.sys, 'platform', 'win32')
    monkeypatch.setattr(installer, 'runtime_python', lambda target: '/different/runtime-python')
    monkeypatch.setattr(installer, 'clean_environment', lambda target: {'PYTHONDONTWRITEBYTECODE': '1'})
    child = Mock(return_value=17)
    monkeypatch.setattr(installer.subprocess, 'call', child)
    monkeypatch.setattr(installer.os, 'execve', Mock(side_effect=AssertionError('detached exec')))
    assert installer.main(['application-check', '--root', str(owner.application), '--manifest', str(manifest)]) == 17
    assert child.call_args.args[0][:2] == ['/different/runtime-python', '-B']
    assert child.call_args.kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'


@pytest.mark.parametrize('child_succeeds', [True, False])
def test_base_python_recovery_verifies_in_restored_runtime_before_admission(transaction, monkeypatch, child_succeeds):
    owner, manifest, _ = transaction
    owner.schema.side_effect = KeyboardInterrupt('after migration')
    with pytest.raises(KeyboardInterrupt):
        owner.upgrade(manifest)
    monkeypatch.setattr(owner, 'verify_work_in_runtime',
                        upgrade.ApplicationUpgrade.verify_work_in_runtime.__get__(owner))
    monkeypatch.setattr(installer, 'runtime_python',
                        lambda _: '/runtime/' + (owner.application / 'code/version').read_text())
    monkeypatch.setattr(installer, 'clean_environment',
                        lambda _: {'PORTAL_DATABASE_NAMESPACE': 'foreign', 'PORTAL_DATABASE_URL': 'private-dsn'})
    def child(command, **kwargs):
        assert command == ['/runtime/original', '-B', '-m', 'scripts.tfi_application_freeze']
        assert 'private-dsn' not in ' '.join(command)
        assert 'PORTAL_DATABASE_NAMESPACE' not in kwargs['env']
        assert kwargs['env']['PYTHONPATH'] == str(Path(upgrade.__file__).resolve().parents[1])
        assert json.loads(kwargs['input'])['expected'] == [{'retained': 'stable'}]
        owner.start.assert_not_called()
        return Mock(returncode=0 if child_succeeds else 1,
                    stdout=json.dumps({'status': 'verified', 'instances': 1}), stderr='private diagnostic')
    monkeypatch.setattr(upgrade.subprocess, 'run', child)
    with monkeypatch.context() as base_python:
        base_python.setitem(installer.sys.modules, 'psycopg', None)
        if child_succeeds:
            assert owner.recover()['status'] == 'restored'
            owner.start.assert_called_once()
        else:
            with pytest.raises(ValueError, match='Retained work verification failed'):
                owner.recover()
            assert installer.read_json(owner.journal_path)['phase'] == 'restoring'
            assert (owner.control / 'work-error.log').read_text() == 'private diagnostic'
            owner.start.assert_not_called()


def test_flat_backup_preserves_deep_paths_without_expanding_them(application, tmp_path):
    app, _ = application
    path = app / ('nested-' + 'x' * 40) / ('local-' + 'y' * 40) / ('business-' + 'z' * 40)
    path.parent.mkdir(parents=True)
    path.write_text('deep retained content')
    backup = tmp_path / 'maintenance-long-parent' / 'backup'
    expected = snapshot.create_backup(app, backup)
    assert all(len(str(p.relative_to(backup))) <= 70 for p in backup.rglob('*'))
    path.write_text('changed')
    snapshot.restore(backup, app, expected, packed=True)
    assert path.read_text() == 'deep retained content'
    assert snapshot.inventory(app) == expected
    blob = backup / 'files' / expected[path.relative_to(app).as_posix()]['sha256']
    blob.write_text('corrupt')
    with pytest.raises(ValueError, match='integrity'):
        snapshot.restore(backup, app, expected, packed=True)
    assert path.read_text() == 'deep retained content'


def test_stopped_codex_definition_rebinds_only_after_new_runtime_is_ready(application, monkeypatch):
    import hashlib
    import os
    from scripts import tfi_release_dependencies, tfi_windows_user_services
    app, state = application
    root = Path(state['target']['install_root'])
    instances = root / 'data/instances'
    current = [str(app / 'dependencies/old/python.exe')]
    monkeypatch.setattr(tfi_release_dependencies, 'runtime_python', lambda _: current[0])
    monkeypatch.setattr(installer, 'runtime_python', lambda _: current[0])
    scope = hashlib.sha256(os.fsencode(instances.resolve())).hexdigest()[:10]
    name = f'portal-codex-app-server@fixture-{scope}.service'
    directory = instances / 'service-units'
    directory.mkdir(parents=True)
    path = directory / (name + '.json')
    value = {'name': name, 'mode': 'user', 'component': 'codex', 'root': str(root),
        'code': str(app / 'code'), 'python': current[0], 'command': [current[0], '-m',
        'management_portal.codex_app_server_host', '--socket', str(instances / 'sockets/codex-fixture.sock'),
        '--binary', str(root / 'data/codex.cmd'), '--account-config', str(root / 'runtime/provider-accounts/codex/fixture/launch.json')]}
    installer.write_json(path, value)
    control = tfi_windows_user_services.WindowsServiceControl(directory)
    monkeypatch.setattr(control, 'status', lambda _: {'pid': 12, 'active': 'active'})
    monkeypatch.setattr(upgrade.providers, 'owned_services', lambda *_: {'controllers': {name: control}})
    owner = upgrade.ApplicationUpgrade(installer, app)
    with pytest.raises(ValueError, match='must stop'):
        owner.detach_codex([state])
    assert path.exists()
    monkeypatch.setattr(control, 'status', lambda _: {'pid': 0, 'active': 'inactive'})
    saved = owner.detach_codex([state])
    assert not path.exists()
    current[0] = str(app / 'dependencies/new/python.exe')
    owner.attach_codex(saved, state)
    updated = control.definition(name)
    assert updated['python'] == updated['command'][0] == current[0]
    assert updated['command'][1:] == value['command'][1:]
    assert {k: v for k, v in updated.items() if k not in {'command', 'python'}} == {
        k: v for k, v in value.items() if k not in {'command', 'python'}}


def test_dependency_failure_restores_detached_account_definition(transaction):
    owner, path, state = transaction
    definition = Path(state['target']['install_root']) / 'data/accounts/service.json'
    definition.write_text('original owned definition')
    owner.detach_codex.side_effect = lambda _: (definition.unlink() or [])
    installer.prepare_dependencies.side_effect = ValueError('offline dependency failure')
    with pytest.raises(ValueError, match='offline dependency'):
        owner.upgrade(path)
    assert definition.read_text() == 'original owned definition'
    assert installer.read_json(owner.journal_path)['phase'] == 'restored'


def test_live_inventory_does_not_read_provider_locked_bytes(application, monkeypatch):
    app, _ = application
    monkeypatch.setattr(snapshot, 'digest', Mock(side_effect=PermissionError('native read lock')))
    metadata = snapshot.inventory(app, content=False)
    assert metadata['instance/one/data/accounts/sentinel']['size'] > 0
    assert 'sha256' not in metadata['instance/one/data/accounts/sentinel']
    assert metadata['instance/one/data/share/alias']['link'] == 'instance/one/data/share/content-directory'
    with pytest.raises(PermissionError):
        snapshot.inventory(app)


def test_gate_intent_is_durable_before_closing_and_restored_before_snapshot(transaction, monkeypatch):
    from scripts import tfi_application_admission as admission
    owner, path, _ = transaction
    order = []
    def close(*_):
        saved = installer.read_json(owner.journal_path)
        assert saved['phase'] == 'quiescing'
        assert saved['database_admission'] and saved['admission_restored'] is False
        order.append('close')
    def capture(*_):
        assert order == ['close']
        order.append('capture')
        return nullcontext(None)
    def restore(*_):
        owner.stop.assert_called_once()
        assert order == ['close', 'capture']
        order.append('restore')
    real_backup = snapshot.create_backup
    def backup(*args, **kwargs):
        assert order == ['close', 'capture', 'restore']
        assert installer.read_json(owner.journal_path)['admission_restored'] is True
        return real_backup(*args, **kwargs)
    monkeypatch.setattr(admission, 'close', close)
    monkeypatch.setattr(admission, 'restore', restore)
    monkeypatch.setattr(upgrade.clients, 'owned_clients', capture)
    monkeypatch.setattr(snapshot, 'create_backup', backup)
    owner.upgrade(path)
    assert installer.read_json(owner.journal_path)['phase'] == 'complete'


def test_interrupted_gate_is_reopened_before_recovery_admits_services(transaction, monkeypatch):
    from scripts import tfi_application_admission as admission
    owner, path, _ = transaction
    monkeypatch.setattr(admission, 'close', Mock(side_effect=SystemExit('crashed after catalog commit')))
    with pytest.raises(SystemExit):
        owner.upgrade(path)
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'quiescing' and journal['admission_restored'] is False
    assert not (owner.paths(journal) / 'backup').exists()
    owner.start.assert_not_called()
    owner.restore_admission_in_runtime.side_effect = ValueError('cannot restore gate')
    with pytest.raises(ValueError, match='cannot restore gate'):
        owner.recover()
    owner.start.assert_not_called()
    assert installer.read_json(owner.journal_path)['phase'] == 'quiescing'
    owner.restore_admission_in_runtime.side_effect = None
    def start(*_):
        assert installer.read_json(owner.journal_path)['admission_restored'] is True
    owner.start.side_effect = start
    assert owner.recover()['status'] == 'prebackup_failed'
    owner.start.assert_called_once()


def test_gate_close_failure_restores_partial_gate_without_backup(transaction, monkeypatch):
    from scripts import tfi_application_admission as admission
    owner, path, _ = transaction
    monkeypatch.setattr(admission, 'close', Mock(side_effect=ValueError('partial gate failure')))
    with pytest.raises(ValueError, match='partial gate failure'):
        owner.upgrade(path)
    owner.stop.assert_not_called()
    owner.restore_admission_in_runtime.assert_called_once()
    owner.start.assert_called_once()
    journal = installer.read_json(owner.journal_path)
    assert journal['phase'] == 'prebackup_failed' and journal['admission_restored']
    assert not (owner.paths(journal) / 'backup').exists()


@pytest.mark.parametrize('result', [
    {'returncode': 1, 'stdout': '', 'stderr': 'child refused'},
    {'returncode': 0, 'stdout': '{"status":"restored","instances":0}', 'stderr': ''},
    {'returncode': 0, 'stdout': 'partial-json', 'stderr': ''},
])
def test_admission_recovery_child_must_return_complete_matching_receipt(transaction, monkeypatch, result):
    owner, _, state = transaction
    monkeypatch.setattr(installer, 'runtime_python', lambda _: 'restored-runtime-python')
    monkeypatch.setattr(installer, 'clean_environment', lambda _: {'PORTAL_DATABASE_NAMESPACE': 'source'})
    def run(command, **kwargs):
        assert command == ['restored-runtime-python', '-B', '-m', 'scripts.tfi_application_admission']
        assert 'PORTAL_DATABASE_NAMESPACE' not in kwargs['env']
        assert json.loads(kwargs['input']) == {'records': [state], 'entries': [{'gate': 'test'}]}
        return Mock(**result)
    monkeypatch.setattr(upgrade.subprocess, 'run', run)
    with pytest.raises(ValueError):
        upgrade.ApplicationUpgrade.restore_admission_in_runtime(owner, [state], [{'gate': 'test'}])
    owner.start.assert_not_called()
