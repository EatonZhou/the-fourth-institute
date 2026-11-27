"""One-time in-place update of the official Family definition.

Only the previous builtin fallback is removed. Explicit user copies retain
their identity and content; old locators resolve to the surviving resource.
"""
from copy import deepcopy
from . import persistence, role_recipes as role
from .registry import now

MARKER = 'family_single_definition_v1'


def family_definition(connection, rows, installed):
    if MARKER in installed:
        return
    from .builtins import definitions
    from .kinds import normalize
    original = next((row for row in rows.values() if row.get('builtin_key') == 'family'), None)
    fallback = next((row for row in rows.values() if row.get('builtin_key') == 'family_roles'), None)
    if original:
        updated = deepcopy(original)
        updated['body']['role_templates'] = role.references()
        updated['body'].pop('tasks', None)
        if updated['body'] != original['body']:
            updated.update(version=original['version'] + 1, updated_at=now(), updated_by='portal')
            canonical = next(raw for raw in definitions() if raw['builtin_key'] == 'family')
            updated['builtin_source'] = persistence.content_hash(normalize(canonical))
            rows[original['template_id']] = updated
    if original and fallback:
        old, current = fallback['template_id'], original['template_id']
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return current if value == old else value
        for identity, row in list(rows.items()):
            if identity == old:
                continue
            updated = replace(row['body'])
            if updated != row['body']:
                rows[identity] = {**row, 'body': updated, 'version': row['version'] + 1,
                                  'updated_at': now(), 'updated_by': 'portal'}
        del rows[old]
        connection.execute('INSERT INTO template_id_redirects(old_id,template_id) VALUES (%s,%s) ON CONFLICT(old_id) DO UPDATE SET template_id=excluded.template_id', (old, current))
    connection.execute('INSERT INTO installed_builtins(builtin_key,installed_at) VALUES (%s,%s)', (MARKER, now()))
