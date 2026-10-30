"""Journal-owned, reversible database admission gate for offline upgrades.

The installer must fsync prepare()'s receipt BEFORE close(), and recover it
before admitting services. These functions neither manage that journal nor
authorize a backup. Existing and late-starting backends still require the
normal ownership capture, SHARE barrier, cluster-zero and work proof checks.
Only pg_database.datallowconn is changed; no business state is written.

Maintenance sessions are short lived. Catalog locking requires privileges in
addition to database ownership; prepare proves them without changing state.
Same-cluster multi-target plans are deliberately unsupported. The caller owns
exclusive application/instance/launcher locks throughout, including recovery.
"""
from contextlib import ExitStack
from functools import wraps
import hashlib
import json


SCHEMA = 'tfi.application-admission.v1'


class AdmissionError(RuntimeError):
    """Fixed public code only: never include DSNs or driver error messages."""


def _require(value, code):
    if not value:
        raise AdmissionError(code)


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AdmissionError:
            raise
        except Exception:
            raise AdmissionError('admission_operation_failed') from None
    return call


def _targets(records):
    _require(isinstance(records, list) and records, 'invalid_records')
    targets = []
    for record in records:
        _require(isinstance(record, dict) and isinstance(record.get('target'), dict), 'invalid_target')
        target = record['target']
        _require(isinstance(target.get('database_name'), str) and target['database_name'], 'invalid_database')
        targets.append(target)
    _require(len({_digest(t) for t in targets}) == len(targets), 'duplicate_target')
    return targets


