"""Registered identities and locations determine runtime paths.

:module: resident.workspace-memory
"""
from pathlib import Path

import pytest

from management_common.instance_locations import current_instance
from management_portal.society_service import project_folders, resident_catalog
from management_portal import portal_pulse_backend


def test_project_directory_name_is_not_an_identity(tmp_path):
    document = {'projects': [{'id': 'registered-id', 'path': str(tmp_path / 'folder-name')}]}
    with pytest.raises(KeyError):
        project_folders.project_record_from_document(document, 'folder-name')


def test_project_area_reference_is_resolved():
    document = {'projects': [{'id': 'registered-id', 'path': 'projects:actual-folder'}]}
    _, path = project_folders.project_record_from_document(document, 'registered-id')
    assert path == current_instance().area('projects') / 'actual-folder'


def test_default_directory_uses_registered_workspace(tmp_path):
    actual = tmp_path / 'relocated-workspace'
    record = {'resident_id': 'resident_0123456789ab',
              'workspace_path': str(tmp_path / 'lineage'),
              'owned_workspace_id': 'workspace_0123456789ab',
              'owned_workspace': {'workspace_id': 'workspace_0123456789ab', 'path': str(actual)}}
    assert resident_catalog.default_cwd_path(record) == str(actual)


def test_pulse_environment_cannot_override_registered_lineage(monkeypatch, tmp_path):
    monkeypatch.setenv('PORTAL_PULSE_ROOT', str(tmp_path / 'override'))
    monkeypatch.setattr(portal_pulse_backend, 'read_site_config', lambda: {
        'deployment': {'pulse': {'lineage_id': 'lineage_0123456789ab'}}})
    monkeypatch.setattr(portal_pulse_backend, 'lineage_folder', lambda _: tmp_path / 'registered')
    assert portal_pulse_backend._pulse_root() == tmp_path / 'registered' / 'memory' / 'pulse'
