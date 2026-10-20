"""Offline native switches on an explicit installer-owned target connection.

The native owner defines bindings and checkpoint transitions. This adapter only
validates target definitions, merges binding keys and persists/readbacks using
the caller's transaction. It never resolves an ambient database or site.
"""
from copy import deepcopy
import json

from psycopg import sql

from management_portal.portal_feature_switches import installation_mapping, mapping_contract
from management_portal.society_service.component_runtime import update_pause_checkpoint


def _object(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise ValueError('Native checkpoint/configuration must be an object')
    return value


def _storage(kind):
    if kind == 'event_source':
        return 'event_source_progress', 'source_id'
    if kind == 'effect':
        return 'operator_work', 'operator_id'
    raise ValueError('Unsupported native switch component kind')


def _read(cursor, binding, component):
    table, key = _storage(component['kind'])
    row = cursor.execute(sql.SQL('SELECT checkpoint_json FROM lineage.{} WHERE lineage_id=%s AND {}=%s FOR UPDATE')
                         .format(sql.Identifier(table), sql.Identifier(key)),
                         (binding['lineage_id'], component['id'])).fetchone()
    if row is None:
        return None
    checkpoint = _object(row['checkpoint_json'])
    if 'paused' in checkpoint and type(checkpoint['paused']) is not bool:
        raise ValueError('Native checkpoint paused must be boolean')
    return checkpoint


def plan(action, cursor, site, *, mode, preserve=False):
    """Validate the entire binding and read all states before any writes."""
    if action['effective_enabled'] and action.get('missing_requirements'):
        raise ValueError('Unready native switch cannot initialize enabled')
    name = action['id']
    contract = mapping_contract(name)
    section = contract['site_section'][1]
    deployment = site.get('deployment', {})
    if not isinstance(deployment, dict):
        raise ValueError('Target deployment configuration must be an object')
    existing = deployment.get(section, {})
    if not isinstance(existing, dict):
        raise ValueError('Target switch configuration must be an object')
    fields = ('lineage_id', contract['components_field'])
    has_local_binding = any(key in existing for key in fields)
    keep = preserve or (mode == 'upgrade' and has_local_binding)
    config = site if keep else action.get('site_config')
    if not isinstance(config, dict):
        raise ValueError('Native switch action requires its minimal site_config')
    # Read only the target definitions. No source selection or runtime is used.
    rows = {('lineage', table): cursor.execute(sql.SQL('SELECT * FROM lineage.{}')
                                             .format(sql.Identifier(table))).fetchall()
            for table in ('lineages', 'lineage_nodes', 'lineage_components')}
    selected = [r['lineage_id'] for r in rows[('lineage', 'lineages')] if not r.get('deleted_at')]
    mapping = installation_mapping(name, site_config=config, selected_lineage_ids=selected, definition_rows=rows)
    result = {'status': 'preserved_existing' if keep else 'applied', 'binding': mapping['binding'],
              'diagnostics': list(mapping['diagnostics']), 'states': [], 'site_fragment': None}
    if mapping['readiness'] == 'conflict':
        raise ValueError('Target native binding conflict: ' + json.dumps(mapping['diagnostics']))
    if mapping['readiness'] != 'ready':
        result['status'] = 'needs_configuration'
        return result
    binding = mapping['binding']
    if keep:
        if action.get('binding') != binding:
            result['diagnostics'].append({'code': 'preserved_target_binding',
                                          'message': 'Incoming binding differs; existing target mapping wins'})
    else:
        if action.get('binding') != binding or action.get('site_config') != mapping['site_config']:
            raise ValueError('Manifest native binding differs from its minimal configuration')
        if has_local_binding:
            for key in fields:
                if key in existing and existing[key] != mapping['site_config']['deployment'][section][key]:
                    raise ValueError('Existing target native binding conflicts with initialization')
            # Do not silently repair a partially configured local binding.
            if any(key not in existing for key in fields):
                result.update(status='needs_configuration', diagnostics=[{'code': 'partial_target_binding'}])
                return result
        result['site_fragment'] = mapping['site_config']
    components = {row['node_id']: row for row in rows[('lineage', 'lineage_components')]}
    for component in binding['components']:
        checkpoint = _read(cursor, binding, component)
        config = _object(components[component['id']]['config_json'])
        result['states'].append({**component, 'checkpoint': checkpoint, 'config': config})
    if mode == 'upgrade' and any(s['checkpoint'] is not None for s in result['states']):
        keep = True
        result['status'] = 'preserved_existing'
    if keep and any(s['checkpoint'] is None for s in result['states']):
        result.update(status='needs_configuration', site_fragment=None)
        result['diagnostics'].append({'code': 'missing_target_runtime_state', 'message': 'Existing mapping has incomplete native state'})
    if keep and not has_local_binding:
        result.update(status='needs_configuration', site_fragment=None)
        result['diagnostics'].append({'code': 'missing_target_binding', 'message': 'Existing feature/state has no target mapping; refusing rebinding'})
    return result


def apply(action, prepared, cursor, site):
    """Persist native checkpoints then read actual target values for evidence."""
    evidence = {'status': prepared['status'], 'binding': prepared['binding'],
                'diagnostics': prepared['diagnostics'], 'native_states': []}
    if prepared['status'] == 'needs_configuration':
        return evidence, False
    binding = prepared['binding']
    for component in prepared['states']:
        if prepared['status'] == 'applied':
            checkpoint = deepcopy(component['checkpoint']) if component['checkpoint'] is not None else {'paused': True}
            update_pause_checkpoint(checkpoint, component['kind'], not action['effective_enabled'], component['config'])
            table, key = _storage(component['kind'])
            cursor.execute(sql.SQL('INSERT INTO lineage.{}(lineage_id,{},checkpoint_json) VALUES(%s,%s,%s) '
                                   'ON CONFLICT(lineage_id,{}) DO UPDATE SET checkpoint_json=EXCLUDED.checkpoint_json')
                           .format(sql.Identifier(table), sql.Identifier(key), sql.Identifier(key)),
                           (binding['lineage_id'], component['id'], json.dumps(checkpoint)))
        actual = _read(cursor, binding, component)
        if actual is None:
            raise ValueError('Native switch readback missing')
        paused = actual.get('paused', False)
        if prepared['status'] == 'applied' and paused != (not action['effective_enabled']):
            raise ValueError('Native switch readback differs from initialization')
        evidence['native_states'].append({'id': component['id'], 'kind': component['kind'], 'paused': paused})
    changed = False
    if prepared['site_fragment']:
        for section, fields in prepared['site_fragment']['deployment'].items():
            target = site.setdefault('deployment', {}).setdefault(section, {})
            for key, value in fields.items():
                if key not in target:
                    target[key] = deepcopy(value)
                    changed = True
    paused = [s['paused'] for s in evidence['native_states']]
    evidence['actual_enabled'] = not all(paused)
    evidence['partial'] = any(paused) and not all(paused)
    evidence['event_sources'] = [{'lineage_id': binding['lineage_id'], 'source_id': s['id']}
                                 for s in evidence['native_states'] if s['kind'] == 'event_source']
    return evidence, changed
