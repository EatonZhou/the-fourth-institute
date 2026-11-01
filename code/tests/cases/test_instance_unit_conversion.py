"""Historical service ownership is accepted only by the offline converter."""
import subprocess
from types import SimpleNamespace

import pytest

from management_common.instance_manifest import load
from scripts import migrate_instance_units as units
from scripts import tfi_release_platform_linux as linux
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    instance = load(write_instance(tmp_path / 'instance'))
    directory = tmp_path / 'units'
    directory.mkdir()
    name = instance.service_name('web') + '.service'
    path = directory / name
    original = f'[Service]\nEnvironment=HOME={instance.root}/home\nEnvironment=PORTAL_PATHS_CONFIG={instance.root}/config/target.json\nExecStart=/old/launcher\n'
    path.write_text(original)
    expected = f'[Service]\nEnvironment=PORTAL_INSTANCE_ROOT={instance.root}\nExecStart=/new/launcher\n'
    monkeypatch.setattr(linux, 'unit_directory', lambda: directory)
    monkeypatch.setattr(units, 'render', lambda *args: {name: expected})
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, f'LoadState=loaded\nFragmentPath={path}\nDropInPaths=\n', '')
    monkeypatch.setattr(units.subprocess, 'run', run)
    return instance, path, original, expected, calls


def test_offline_conversion_replaces_owned_old_unit_without_starting(conversion):
    instance, path, original, expected, calls = conversion
    replacements = units.plan(instance)
    units.apply(replacements)
    assert path.read_text() == expected
    assert all('start' not in command and 'restart' not in command for command in calls)
    units.rollback(replacements)
    assert path.read_text() == original


def test_foreign_unit_is_rejected_before_writing(conversion):
    instance, path, original, expected, calls = conversion
    path.write_text(original.replace(str(instance.root), '/foreign'))
    with pytest.raises(ValueError, match='another instance'):
        units.plan(instance)
    assert path.read_text() != expected
    assert calls == []


def test_changed_unit_is_not_overwritten_by_a_stale_plan(conversion):
    instance, path, original, expected, calls = conversion
    replacements = units.plan(instance)
    path.write_text(original + '# concurrent change\n')
    with pytest.raises(ValueError, match='changed after preflight'):
        units.apply(replacements)
    assert path.read_text().endswith('# concurrent change\n')


def test_renamed_gateway_inherits_enable_state_and_rollback_restores_it(conversion, monkeypatch):
    instance, path, original, expected, calls = conversion
    old = path.parent / 'old-gateway.service'
    old.write_text(original)
    override = path.parent / (old.name + '.d') / 'tfi-development.conf'
    override.parent.mkdir()
    override.write_text('[Service]\n'
                        f'Environment=PORTAL_DEPLOYMENT_REGISTRY_PATH={instance.root}/config/deployments.json\n'
                        f'Environment=PORTAL_SHARED_SOURCE_ROOT={instance.code_root()}\n')
    def run(command, **kwargs):
        calls.append(command)
        if 'show' in command:
            selected = old if command[3] == old.name else path
            return subprocess.CompletedProcess(command, 0,
                f'LoadState=loaded\nFragmentPath={selected}\n'
                f'DropInPaths={override if selected == old else ""}\n'
                f'UnitFileState={"enabled" if selected == old else "disabled"}\n', '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(units.subprocess, 'run', run)
    replacements = units.plan(instance, obsolete_units=(old.name,), renamed_units={old.name: path.name})
    units.apply(replacements)
    assert not old.exists() and not override.exists()
    assert path.read_text() == expected
    assert ['systemctl', '--user', 'disable', old.name] in calls
    assert ['systemctl', '--user', 'reenable', path.name] in calls
    units.rollback(replacements)
    assert old.read_text() == original and override.is_file()
    assert path.read_text() == original
    assert ['systemctl', '--user', 'disable', path.name] in calls
    assert calls[-1] == ['systemctl', '--user', 'reenable', old.name]
    assert not any('start' in command or 'restart' in command for command in calls)


def test_failed_enable_restores_original_files_and_enable_state(conversion, monkeypatch):
    instance, path, original, expected, calls = conversion
    old = path.parent / 'retired.service'
    old.write_text(original)
    replacements = [units.Replacement(path, expected, original, path.name, False, True),
                    units.Replacement(old, None, original, old.name, True)]
    def run(command, **kwargs):
        calls.append(command)
        if command == ['systemctl', '--user', 'reenable', path.name]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(units.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        units.apply(replacements)
    assert path.read_text() == original and old.read_text() == original
    assert calls[-1] == ['systemctl', '--user', 'reenable', old.name]


def test_template_instances_become_individual_units_without_losing_enable_state(conversion, monkeypatch):
    instance, path, original, expected, calls = conversion
    template = path.parent / (instance.service_prefix + '-codex-app-server@.service')
    template.write_text(original)
    account = path.parent / (instance.service_prefix + '-codex-app-server@one.service')
    monkeypatch.setattr(units, 'render', lambda *args: {account.name: expected})
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0,
            f'LoadState=loaded\nFragmentPath={template}\nDropInPaths=\nUnitFileState=enabled\n', '')
    monkeypatch.setattr(units.subprocess, 'run', run)
    replacements = units.plan(instance)
    units.apply(replacements)
    assert account.read_text() == expected and not template.exists()
    assert calls[-1] == ['systemctl', '--user', 'reenable', account.name]
    units.rollback(replacements)
    assert not account.exists() and template.read_text() == original
    assert calls[-1] == ['systemctl', '--user', 'reenable', account.name]


def test_retirement_cannot_delete_a_current_service(conversion):
    instance, path, original, expected, calls = conversion
    with pytest.raises(ValueError, match='inconsistent'):
        units.plan(instance, obsolete_units=(path.name,))
    assert path.read_text() == original
    assert calls == []
