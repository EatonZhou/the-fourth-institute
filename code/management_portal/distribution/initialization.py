"""Initialize declared defaults on an explicitly supplied independent database.

No source connection, credentials, service startup, job dispatch or global
runtime settings client is used. The installer owns target validation,
backup/restore, its transaction boundary and the no-active-work check.
"""
from contextlib import contextmanager
import json
from pathlib import Path

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class _TargetDatabase:
    def __init__(self, connection, schema):
        self.connection, self.schema = connection, schema

    @contextmanager
    def transaction(self):
        with self.connection.transaction():
            with self.connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.schema)))
                cursor.execute('SHOW search_path')
                old = cursor.fetchone()['search_path']
                cursor.execute('SELECT set_config(%s,%s,true)', ('search_path', self.schema))
                try:
                    yield cursor
                finally:
                    cursor.execute('SELECT set_config(%s,%s,true)', ('search_path', old))


def _lineage_timer_sources(cursor, results):
    """Only ready, effectively open native time sources need the timer owner."""
    sources = []
    for action in results:
        if (not action.get('effective_enabled') or action.get('missing_requirements')
                or action['status'] not in {'applied', 'preserved_existing'}):
            continue
        for event in action.get('event_sources', []):
            row = cursor.execute('SELECT c.config_json,p.checkpoint_json FROM lineage.lineage_components c '
                                 'JOIN lineage.event_source_progress p ON p.source_id=c.node_id '
                                 'WHERE c.node_id=%s AND p.lineage_id=%s',
                                 (event['source_id'], event['lineage_id'])).fetchone()
            if not row:
                continue
            config = json.loads(row['config_json']) if isinstance(row['config_json'], str) else row['config_json']
            checkpoint = json.loads(row['checkpoint_json']) if isinstance(row['checkpoint_json'], str) else row['checkpoint_json']
            if config.get('event_type') == 'time.reached' and not checkpoint.get('paused', False):
                item = {'feature_id': action['id'], **event}
                if item not in sources:
                    sources.append(item)
    return sources


