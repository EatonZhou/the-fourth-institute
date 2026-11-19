"""Rebuildable directory dependency index; owner rows remain authoritative.

Only the offline migration creates this schema and certifies completeness.
Mutation hooks replace one object's derived entries in the owner's transaction.
Triggers invalidate unrefreshed writes, including legacy SQL callers. Deletion
never repairs or scans owner tables to compensate for missing migration.
"""
import json

from .directory_paths import cleanup_location, cleanup_path, lexical_path, DirectoryComparison
from .portal_database import _database_schema


class DirectoryDependenciesMissing(RuntimeError):
    pass


def schema():
    return '"' + _database_schema('directory_lifecycle') + '"'


def installed(connection):
    return connection.execute('SELECT to_regclass(%s)',
        (schema() + '.directory_dependencies',)).fetchone()[0] is not None


def available(connection):
    if not installed(connection):
        return False
    row = connection.execute(f'SELECT ready FROM {schema()}.directory_dependency_state WHERE singleton').fetchone()
    return bool(row and row['ready'])


def prepare_write(connection):
    """Version fence: a waiting old structural writer must fail, then retry."""
    connection.execute("SELECT set_config('tfi.directory_dependency_writer','v1',true)")


def path_keys(path):
    # A conservative candidate index, independent of changing native case
    # flags. Native DirectoryComparison makes the final membership decision.
    path = lexical_path(path)
    return path.as_posix().casefold(), [p.as_posix().casefold() for p in (path, *path.parents)]


