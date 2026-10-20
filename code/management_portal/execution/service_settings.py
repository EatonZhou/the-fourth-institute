"""Shared execution policy, including local projections of workflow switches.

Lineage owns installed workflow switches. Projection writes never call back
into Lineage or change Provider transport lifecycles.
"""
from contextlib import contextmanager

from management_common.portal_database import portal_database


WORKFLOW_SETTING_FIELDS = {
    'system:request-timing:enabled': 'provider_timing_proxy_enabled',
    'system:harness-recording:enabled': 'harness_recording_enabled',
}
SETTING_WORKFLOW_KEYS = {
    'model_request_timing_enabled': 'system:request-timing:enabled',
    'harness_recording_enabled': 'system:harness-recording:enabled',
}
# Retention periods in days: (minimum, maximum, default). Harness evidence must
# cover the longest Harness evaluation window (a challenger runs up to 21 days;
# the offline replay reads 14), so a shorter period would starve evaluations.
RETENTION_DAYS = {
    'harness_retention_days': (21, 365, 30),
}


class ExecutionSettings:
    def __init__(self, database=None, *, initialize=True):
        self.database = database or portal_database(schema='execution_runtime')
        if not initialize:
            return
        with self.database.transaction() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS service_settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                provider_timing_proxy_enabled BOOLEAN NOT NULL
            )''')
            # Retain the physical column through rolling upgrades: older
            # draining generations still read it. The public contract names
            # the observation policy, not the retired transport mechanism.
            connection.execute('''INSERT INTO service_settings(singleton, provider_timing_proxy_enabled)
                VALUES (1, TRUE) ON CONFLICT (singleton) DO NOTHING''')
            columns = {row['attname'] for row in connection.execute('''
                SELECT attname FROM pg_attribute
                WHERE attrelid = 'service_settings'::regclass
                    AND attnum > 0 AND NOT attisdropped''').fetchall()}
            # Even a no-op ALTER takes ACCESS EXCLUSIVE and conflicts with
            # pg_dump's read locks. Existing stores need no schema mutation.
            additions = {
                'harness_recording_enabled': 'BOOLEAN NOT NULL DEFAULT TRUE',
                'harness_recording_started_at': "TIMESTAMPTZ NOT NULL DEFAULT 'epoch'",
                'managed_workflow_keys': 'TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]',
                'permission_system_enabled': 'BOOLEAN NOT NULL DEFAULT TRUE',
                **{name: f'INTEGER NOT NULL DEFAULT {default}'
                   for name, (_minimum, _maximum, default) in RETENTION_DAYS.items()},
            }
            missing = [f'ADD COLUMN IF NOT EXISTS {name} {definition}'
                       for name, definition in additions.items() if name not in columns]
            if missing:
                connection.execute('ALTER TABLE service_settings ' + ', '.join(missing))

    def get(self):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT provider_timing_proxy_enabled, harness_recording_enabled, permission_system_enabled, '
                                     + ', '.join(RETENTION_DAYS) + ' FROM service_settings WHERE singleton = 1').fetchone()
            return {
                'model_request_timing_enabled': bool(row['provider_timing_proxy_enabled']) if row else True,
                'harness_recording_enabled': bool(row['harness_recording_enabled']) if row else True,
                'permission_system_enabled': bool(row['permission_system_enabled']) if row else True,
                **{name: int(row[name]) if row else default
                   for name, (_minimum, _maximum, default) in RETENTION_DAYS.items()},
            }

    def model_request_timing_enabled(self):
        """The response persistence path reads only the column every schema generation has."""
        with self.database.transaction() as connection:
            row = connection.execute('SELECT provider_timing_proxy_enabled FROM service_settings WHERE singleton = 1').fetchone()
            return bool(row['provider_timing_proxy_enabled']) if row else True

    def permission_system_enabled(self):
        """Whether the Portal checks permissions."""
        with self.database.transaction() as connection:
            row = connection.execute('SELECT permission_system_enabled FROM service_settings WHERE singleton = 1').fetchone()
            return bool(row['permission_system_enabled']) if row else True

    def update_permission_system(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('permission_system_enabled must be a boolean')
        with self.database.transaction() as connection:
            connection.execute('UPDATE service_settings SET permission_system_enabled = %s WHERE singleton = 1', (enabled,))
        return self.get()

    def update_retention(self, changes):
        """Set retention periods; validated as a whole before any is written."""
        if not isinstance(changes, dict) or not changes or set(changes) - RETENTION_DAYS.keys():
            raise ValueError('expected retention fields')
        for name, days in changes.items():
            minimum, maximum, _default = RETENTION_DAYS[name]
            if type(days) is not int or not minimum <= days <= maximum:
                raise ValueError(f'{name} must be an integer from {minimum} to {maximum}')
        with self.database.transaction() as connection:
            connection.execute('UPDATE service_settings SET ' + ', '.join(f'{name} = %s' for name in changes)
                               + ' WHERE singleton = 1', tuple(changes.values()))
        return self.get()

    def managed_workflow_keys(self):
        """Binding provenance only; an absent key has never been projected."""
        with self.database.transaction() as connection:
            row = connection.execute('SELECT managed_workflow_keys FROM service_settings WHERE singleton = 1').fetchone()
            return set(row['managed_workflow_keys']) if row else set()

    def project_workflow_switch(self, client_key, enabled):
        """Persist an installed Event's effective open state, locally only.

        The caller owns binding discovery, serialization and deletion: an
        absent never-installed binding leaves settings alone; removing an
        installed Event projects False. Reconciliation is idempotent.
        """
        if type(enabled) is not bool:
            raise ValueError('workflow enabled must be a boolean')
        if client_key not in WORKFLOW_SETTING_FIELDS:
            raise ValueError('unknown execution workflow switch')
        with self.database.transaction() as connection:
            self._set(connection, client_key, enabled)
            connection.execute('''UPDATE service_settings SET
                managed_workflow_keys = array_append(managed_workflow_keys, %s)
                WHERE singleton = 1 AND NOT (%s = ANY(managed_workflow_keys))''', (client_key, client_key))
        return self.get()

    @staticmethod
    def _set(connection, client_key, enabled):
        if WORKFLOW_SETTING_FIELDS[client_key] == 'harness_recording_enabled':
            connection.execute('''UPDATE service_settings SET
                harness_recording_started_at = CASE
                    WHEN %s AND NOT harness_recording_enabled THEN clock_timestamp()
                    ELSE harness_recording_started_at END,
                harness_recording_enabled = %s WHERE singleton = 1''', (enabled, enabled))
        else:
            connection.execute('UPDATE service_settings SET provider_timing_proxy_enabled = %s WHERE singleton = 1', (enabled,))

    @contextmanager
    def admit_harness_recording(self, recorded_at):
        """Serialize a short telemetry write with closing its Event.

        Used by the background writer, never the Provider response callback.
        A re-open does not resurrect telemetry queued before that re-open.
        """
        with self.database.transaction() as connection:
            row = connection.execute('''SELECT harness_recording_enabled,
                EXTRACT(EPOCH FROM harness_recording_started_at) AS started_at
                FROM service_settings WHERE singleton = 1 FOR SHARE''').fetchone()
            yield bool(row and row['harness_recording_enabled']
                       and float(recorded_at) >= float(row['started_at']))

    def update(self, enabled):
        return self.update_unmanaged({'model_request_timing_enabled': enabled})

    def update_unmanaged(self, changes):
        """Compatibility before installation; never claims a workflow key."""
        if not isinstance(changes, dict) or not changes or set(changes) - SETTING_WORKFLOW_KEYS.keys():
            raise ValueError('expected execution setting fields')
        if any(type(enabled) is not bool for enabled in changes.values()):
            raise ValueError('execution settings must be boolean')
        with self.database.transaction() as connection:
            row = connection.execute('SELECT managed_workflow_keys FROM service_settings WHERE singleton = 1 FOR UPDATE').fetchone()
            managed = set(row['managed_workflow_keys']) if row else set()
            if managed.intersection(SETTING_WORKFLOW_KEYS[field] for field in changes):
                raise ValueError('setting is managed by a Lineage Event; use PauseComponent')
            for field, enabled in changes.items():
                self._set(connection, SETTING_WORKFLOW_KEYS[field], enabled)
        return self.get()