def initialize(manifest, connection, instance, *, site_config_path=None):
    """Apply a bundle's feature defaults to a newly created instance; return evidence.

    ``connection`` must be the installer-owned connection to the new database
    and ``instance`` its validated manifest. Call after history freeze
    verification and before services. The example instance's content is used
    only at creation; code updates never re-run this. A repeated call on the
    same database keeps defaults already recorded in its ledger.
    """
    resolution = manifest.get('feature_resolution') or {}
    if not resolution:
        return {'status': 'not_declared', 'features': [], 'lineage_timer_required': False,
                'lineage_timer_sources': [], 'work_dispatched': False}
    if resolution.get('valid') is not True or resolution.get('conflicts'):
        raise ValueError('Feature initialization contains unresolved conflicts')
    actions = resolution.get('initialization', {}).get('actions', [])
    supported = {'execution_setting','permission_setting','embedding_setting','wall_setting','application_admission','lineage_events','telepus_services',
                 'native_component_switch'}
    if any(a.get('adapter') not in supported or type(a.get('effective_enabled')) is not bool for a in actions):
        raise ValueError('Unknown or unresolved feature initialization adapter')
    if any(a.get('workflow_binding') or a['adapter'] == 'native_component_switch' for a in actions) and site_config_path is None:
        raise ValueError('Installed workflow switches require the explicit target site_config_path')
    site_path = Path(site_config_path) if site_config_path is not None else None
    site = json.loads(site_path.read_text(encoding='utf-8')) if site_path and site_path.exists() else {}
    site_changed = False
    results = []
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended('tfi.feature-initialization',0))")
            ledger = c.execute("SELECT to_regclass('distribution_installation.feature_initializations') AS present").fetchone()['present']
            applied = {r['id'] for r in c.execute('SELECT id FROM distribution_installation.feature_initializations')} if ledger else set()
            native_plans = {}
            for action in actions:
                if action['adapter'] == 'native_component_switch':
                    from . import target_switches
                    native_plans[action['id']] = target_switches.plan(action, c, site, preserve=action['id'] in applied)
            c.execute('CREATE SCHEMA IF NOT EXISTS distribution_installation')
            c.execute('CREATE TABLE IF NOT EXISTS distribution_installation.feature_initializations(id TEXT PRIMARY KEY,evidence JSONB NOT NULL)')
            for action in actions:
                identity, adapter = action['id'], action['adapter']
                evidence = {**action, 'status': 'applied'}
                if adapter == 'native_component_switch':
                    native, changed = target_switches.apply(action, native_plans[identity], c, site)
                    evidence.update(native)
                    site_changed |= changed
                    if action['missing_requirements'] and evidence['status'] == 'applied':
                        evidence['status'] = 'needs_configuration'
                    if evidence['status'] != 'needs_configuration':
                        c.execute('INSERT INTO distribution_installation.feature_initializations(id,evidence) VALUES(%s,%s) '
                                  'ON CONFLICT(id) DO NOTHING', (identity, Jsonb(evidence)))
                    results.append(evidence)
                    continue
                if identity in applied:
                    results.append({**evidence, 'status': 'preserved_existing'})
                    continue
                enabled = action['effective_enabled']
                if action.get('application_binding'):
                    from .application_flows import validate_target
                    validate_target(c, action['application_binding'])
                if adapter in {'execution_setting', 'permission_setting'}:
                    from management_portal.execution.service_settings import ExecutionSettings
                    owner = ExecutionSettings(_TargetDatabase(connection, 'execution_runtime'))
                    if adapter == 'permission_setting':
                        owner.update_permission_system(enabled)
                    else:
                        owner.update_unmanaged({action['field']: enabled})
                elif adapter == 'embedding_setting':
                    # The location management_common.embedding_feature reads.
                    path = instance.directory('runtime') / 'settings/resident_embeddings.json'
                    if path.exists():
                        evidence['status'] = 'preserved_existing'
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open('x', encoding='utf-8') as stream:
                            stream.write(json.dumps({'enabled': enabled}) + '\n')
                elif adapter == 'wall_setting':
                    with _TargetDatabase(connection, 'wall').transaction() as wall:
                        from management_portal.wall_store import DDL
                        wall.execute(DDL)
                        old = wall.execute("SELECT enabled FROM wall_settings WHERE name='scheduled_jobs'").fetchone()
                        if old:
                            evidence['status'] = 'preserved_existing'
                        else:
                            wall.execute("INSERT INTO wall_settings(name,enabled) VALUES('scheduled_jobs',%s)", (enabled,))
                elif adapter == 'application_admission':
                    with _TargetDatabase(connection, 'lineage').transaction() as target:
                        exists = target.execute("SELECT to_regclass('lineage.application_targets') AS present").fetchone()['present']
                        old = target.execute('SELECT application FROM application_targets WHERE application=%s', (action['application'],)).fetchone() if exists else None
                        if old:
                            target.execute('UPDATE application_targets SET admission_paused=%s WHERE application=%s', (not enabled, action['application']))
                        else:
                            # A target row requires a real Resident/Lineage FK.
                            # Keep the default in package configuration for its
                            # first legitimate creation; never invent a target.
                            evidence['status'] = 'needs_configuration'
                elif adapter == 'telepus_services':
                    # A release cannot infer target service identities or import
                    # source systemd units. Leave native SetTelepusEnabled usable.
                    evidence['status'] = 'needs_configuration' if action['desired_enabled'] else 'disabled'
                if action.get('event_sources') and evidence['status'] != 'preserved_existing':
                    for event in action['event_sources']:
                        definition = c.execute('SELECT config_json FROM lineage.lineage_components WHERE node_id=%s', (event['source_id'],)).fetchone()
                        if not definition:
                            raise ValueError('Initialization references an absent event source')
                        checkpoint = {'paused': True}
                        if enabled:
                            from management_portal.society_service.component_runtime import _resume_event_source
                            config = definition['config_json']
                            _resume_event_source(checkpoint, json.loads(config) if isinstance(config,str) else config)
                        c.execute('INSERT INTO lineage.event_source_progress(lineage_id,source_id,checkpoint_json) VALUES(%s,%s,%s) '
                                  'ON CONFLICT(lineage_id,source_id) DO UPDATE SET checkpoint_json=EXCLUDED.checkpoint_json',
                                  (event['lineage_id'], event['source_id'], json.dumps(checkpoint)))
                if action['missing_requirements'] and evidence['status'] == 'applied':
                    evidence['status'] = 'needs_configuration'
                if action.get('workflow_binding') and evidence['status'] not in {'preserved_existing'}:
                    bindings = site.setdefault('deployment', {}).setdefault('system_workflow', {})
                    for key, lineage_id in action['workflow_binding'].items():
                        if key in bindings and bindings[key] != lineage_id:
                            raise ValueError('Target workflow binding differs from release default')
                        if key not in bindings:
                            bindings[key] = lineage_id
                            site_changed = True
                c.execute('INSERT INTO distribution_installation.feature_initializations(id,evidence) VALUES(%s,%s)', (identity, Jsonb(evidence)))
                results.append(evidence)
            timer_sources = _lineage_timer_sources(c, results)
            if site_changed:
                site_path.parent.mkdir(parents=True, exist_ok=True)
                site_path.write_text(json.dumps(site, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'status': 'initialized', 'features': results, 'work_dispatched': False,
            'lineage_timer_required': bool(timer_sources), 'lineage_timer_sources': timer_sources}
