"""Moving Provider programs preserves executables and can undo a failed apply."""
import json
import os
from pathlib import Path

import pytest

from management_common.instance_manifest import load
from scripts import migrate_provider_programs as migration
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    code = tmp_path / 'repo/code'
    (code / 'management_portal').mkdir(parents=True)
    (code / 'management_portal/claude_app_server_version.txt').write_text('1.0-2')
    root = write_instance(tmp_path / 'instance', code=code)
    source = root / 'home/programs'
    sdk = source / 'claude-agent-sdk-1.0'
    (sdk / 'bin').mkdir(parents=True)
    (sdk / 'bin/python').symlink_to('/usr/bin/python3')
    (sdk / 'bin/pip').write_text('#!' + str(sdk / 'bin/python') + '\n')
    (source / 'claude-agent-sdk-current').symlink_to(sdk)
    codex = source / 'codex-cli-1.0'
    entry = codex / 'lib/node_modules/@openai/codex/bin/codex.js'
    entry.parent.mkdir(parents=True)
    entry.write_text('// fixture program')
    (codex / 'portal-codex').write_text('#!/bin/sh\nexec old-node ' + str(entry) + '\n')
    (codex / 'portal-codex').chmod(0o755)
    old_package = source / 'claude-app-server-1.0-1'
    old_package.mkdir()
    (source / 'claude-app-server-current').symlink_to(old_package)
    package = tmp_path / 'repo/dependencies/claude-app-server-1.0-2'
    package.mkdir(parents=True)
    (package / 'VERSION').write_text('1.0-2')
    monkeypatch.setattr(migration, 'native_binary', lambda code, package: Path('/prepared/node'))
    settings = {'providers': {'runtime_root': str(source), 'codex_binary': str(codex / 'portal-codex')}}
    return load(root), settings, package


def test_move_rewrites_only_program_links_and_launchers(conversion):
    instance, settings, package = conversion
    operation = migration.plan(instance, settings, package)
    source = operation.source
    inode = (source / 'codex-cli-1.0/portal-codex').stat().st_ino
    operation.apply()
    target = operation.destination
    assert not source.exists()
    assert (target / 'codex').resolve() == target / 'codex-cli-1.0/portal-codex'
    assert (target / 'codex').stat().st_ino == inode
    assert '/prepared/node' in (target / 'codex').read_text()
    assert str(source) not in (target / 'codex').read_text()
    assert str(target) in (target / 'claude-agent-sdk-1.0/bin/pip').read_text()
    assert os.readlink(target / 'claude-agent-sdk-1.0/bin/python') == '/usr/bin/python3'
    assert (target / 'claude-app-server-current').resolve() == target / package.name
    operation.rollback()
    assert source.exists() and not target.exists() and package.exists()
    assert str(source) in (source / 'codex-cli-1.0/portal-codex').read_text()
    assert not (source / 'codex').exists()


def test_unknown_program_root_entry_requires_owner_review(conversion):
    instance, settings, package = conversion
    source = Path(settings['providers']['runtime_root'])
    (source / 'personal-file').write_text('unowned')
    with pytest.raises(ValueError, match='owner review'):
        migration.plan(instance, settings, package)
    assert source.exists()


def test_failed_package_move_restores_programs(conversion, monkeypatch):
    instance, settings, package = conversion
    operation = migration.plan(instance, settings, package)
    rename = Path.rename
    def fail(path, destination):
        if path == package:
            raise OSError('fixture package move failure')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(OSError, match='fixture'):
        operation.apply()
    assert operation.source.exists() and not operation.destination.exists()
    assert (operation.source / 'claude-app-server-current').resolve().name == 'claude-app-server-1.0-1'
