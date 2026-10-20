"""Release permission defaults use the target's native owner, never source state."""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from management_portal.distribution.features import catalog, normalize_snapshot, resolve_features, selection_snapshot
from management_portal.distribution.initialization import initialize, _TargetDatabase
from management_portal.execution.service_settings import ExecutionSettings
from management_portal.distribution.store import ReleaseStore


@pytest.mark.parametrize('intent', [None, True, False])
def test_permission_default_is_runtime_intent_with_required_code(intent):
    snapshot = {'feature_states': {'permission_system': {'included': False, 'default_enabled': intent}}}
    result = resolve_features(snapshot)
    spec = next(f for f in catalog()['features'] if f['id'] == 'permission_system')
    assert spec['mandatory'] and spec['default_applicable']
    state = result['states']['permission_system']
    assert state['included'] and state['kind'] == 'runtime_feature'
    assert state['effective_enabled'] is (intent is not False)
    action = next(a for a in result['initialization']['actions'] if a['id'] == 'permission_system')
    assert action['adapter'] == 'permission_setting'
    assert action['effective_enabled'] is (intent is not False)
    frozen = selection_snapshot(snapshot, result)
    assert normalize_snapshot(frozen)['feature_states']['permission_system']['default_enabled'] is intent
    assert snapshot['feature_states']['permission_system']['included'] is False


def test_permission_intent_save_copy_and_frozen_run():
    store = ReleaseStore()
    off = {'feature_states': {'permission_system': {'included': True, 'default_enabled': False}}}
    saved = store.save_config(actor='permission-default-test', name='off', snapshot=off)
    run = store.create_run(actor='permission-default-test', request_key='permission-off', mode='publish', config_id=saved['config_id'])
    on = {'feature_states': {'permission_system': {'included': True, 'default_enabled': True}}}
    store.save_config(actor='permission-default-test', name='on', snapshot=on, config_id=saved['config_id'])
    copy = store.save_config(actor='permission-default-test', name='copy', snapshot=store.get_config(saved['config_id'])['snapshot'])
    assert copy['snapshot']['feature_states'] == on['feature_states']
    assert store.get_run(run['run_id'])['snapshot']['feature_states'] == off['feature_states']


@pytest.mark.parametrize('intent', [True, False])
def test_permission_default_applies_on_fresh_target_and_preserves_local_choice(tmp_path, intent):
    resolution = resolve_features({'feature_states': {'permission_system': {'included': True, 'default_enabled': intent}}})
    manifest = {'feature_resolution': resolution}
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True, row_factory=dict_row) as c:
        try:
            result = initialize(manifest, c, {'instances_root': str(tmp_path)})
            owner = ExecutionSettings(_TargetDatabase(c, 'execution_runtime'))
            assert owner.permission_system_enabled() is intent
            assert not result['work_dispatched']
            owner.update_permission_system(not intent)
            for mode in ['fresh', 'upgrade']:
                retried = initialize(manifest, c, {'instances_root': str(tmp_path)}, mode=mode)
                assert owner.permission_system_enabled() is not intent
                assert next(r for r in retried['features'] if r['id'] == 'permission_system')['status'] == 'preserved_existing'
            # An existing installation without this feature's ledger also owns its switch.
            c.execute('DROP SCHEMA distribution_installation CASCADE')
            upgraded = initialize(manifest, c, {'instances_root': str(tmp_path)}, mode='upgrade')
            assert owner.permission_system_enabled() is not intent
            assert next(r for r in upgraded['features'] if r['id'] == 'permission_system')['status'] == 'preserved_existing'
        finally:
            c.execute('DROP SCHEMA IF EXISTS execution_runtime CASCADE')
            c.execute('DROP SCHEMA IF EXISTS distribution_installation CASCADE')
