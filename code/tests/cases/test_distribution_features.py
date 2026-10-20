"""Release defaults preserve intent, native switches, and local upgrade choices."""
import json
import os
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from management_portal.distribution.features import normalize_snapshot, resolve_features, selection_snapshot
from management_portal.distribution.initialization import initialize
from management_portal.distribution.store import ReleaseStore


@pytest.mark.parametrize('included', [set(), {'model_request_timing'}, {'harness_recording'}, {'model_request_timing', 'harness_recording'}])
def test_copied_execution_owner_respects_omission_and_local_switches(tmp_path, included):
    from pathlib import Path
    from management_portal.distribution.engineering import execution_feature_slice
    from management_portal.distribution.initialization import _TargetDatabase
    source = Path(__file__).resolve().parents[2] / 'management_portal/execution/service_settings.py'
    original = source.read_text()
    target = tmp_path / 'management_portal/execution/service_settings.py'
    target.parent.mkdir(parents=True)
    target.write_text(original)
    execution_feature_slice(tmp_path, included)
    namespace = {'__name__': 'copied_execution_settings'}
    exec(compile(target.read_text(), str(target), 'exec'), namespace)
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True, row_factory=dict_row) as connection:
        try:
            settings = namespace['ExecutionSettings'](_TargetDatabase(connection, 'distribution_execution_test'))
            # Permission code and its native switch survive every optional slice.
            assert settings.permission_system_enabled() is True
            settings.update_permission_system(False)
            assert settings.permission_system_enabled() is False
            settings.update_permission_system(True)
            row = connection.execute('SELECT * FROM distribution_execution_test.service_settings').fetchone()
            assert row['provider_timing_proxy_enabled'] is ('model_request_timing' in included)
            assert row['harness_recording_enabled'] is ('harness_recording' in included)
            for feature in {'model_request_timing', 'harness_recording'}:
                field = feature + '_enabled'
                if feature in included:
                    settings.update_unmanaged({field: False})
                    assert settings.get()[field] is False
                    settings.update_unmanaged({field: True})
                    assert settings.get()[field] is True
                else:
                    with pytest.raises(ValueError, match='not included'):
                        settings.update_unmanaged({field: True})
                    assert settings.get()[field] is False
            # Even a retained old row cannot make an omitted capability run.
            connection.execute('UPDATE distribution_execution_test.service_settings SET provider_timing_proxy_enabled=TRUE,harness_recording_enabled=TRUE')
            assert settings.model_request_timing_enabled() is ('model_request_timing' in included)
            with settings.admit_harness_recording(9999999999) as admitted:
                assert admitted is ('harness_recording' in included)
        finally:
            connection.execute('DROP SCHEMA IF EXISTS distribution_execution_test CASCADE')
    assert source.read_text() == original


def system_source():
    return SimpleNamespace(rows=lambda *args, **kwargs: [], definition_rows=lambda _: {
        ('lineage','lineage_nodes'): [{'node_id':'event_source_111111111111','node_type':'event_source'}],
        ('lineage','lineage_components'): [{'node_id':'event_source_111111111111','client_key':'system:request-timing:enabled'}],
        ('lineage','function_blocks'): [{'function_block_id':'fbi_111111111111','title':'计时'}],
        ('lineage','function_block_elements'): [{'function_block_id':'fbi_111111111111','element_id':'event_source_111111111111'}],
    })


