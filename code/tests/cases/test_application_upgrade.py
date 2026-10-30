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
            'accounts_root': str(root / 'data/accounts'), 'share_root': str(root / 'data/share'),
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
    monkeypatch.setattr(installer, 'prepare_dependencies', Mock())
    monkeypatch.setattr(installer, 'service_controller', lambda: Mock())
    monkeypatch.setattr(installer, 'wait_database', Mock())
    from scripts import tfi_application_work as work
    monkeypatch.setattr(work, 'inspect', Mock(return_value={'retained': 'stable'}))
    monkeypatch.setattr(upgrade.barrier, 'freeze', lambda *_: nullcontext([Mock()]))
    monkeypatch.setattr(upgrade.barrier, 'assert_no_clients', Mock())
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
    assert (owner.application / 'code/version').read_text() == 'new'
    owner.start.side_effect = None
    assert owner.recover()['status'] == 'complete'
    assert business.read_text() == 'waiting input progressed after admission'
    assert not upgrade.pending(owner.application)


def test_unknown_database_client_restores_service_availability_without_backup(transaction, monkeypatch):
    owner, path, _ = transaction
    monkeypatch.setattr(upgrade.barrier, 'assert_no_clients', Mock(side_effect=RuntimeError('other client')))
    with pytest.raises(RuntimeError, match='other client'):
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
        '--binary', str(root / 'data/codex.cmd'), '--account-home', str(root / 'data/accounts/codex/fixture')]}
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
