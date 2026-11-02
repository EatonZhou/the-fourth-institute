"""Resident preparation supports native long paths without changing identity.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
from unittest.mock import patch
import shutil

from management_common.filesystem_paths import filesystem_path, logical_path
from management_portal.society_service import resident_workspace_domain as workspace


def exercise_long_workspace(root: Path, module=workspace):
    folder = root / ('project-' + 'p' * 95) / ('.tfi-create-' + 's' * 32) / 'residents' / ('resident_' + 'r' * 12)
    # Keep the logical identity; only actual filesystem calls use extended paths.
    while len(str(folder / 'workspace')) <= 280:
        folder = folder / 'deep-directory'
    try:
        with patch.object(module.resident_backend, 'validate_resident_session_identity'), \
                patch.object(module, 'resident_workspaces_text', return_value='logical workspace identity'):
            module.ensure_resident_workspace({'resident_id': 'resident_123456789abc'}, folder=folder)
        for name in ['workspace', 'runtime']:
            assert filesystem_path(folder / name).is_dir()
        path = module.ensure_resident_file(folder, 'resident.md', initial_content='must not overwrite')
        assert logical_path(path) == folder / 'resident.md'
        assert filesystem_path(path).read_text() == ''
        assert filesystem_path(folder / 'workspaces.md').read_text() == 'logical workspace identity'
        return {'path_length': len(str(folder / 'workspace')), 'created': True, 'logical_identity': True, 'authored_file_preserved': True}
    finally:
        if filesystem_path(root).exists():
            shutil.rmtree(filesystem_path(root))


def test_long_resident_staging_directory(tmp_path):
    exercise_long_workspace(tmp_path / 'long-workspace')