def test_single_identity_defaults_dependencies_and_legacy_are_separate():
    legacy = {'optional_features': {'resident_semantic_search': True}, 'system_lineage': {'selected':[]}}
    result = resolve_features(legacy)
    state = result['states']['resident_semantic_search']
    assert result['valid'] and state['included'] and state['default_enabled'] is None
    assert state['resolved_default_enabled'] is False and state['effective_enabled'] is False
    assert 'feature_states' not in legacy
    selected = {'feature_states': {'model_request_timing': {'included':True,'default_enabled':False}}}
    resolved = resolve_features(selected, source=system_source())
    assert resolved['valid'] and 'flow:fbi_111111111111' not in resolved['states']
    assert selection_snapshot(selected, resolved)['system_lineage']['selected'] == ['fbi_111111111111']
    conflict = resolve_features({'feature_states': {'tau': {'included':True,'default_enabled':True}, 'test_framework': {'included':False,'default_enabled':False}}})
    assert 'test_framework' in conflict['included_features']  # Required code is still included.
    assert conflict['valid']  # Code support does not claim a runtime switch.
    assert conflict['states']['test_framework']['resolved_default_enabled'] is None
    omitted = resolve_features({'feature_states': {'telepus': {'included':False,'default_enabled':True}}})
    assert omitted['states']['telepus']['effective_enabled'] is None
    pending = resolve_features({'feature_states': {'resident_semantic_search': {'included':True,'default_enabled':True}}})
    assert pending['valid'] and pending['states']['resident_semantic_search']['readiness'] == 'needs_configuration'
    assert pending['states']['resident_semantic_search']['default_enabled'] is True
    assert not resolve_features({'feature_states': {'future_feature': {'included':True,'default_enabled':True}}})['valid']
    duplicate = resolve_features({'feature_states': {'model_request_timing': {'included':True,'default_enabled':True}, 'flow:fbi_111111111111': {'included':True,'default_enabled':False}}}, source=system_source())
    assert not duplicate['valid']


def test_save_load_copy_and_run_freeze_feature_states():
    store = ReleaseStore()
    initial = {'feature_states': {'harness_recording': {'included':True,'default_enabled':False}}}
    saved = store.save_config(actor='feature-publisher', name='disabled', snapshot=initial)
    run = store.create_run(actor='feature-publisher', request_key='feature-defaults', mode='publish', config_id=saved['config_id'])
    changed = {'feature_states': {'harness_recording': {'included':True,'default_enabled':True}}}
    store.save_config(actor='feature-publisher', name='enabled', snapshot=changed, config_id=saved['config_id'])
    copied = store.save_config(actor='feature-publisher', name='copy', snapshot=store.get_config(saved['config_id'])['snapshot'])
    assert copied['snapshot']['feature_states'] == changed['feature_states']
    assert store.get_run(run['run_id'])['snapshot']['feature_states'] == initial['feature_states']
    assert normalize_snapshot({'optional_features':{'harness_recording':True}})['feature_states']['harness_recording']['default_enabled'] is None


