"""Society's frozen creation request; filesystem ownership belongs to Creation."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.resource_lifecycle import current_lifecycle
from management_portal.resources.creation import creation_operation_id

_DIRECTORY_CREATION = ContextVar('society_directory_creation', default='')
WORKSPACE_INTENT_KINDS = (
    'resident', 'create_resident_full_context_fork', 'create_resident_compacted_context_fork',
    'create_resident_fork_from_message', 'create_resident_context_fork',
)


def pending_resident_workspace_intents(connection):
    from management_common.portal_database import _database_schema

    table = f'"{_database_schema("directory_lifecycle")}".society_creation_intents'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return ()
    return connection.execute(
        f"SELECT operation_id,request,scope_id,"
        "jsonb_build_object("
        "'resident_id',snapshot#>'{record,resident_id}',"
        "'created_by',snapshot#>'{record,created_by}',"
        "'workspace_bindings',snapshot#>'{record,workspace_bindings}',"
        "'workspace_path',snapshot#>'{record,workspace_path}',"
        "'lineage_id',snapshot#>'{record,lineage_id}',"
        f"'project_id',snapshot#>'{{record,project_id}}') AS record FROM {table} "
        "WHERE split_part(operation_id, ':', 1)=ANY(%s) "
        "AND snapshot->'completed' IS DISTINCT FROM 'true'::jsonb "
        "AND snapshot->'workspace_preclaim_failed' IS DISTINCT FROM 'true'::jsonb "
        "AND NOT (snapshot ? 'cancellation')",
        (list(WORKSPACE_INTENT_KINDS),),
    ).fetchall()


def current_directory_creation():
    return _DIRECTORY_CREATION.get()


@contextmanager
def directory_creation_scope(operation_id):
    token = _DIRECTORY_CREATION.set(operation_id)
    try:
        yield
    finally:
        _DIRECTORY_CREATION.reset(token)


def initialize_society_creation_schema(connection, schema):
    """Fresh-schema DDL, also exposed for the parent's offline migration."""
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (schema + ':society-creation:init',))
    connection.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".society_creation_intents (
        operation_id TEXT PRIMARY KEY, request JSONB NOT NULL,
        scope_id TEXT NOT NULL, snapshot JSONB NOT NULL
    )''')


def operation_identity(explicit=''):
    from management_portal.hatg.kernel import current_instruction_id
    return creation_operation_id(explicit or current_instruction_id())


class CreationIntent:
    def __init__(self, kind, actor, request, operation_id=''):
        from management_common.portal_database import portal_database
        self.db = portal_database(schema='directory_lifecycle')
        self.table = f'"{self.db.schema}".society_creation_intents'
        invocation_operation = operation_identity()
        self.source_operation_id = operation_id or invocation_operation
        self.instruction_id = invocation_operation if invocation_operation.startswith('inst_') else ''
        self.key = kind + ':' + hashlib.sha256((actor + '\0' + self.source_operation_id).encode()).hexdigest()
        self.request = request
        with self.db.read() as conn:
            ready = conn.execute("SELECT to_regclass('society_creation_intents')").fetchone()[0]
        if not ready:
            self.db.write(lambda conn: initialize_society_creation_schema(conn, self.db.schema))

    @contextmanager
    def open(self, allocate):
        lock = int.from_bytes(hashlib.sha256(self.key.encode()).digest()[:8], 'big', signed=True)
        with self.db.advisory_lock(lock):
            with self.db.read() as conn:
                row = conn.execute('SELECT * FROM society_creation_intents WHERE operation_id=%s', (self.key,)).fetchone()
            if row:
                if row['request'] != self.request:
                    raise ValueError('Creation operation ID was already used with different arguments')
                self.snapshot = row['snapshot']
                if self.snapshot.get('cancellation') or self.snapshot.get('cancellation_requested'):
                    raise ValueError('Creation operation was cancelled')
                if not self.snapshot.get('completed') and row['scope_id'] != current_lifecycle():
                    raise ValueError('Unfinished creation belongs to another resource lifecycle')
                if self.snapshot.pop('workspace_preclaim_failed', False):
                    self.save()
            else:
                self.snapshot = allocate()
                def insert(connection):
                    self._lock_workspace_dependencies(connection)
                    connection.execute(
                        'INSERT INTO society_creation_intents VALUES (%s,%s::jsonb,%s,%s::jsonb)',
                        (self.key, json.dumps(self.request), current_lifecycle(), json.dumps(self.snapshot)),
                    )
                self.db.write(insert)
            try:
                yield self
            except Exception:
                if self.key.partition(':')[0] in WORKSPACE_INTENT_KINDS and not self.snapshot.get('record'):
                    # Rejection before freezing any Resident may be retried.
                    # An admitted request with a frozen record stays visible.
                    self.snapshot['workspace_preclaim_failed'] = True
                    self.save()
                raise

    def save(self):
        self.db.write(self._save_open)

    def refresh(self):
        """Discard in-memory completion from a rolled-back registration transaction."""
        with self.db.read() as connection:
            row = connection.execute(f'SELECT snapshot FROM {self.table} WHERE operation_id=%s',
                                     (self.key,)).fetchone()
        if row is None:
            raise KeyError(self.key)
        self.snapshot = row['snapshot']

    def _save_open(self, connection):
        self._lock_workspace_dependencies(connection)
        cursor = connection.execute(
            f"UPDATE {self.table} SET snapshot=%s::jsonb WHERE operation_id=%s AND NOT (snapshot ? 'cancellation')",
            (json.dumps(self.snapshot), self.key))
        if cursor.rowcount != 1:
            raise ValueError('Creation operation was cancelled or removed')

    def _lock_workspace_dependencies(self, connection):
        if self.key.partition(':')[0] in WORKSPACE_INTENT_KINDS:
            get_directory_cleanup_store().lock(connection)

    def complete(self, connection):
        self.snapshot['completed'] = True
        self._save_open(connection)


@contextmanager
def resident_creation_intent(resource_id):
    """Lock the frozen allocation with the same key used by creation/recovery."""
    from management_common.portal_database import portal_database
    db = portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        if conn.execute("SELECT to_regclass('society_creation_intents')").fetchone()[0] is None:
            raise KeyError(resource_id)
        rows = conn.execute("SELECT operation_id FROM society_creation_intents "
            "WHERE snapshot->'record'->>'resident_id'=%s", (resource_id,)).fetchall()
    if len(rows) != 1:
        raise ValueError('Creation requires one unambiguous frozen Resident intent')
    key = rows[0]['operation_id']
    lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    with db.advisory_lock(lock):
        with db.read() as conn:
            row = conn.execute('SELECT * FROM society_creation_intents WHERE operation_id=%s', (key,)).fetchone()
        intent = CreationIntent.__new__(CreationIntent)
        intent.db, intent.key = db, key
        intent.table = f'"{db.schema}".society_creation_intents'
        intent.request, intent.snapshot = row['request'], row['snapshot']
        yield intent


def record_intent_cancellation(intent, actor, connection):
    from management_common.clock import utc_now_iso
    intent.snapshot['cancellation'] = {'actor': actor, 'at': utc_now_iso()}
    connection.execute(f'UPDATE {intent.table} SET snapshot=%s::jsonb WHERE operation_id=%s',
                       (json.dumps(intent.snapshot), intent.key))
