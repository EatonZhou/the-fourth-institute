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
                if not self.snapshot.get('completed') and row['scope_id'] != current_lifecycle():
                    raise ValueError('Unfinished creation belongs to another resource lifecycle')
            else:
                self.snapshot = allocate()
                self.db.write(lambda conn: conn.execute(
                    'INSERT INTO society_creation_intents VALUES (%s,%s::jsonb,%s,%s::jsonb)',
                    (self.key, json.dumps(self.request), current_lifecycle(), json.dumps(self.snapshot))))
            yield self

    def save(self):
        self.db.write(lambda conn: conn.execute(
            'UPDATE society_creation_intents SET snapshot=%s::jsonb WHERE operation_id=%s',
            (json.dumps(self.snapshot), self.key)))

    def complete(self, connection):
        self.snapshot['completed'] = True
        connection.execute(f'UPDATE {self.table} SET snapshot=%s::jsonb WHERE operation_id=%s',
                           (json.dumps(self.snapshot), self.key))
