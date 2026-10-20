"""Freeze native mapping inputs; never infer defaults from source runtime."""
from contextlib import nullcontext
from copy import deepcopy
import json
import os

import pytest

from management_portal.distribution import database
from management_portal.distribution.features import resolve_features
from management_portal.distribution.native_switches import FIELD, minimal_site_config
from management_portal.distribution.store import ReleaseStore
from tests.cases.test_feature_switch_mapping import site, definitions

LID = 'lineage_custom'


class Source:
    def __init__(self):
        self.site = site()
        self.definitions = definitions()

    def installation_site_config(self):
        return deepcopy(self.site)

    def definition_rows(self, selected):
        return deepcopy(self.definitions) if LID in selected else {}

    def rows(self, schema, table, *, predicates=None):
        return deepcopy(self.definitions.get((schema, table), []))


def native_actions(result):
    return [action for action in result['initialization']['actions']
            if action['id'] in {'pulse', 'architecture_audit'}]


def snapshot(name='pulse', enabled=False):
    return {'lineages': [{'id': LID}], 'feature_states': {name: {'included': True, 'default_enabled': enabled}}}


@pytest.mark.parametrize('name', ['pulse', 'architecture_audit'])
@pytest.mark.parametrize('enabled', [False, True])
def test_explicit_native_intent_uses_owner_mapping_without_source_runtime(monkeypatch, name, enabled):
    from management_portal import portal_feature_switches as owner
    def forbidden(*args, **kwargs):
        pytest.fail('Catalog must not read/write source runtime or ambient site')
    monkeypatch.setattr(owner, 'read_site_config', forbidden)
    monkeypatch.setattr(owner, 'status', forbidden)
    monkeypatch.setattr(owner, 'set_enabled', forbidden)
    result = resolve_features(snapshot(name, enabled), source=Source())
    assert result['valid']
    action, = native_actions(result)
    assert action['id'] == name and action['adapter'] == 'native_component_switch'
    assert action['desired_enabled'] is enabled and action['effective_enabled'] is enabled
    assert action['mapping_readiness'] == 'ready' and action['mapping_diagnostics'] == []
    assert action['binding']['lineage_id'] == LID
    assert action['site_config'] == {'deployment': {k: v for k, v in minimal_site_config(site())['deployment'].items()
        if k == ('pulse' if name == 'pulse' else 'development_projection')}}
    assert result['states'][name]['initialization_status'] == 'pending_installation'
    assert 'private_unrelated' not in str(result)


def test_code_inclusion_does_not_manufacture_a_default_action():
    result = resolve_features(snapshot(enabled=None), source=Source())
    assert result['valid'] and 'pulse' in result['included_features']
    assert result['states']['pulse']['readiness'] == 'code_only'
    assert result['states']['pulse']['effective_enabled'] is None
    assert native_actions(result) == []


@pytest.mark.parametrize('enabled', [False, True])
def test_unselected_or_missing_mapping_keeps_intent_without_claiming_off(enabled):
    source = Source()
    chosen = snapshot(enabled=enabled)
    chosen['lineages'] = []
    result = resolve_features(chosen, source=source)
    state = result['states']['pulse']
    assert state['resolved_default_enabled'] is enabled
    assert state['readiness'] == 'needs_configuration' and state['effective_enabled'] is None
    assert state['mapping_diagnostics'][0]['code'] == 'lineage_not_selected'
    assert native_actions(result) == []


def test_type_conflict_blocks_generation_and_has_no_action():
    source = Source()
    source.definitions['lineage', 'lineage_nodes'][0]['node_type'] = 'effect'
    result = resolve_features(snapshot(), source=source)
    assert not result['valid'] and result['states']['pulse']['readiness'] == 'conflict'
    assert result['states']['pulse']['mapping_diagnostics'][0]['code'] == 'component_mismatch'
    assert native_actions(result) == []


def test_save_freezes_minimal_site_and_config_run_does_not_follow_source_changes(monkeypatch):
    source = Source()
    monkeypatch.setattr(database, 'read_snapshot', lambda: nullcontext(source))
    store = ReleaseStore()
    config = store.save_config(actor='mapping-reviewer', name='native', snapshot=snapshot())
    assert config['snapshot'][FIELD] == minimal_site_config(site())
    source.site = {}
    run = store.create_run(actor='mapping-reviewer', request_key='native-frozen', mode='publish', config_id=config['config_id'])
    assert run['snapshot'][FIELD] == config['snapshot'][FIELD]
    result = resolve_features(run['snapshot'], source=source, dependencies=run['snapshot']['resource_dependencies'])
    assert native_actions(result)[0]['binding']['lineage_id'] == LID
    copy = store.save_config(actor='mapping-reviewer', name='copy', snapshot=config['snapshot'])
    assert copy['snapshot'][FIELD] == config['snapshot'][FIELD]
    source.definitions['lineage', 'lineage_components'] = []
    changed = resolve_features(run['snapshot'], source=source, dependencies=run['snapshot']['resource_dependencies'])
    assert changed['states']['pulse']['readiness'] == 'needs_configuration'
    assert native_actions(changed) == []


@pytest.mark.skipif(not os.environ.get('TFI_UPGRADE_TEST_PG_BIN'), reason='explicit independent PG binaries required')
def test_resolved_manifest_actions_initialize_target_checkpoints():
    from tests.cases.test_release_native_switches_pg import NativeTargetTests
    from management_portal.distribution.initialization import initialize

    # Reuse the target owner's disposable cluster boundary; never attach to a site DB.
    NativeTargetTests.setUpClass()
    try:
        target = NativeTargetTests()
        evidence = []
        for enabled in (False, True):
            target.setUp()
            source = Source()
            source.definitions = {('lineage', table): target.c.execute(f'SELECT * FROM lineage.{table}').fetchall()
                                  for table in ('lineages', 'lineage_nodes', 'lineage_components')}
            source.definition_rows = lambda selected: deepcopy(source.definitions)
            source.site = {'deployment': {
                'pulse': {'lineage_id': 'local_lineage', 'event_source_ids': ['event_a', 'event_b']},
                'development_projection': {'lineage_id': 'local_lineage', 'architecture_audit_effect_id': 'effect_a'}}}
            selected = {'feature_states': {name: {'included': True, 'default_enabled': enabled}
                                          for name in ('pulse', 'architecture_audit')}, FIELD: minimal_site_config(source.site)}
            resolution = resolve_features(selected, source=source, dependencies={'lineages': [{'id': 'local_lineage'}]})
            assert resolution['valid']
            # Exercise the serialized manifest boundary used by engineering.generate.
            manifest = json.loads(json.dumps({'selection_snapshot': selected, 'feature_resolution': resolution}))
            result = initialize(manifest, target.c, target.roots, site_config_path=target.site)
            assert {item['id'] for item in result['features']} == {'pulse', 'architecture_audit'}
            assert all(item['actual_enabled'] is enabled for item in result['features'])
            assert result['lineage_timer_required'] is enabled
            checkpoints = target.checkpoints()
            assert len(checkpoints['event_source_progress']) == 2
            assert len(checkpoints['operator_work']) == 1
            assert all(bool(row['checkpoint_json'].get('paused')) is (not enabled)
                       for rows in checkpoints.values() for row in rows)
            installed = json.loads(target.site.read_text())
            assert installed['deployment']['pulse']['event_source_ids'] == ['event_a', 'event_b']
            assert installed['keep'] == {'value': 1}
            evidence.append({'default_enabled': enabled, 'result': result})
        target.evidence['checks']['resolver_manifest_to_target'] = evidence
    finally:
        NativeTargetTests.doClassCleanups()
