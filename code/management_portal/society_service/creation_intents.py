"""Society's frozen creation request; filesystem ownership belongs to Creation."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

from management_common.resource_lifecycle import current_lifecycle
from management_portal.resources.creation import creation_operation_id

_DIRECTORY_CREATION = ContextVar('society_directory_creation', default='')


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
        self.key = kind + ':' + hashlib.sha256((actor + '\0' + operation_identity(operation_id)).encode()).hexdigest()
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
                if self.snapshot.get('cancellation'):
                    raise ValueError('Creation operation was cancelled')
                if not self.snapshot.get('completed') and row['scope_id'] != current_lifecycle():
                    raise ValueError('Unfinished creation belongs to another resource lifecycle')
            else:
                self.snapshot = allocate()
                self.db.write(lambda conn: conn.execute(
                    'INSERT INTO society_creation_intents VALUES (%s,%s::jsonb,%s,%s::jsonb)',
                    (self.key, json.dumps(self.request), current_lifecycle(), json.dumps(self.snapshot))))
            yield self

    def save(self):
        self.db.write(self._save_open)

    def _save_open(self, connection):
        cursor = connection.execute(
            f"UPDATE {self.table} SET snapshot=%s::jsonb WHERE operation_id=%s AND NOT (snapshot ? 'cancellation')",
            (json.dumps(self.snapshot), self.key))
        if cursor.rowcount != 1:
            raise ValueError('Creation operation was cancelled or removed')

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
