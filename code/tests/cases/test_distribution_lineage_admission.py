"""Imported teams accept fresh events without replaying source work."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from management_common.instance_manifest import load
from management_portal.distribution.initialization import initialize
from management_portal.distribution.initialization import restore_lineage_events
from management_portal.distribution.features import SYSTEM
from tests.cases.instance_test_support import write_instance
from tests.cases.test_distribution_application_flows import distribution_target_dsn


@pytest.mark.parametrize('entrypoint', ['installer', 'child'])
@pytest.mark.parametrize('seed_version', [None, '', 'different-seed'])
def test_seed_repair_requires_positive_provenance_even_when_runtime_accepts_metadata(
        tmp_path, monkeypatch, entrypoint, seed_version):
    from management_common import instance_manifest
    from scripts import tfi_release_install as installer, tfi_release_initialization as child
    instance = load(write_instance(tmp_path / 'instance'))
    if seed_version is None:
        instance.document.pop('seed_version')
    else:
        instance.document['seed_version'] = seed_version
    instance_manifest.validate(instance.root, instance.document)
    manifest = tmp_path / 'seed.json'
    manifest.write_text(json.dumps({'history_freeze': {'event_sources_paused': True}}))
    monkeypatch.setattr(instance_manifest, 'load', lambda: instance)
    with pytest.raises(ValueError, match='seed'):
        if entrypoint == 'installer':
            installer.repair_lineage_events(instance, manifest)
        else:
            child.restore_lineage_events(manifest)


def test_imported_ordinary_team_accepts_new_events_and_preserves_local_choices(tmp_path, distribution_target_dsn):
    manifest = {'history_freeze': {'event_sources_paused': True},
                'feature_resolution': {'valid': True, 'initialization': {'actions': []}}}
    instance = load(write_instance(tmp_path / 'instance'))
    with psycopg.connect(distribution_target_dsn, autocommit=True, row_factory=dict_row) as c:
        c.execute('CREATE SCHEMA lineage')
        c.execute('CREATE TABLE lineage.lineage_nodes(node_id TEXT PRIMARY KEY,lineage_id TEXT,node_type TEXT,deleted_at TEXT)')
        c.execute('CREATE TABLE lineage.lineage_components(node_id TEXT PRIMARY KEY,config_json TEXT)')
        c.execute('CREATE TABLE lineage.event_source_progress(lineage_id TEXT,source_id TEXT,checkpoint_json TEXT,PRIMARY KEY(lineage_id,source_id))')
        cases = [
            ('facade', 'ordinary', 'manual.triggered', {'paused': True}),
            ('observer', 'ordinary', 'resident.completed', {'paused': True}),
            ('timer', 'ordinary', 'time.reached', {'paused': True}),
            ('system', SYSTEM, 'manual.triggered', {'paused': True}),
            ('local_pause', 'ordinary', 'manual.triggered', {'paused': True, 'paused_at': 'local-choice'}),
            ('live', 'ordinary', 'manual.triggered', {'consumed_cursors': ['local-event']}),
            ('managed', 'ordinary', 'manual.triggered', {'paused': True}),
        ]
        manifest['feature_resolution']['initialization']['actions'] = [{
            'id': 'disabled-flow', 'adapter': 'lineage_events', 'effective_enabled': False,
            'missing_requirements': [], 'event_sources': [{'lineage_id': 'ordinary', 'source_id': 'managed'}]}]
        for cid, lid, kind, checkpoint in cases:
            c.execute('INSERT INTO lineage.lineage_nodes VALUES(%s,%s,%s,NULL)', (cid, lid, 'event_source'))
            c.execute('INSERT INTO lineage.lineage_components VALUES(%s,%s)', (cid, json.dumps({'event_type': kind})))
            c.execute('INSERT INTO lineage.event_source_progress VALUES(%s,%s,%s)', (lid, cid, json.dumps(checkpoint)))
        result = initialize(manifest, c, instance)
        states = {r['source_id']: json.loads(r['checkpoint_json']) for r in c.execute('SELECT * FROM lineage.event_source_progress')}
        assert not states['facade'].get('paused'), 'the facade event must reach the Router'
        assert states['observer'] == {'rebaseline': True}, 'old observation transitions must not replay'
        for cid in ('timer', 'system', 'local_pause', 'live', 'managed'):
            assert states[cid] == next(row[3] for row in cases if row[0] == cid)
        assert result['work_dispatched'] is False
        assert result['lineage_timer_required'] is False
        c.execute('UPDATE lineage.event_source_progress SET checkpoint_json=%s WHERE source_id=%s', ('{"paused":true}', 'facade'))
        initialize(manifest, c, instance)
        assert json.loads(c.execute("SELECT checkpoint_json FROM lineage.event_source_progress WHERE source_id='facade'").fetchone()['checkpoint_json']) == {'paused': True}


@pytest.mark.parametrize('started,old_ledger', [(False, False), (True, False), (True, True), (True, 'partial')])
def test_public_example_seed_repair_is_once_only_and_does_not_replay(distribution_target_dsn, monkeypatch, started, old_ledger):
    from management_portal.distribution.history import verify_frozen
    seed = Path(__file__).resolve().parents[3] / 'instance/example_instance/seed'
    manifest = json.loads((seed / 'manifest.json').read_text())['engineering']
    from tests.cases.postgres_test_database import _binary, _postgres_environment
    imported = subprocess.run([str(_binary('psql')), distribution_target_dsn, '-X', '-v', 'ON_ERROR_STOP=1',
                               '-f', str(seed / manifest['database']['path'])],
                              env=_postgres_environment(), capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr[-1200:]
    with psycopg.connect(distribution_target_dsn, autocommit=True, row_factory=dict_row) as c:
        # The same source pause which blocked all shipped teams is present.
        with c.cursor(row_factory=psycopg.rows.tuple_row) as cursor:
            assert verify_frozen(cursor)['passed']
        if started:
            # SocietyService performs this real migration at startup, before
            # an administrator can run the repair on an installed instance.
            from management_portal.distribution.initialization import _TargetDatabase
            from management_portal.society_service import lineage_program_runtime as runtime

            class Database(_TargetDatabase):
                read_current = _TargetDatabase.transaction

                def write(self, fn):
                    with self.transaction() as cursor:
                        return fn(cursor)

            monkeypatch.setattr(runtime, 'get_lineage_data_store', lambda: SimpleNamespace(database=Database(c, 'lineage')))
            assert runtime.migrate_stored_runtime()['migrated'] > 0
        if old_ledger:
            c.execute('CREATE SCHEMA distribution_installation')
            c.execute('CREATE TABLE distribution_installation.feature_initializations(id TEXT PRIMARY KEY,evidence JSONB NOT NULL)')
            restored = []
            if old_ledger == 'partial':
                row = c.execute("SELECT n.lineage_id,n.node_id AS source_id FROM lineage.lineage_nodes n "
                                "JOIN lineage.lineage_components c ON c.node_id=n.node_id "
                                "WHERE n.node_type='event_source' AND n.lineage_id<>%s "
                                "AND c.config_json::jsonb->>'event_type'='resident.message_received' LIMIT 1", (SYSTEM,)).fetchone()
                restored = [dict(row)]
            c.execute("INSERT INTO distribution_installation.feature_initializations VALUES('ordinary_lineage_events.v1',%s)",
                      (json.dumps({'status': 'applied', 'event_sources': restored, 'work_dispatched': False}),))
        old_work = {table: c.execute('SELECT count(*) AS n FROM lineage.' + table).fetchone()['n']
                    for table in ('pending_event_deliveries', 'resident_execution_work', 'trigger_paths')}
        result = restore_lineage_events(manifest, c)
        assert result['event_sources'], 'the actual public seed must have usable team event entries'
        assert result['work_dispatched'] is False
        if old_ledger == 'partial':
            assert not any(event in result['event_sources'] for event in restored)
            old = restored[0]
            state = c.execute('SELECT checkpoint_json FROM lineage.event_source_progress WHERE lineage_id=%s AND source_id=%s',
                              (old['lineage_id'], old['source_id'])).fetchone()['checkpoint_json']
            assert (json.loads(state) if isinstance(state, str) else state)['paused'] is True
        for event in result['event_sources']:
            row = c.execute('SELECT config_json FROM lineage.lineage_components WHERE node_id=%s', (event['source_id'],)).fetchone()
            config = row['config_json'] if isinstance(row['config_json'], dict) else json.loads(row['config_json'])
            assert config['event_type'] != 'time.reached'
            assert event['lineage_id'] != SYSTEM
        # Check the actual shipped Family facade entries, including both
        # development teams, rather than only an aggregate restored count.
        entries = c.execute("SELECT f.title,f.lineage_id,e.source_node_id AS source_id,p.checkpoint_json "
                            "FROM lineage.function_blocks f JOIN lineage.family_residents r "
                            "ON r.family_id=f.function_block_id AND r.role='facade' "
                            "JOIN lineage.lineage_edges e ON e.target_node_id=r.resident_id AND e.edge_type='watch' "
                            "JOIN lineage.lineage_components co ON co.node_id=e.source_node_id "
                            "JOIN lineage.event_source_progress p ON p.lineage_id=f.lineage_id AND p.source_id=e.source_node_id "
                            "WHERE co.config_json::jsonb->>'event_type'='resident.message_received'").fetchall()
        assert {'开发团队', 'Portal 开发'} <= {entry['title'] for entry in entries}
        for entry in entries:
            if old_ledger == 'partial' and any(entry['source_id'] == event['source_id'] for event in restored):
                continue
            checkpoint = entry['checkpoint_json']
            assert not (json.loads(checkpoint) if isinstance(checkpoint, str) else checkpoint).get('paused'), entry['title']
        assert old_work == {table: c.execute('SELECT count(*) AS n FROM lineage.' + table).fetchone()['n'] for table in old_work}
        assert restore_lineage_events(manifest, c)['status'] == 'preserved_existing'
        print(f"Public example seed: restored {len(result['event_sources'])} non-timer team sources; no work replayed")


@pytest.mark.parametrize('running,child_error', [(False, False), (True, False), (False, True)])
def test_repair_command_requires_stopped_apps_and_restores_postgres_lifecycle(tmp_path, monkeypatch, running, child_error):
    from scripts import tfi_release_install as installer
    instance = load(write_instance(tmp_path / 'instance'))
    manifest = tmp_path / 'seed.json'
    manifest.write_text(json.dumps({'engineering': {'run_id': instance.document['seed_version'],
                                                   'history_freeze': {'event_sources_paused': True}}}))
    calls = []
    monkeypatch.setattr(installer.instance_services, 'status', lambda _: {
        'postgres': {'active': 'inactive'}, 'web': {'active': 'active' if running else 'inactive'}})
    monkeypatch.setattr(installer.instance_services, 'controller', lambda _: SimpleNamespace(
        start=lambda unit: calls.append('pg_start'), stop=lambda unit: calls.append('pg_stop')))
    monkeypatch.setattr(installer.instance_services, 'wait_database', lambda _: calls.append('pg_ready'))

    def child(_instance, script, command, seed):
        calls.append(command)
        assert script == 'tfi_release_initialization.py' and Path(seed) == manifest
        if child_error:
            raise RuntimeError('repair failed')
        return '{"status":"applied","event_sources":[],"work_dispatched":false}'

    monkeypatch.setattr(installer, 'code_child', child)
    if running:
        with pytest.raises(ValueError, match='Stop the instance'):
            installer.repair_lineage_events(instance, manifest)
        assert calls == []
    elif child_error:
        with pytest.raises(RuntimeError, match='repair failed'):
            installer.repair_lineage_events(instance, manifest)
        assert calls == ['pg_start', 'pg_ready', 'restore-lineage-events', 'pg_stop']
    else:
        assert installer.repair_lineage_events(instance, manifest)['status'] == 'repaired'
        assert calls == ['pg_start', 'pg_ready', 'restore-lineage-events', 'pg_stop']
    manifest.write_text('{"run_id":"a-different-seed","history_freeze":{"event_sources_paused":true}}')
    with pytest.raises(ValueError, match='original frozen seed'):
        installer.repair_lineage_events(instance, manifest)