def initialize(connection):
    s = schema()
    connection.execute(f'CREATE SCHEMA IF NOT EXISTS {s}')
    statements = (
        f'''CREATE TABLE IF NOT EXISTS {s}.directory_dependency_state (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), ready boolean NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS {s}.directory_dependencies (
            kind text NOT NULL, object_id text NOT NULL, slot text NOT NULL,
            owner_id text NOT NULL, location text NOT NULL, path_key text NOT NULL,
            ancestors text[] NOT NULL, disposition text NOT NULL,
            PRIMARY KEY(kind,object_id,slot))''',
        f'CREATE INDEX IF NOT EXISTS directory_dependency_owner ON {s}.directory_dependencies(owner_id,kind)',
        f'CREATE INDEX IF NOT EXISTS directory_dependency_path ON {s}.directory_dependencies(path_key)',
        f'CREATE INDEX IF NOT EXISTS directory_dependency_ancestors ON {s}.directory_dependencies USING gin(ancestors)',
        f'''CREATE TABLE IF NOT EXISTS {s}.directory_dependency_dirty (
            kind text NOT NULL, object_id text NOT NULL, PRIMARY KEY(kind,object_id))''',
        f'''CREATE TABLE IF NOT EXISTS {s}.identity_retirements (
            identity text PRIMARY KEY, root text NOT NULL, plan jsonb NOT NULL)''',
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(f'INSERT INTO {s}.directory_dependency_state VALUES (true,false) '
                       'ON CONFLICT(singleton) DO NOTHING')


def require_ready(connection):
    if not installed(connection):
        raise DirectoryDependenciesMissing('Identity directories require the offline dependency migration')
    s = schema()
    ready = connection.execute(f'SELECT ready FROM {s}.directory_dependency_state WHERE singleton').fetchone()
    dirty = connection.execute(f'SELECT kind,object_id FROM {s}.directory_dependency_dirty LIMIT 1').fetchone()
    if not ready or not ready['ready'] or dirty:
        raise DirectoryDependenciesMissing('Directory dependency migration or owner refresh is incomplete')


def _installed_queue(connection):
    from .directory_cleanup_store import DirectoryCleanupStore
    queue = DirectoryCleanupStore(create=False)
    if any(connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None
           for table in (queue.table, queue.reservations)):
        raise DirectoryDependenciesMissing('Directory claim storage requires installation')
    return queue


def validate_initial_registration(connection, *, protection):
    """Consume an owner's live, transaction-bound proof; never infer authority.

    The owner checks completeness before its first authority write. Later
    hooks may have their own unrefreshed dirty entry, so retain/check the ready
    row here without mistaking that in-transaction entry for failed migration.
    """
    operation, roots, transaction = protection.assert_valid(connection)
    if not installed(connection):
        raise DirectoryDependenciesMissing('Initial registration requires the dependency migration')
    ready = connection.execute(f'SELECT ready FROM {schema()}.directory_dependency_state '
                               'WHERE singleton FOR SHARE').fetchone()
    if not ready or not ready['ready']:
        raise DirectoryDependenciesMissing('Initial registration requires a ready dependency index')
    queue = _installed_queue(connection)
    rows = connection.execute(f'SELECT location,purpose,xmin::text AS tx FROM {queue.reservations} '
                              'WHERE operation_id=%s FOR SHARE', (operation,)).fetchall()
    durable = {r['location'] for r in rows if r['purpose'] == 'resource_creation'
               and r['tx'] != str(int(transaction) & 0xffffffff)}
    if not roots or any(cleanup_location(root) not in durable for root in roots):
        raise RuntimeError('Initial registration requires its durable creation reservations')
    return roots


def replace_initial_identity(connection, identity, root, *, protection):
    """A first identity covered by live owner claims needs no global path lock.

    Claims remain committed until the owner's final locked registration check.
    Pending cleanup may be enqueued meanwhile but cannot be claimed; the final
    check rejects that conflict and rolls back authority and projection alike.
    """
    roots = validate_initial_registration(connection, protection=protection)
    comparison = DirectoryComparison()
    if not any(comparison.contains(path, root) for path in roots):
        raise ValueError('Identity directory is outside the creation reservations')
    s = schema()
    if connection.execute(f'SELECT 1 FROM {s}.directory_dependencies '
                          "WHERE kind='identity' AND object_id=%s", (identity,)).fetchone():
        raise ValueError('Reserved registration only supports a first identity directory')
    if connection.execute(f'SELECT 1 FROM {s}.identity_retirements WHERE identity=%s', (identity,)).fetchone():
        raise ValueError('Cannot attach a directory to a retired identity')
    _write_entries(connection, 'identity', identity, [('', identity, root, 'owned')])


def replace(connection, kind, object_id, entries):
    """Replace only derived entries after the authoritative write, in its TX."""
    if not installed(connection):
        return
    entries = list(entries)
    if kind == 'identity' and len(entries) == 1:
        row = connection.execute(f'SELECT location,owner_id FROM {schema()}.directory_dependencies '
            'WHERE kind=%s AND object_id=%s AND slot=%s', (kind, object_id, entries[0][0])).fetchone()
        if row and row['location'] == cleanup_location(entries[0][2]) and row['owner_id'] == entries[0][1]:
            return
    queue = _installed_queue(connection)
    queue.lock(connection)
    s = schema()
    migrating = connection.execute("SELECT current_setting('tfi.directory_dependency_migration',true)").fetchone()[0] == 'v1'
    old = {row['location'] for row in connection.execute(
        f'SELECT location FROM {s}.directory_dependencies WHERE kind=%s AND object_id=%s', (kind, object_id)).fetchall()}
    for _, owner, path, disposition in entries:
        if disposition == 'owned' and connection.execute(
                f'SELECT 1 FROM {s}.identity_retirements WHERE identity=%s', (owner,)).fetchone():
            raise ValueError('Cannot attach a directory to a retired identity')
        if not migrating and disposition != 'claim' and cleanup_location(path) not in old:
            pending = queue.pending_at(path, connection=connection)
            if pending:
                from .directory_cleanup_store import DirectoryPendingCleanup
                raise DirectoryPendingCleanup(pending[0])
    _write_entries(connection, kind, object_id, entries)


def _write_entries(connection, kind, object_id, entries):
    s = schema()
    connection.execute(f'DELETE FROM {s}.directory_dependencies WHERE kind=%s AND object_id=%s', (kind, object_id))
    for slot, owner, path, disposition in entries:
        key, ancestors = path_keys(path)
        connection.execute(f'INSERT INTO {s}.directory_dependencies '
            '(kind,object_id,slot,owner_id,location,path_key,ancestors,disposition) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (kind, object_id, slot, owner, cleanup_location(path), key, ancestors, disposition))
    connection.execute(f'DELETE FROM {s}.directory_dependency_dirty WHERE kind=%s AND object_id=%s', (kind, object_id))


def overlaps(connection, path, *, comparison, kinds=None):
    key, ancestors = path_keys(path)
    rows = connection.execute(f'SELECT * FROM {schema()}.directory_dependencies '
        'WHERE (path_key=ANY(%s::text[]) OR ancestors @> ARRAY[%s]::text[]) '
        'AND (%s::text[] IS NULL OR kind=ANY(%s::text[]))', (ancestors, key, kinds, kinds)).fetchall()
    return [dict(row) for row in rows if comparison.overlaps(path, cleanup_path(row['location']))]


def install_source_guard(connection, *, table, kind, key, watched, condition='TRUE'):
    """A raw write cannot silently leave a certified projection stale."""
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return
    s = schema()
    function = 'guard_' + kind
    # All identifiers/expressions originate in installer code, never input.
    changed = ' OR '.join(f'OLD.{field} IS DISTINCT FROM NEW.{field}' for field in watched)
    connection.execute(f'''CREATE OR REPLACE FUNCTION {s}.{function}() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE k text; ident text;
    BEGIN
      IF TG_OP='UPDATE' AND NOT ({changed}) THEN RETURN NEW; END IF;
      IF TG_OP <> 'DELETE' AND NOT ({condition}) THEN RETURN NEW; END IF;
      IF TG_OP <> 'DELETE' AND current_setting('tfi.directory_dependency_writer',true) IS DISTINCT FROM 'v1' THEN
        RAISE EXCEPTION 'Directory dependency writer upgrade required';
      END IF;
      IF TG_OP='DELETE' OR TG_OP='UPDATE' THEN
        k := {kind!r}; ident := OLD.{key.format(row='OLD')};
        DELETE FROM {s}.directory_dependencies WHERE kind=k AND object_id=ident;
        DELETE FROM {s}.directory_dependency_dirty WHERE kind=k AND object_id=ident;
      END IF;
      IF TG_OP <> 'DELETE' AND ({condition}) THEN
        INSERT INTO {s}.directory_dependency_dirty(kind,object_id) VALUES ({kind!r},NEW.{key.format(row='NEW')}) ON CONFLICT DO NOTHING;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$''')
    connection.execute(f'DROP TRIGGER IF EXISTS directory_dependency_guard ON {table}')
    connection.execute(f'CREATE TRIGGER directory_dependency_guard AFTER INSERT OR UPDATE OR DELETE ON {table} '
                       f'FOR EACH ROW EXECUTE FUNCTION {s}.{function}()')


def guard_new_table(connection, *, table, kind, key, watched, condition='TRUE'):
    if installed(connection):
        install_source_guard(connection, table=table, kind=kind, key=key, watched=watched, condition=condition)


def install_claim_guard(connection, *, table, kind, key):
    s = schema()
    connection.execute(f'''CREATE OR REPLACE FUNCTION {s}.claim_{kind}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN
        DELETE FROM {s}.directory_dependencies WHERE kind={kind!r} AND object_id=OLD.{key}
          AND location=OLD.location;
        RETURN OLD;
      END IF;
      IF TG_OP='UPDATE' AND OLD.location=NEW.location AND OLD.{key}=NEW.{key} THEN RETURN NEW; END IF;
      IF current_setting('tfi.directory_dependency_writer',true) IS DISTINCT FROM 'v1' THEN
        RAISE EXCEPTION 'Directory dependency writer upgrade required';
      END IF;
      INSERT INTO {s}.directory_dependency_dirty(kind,object_id) VALUES ({kind!r},NEW.{key}) ON CONFLICT DO NOTHING;
      RETURN NEW;
    END $$''')
    connection.execute(f'DROP TRIGGER IF EXISTS directory_claim_guard ON {table}')
    connection.execute(f'CREATE TRIGGER directory_claim_guard AFTER INSERT OR UPDATE OR DELETE ON {table} '
                       f'FOR EACH ROW EXECUTE FUNCTION {s}.claim_{kind}()')


def install_writer_fence(connection, *, table, name, watched):
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return
    changed = ' OR '.join(f'OLD.{field} IS DISTINCT FROM NEW.{field}' for field in watched)
    connection.execute(f'''CREATE OR REPLACE FUNCTION {schema()}.fence_{name}() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND NOT ({changed}) THEN RETURN NEW; END IF;
      IF current_setting('tfi.directory_dependency_writer',true) IS DISTINCT FROM 'v1' THEN
        RAISE EXCEPTION 'Directory dependency writer upgrade required';
      END IF;
      RETURN NEW;
    END $$''')
    connection.execute(f'DROP TRIGGER IF EXISTS directory_writer_fence ON {table}')
    connection.execute(f'CREATE TRIGGER directory_writer_fence AFTER INSERT OR UPDATE ON {table} '
                       f'FOR EACH ROW EXECUTE FUNCTION {schema()}.fence_{name}()')


