"""Versioned application routing and durable admission fences.

Each application keeps its row in application_targets. Only applications
that check the fence while durably registering new work may expose admission
controls. Targets change through the host maintenance script
scripts/application_target.py. The Test workbench also exposes TAU's admission
pause through this same fence; it cannot change the target Lineage.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import re
from management_common.database_time import iso_time
from management_common.operation_errors import OperationError
from management_common.portal_database import portal_database
from management_portal.application_targets import CONFIGURATION_COLUMNS, supports_admission_pause, target_field

class ConfigurationConflict(OperationError):
    code = 'instruction.conflict'
    http_status = 409
    commit_state = 'not_committed'

class AdmissionPaused(ConfigurationConflict):
    """New work was refused before it was accepted."""

def _row(connection, application, field, *, lock: str=''):
    return connection.execute(f'SELECT {field} AS target_id,{','.join(CONFIGURATION_COLUMNS)} FROM application_targets WHERE application=%s' + lock, (application,)).fetchone()

def _available(connection, field, target_id: str, *, lock: bool=False) -> bool:
    if not target_id:
        return False
    if field == 'lineage_id':
        query = 'SELECT lineage_id FROM lineages WHERE lineage_id=%s AND deleted_at IS NULL'
    else:
        query = "SELECT r.resident_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id JOIN lineages l ON l.lineage_id=n.lineage_id WHERE r.resident_id=%s AND r.deleted_at IS NULL AND n.deleted_at IS NULL AND n.node_type='resident' AND l.deleted_at IS NULL"
    return bool(connection.execute(query + (' FOR SHARE' if lock else ''), (target_id,)).fetchone())

def _view(connection, application, field, row) -> dict:
    value = dict(row) if row else {'target_id': '', 'revision': 0, 'admission_paused': __import__('management_common.distribution_features', fromlist=['initial_admission_paused']).initial_admission_paused(application), 'reason': '', 'updated_by': '', 'updated_at': None}
    value['target_id'] = value['target_id'] or ''
    value['updated_at'] = iso_time(value['updated_at']) or ''
    value['target_available'] = _available(connection, field, value['target_id'])
    value['admission_state'] = 'paused' if value['admission_paused'] else 'open' if value['target_available'] else 'unconfigured'
    return {'schema': 'tfi.application-target.v1', 'application': application, 'supports_admission_pause': supports_admission_pause(application), **value}

def get(application: str) -> dict:
    field = target_field(application)
    with portal_database(schema='lineage').read() as connection:
        return _view(connection, application, field, _row(connection, application, field))

def update(application: str, changes: dict, actor: str, *, expected_revision: int) -> dict:
    """Apply changes at expected_revision; actor is recorded as updated_by."""
    field = target_field(application)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError('actor is required')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a nonnegative integer')
    if not isinstance(changes, dict) or not changes or set(changes) - {'target_id', 'admission_paused', 'reason'}:
        raise ValueError('changes requires target_id, admission_paused or reason')
    if 'admission_paused' in changes:
        if type(changes['admission_paused']) is not bool:
            raise ValueError('admission_paused must be boolean')
        if not supports_admission_pause(application):
            raise ConfigurationConflict('This application does not support admission controls')
    if 'reason' in changes and (not isinstance(changes['reason'], str) or len(changes['reason']) > 1000):
        raise ValueError('reason must be text of at most 1000 characters')
    if 'target_id' in changes and (not isinstance(changes['target_id'], str) or not re.fullmatch(field.removesuffix('_id') + '_[0-9a-f]{12}', changes['target_id'])):
        raise ValueError("target_id must match the application's target type")
    with portal_database(schema='lineage').transaction() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))', (':application-target:' + application,))
        before = _row(connection, application, field, lock=' FOR UPDATE')
        revision = int(before['revision']) if before else 0
        if expected_revision != revision:
            raise ConfigurationConflict('Application configuration revision changed', code='instruction.version_conflict', details={'expected_revision': expected_revision, 'current_revision': revision})
        value = {**(dict(before) if before else {'target_id': '', 'admission_paused': __import__('management_common.distribution_features', fromlist=['initial_admission_paused']).initial_admission_paused(application), 'reason': ''}), **changes}
        target_changed = not before or value['target_id'] != before['target_id']
        if supports_admission_pause(application) and before and target_changed and (not before['admission_paused'] or not value['admission_paused']):
            raise ConfigurationConflict('Pause admission before switching the target; resume separately')
        if target_changed or not value['admission_paused']:
            if not _available(connection, field, value['target_id'], lock=True):
                raise KeyError('Application target does not exist or is deleted')
        connection.execute(f'INSERT INTO application_targets (application,{field},revision,admission_paused,reason,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (application) DO UPDATE SET {field}=EXCLUDED.{field},revision=EXCLUDED.revision,admission_paused=EXCLUDED.admission_paused,reason=EXCLUDED.reason,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at', (application, value['target_id'], revision + 1, value['admission_paused'], value['reason'], actor, datetime.now(timezone.utc)))
        return _view(connection, application, field, _row(connection, application, field))

@contextmanager
def admission_scope(application: str):
    """Hold the row lock until new work is durable; launch/recovery stay outside."""
    field = target_field(application)
    if not supports_admission_pause(application):
        raise ConfigurationConflict('This application does not support admission controls')
    with portal_database(schema='lineage').transaction() as connection:
        row = _row(connection, application, field, lock=' FOR SHARE')
        if row and row['admission_paused']:
            raise AdmissionPaused('Application admission is paused', details={'application': application, 'revision': row['revision'], 'reason': row['reason'], 'target_id': row['target_id']})
        if not row or not _available(connection, field, row['target_id']):
            raise ConfigurationConflict('Application target is not configured or unavailable')
        yield