def _digest(target):
    return hashlib.sha256(json.dumps(target, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _system(connection):
    return connection.execute('SELECT system_identifier::text FROM pg_catalog.pg_control_system()').fetchone()[0]


def _database(connection, name):
    row = connection.execute('''SELECT oid, datname, datdba, datallowconn
        FROM pg_catalog.pg_database WHERE datname=%s AND datconnlimit<>-2''', (name,)).fetchone()
    _require(row is not None, 'database_missing')
    return dict(zip(('oid', 'name', 'owner_oid', 'allow_connections'), row))


def _connect(api, target, name, stack):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    dsn = api.clean_environment(target)['PORTAL_DATABASE_URL']
    _require(conninfo_to_dict(dsn).get('dbname') == target['database_name'], 'target_dsn_mismatch')
    connection = psycopg.connect(make_conninfo(dsn, dbname=name, connect_timeout=3))
    stack.callback(connection.close)  # rollback on failure, never implicit commit
    connection.execute("SET lock_timeout='3s'")
    connection.execute("SET statement_timeout='5s'")
    return connection


def _catalog_lock(connection):
    # Serialize validation+ALTER against rename/drop/recreate/owner changes.
    # This is not a lock on business tables and is released at commit/close.
    connection.execute('LOCK TABLE pg_catalog.pg_database IN SHARE ROW EXCLUSIVE MODE')


def _verify(connection, entry):
    _require(_system(connection) == entry['system_identifier'], 'cluster_identity_changed')
    current = connection.execute('SELECT current_database()').fetchone()[0]
    maintenance = _database(connection, current)
    _require({k: maintenance[k] for k in ('oid', 'name')} == entry['maintenance']
             and maintenance['allow_connections'] is True, 'maintenance_identity_changed')
    database = _database(connection, entry['database']['name'])
    _require(all(database[k] == entry['database'][k] for k in ('oid', 'name', 'owner_oid')),
             'database_identity_changed')
    allowed = connection.execute('''SELECT r.rolsuper OR pg_catalog.pg_has_role(current_user,%s,'USAGE')
        FROM pg_catalog.pg_roles r WHERE r.rolname=current_user''', (database['owner_oid'],)).fetchone()[0]
    _require(allowed, 'database_owner_required')
    return database


@_public
def prepare(api, records, connections):
    """Read original identities; preserve supplied connections/transactions."""
    targets = _targets(records)
    _require(len(connections) == len(targets), 'connection_count_mismatch')
    excluded = {t['database_name'] for t in targets}
    entries = []
    for target, held in zip(targets, connections, strict=True):
        _require(held.execute('SELECT current_database()').fetchone()[0] == target['database_name'],
                 'barrier_database_mismatch')
        database = _database(held, target['database_name'])
        _require(database['allow_connections'] is True, 'original_admission_closed')
        candidates = held.execute('''SELECT oid,datname FROM pg_catalog.pg_database
            WHERE datname IN ('postgres','template1') AND datallowconn AND datconnlimit<>-2
              AND pg_catalog.has_database_privilege(current_user,oid,'CONNECT')
            ORDER BY CASE WHEN datname='postgres' THEN 0 ELSE 1 END''').fetchall()
        candidate = next((r for r in candidates if r[1] not in excluded), None)
        _require(candidate is not None, 'maintenance_database_unavailable')
        entries.append({'schema': SCHEMA, 'target_sha256': _digest(target),
                        'system_identifier': _system(held), 'database': database,
                        'maintenance': dict(zip(('oid', 'name'), candidate))})
    _entries(targets, entries)
    with ExitStack() as stack:
        controls = [_connect(api, t, e['maintenance']['name'], stack)
                    for t, e in zip(targets, entries, strict=True)]
        for control, entry in zip(controls, entries, strict=True):
            _catalog_lock(control)
            _require(_verify(control, entry)['allow_connections'] is True, 'original_admission_closed')
    return entries


def _entries(targets, entries):
    _require(isinstance(entries, list) and len(entries) == len(targets), 'entry_count_mismatch')
    excluded = {t['database_name'] for t in targets}
    for target, entry in zip(targets, entries, strict=True):
        _require(isinstance(entry, dict) and set(entry) == {
            'schema', 'target_sha256', 'system_identifier', 'database', 'maintenance'}, 'invalid_entry')
        _require(entry['schema'] == SCHEMA and entry['target_sha256'] == _digest(target), 'target_binding_changed')
        _require(isinstance(entry['system_identifier'], str) and entry['system_identifier'].isdigit(), 'invalid_cluster_identity')
        database, maintenance = entry['database'], entry['maintenance']
        _require(isinstance(database, dict) and set(database) == {'oid', 'name', 'owner_oid', 'allow_connections'}, 'invalid_database_receipt')
        _require(type(database['oid']) is int and database['oid'] > 0
                 and type(database['owner_oid']) is int and database['owner_oid'] > 0
                 and database['name'] == target['database_name']
                 and database['allow_connections'] is True, 'invalid_database_receipt')
        _require(isinstance(maintenance, dict) and set(maintenance) == {'oid', 'name'}, 'invalid_maintenance_receipt')
        _require(type(maintenance['oid']) is int and maintenance['oid'] > 0
                 and maintenance['name'] in ('postgres', 'template1')
                 and maintenance['name'] not in excluded, 'invalid_maintenance_receipt')
    _require(len({e['system_identifier'] for e in entries}) == len(entries), 'same_cluster_targets_unsupported')


def _apply(api, records, entries, *, restoring):
    from psycopg import sql
    targets = _targets(records)
    _entries(targets, entries)  # validate the entire receipt before any connection/action
    with ExitStack() as stack:
        controls = [_connect(api, t, e['maintenance']['name'], stack)
                    for t, e in zip(targets, entries, strict=True)]
        # No mutation unless every target passes live validation under its
        # catalog lock. Independent cluster commits are NOT an atomic batch:
        # a later failure must be recovered from the installer's sealed intent.
        for control, entry in zip(controls, entries, strict=True):
            _catalog_lock(control)
            _verify(control, entry)
        for control, entry in zip(controls, entries, strict=True):
            desired = entry['database']['allow_connections'] if restoring else False
            database = _verify(control, entry)
            if database['allow_connections'] != desired:
                control.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS {}').format(
                    sql.Identifier(database['name']), sql.SQL('true' if desired else 'false')))
            control.commit()
            # A lost commit acknowledgement is an error, not success. Restore
            # can safely be retried whether the commit happened or not.
            _require(_verify(control, entry)['allow_connections'] is desired, 'admission_readback_failed')


@_public
def close(api, records, entries):
    """Commit disabled admission. Caller must have durably sealed intent."""
    _apply(api, records, entries, restoring=False)


@_public
def restore(api, records, entries):
    """Restore sealed original admission through an independent database."""
    _apply(api, records, entries, restoring=True)


def main():
    import sys
    from scripts import tfi_release_install as api
    try:
        request = json.load(sys.stdin)
        _require(isinstance(request, dict) and set(request) == {'records', 'entries'}, 'invalid_request')
        restore(api, request['records'], request['entries'])
    except AdmissionError as exc:
        print(json.dumps({'status': 'refused', 'code': str(exc)}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'status': 'refused', 'code': 'admission_operation_failed'}), file=sys.stderr)
        return 1
    print(json.dumps({'status': 'restored', 'instances': len(request['records'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
