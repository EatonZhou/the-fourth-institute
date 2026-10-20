"""Read copies of canonical domain facts, from the domain owner; the permission system answers who may act on them."""
from __future__ import annotations
import copy
from . import identity_contract


def canonical_view(record, *, resource_id, actor=''):
    identity_contract.validate_id(resource_id, 'resident' if resource_id.startswith('resident_') else 'lineage')
    id_field = 'resident_id' if resource_id.startswith('resident_') else 'lineage_id'
    if record.get(id_field) != resource_id:
        raise ValueError('Canonical record and resource identity must match')
    return copy.deepcopy(dict(record))


def canonical_views(records, *, id_field, actor=''):
    return [canonical_view(record, resource_id=record[id_field], actor=actor) for record in records]


def read_records(resource_ids, *, state=None):
    """Read canonical authority once, never recurse through ResourceService."""
    from .state_store import get_lineage_data_store
    state = state or get_lineage_data_store()
    ids = sorted(set(resource_ids))
    result = {}
    with state.database.snapshot():
        result.update({row['resident_id']: row for row in state.residents_by_id(
            [rid for rid in ids if rid.startswith('resident_')])})
        for rid in ids:
            if rid.startswith('lineage_'):
                try:
                    result[rid] = state.lineage_definition(rid)
                except KeyError:
                    pass
        family_ids = [rid for rid in ids if rid.startswith('family_')]
        if family_ids:
            from . import family_storage
            with state.database.snapshot() as connection:
                for record in family_storage.resource_records(connection, resource_ids=family_ids):
                    result[record['resource_id']] = record
    return result


def project_resources(records):
    """Family records take their fields from the canonical Family."""
    # Other resource types are readonly pass-through values. The query owner
    # detaches the returned page; copying unrelated bodies here multiplies the
    # cost of every subsequent domain projection.
    items = [dict(item) if item.get('resource_type') == 'family' else item for item in records]
    family_ids = [item['resource_id'] for item in items if item.get('resource_type') == 'family']
    if not family_ids:
        return items
    from .resource_client import _family_resource_projection
    from management_portal.resources.collaboration_registry import descriptor_resource_record
    family_facts = read_records(family_ids)
    for item in items:
        if item.get('resource_type') != 'family':
            continue
        canonical_family = family_facts.get(item['resource_id'])
        if canonical_family is not None:
            item.update(descriptor_resource_record(_family_resource_projection(canonical_family)['descriptor']))
        else:
            item['attributes'] = {**item.get('attributes', {}), 'status': 'deleted'}
    return items


from contextlib import contextmanager


@contextmanager
def projection_transaction(prepared, registry):
    """Canonical lifecycle and descriptor commit share one physical transaction."""
    from .state_store import get_lineage_data_store
    active = [descriptor for descriptor in prepared
              if descriptor['status'] != 'deleted'
              and descriptor['resource_id'].startswith(('resident_', 'lineage_', 'family_'))]
    if not active:
        yield None
        return
    state = get_lineage_data_store()
    if state.database.dsn != registry.database.dsn:
        raise ValueError('Domain projection requires the same physical Portal database')
    registry._ensure()
    with state.database.transaction() as connection:
        resident_ids = sorted({item['resource_id'] for item in active
                               if item['resource_id'].startswith('resident_')})
        lineage_ids = {item['resource_id'] for item in active
                       if item['resource_id'].startswith('lineage_')}
        family_ids = sorted({item['resource_id'] for item in active if item['resource_id'].startswith('family_')})
        families = connection.execute(
            'SELECT resource_id,lineage_id FROM families WHERE resource_id=ANY(%s) '
            'AND deleted_at IS NULL ORDER BY resource_id FOR SHARE NOWAIT', (family_ids,),
        ).fetchall() if family_ids else []
        if len(families) != len(family_ids):
            raise ValueError('Stale canonical domain projection: missing Family')
        lineage_ids.update(row['lineage_id'] for row in families)
        # Canonical writers fence identity/configuration at the Resident row
        # and graph membership at the Lineage row. Lock just these identities,
        # including current parents whose metadata determines Resident attrs.
        # NOWAIT avoids a lock-order cycle with graph/move/deletion writers;
        # unrelated Lineages and runtime progress remain independent.
        residents = connection.execute(
            'SELECT resident_id FROM residents WHERE resident_id=ANY(%s) '
            'AND deleted_at IS NULL ORDER BY resident_id FOR SHARE NOWAIT',
            (resident_ids,),
        ).fetchall()
        if len(residents) != len(resident_ids):
            raise ValueError('Stale canonical domain projection: missing Resident')
        homes = connection.execute(
            'SELECT lineage_id FROM lineage_nodes WHERE node_id=ANY(%s) '
            'ORDER BY node_id FOR SHARE NOWAIT', (resident_ids,),
        ).fetchall()
        lineage_ids.update(row['lineage_id'] for row in homes)
        lineages = connection.execute(
            'SELECT lineage_id FROM lineages WHERE lineage_id=ANY(%s) '
            'AND deleted_at IS NULL ORDER BY lineage_id FOR SHARE NOWAIT',
            (sorted(lineage_ids),),
        ).fetchall()
        if len(lineages) != len(lineage_ids):
            raise ValueError('Stale canonical domain projection: missing Lineage')
        # Workspace retirement removes bindings directly in its own domain
        # transaction. Fence those existing relationships too; new bindings
        # are created through the locked Resident identity owner.
        connection.execute(
            'SELECT resident_id FROM resident_workspace_bindings '
            'WHERE resident_id=ANY(%s) ORDER BY resident_id,workspace_id '
            'FOR SHARE NOWAIT', (resident_ids,),
        ).fetchall()
        yield connection


