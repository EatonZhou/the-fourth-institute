"""Offline application backup and transaction boundaries, without host services."""
import json
from pathlib import Path
from unittest.mock import Mock
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
            'accounts_root': str(root / 'data/accounts'), 'share_root': str(root / 'data/share')}},
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
    monkeypatch.setattr(installer, 'prepare_dependencies', Mock())
    monkeypatch.setattr(installer, 'service_controller', lambda: Mock())
    monkeypatch.setattr(installer, 'wait_database', Mock())
    from scripts import tfi_release_upgrade
    monkeypatch.setattr(tfi_release_upgrade, 'check_quiescent', Mock())
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
    assert (Path(journal['failed_generation']) / 'instance/one/data/database/business').read_text() == 'partly migrated rows'


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
    from scripts import tfi_release_upgrade
    monkeypatch.setattr(tfi_release_upgrade, 'check_quiescent', Mock(side_effect=RuntimeError('active work')))
    with pytest.raises(RuntimeError, match='active work'):
        owner.upgrade(path)
    owner.stop.assert_not_called()
    assert not owner.journal_path.exists()


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