def test_legacy_page_flags_and_structural_dependencies_share_canonical_resolution(monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from management_portal.distribution import candidates
    legacy = {'projects_page': True, 'test_view': True,
              'society_templates': {'selected': [{'id': 'template_111111111111'}]},
              'resource_types': [{'type': 'paper', 'instances': []}]}
    before = deepcopy(legacy)
    normalized = normalize_snapshot(legacy)
    for identity in ['projects', 'test_view']:
        assert normalized['feature_states'][identity] == {'included': True, 'default_enabled': None}
    @contextmanager
    def source():
        yield system_source()
    monkeypatch.setattr(candidates, 'read_snapshot', source)
    candidate = candidates.candidate('release_features', {'snapshot': legacy})['resolution']
    exported = resolve_features(legacy, source=system_source())
    assert {key:value for key,value in candidate.items() if key != 'resource_dependencies'} == {key:value for key,value in exported.items() if key != 'catalog'}
    assert {'projects', 'test_view', 'test_framework', 'society_templates', 'paper'} <= set(candidate['included_features'])
    assert candidate['states']['society_templates']['code_required_by'] == ['society_templates.selected']
    assert candidate['states']['paper']['code_required_by'] == ['resource_types.paper']
    assert legacy == before  # Reading never rewrites an old run/submission.
    explicit = {**legacy, 'feature_states': {'projects': {'included': False, 'default_enabled': None},
                                          'test_view': {'included': False, 'default_enabled': None}}}
    current = normalize_snapshot(explicit)
    assert current['projects_page'] is False and current['test_view'] is False
    resolution = resolve_features(explicit)
    assert not resolution['states']['projects']['included']
    assert not resolution['states']['test_view']['included']
    assert resolution['states']['society_templates']['included']  # The selected object still needs its code.
    projected = selection_snapshot(explicit, resolution)
    assert projected['projects_page'] is False and projected['test_view'] is False


def test_legacy_page_migration_save_copy_does_not_change_frozen_run():
    store = ReleaseStore()
    saved = store.save_config(actor='page-migration', name='legacy', snapshot={'projects_page': True, 'test_view': True})
    frozen = store.create_run(actor='page-migration', request_key='legacy-page-freeze', mode='publish', config_id=saved['config_id'])
    changed = {**saved['snapshot'], 'feature_states': {'projects': {'included': False}, 'test_view': {'included': False}}}
    revised = store.save_config(actor='page-migration', name='edited', snapshot=changed, config_id=saved['config_id'])
    copied = store.save_config(actor='page-migration', name='copy', snapshot=revised['snapshot'])
    assert copied['snapshot']['feature_states'] == revised['snapshot']['feature_states']
    assert not copied['snapshot']['projects_page'] and not copied['snapshot']['test_view']
    assert store.get_run(frozen['run_id'])['snapshot'] == frozen['snapshot']
    assert frozen['snapshot']['feature_states']['projects']['included'] is True


@pytest.mark.parametrize('enabled,missing,event_type,required', [
    (True, [], 'time.reached', True),
    (False, [], 'time.reached', False),
    (False, ['local_workflow_configuration'], 'time.reached', False),
    (True, [], 'manual.triggered', False),
])
def test_initialization_reports_only_ready_open_native_timer_sources(tmp_path, enabled, missing, event_type, required):
    action = {'id': 'flow:event_source_222222222222', 'adapter': 'lineage_events',
              'desired_enabled': True, 'effective_enabled': enabled, 'missing_requirements': missing,
              'event_sources': [{'lineage_id': 'lineage_222222222222', 'source_id': 'event_source_222222222222'}]}
    manifest = {'feature_resolution': {'valid': True, 'initialization': {'actions': [action]}}}
    roots = {'instances_root': str(tmp_path)}
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True, row_factory=dict_row) as c:
        c.execute('CREATE SCHEMA lineage')
        c.execute('CREATE TABLE lineage.lineage_components(node_id TEXT PRIMARY KEY,config_json TEXT)')
        c.execute('CREATE TABLE lineage.event_source_progress(lineage_id TEXT,source_id TEXT,checkpoint_json TEXT,PRIMARY KEY(lineage_id,source_id))')
        c.execute('INSERT INTO lineage.lineage_components VALUES(%s,%s)', ('event_source_222222222222', json.dumps({'event_type': event_type, 'schedule_mode': 'at', 'scheduled_at': '2099-01-01T00:00:00Z'})))
        c.execute('INSERT INTO lineage.event_source_progress VALUES(%s,%s,%s)', ('lineage_222222222222', 'event_source_222222222222', '{"paused":true,"old_work":"must-not-replay"}'))
        try:
            first = initialize(manifest, c, roots)
            assert first['lineage_timer_required'] is required
            assert bool(first['lineage_timer_sources']) is required
            assert first['work_dispatched'] is False
            assert 'old_work' not in c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json']
            assert initialize(manifest, c, roots)['lineage_timer_required'] is required
            if required:
                # Incoming default-off/not-ready cannot turn off a site's
                # already enabled timer, even if its action disappears.
                incoming = {'feature_resolution': {'valid': True, 'initialization': {'actions': [
                    {**action, 'desired_enabled': False, 'effective_enabled': False, 'missing_requirements': ['new_package_requirement']} ]}}}
                before = c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json']
                upgraded = initialize(incoming, c, roots, mode='upgrade', existing_feature_ids=[action['id']])
                assert upgraded['lineage_timer_required'] is True
                assert upgraded['lineage_timer_sources'][0]['origin'] == 'existing_target_state'
                absent = {'feature_resolution': {'valid': True, 'initialization': {'actions': []}}}
                omitted = initialize(absent, c, roots, mode='upgrade', existing_feature_ids=[action['id']])
                assert omitted['lineage_timer_required'] is True
                assert 'feature_id' not in omitted['lineage_timer_sources'][0]
                assert initialize({}, c, roots, mode='upgrade')['lineage_timer_required'] is True
                assert c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json'] == before
            c.execute('UPDATE lineage.event_source_progress SET checkpoint_json=%s', ('{"paused":true}',))
            assert initialize(manifest, c, roots)['lineage_timer_required'] is False
            assert initialize({}, c, roots)['lineage_timer_required'] is False
            assert initialize({}, c, roots, mode='upgrade')['lineage_timer_required'] is False
        finally:
            c.execute('DROP SCHEMA lineage CASCADE')
            c.execute('DROP SCHEMA distribution_installation CASCADE')


