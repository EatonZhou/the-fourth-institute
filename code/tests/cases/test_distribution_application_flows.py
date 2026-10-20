"""Fresh release request admission is separate from frozen historical work."""
from copy import deepcopy
import json
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from management_portal.distribution.features import resolve_features, SYSTEM
from management_portal.distribution.initialization import initialize

EVENT = 'event_source_111111111111'
EFFECT = 'effect_222222222222'
RESIDENT = 'resident_333333333333'
BLOCK = 'fbi_444444444444'


@pytest.fixture
def distribution_target_dsn():
    # Installations own fixed schema names. Give this target its own database
    # on the server selected by conftest, without dropping another case's data.
    dsn = os.environ['PORTAL_DATABASE_URL']
    name = 'distribution_target_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            yield make_conninfo(dsn, dbname=name)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


class Source:
    def __init__(self):
        self.target = {'application': 'resident_discovery', 'lineage_id': SYSTEM, 'resident_id': RESIDENT}
        self.definitions = {
            ('lineage', 'lineage_nodes'): [dict(node_id=i, node_type=k, lineage_id=SYSTEM)
                for i, k in [(EVENT, 'event_source'), (EFFECT, 'effect'), (RESIDENT, 'resident')]],
            ('lineage', 'lineage_components'): [
                dict(node_id=EVENT, client_key='system:resident_discovery:event', config_json='{"event_type":"manual.triggered"}'),
                dict(node_id=EFFECT, client_key='system:resident_discovery:effect', config_json='{}')],
            ('lineage', 'lineage_edges'): [
                dict(edge_id='route', lineage_id=SYSTEM, edge_type='route', source_node_id=EVENT, source_port='output', target_node_id=EFFECT, target_port='invoke'),
                dict(edge_id='target', lineage_id=SYSTEM, edge_type='target', source_node_id=EFFECT, source_port='target', target_node_id=RESIDENT, target_port=None)],
            ('lineage', 'function_blocks'): [dict(function_block_id=BLOCK, title='Which Resident')],
            ('lineage', 'function_block_elements'): [dict(function_block_id=BLOCK, element_id=i) for i in (EVENT, EFFECT, RESIDENT)],
        }

    def definition_rows(self, _):
        return deepcopy(self.definitions)

    def rows(self, schema, table, **kwargs):
        return [deepcopy(self.target)] if (schema, table) == ('lineage', 'application_targets') else []


def resolution(source, enabled):
    return resolve_features({'feature_states': {'flow:' + BLOCK: {'included': True, 'default_enabled': enabled}}}, source=source)


@pytest.mark.parametrize('enabled', [True, False])
def test_complete_application_resolves_explicit_intent(enabled):
    result = resolution(Source(), enabled)
    assert result['valid']
    action, = [a for a in result['initialization']['actions'] if a['id'] == 'flow:' + BLOCK]
    assert action['effective_enabled'] is enabled
    assert action['missing_requirements'] == []
    assert action['application_binding']['resident_id'] == RESIDENT


def test_incomplete_definition_is_not_silently_enabled():
    source = Source()
    source.definitions['lineage', 'lineage_edges'].pop()
    action, = [a for a in resolution(source, True)['initialization']['actions'] if a['id'] == 'flow:' + BLOCK]
    assert action['effective_enabled'] is False
    assert action['missing_requirements'] == ['local_workflow_configuration']
    assert 'application_binding' not in action


@pytest.mark.parametrize('enabled', [True, False])
def test_serialized_manifest_initializes_only_new_request_entry(tmp_path, enabled, distribution_target_dsn):
    source = Source()
    manifest = json.loads(json.dumps({'feature_resolution': resolution(source, enabled)}))
    with psycopg.connect(distribution_target_dsn, autocommit=True, row_factory=dict_row) as c:
        c.execute('CREATE SCHEMA lineage')
        c.execute('CREATE TABLE lineage.application_targets(application TEXT,lineage_id TEXT,resident_id TEXT)')
        c.execute('CREATE TABLE lineage.lineage_nodes(node_id TEXT,node_type TEXT,lineage_id TEXT)')
        c.execute('CREATE TABLE lineage.lineage_components(node_id TEXT,client_key TEXT,config_json TEXT)')
        c.execute('CREATE TABLE lineage.lineage_edges(edge_id TEXT,lineage_id TEXT,edge_type TEXT,source_node_id TEXT,source_port TEXT,target_node_id TEXT,target_port TEXT)')
        c.execute('CREATE TABLE lineage.event_source_progress(lineage_id TEXT,source_id TEXT,checkpoint_json TEXT,PRIMARY KEY(lineage_id,source_id))')
        c.execute('INSERT INTO lineage.application_targets VALUES(%s,%s,%s)', ('resident_discovery', SYSTEM, RESIDENT))
        for table in ('lineage_nodes', 'lineage_components', 'lineage_edges'):
            for row in source.definitions['lineage', table]:
                columns = list(row)
                c.execute(f'INSERT INTO lineage.{table} ({",".join(columns)}) VALUES ({",".join(["%s"] * len(columns))})', list(row.values()))
        c.execute('INSERT INTO lineage.event_source_progress VALUES(%s,%s,%s)', (SYSTEM, EVENT, '{"paused":true}'))
        try:
            c.execute('UPDATE lineage.application_targets SET resident_id=%s', ('resident_other',))
            with pytest.raises(ValueError, match='target differs'):
                initialize(manifest, c, {'instances_root': str(tmp_path)})
            assert c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json'] == '{"paused":true}'
            c.execute('UPDATE lineage.application_targets SET resident_id=%s', (RESIDENT,))
            result = initialize(manifest, c, {'instances_root': str(tmp_path)})
            assert result['work_dispatched'] is False
            assert result['lineage_timer_required'] is False
            checkpoint = json.loads(c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json'])
            assert checkpoint.get('paused', False) is (not enabled)
            c.execute('UPDATE lineage.event_source_progress SET checkpoint_json=%s', ('{"paused":true,"local_marker":"unchanged"}',))
            initialize(manifest, c, {'instances_root': str(tmp_path)}, mode='upgrade')
            assert json.loads(c.execute('SELECT checkpoint_json FROM lineage.event_source_progress').fetchone()['checkpoint_json']) == {'paused': True, 'local_marker': 'unchanged'}
        finally:
            c.execute('DROP SCHEMA lineage CASCADE')
            c.execute('DROP SCHEMA IF EXISTS distribution_installation CASCADE')
