"""Windows path semantics at the JSON/Project persistence boundary."""
import json
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest


@pytest.fixture
def windows_locations(tmp_path, monkeypatch):
    from tests.cases.instance_test_support import select_instance
    from management_common import location_records, instance_locations, directory_paths
    select_instance(monkeypatch, tmp_path / 'instance')

    class NativePath(PureWindowsPath):
        def resolve(self):
            return self

    instance = SimpleNamespace(area=lambda area: NativePath('C:/TFI/instance/data') / area)
    monkeypatch.setattr(location_records, 'Path', NativePath)
    monkeypatch.setattr(instance_locations, 'Path', NativePath)
    monkeypatch.setattr(instance_locations, 'current_instance', lambda: instance)
    monkeypatch.setattr(directory_paths, 'current_instance', lambda: instance)
    return instance


@pytest.mark.parametrize('path', ['C:/TFI/instance/data/projects/AI员工', r'C:\TFI\instance\data\projects\AI员工'])
def test_json_writer_keeps_windows_area_locations_and_external_paths(windows_locations, path):
    from management_common.json_columns import json_text, json_value
    external = ['D:/Research/Shared', r'\\server\share\Documents']
    document = {'path': path, 'description': path, 'paths': external}
    stored = json.loads(json_text(document))
    assert stored == {**document, 'path': 'projects:AI员工'}
    assert json_value(stored)['path'] == 'C:/TFI/instance/data/projects/AI员工'


@pytest.mark.parametrize('legacy_absolute', [False, True])
def test_project_edit_and_upgrade_keep_windows_persisted_references(windows_locations, legacy_absolute):
    from management_portal import project_registry
    from management_portal.resources.registry_store import get_registry_store
    registry = get_registry_store()
    document = {'schema_version': 1, 'status_vocab': [], 'tag_vocab': [], 'projects': [{
        'id': 'AI员工', 'path': 'C:/TFI/instance/data/projects/AI员工' if legacy_absolute else 'projects:AI员工',
        'description': 'before', 'directory': {'owner_id': 'AI员工'}}]}
    with registry.writer('project_registry') as writer:
        writer.connection.execute('INSERT INTO registry_documents VALUES (%s,%s,%s,%s)',
                                  ('project_registry', 'projects', 0, json.dumps(document)))
    if legacy_absolute:
        assert project_registry.load()['projects'][0]['path'] == 'C:/TFI/instance/data/projects/AI员工'
    else:
        with project_registry.mutate() as payload:
            assert payload['projects'][0]['path'] == 'C:/TFI/instance/data/projects/AI员工'
            payload['projects'][0]['description'] = 'after'
    with registry.database.read_current() as connection:
        raw = connection.execute("SELECT document_json FROM registry_documents WHERE collection='project_registry' AND document_id='projects'").fetchone()[0]
        stored = json.loads(raw) if isinstance(raw, str) else raw
    expected = {**document['projects'][0], 'path': 'projects:AI员工', 'description': 'before' if legacy_absolute else 'after'}
    assert stored['projects'] == [expected]


def test_project_upgrade_without_selected_instance_keeps_unmodified_fields(monkeypatch):
    from management_portal import project_registry
    from management_portal.resources.registry_store import get_registry_store
    registry = get_registry_store()
    document = {'projects': [{'id': 'Example', 'path': 'projects:Example',
                             'directory': {'owner_id': 'Example', 'origin': 'created'}}]}
    with registry.writer('project_registry') as writer:
        writer.connection.execute('INSERT INTO registry_documents VALUES (%s,%s,%s,%s)',
                                  ('project_registry', 'projects', 0, json.dumps(document)))
        monkeypatch.delenv('PORTAL_INSTANCE_ROOT', raising=False)
        assert project_registry.upgrade_directory_ownership(writer.connection) == 1
        raw = writer.connection.execute("SELECT document_json FROM registry_documents WHERE collection='project_registry' AND document_id='projects'").fetchone()[0]
        stored = json.loads(raw) if isinstance(raw, str) else raw
        assert stored['projects'] == [{'id': 'Example', 'path': 'projects:Example', 'directory': {'owner_id': 'Example'}}]
