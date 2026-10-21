"""Explicit disposable PostgreSQL integration; never attaches to a Portal DB.

Run with TFI_UPGRADE_TEST_PG_BIN (and optional PG_LIB/PG_SHARE) set. Runtime
authority/projection are test boundaries; native checkpoint storage, reducers,
admission and compensation execute against the real independent database.
"""
from contextlib import contextmanager, ExitStack, nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psycopg
from psycopg.rows import dict_row

from management_portal.distribution.initialization import initialize, _TargetDatabase


@unittest.skipUnless(os.environ.get('TFI_UPGRADE_TEST_PG_BIN'), 'explicit independent PG binaries required')
class NativeTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='tfi-native-switch-')
        cls.root = Path(cls.temp.name)
        cls.pg = Path(os.environ['TFI_UPGRADE_TEST_PG_BIN'])
        cls.env = {'PATH': '/usr/bin:/bin', 'HOME': str(cls.root), 'LANG': 'C.UTF-8',
                   'LD_LIBRARY_PATH': os.environ.get('TFI_UPGRADE_TEST_PG_LIB', '')}
        cls.socket = cls.root / 's'
        cls.socket.mkdir()
        init = [str(cls.pg / 'initdb'), '-D', str(cls.root / 'pg'), '-U', 'tfi', '-A', 'trust', '--no-locale', '--encoding=UTF8']
        if os.environ.get('TFI_UPGRADE_TEST_PG_SHARE'):
            init += ['-L', os.environ['TFI_UPGRADE_TEST_PG_SHARE']]
        subprocess.run(init, env=cls.env, check=True, capture_output=True)
        subprocess.run([str(cls.pg / 'pg_ctl'), '-D', str(cls.root / 'pg'), '-l', str(cls.root / 'pg.log'),
                        '-o', f"-k {cls.socket} -p 56439 -c listen_addresses=''", '-w', 'start'],
                       env=cls.env, check=True, capture_output=True)
        cls.addClassCleanup(cls.cleanup_cluster)
        cls.c = psycopg.connect(f'host={cls.socket} port=56439 user=tfi dbname=postgres', autocommit=True, row_factory=dict_row)
        cls.addClassCleanup(cls.c.close)
        assert cls.c.execute('SHOW data_directory').fetchone()['data_directory'] == str(cls.root / 'pg')
        cls.evidence = {'scope': 'independent PG component integration; no full_test run created',
                        'data_directory': str(cls.root / 'pg'), 'socket': str(cls.socket),
                        'listen_addresses': cls.c.execute('SHOW listen_addresses').fetchone()['listen_addresses'],
                        'postgres_version': cls.c.execute('SELECT version() AS version').fetchone()['version'],
                        'source_services_called': False, 'checks': {}}
        cls.roots = {k: str(cls.root / 'data' / k) for k in ('share_root','staging_root','instances_root','accounts_root','secrets_dir')}
        cls.paths = cls.root / 'paths.json'
        cls.paths.write_text(json.dumps(cls.roots))
        cls.environment = patch.dict(os.environ, {'PORTAL_PATHS_CONFIG': str(cls.paths),
                                                  'PORTAL_DATABASE_URL': cls.c.info.dsn,
                                                  'PYTHONDONTWRITEBYTECODE': '1'})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)

    @classmethod
    def cleanup_cluster(cls):
        subprocess.run([str(cls.pg / 'pg_ctl'), '-D', str(cls.root / 'pg'), '-m', 'fast', '-w', 'stop'],
                       env=cls.env, check=True, capture_output=True)
        cls.temp.cleanup()
        if os.environ.get('TFI_NATIVE_SWITCH_EVIDENCE') and hasattr(cls, 'evidence'):
            cls.evidence['cluster_stopped'] = True
            cls.evidence['root_removed'] = not cls.root.exists()
            Path(os.environ['TFI_NATIVE_SWITCH_EVIDENCE']).write_text(json.dumps(cls.evidence, ensure_ascii=False, indent=2))

    def setUp(self):
        from management_portal.society_service import program_work_storage
        self.c.execute('DROP SCHEMA IF EXISTS distribution_installation CASCADE')
        self.c.execute('DROP SCHEMA IF EXISTS lineage CASCADE')
        self.c.execute('CREATE SCHEMA lineage')
        self.c.execute('CREATE TABLE lineage.lineages(lineage_id text PRIMARY KEY,deleted_at text)')
        self.c.execute('CREATE TABLE lineage.lineage_nodes(node_id text PRIMARY KEY,lineage_id text,node_type text,deleted_at text)')
        self.c.execute('CREATE TABLE lineage.lineage_components(node_id text PRIMARY KEY,config_json jsonb)')
        with _TargetDatabase(self.c, 'lineage').transaction() as cur:
            program_work_storage.create_schema(cur)
        self.c.execute("INSERT INTO lineage.lineages VALUES('local_lineage',NULL)")
        self.configs = {'event_a': {'event_type': 'manual.triggered'}, 'event_b': {'event_type': 'time.reached',
                        'schedule_mode': 'at', 'scheduled_at': '2000-01-01T00:00:00Z'}, 'effect_a': {}}
        for cid, cfg in self.configs.items():
            self.c.execute('INSERT INTO lineage.lineage_nodes VALUES(%s,%s,%s,NULL)',
                           (cid, 'local_lineage', 'effect' if cid.startswith('effect') else 'event_source'))
            self.c.execute('INSERT INTO lineage.lineage_components VALUES(%s,%s)', (cid, json.dumps(cfg)))
        self.site = self.root / 'site.json'
        self.site.write_text(json.dumps({'keep': {'value': 1}, 'deployment': {'pulse': {'other': 'retained'},
                              'development_projection': {'other': 'retained'}}}))

    def actions(self, enabled):
        from management_portal.portal_feature_switches import installation_mapping
        site = {'deployment': {'pulse': {'lineage_id': 'local_lineage', 'event_source_ids': ['event_a','event_b']},
                'development_projection': {'lineage_id': 'local_lineage', 'architecture_audit_effect_id': 'effect_a'}}}
        rows = {('lineage', t): self.c.execute(f'SELECT * FROM lineage.{t}').fetchall()
                for t in ('lineages','lineage_nodes','lineage_components')}
        result = []
        for name in ('pulse','architecture_audit'):
            m = installation_mapping(name, site_config=site, selected_lineage_ids=['local_lineage'], definition_rows=rows)
            assert m['readiness'] == 'ready'
            result.append({'id': name, 'adapter': 'native_component_switch', 'binding': m['binding'],
                           'site_config': m['site_config'], 'effective_enabled': enabled, 'missing_requirements': []})
        return result

    def initialize(self, enabled, **kwargs):
        actions = kwargs.pop('actions') if 'actions' in kwargs else self.actions(enabled)
        return initialize({'feature_resolution': {'valid': True, 'initialization': {'actions': actions}}},
                          self.c, self.roots, site_config_path=self.site, **kwargs)

    def checkpoints(self):
        return {t: self.c.execute(f'SELECT * FROM lineage.{t} ORDER BY 1,2').fetchall()
                for t in ('event_source_progress','operator_work')}

    @contextmanager
    def native_runtime(self):
        from management_portal import portal_feature_switches as switches
        from management_portal.society_service import (component_runtime as cr, lineage_program_runtime as rt,
            program_work_storage as storage, lineage_domain, system_workflow_switches)
        database = _TargetDatabase(self.c, 'lineage')
        @contextmanager
        def transaction(lid):
            with storage.transaction(database, lid) as state:
                yield state
        def read_object(lid, field, key):
            with database.transaction() as cursor:
                return storage.read_object(cursor, lid, field, key)
        def views(lid, actor, *, component_id):
            kind = 'effect' if component_id.startswith('effect') else 'event_source'
            state = read_object(lid, 'operators' if kind == 'effect' else 'event_sources', component_id) or {}
            return [{'component_id': component_id, 'kind': kind, 'paused': bool(state.get('paused'))}]
        with ExitStack() as stack:
            for obj, key, value in [(rt, '_runtime_transaction', transaction), (rt, '_read_object', read_object),
                (cr, '_plan', lambda lid, actor: {'event_sources': {k:{**v, 'kind':'event_source'} for k,v in self.configs.items() if k.startswith('event')},
                                                  'effects': {'effect_a': {}}}),
                (cr, 'views', views), (lineage_domain, '_lineage_execution_authority', lambda lid: nullcontext()),
                (system_workflow_switches, 'project_after_commit', lambda lid: None),
                (switches, 'read_site_config', lambda: json.loads(self.site.read_text()))]:
                stack.enter_context(patch.object(obj, key, value))
            yield switches, cr, rt

    def test_fresh_off_on_native_admission_and_pause_preserves_work(self):
        from management_portal.society_service import lineage_program_effect_execution as effect
        result = self.initialize(False)
        self.assertTrue(all(x['native_states'] and not x['actual_enabled'] for x in result['features']))
        self.assertEqual(json.loads(self.site.read_text())['deployment']['pulse']['other'], 'retained')
        # Existing work is not cancelled by a control change.
        self.c.execute("UPDATE lineage.operator_work SET checkpoint_json=checkpoint_json || %s::jsonb",
                       (json.dumps({'pending_deliveries': {'keep': {'value': 1}}, 'custom': 7}),))
        with self.native_runtime() as (switches, cr, rt):
            self.assertFalse(rt.claim_event_occurrence('local_lineage', 'event_a', 'closed')['accepted'])
            with self.assertRaises(effect._EffectPaused): effect._require_effect_open('local_lineage','effect_a')
            switches.set_enabled('pulse', True, 'test-admin')
            switches.set_enabled('architecture_audit', True, 'test-admin')
            self.assertTrue(rt.claim_event_occurrence('local_lineage','event_a','new-after-resume')['accepted'])
            effect._require_effect_open('local_lineage','effect_a')
            switches.set_enabled('architecture_audit', False, 'test-admin')
        states = self.checkpoints()
        effect_state = states['operator_work'][0]['checkpoint_json']
        self.assertEqual(effect_state['pending_deliveries'], {'keep': {'value': 1}})
        self.assertEqual(effect_state['custom'], 7)
        # Native resume consumes an already elapsed at-time instead of replaying it.
        timer = states['event_source_progress'][1]['checkpoint_json']
        self.assertIn('time:2000-01-01T00:00:00Z', timer['consumed_cursors'])
        self.evidence['checks']['native_admission'] = {'paused_event_rejected': True, 'paused_effect_rejected': True,
            'resume_event_accepted': True, 'resume_effect_admitted': True, 'pending_work_preserved': effect_state['pending_deliveries'],
            'expired_time_consumed': timer['consumed_cursors']}

    def test_fresh_enabled_readback_and_ledger_retry_preserve_local_pause(self):
        result = self.initialize(True)
        self.assertTrue(all(f['actual_enabled'] for f in result['features']))
        self.assertTrue(result['lineage_timer_required'])
        with self.native_runtime() as (switches, cr, rt): switches.set_enabled('pulse', False, 'test-admin')
        before = self.checkpoints()
        again = self.initialize(True)
        self.assertEqual(self.checkpoints(), before)
        self.assertEqual(again['features'][0]['status'], 'preserved_existing')
        self.assertFalse(again['lineage_timer_required'])
        self.evidence['checks']['fresh_and_retry'] = {'fresh_states': result['features'],
            'local_repause_preserved': True, 'retry_timer_required': again['lineage_timer_required']}

    def test_upgrade_preserves_mapping_and_exact_checkpoints_and_partial_config(self):
        self.initialize(False)
        with self.native_runtime() as (switches, cr, rt): cr.set_paused('local_lineage','event_a',False,'test-admin')
        before, site = self.checkpoints(), self.site.read_bytes()
        incoming = self.actions(True)
        incoming[0]['binding']['components'].reverse()
        incoming[0]['site_config']['deployment']['pulse']['event_source_ids'].reverse()
        result = self.initialize(True, mode='upgrade', existing_feature_ids=['pulse','architecture_audit'], actions=incoming)
        self.assertEqual(self.checkpoints(), before)

        self.assertEqual(self.site.read_bytes(), site)
        self.assertTrue(result['features'][0]['partial'])
        self.assertEqual(result['features'][0]['diagnostics'][0]['code'], 'preserved_target_binding')
        doc = json.loads(site); del doc['deployment']['pulse']['event_source_ids']
        self.site.write_text(json.dumps(doc)); partial = self.site.read_bytes()
        result = self.initialize(True, mode='upgrade', existing_feature_ids=['pulse','architecture_audit'])
        self.assertEqual(result['features'][0]['status'], 'needs_configuration')
        self.assertEqual(self.site.read_bytes(), partial)
        self.assertEqual(self.checkpoints(), before)
        self.evidence['checks']['upgrade'] = {'exact_checkpoints_preserved': True, 'target_mapping_preserved': True,
            'partial_target_mapping_status': result['features'][0]['status'], 'site_unrelated_fields_preserved': True}

    def test_unready_enabled_default_is_rejected_before_state_writes(self):
        actions = self.actions(True)
        actions[0]['missing_requirements'] = ['local_runtime_requirement']
        with self.assertRaisesRegex(ValueError, 'Unready native switch'): self.initialize(True, actions=actions)
        self.assertEqual(self.checkpoints(), {'event_source_progress': [], 'operator_work': []})

    def test_binding_preflight_rejects_wrong_kind_before_other_action_writes(self):
        incoming = self.actions(False)
        self.c.execute("UPDATE lineage.lineage_nodes SET node_type='effect' WHERE node_id='event_b'")
        before = self.site.read_bytes()
        with self.assertRaisesRegex(ValueError, 'conflict'): self.initialize(False, actions=incoming)
        self.assertEqual(self.checkpoints(), {'event_source_progress': [], 'operator_work': []})
        self.assertEqual(self.site.read_bytes(), before)

    def test_target_transaction_failure_rolls_back_all_native_states_and_site(self):
        before = self.site.read_bytes()
        self.c.execute("""CREATE FUNCTION lineage.reject_second() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.source_id='event_b' THEN RAISE EXCEPTION 'injected target storage failure'; END IF;
            RETURN NEW; END $$""")
        self.c.execute('CREATE TRIGGER reject_second BEFORE INSERT ON lineage.event_source_progress '
                       'FOR EACH ROW EXECUTE FUNCTION lineage.reject_second()')
        with self.assertRaisesRegex(psycopg.errors.RaiseException, 'target storage failure'):
            self.initialize(True)
        self.assertEqual(self.checkpoints(), {'event_source_progress': [], 'operator_work': []})
        self.assertEqual(self.site.read_bytes(), before)
        self.assertIsNone(self.c.execute("SELECT to_regclass('distribution_installation.feature_initializations') AS table").fetchone()['table'])

    def test_upgrade_quiescence_distinguishes_pause_settings_from_pending_work(self):
        from scripts.tfi_release_upgrade import check_quiescent
        self.initialize(False)
        self.c.execute('CREATE TABLE lineage.conversation_input_states(state text)')
        self.c.execute('CREATE TABLE lineage.resident_execution_work(id text)')
        self.c.execute('CREATE SCHEMA IF NOT EXISTS agent')
        self.c.execute('CREATE TABLE IF NOT EXISTS agent.executions(status text)')
        api = SimpleNamespace(clean_environment=lambda target: {'PORTAL_DATABASE_URL': self.c.info.dsn})
        check_quiescent(api, {})
        self.c.execute("UPDATE lineage.operator_work SET checkpoint_json=checkpoint_json || %s::jsonb",
                       (json.dumps({'pending_deliveries': {'unfinished': {}}}),))
        with self.assertRaisesRegex(RuntimeError, 'active/queued'): check_quiescent(api, {})
        self.c.execute("UPDATE lineage.operator_work SET checkpoint_json=checkpoint_json-'pending_deliveries'")
        self.c.execute("INSERT INTO lineage.pending_event_deliveries VALUES('local_lineage','wire','claim',0,'{}')")
        with self.assertRaisesRegex(RuntimeError, 'active/queued'): check_quiescent(api, {})

    def test_multi_component_commit_then_failure_compensates_and_failed_recovery_is_visible(self):
        from management_portal.society_service import system_workflow_switches
        self.initialize(True)
        with self.native_runtime() as (switches, cr, rt):
            calls = []
            def fail_after_commit(lid):
                calls.append(lid)
                if len(calls) == 2: raise RuntimeError('post-commit projection failed')
            with patch.object(system_workflow_switches, 'project_after_commit', fail_after_commit):
                with self.assertRaisesRegex(RuntimeError, 'post-commit'): switches.set_enabled('pulse',False,'test-admin')
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(not row['checkpoint_json'].get('paused',False) for row in self.checkpoints()['event_source_progress']))
            native = cr.set_paused
            def fail_restore(lid, cid, paused, actor):
                if not paused and cid == 'event_b': raise RuntimeError('restore unavailable')
                answer = native(lid,cid,paused,actor)
                if paused and cid == 'event_b': raise RuntimeError('committed before failure')
                return answer
            with patch.object(cr, 'set_paused', fail_restore):
                with self.assertRaisesRegex(RuntimeError, '恢复未确认.*event_b'): switches.set_enabled('pulse',False,'test-admin')
            state = switches.status('pulse','test-admin')
            self.assertTrue(state['partial'] and state['enabled'])
            self.assertEqual([c['paused'] for c in state['components']], [False,True])
            self.evidence['checks']['compensation'] = {'commit_then_fail_calls': len(calls),
                'first_compensation_restored_all_open': True, 'failed_recovery_readback': state,
                'group_atomicity': False, 'runtime_permissions_and_projection': 'test boundary; real native checkpoint storage/reducers'}

    def test_installer_child_uses_explicit_target_and_returns_readback(self):
        from scripts import tfi_release_install as api, tfi_release_initialization as boundary
        (self.root / 'code').symlink_to(Path(__file__).resolve().parents[2], target_is_directory=True)
        self.addCleanup((self.root / 'code').unlink)
        target = {'install_root': str(self.root), 'run_id': 'isolated-component-validation',
                  'supervisor_resident_id': 'resident_e4f55ad12dae', 'service_prefix': 'tfi-native-switch-test',
                  'ports': {'postgres':56439,'nats':14229}, 'database_name':'postgres', 'python':sys.executable,
                  'pg_bin':str(self.pg), 'data_roots':self.roots}
        # Installer's connection uses runtime/s; point it at this private socket.
        (self.root / 'runtime').mkdir(exist_ok=True)
        (self.root / 'runtime/s').symlink_to(self.socket, target_is_directory=True)
        self.addCleanup((self.root / 'runtime/s').unlink)
        manifest={'feature_resolution':{'valid':True,'initialization':{'actions':self.actions(False)}}}
        path=self.root/'runtime/engineering-manifest.json';api.write_json(path,manifest)
        api.write_json(self.root/'config/paths.json', self.roots)
        api.write_json(self.root/'config/site.json', json.loads(self.site.read_text()))
        state={'target':target,'manifest_sha256':api.digest(path),'completed':['database']}
        evidence=self.root/'freeze.json';api.write_json(evidence,{**{k:target[k] for k in ('run_id','supervisor_resident_id')},
            'manifest_sha256':state['manifest_sha256'],'passed':True,'violations':{'old_work':0}})
        controller=Mock();controller.status.return_value={'active':'inactive','pid':0}
        with patch.object(api,'service_controller',return_value=controller):
            result=boundary.apply(api,state,manifest,freeze_evidence=evidence)
        self.assertFalse(result['work_dispatched'])
        self.assertTrue(all(not f['actual_enabled'] for f in result['features']))
        self.assertEqual(state['feature_initialization']['status'],'applied')
        self.evidence['checks']['installer_child'] = {'work_dispatched': result['work_dispatched'],
            'status': state['feature_initialization']['status'], 'features': result['features'],
            'services': 'inactive test controller; real isolated child process and target PG'}


if __name__ == '__main__':
    unittest.main()
