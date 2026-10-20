"""Validate packaged request flows using their native definition contract.

Only definitions and explicit targets are inspected; source runtime pauses,
queues and credentials are not inputs to installation defaults.
"""
import json

from management_portal.system_application_flows import component_binding


def request_binding(rows, application, target):
    lineage_id = target['lineage_id']
    nodes = {r['node_id']: r for r in rows.get(('lineage', 'lineage_nodes'), [])
             if not r.get('deleted_at') and r.get('lineage_id', lineage_id) == lineage_id}
    if target.get('resident_id') not in nodes or nodes[target['resident_id']]['node_type'] != 'resident':
        raise ValueError('Application target Resident is absent from the selected definition')
    components = []
    for row in rows.get(('lineage', 'lineage_components'), []):
        if row['node_id'] not in nodes:
            continue
        config = row.get('config_json') or {}
        components.append({'id': row['node_id'], 'kind': nodes[row['node_id']]['node_type'],
                           'client_key': row.get('client_key'),
                           'config': json.loads(config) if isinstance(config, str) else config})
    wires = [{'id': r['edge_id'], 'kind': r['edge_type'], 'source': r['source_node_id'],
              'target': r['target_node_id'], 'source_port': r.get('source_port'),
              'target_port': r.get('target_port')}
             for r in rows.get(('lineage', 'lineage_edges'), [])
             if not r.get('deleted_at') and r.get('lineage_id', lineage_id) == lineage_id]
    return component_binding(application, target, {'program': {'components': components, 'wires': wires}})


def installation_binding(source, rows, members, events):
    """Recognize only the complete selected Which Resident request entry."""
    from management_portal.system_application_flows import ApplicationFlowUnavailable
    targets = source.rows('lineage', 'application_targets', predicates={'application': {'resident_discovery'}})
    targets = [r for r in targets if r.get('application') == 'resident_discovery']
    if len(targets) != 1:
        return None
    target = {key: targets[0].get(key) for key in ('lineage_id', 'resident_id')}
    try:
        binding = request_binding(rows, 'resident_discovery', target)
    except (KeyError, ValueError, ApplicationFlowUnavailable):
        return None
    if (events != [binding['event_source_id']]
            or not {binding['event_source_id'], binding['effect_id'], binding['resident_id']} <= members):
        return None
    return {'application': 'resident_discovery', **binding}


def validate_target(cursor, binding):
    """Recheck the actual copy before enabling its new-request entry."""
    application = binding['application']
    target = cursor.execute('SELECT lineage_id,resident_id FROM lineage.application_targets WHERE application=%s',
                            (application,)).fetchone()
    if not target or any(target[k] != binding[k] for k in ('lineage_id', 'resident_id')):
        raise ValueError('Installed application target differs from release binding')
    rows = {('lineage', table): cursor.execute(f'SELECT * FROM lineage.{table} WHERE lineage_id=%s',
                                               (target['lineage_id'],)).fetchall()
            for table in ('lineage_nodes', 'lineage_edges')}
    rows['lineage', 'lineage_components'] = cursor.execute(
        'SELECT c.* FROM lineage.lineage_components c JOIN lineage.lineage_nodes n USING(node_id) '
        'WHERE n.lineage_id=%s', (target['lineage_id'],)).fetchall()
    if {'application': application, **request_binding(rows, application, target)} != binding:
        raise ValueError('Installed application request flow differs from release binding')
