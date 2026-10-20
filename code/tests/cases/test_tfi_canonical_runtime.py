"""Runtime names remain canonical even when stale compatibility settings exist."""
import json
import re
import importlib.util
from pathlib import Path

import pytest

from management_common.product_environment import read_product_env, mirror_product_env
from management_portal.resident_shell_environment import resident_identity_environment
from management_portal.service_invocation_contract import digest, plan_fingerprint_matches
from management_portal.society_service.seed_state_marker import marker_name_pattern


@pytest.fixture
def stale_site(tmp_path, monkeypatch):
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'deployment': {'compatibility': {
        'environment_aliases': {'PORTAL_RESIDENT_ID': ['ARCHIVE_RESIDENT_ID']},
        'domain_values': {
            'seed_state_markers': ['archive-hatg-seed-state'],
            'service_invocation_plan_schemas': ['archive.service-invocation-plan.v1'],
            'test_framework.user_state_root': '/archive/state',
        },
    }}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))


def test_environment_alias_settings_cannot_enable_old_variables(stale_site):
    assert read_product_env('PORTAL_RESIDENT_ID', environ={'ARCHIVE_RESIDENT_ID': 'old'}) is None
    assert read_product_env('PORTAL_RESIDENT_ID', environ={
        'PORTAL_RESIDENT_ID': 'current', 'ARCHIVE_RESIDENT_ID': 'other'}) == 'current'
    values = {}
    mirror_product_env(values, 'PORTAL_RESIDENT_ID', 'current')
    assert values == {'PORTAL_RESIDENT_ID': 'current'}
    with pytest.raises(ValueError, match='Unexpected Resident'):
        resident_identity_environment({'ARCHIVE_RESIDENT_ID': 'resident_a9c0b75f1989'})


def test_seed_and_plan_aliases_cannot_enable_retired_formats(stale_site):
    assert not re.fullmatch(marker_name_pattern(), 'archive-hatg-seed-state')
    assert re.fullmatch(marker_name_pattern(), 'tfi-hatg-seed-state')
    job = {'created_by': 'test', 'surface': 'test', 'search_id': '', 'run_generation': 1}
    batches = [{'batch_id': 'batch-000001', 'input_fingerprint': '0' * 64}]
    old = digest({'schema': 'archive.service-invocation-plan.v1', **job, 'batches': batches})
    current = digest({'schema': 'tfi.service-invocation-plan.v1', **job, 'batches': batches})
    assert not plan_fingerprint_matches(job, batches, old)
    assert plan_fingerprint_matches(job, batches, current)


def test_test_state_root_uses_canonical_directory(stale_site, monkeypatch, tmp_path):
    from management_portal.test_framework.paths import user_state_root
    monkeypatch.setenv('HOME', str(tmp_path))
    assert user_state_root() == tmp_path / '.local/state/tfi-portal'


def test_resource_tool_bootstrap_ignores_retired_root_alias(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'deployment': {'compatibility': {
        'environment_aliases': {'PORTAL_ROOT': ['ARCHIVE_PORTAL_ROOT']},
    }}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))
    monkeypatch.delenv('PORTAL_ROOT', raising=False)
    monkeypatch.setenv('ARCHIVE_PORTAL_ROOT', str(tmp_path / 'retired-checkout'))
    spec = importlib.util.spec_from_file_location('canonical_resource_bootstrap', root / 'core_skills/tfi-unified-id/scripts/resource_id.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool._source_root() == root
