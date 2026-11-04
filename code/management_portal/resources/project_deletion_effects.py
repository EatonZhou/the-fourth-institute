"""Retry Project-owned effects from the deletion receipt, without reauthorization.

The identity owner creates the pending marker in its deletion transaction.
Maintenance and the foreground share this worker; no paths or threads are owned
here. Each feature owns its relation mutation and each effect is idempotent.
"""
from hashlib import sha256
from copy import deepcopy

from .registry_store import get_registry_store

COLLECTION = 'project_deletions'


def status_for_actor(project_id, actor):
    """Read the original deleter's receipt only while that identity is absent.

    Removing permission facts is part of deletion. Its completion observation
    must not require permission on an object that no longer exists, nor expose
    another actor's audit or mistake a reused Project name for the old object.
    """
    from management_portal import project_registry

    registry = get_registry_store()
    entry = registry.get(COLLECTION, project_id)
    if not actor or not entry or entry.get('deleted_by') != actor:
        raise KeyError(project_id)
    document = registry.get(project_registry.COLLECTION, project_registry.DOCUMENT_ID) or {}
    if any(item.get('id') == project_id for item in document.get('projects', [])):
        raise KeyError(project_id)
    return {'project_id': project_id, 'registration_status': 'unregistered',
            'deleted_at': entry.get('deleted_at'), 'effects': deepcopy(entry.get('effects') or {})}


def _permissions(entry):
    from management_portal import permission_system
    return permission_system.unregister_resources(entry['resource_ids'])


def _avatar(entry):
    from management_portal.avatar_store import scope_avatar_store, scope_subject
    scope_avatar_store().delete(scope_subject('project', entry['project_id']))


def _index(entry):
    from . import get_resource_service
    return get_resource_service()._index().project_unregistered('project', entry['project_id'])


def _effects():
    from management_portal import portal_features
    return [
        *[('relation:' + feature, lambda entry, relate=relate: relate(entry['project_id']))
          for feature, relate in portal_features.contributions('project.relations')],
        ('permissions', _permissions), ('avatar', _avatar), ('index', _index),
    ]


def run(project_id):
    registry = get_registry_store()
    key = int.from_bytes(sha256((registry.database.schema + ':project-effects:' + project_id).encode()).digest()[:8], 'big', signed=True)
    with registry.database.advisory_lock(key):
        entry = registry.get(COLLECTION, project_id)
        if not entry or not entry.get('effects') or entry['effects']['status'] == 'complete':
            return entry
        effects = entry['effects']
        effects['attempts'] += 1
        for name, apply in _effects():
            if name in effects['completed']:
                continue
            try:
                effects['completed'][name] = apply(entry)
                effects['errors'].pop(name, None)
            except Exception as exc:
                effects['errors'][name] = str(exc) or type(exc).__name__
            # Save each cursor independently: one unavailable feature does not
            # discard other owners' progress. A crash repeats at most one effect.
            with registry.writer(COLLECTION) as writer:
                writer.put(COLLECTION, project_id, entry)
        effects['status'] = 'pending' if effects['errors'] else 'complete'
        with registry.writer(COLLECTION) as writer:
            writer.put(COLLECTION, project_id, entry)
        return entry


def run_pending(*, limit=100):
    """One bounded maintenance pass. Historical receipts without markers stay put."""
    entries = get_registry_store().documents(COLLECTION)
    results = []
    for project_id, entry in entries.items():
        if entry.get('effects', {}).get('status') != 'pending':
            continue
        try:
            result = run(project_id)
            results.append({'project_id': project_id, 'effects': result['effects']})
        except Exception as exc:
            results.append({'project_id': project_id, 'error': str(exc)})
        if len(results) >= limit:
            break
    return results


def finish(contents):
    """Report the committed identity independently from file/projection progress."""
    result = {**contents, 'registration_status': 'unregistered'}
    try:
        entry = run(contents['project_id'])
        if entry:
            result['effects'] = entry.get('effects', {})
    except Exception as exc:
        result['effects'] = {**contents.get('effects', {}), 'status': 'pending', 'error': str(exc)}
    result['unlinked'] = {name.removeprefix('relation:'): ids for name, ids in
                          result.get('effects', {}).get('completed', {}).items() if name.startswith('relation:')}
    return result
