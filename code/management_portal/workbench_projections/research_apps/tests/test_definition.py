"""The migration generator must obey the authoritative Workbench update API."""
import importlib.util
import json
from pathlib import Path

from management_portal.workbench_projections.service import DEFINITION_FIELDS


def test_migration_patch_uses_workbench_fields_and_preserves_metadata(tmp_path, monkeypatch):
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'deployment': {'development_projection': {'repo_id': 'repo_123456789abc', 'code_directory': 'code'}}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))
    path = Path(__file__).resolve().parents[1] / 'definition.py'
    spec = importlib.util.spec_from_file_location('migration_definition', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definitions = module.definitions('a' * 40)
    assert {row['resource_id']: row['fields']['expected_revision'] for row in definitions} == {
        'workbench_d82dedd31208': 7, 'workbench_0d598218b194': 22}
    for row in definitions:
        assert row['fields']['application']['repo_id'] == 'repo_123456789abc'
        assert row['fields']['application']['directory'] == 'code/management_portal/workbench_projections/research_apps'
        assert set(row['fields']) <= DEFINITION_FIELDS | {'expected_revision'}
        assert not set(row['fields']) & {'title', 'description', 'owner_ids', 'project_ids', 'navigation'}
        roles = row['fields']['bindings']['main']['residents']
        assert roles['facade'] == roles['coordinator'] == 'resident_5549c42d1317'