def test_target_initialization_and_upgrade_preserve_local_values(tmp_path):
    snapshot = {'feature_states': {'model_request_timing': {'included':True,'default_enabled':False},
                                  'wall_scheduling': {'included':True,'default_enabled':False},
                                  'resident_semantic_search': {'included':True,'default_enabled':False},
                                  'tau': {'included':True,'default_enabled':False}}}
    resolution = resolve_features(snapshot, source=system_source())
    assert resolution['valid']
    manifest = {'feature_resolution': resolution}
    roots = {'instances_root': str(tmp_path / 'instances')}
    site = tmp_path / 'site.json'
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True, row_factory=dict_row) as c:
        c.execute('CREATE SCHEMA lineage')
        c.execute('CREATE TABLE lineage.lineage_components(node_id TEXT PRIMARY KEY,config_json TEXT)')
        c.execute('CREATE TABLE lineage.event_source_progress(lineage_id TEXT,source_id TEXT,checkpoint_json TEXT,PRIMARY KEY(lineage_id,source_id))')
        c.execute("INSERT INTO lineage.lineage_components VALUES('event_source_111111111111',%s)", (json.dumps({'event_type':'manual.triggered'}),))
        c.execute("INSERT INTO lineage.event_source_progress VALUES('lineage_ed619225d417','event_source_111111111111','{\"paused\":true}')")
        try:
            result = initialize(manifest, c, roots, site_config_path=site)
            assert not result['work_dispatched']
            assert c.execute('SELECT provider_timing_proxy_enabled FROM execution_runtime.service_settings').fetchone()['provider_timing_proxy_enabled'] is False
            assert c.execute("SELECT enabled FROM wall.wall_settings WHERE name='scheduled_jobs'").fetchone()['enabled'] is False
            assert json.loads((tmp_path / 'instances/settings/resident_embeddings.json').read_text()) == {'enabled':False}
            assert json.loads(c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json']) == {'paused':True}
            assert json.loads(site.read_text())['deployment']['system_workflow']['request_timing_lineage_id'] == 'lineage_ed619225d417'
            # Local administrator chooses enabled; retries/upgrades must keep it.
            c.execute('UPDATE execution_runtime.service_settings SET provider_timing_proxy_enabled=TRUE')
            c.execute('UPDATE wall.wall_settings SET enabled=TRUE')
            second = initialize(manifest, c, roots, mode='upgrade', existing_feature_ids=resolution['included_features'], site_config_path=site)
            assert all(s['status'] == 'preserved_existing' for s in second['features'])
            assert c.execute('SELECT provider_timing_proxy_enabled FROM execution_runtime.service_settings').fetchone()['provider_timing_proxy_enabled'] is True
            assert c.execute('SELECT enabled FROM wall.wall_settings').fetchone()['enabled'] is True
        finally:
            for schema in ['lineage','execution_runtime','wall','distribution_installation']:
                c.execute('DROP SCHEMA ' + schema + ' CASCADE')


def test_code_support_has_no_runtime_default_or_initialization_action():
    selected = {'feature_states': {'wall_scheduling': {'included': True, 'default_enabled': False},
                                  'wall': {'included': False, 'default_enabled': False}}}
    result = resolve_features(selected)
    assert result['valid']
    support = result['states']['wall']
    assert support['included'] and support['kind'] == 'code_support'
    assert not support['default_applicable']
    assert support['default_enabled'] is False  # Preserve old intent without inventing an action.
    assert support['resolved_default_enabled'] is None and support['effective_enabled'] is None
    assert support['code_required_by_details'] == [{'id': 'wall_scheduling', 'title': 'WALL 定时任务', 'source': 'wall_scheduling'}]
    actions = [a for a in result['initialization']['actions'] if a['id'] != 'permission_system']
    assert len(actions) == 1 and actions[0]['id'] == 'wall_scheduling'
    assert actions[0]['desired_enabled'] is False and actions[0]['effective_enabled'] is False
    unknown = resolve_features({'feature_states': {'unknown_flow': {'included': True, 'default_enabled': True}}})
    assert not unknown['valid']
    assert unknown['states']['unknown_flow']['readiness'] == 'conflict'
    assert unknown['states']['unknown_flow']['effective_enabled'] is False