def _current_domain_relations(descriptor, current):
    """Replace owned relationships, including relationships that disappeared."""
    owned = ({'lineage', 'source', 'workspace'} if descriptor['resource_id'].startswith('resident_')
             else {'lineage', 'member', 'facade', 'steward', 'router'} if descriptor['resource_id'].startswith('family_')
             else {'member'})
    return [*[relation for relation in current['relations'] if relation['kind'] not in owned],
            *descriptor['relations']]


def live_projection(descriptor, writer, store):
    """Interpret an old committed payload against current canonical metadata."""
    from . import resource_client
    rid = descriptor['resource_id']
    canonical = read_records([rid]).get(rid)
    if canonical is None or canonical.get('deleted_at'):
        raise ValueError('Stale canonical domain projection: ' + rid)
    projector = (resource_client._resident_resource_projection if rid.startswith('resident_')
                 else resource_client._family_resource_projection if rid.startswith('family_')
                 else resource_client._lineage_resource_projection)
    generated = projector(canonical)['descriptor']
    expected = store.normalize(generated)
    if rid.startswith('resident_'):
        hierarchy = expected['attributes'].get('principal_hierarchy') or {}
        supplied = descriptor['attributes'].get('principal_hierarchy')
        if 'level' in hierarchy and isinstance(supplied, dict):
            # Level follows the current home and leader. A membership commit
            # can change it after the Resident's publication intent is written.
            # Category and scopes remain authored facts checked below.
            descriptor = {**descriptor, 'attributes': {**descriptor['attributes'],
                'principal_hierarchy': {**supplied, 'level': hierarchy['level']}}}
    # Old payloads predate direct business attrs; lack of those attrs does not
    # clear them. Every other generated field must still identify this event.
    for field in ('title', 'summary', 'status', 'project_ids', 'relations', 'created_at', 'updated_at', 'deleted_at'):
        if descriptor[field] != expected[field]:
            raise ValueError('Stale canonical domain projection: ' + rid)
    if any(descriptor['attributes'].get(key) != value for key, value in expected['attributes'].items()):
        raise ValueError('Stale canonical domain projection: ' + rid)
    current = writer.get(rid.split('_', 1)[0], rid)
    if current is None:
        return descriptor
    current = store.normalize(current)
    from datetime import datetime
    instant = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    # Exact original events can retry after independent descriptor edits, but
    # cannot resurrect a descriptor tombstoned by a later domain operation.
    source = writer.get('domain_live_sources', rid)
    if source == descriptor and current['status'] != 'deleted':
        return {**current, 'relations': _current_domain_relations(descriptor, current)}
    current_time = max(instant(current['updated_at']),
                       instant(current['deleted_at']) if current['deleted_at'] else instant(current['updated_at']))
    incoming_time = instant(descriptor['updated_at'])
    if current_time > incoming_time or (current['status'] == 'deleted' and current_time >= incoming_time):
        raise ValueError('Stale canonical domain projection: ' + rid)
    # A later canonical Restore/update can publish its live descriptor. The
    # locked canonical record proves lifecycle.
    return {**descriptor, 'attributes': {**current['attributes'], **descriptor['attributes']},
            'relations': _current_domain_relations(descriptor, current)}
