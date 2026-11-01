"""Workspace registration policy; Creation owns all filesystem recovery."""
import hashlib
from pathlib import Path

from management_common.directory_paths import cleanup_location, cleanup_path, validate_filename

from .creation import Creation
from .workspace_store import get_workspace_store


def retire_workspace_creation_schema(connection, schema):
    """Offline only. Unknown legacy operations must be reviewed, never guessed."""
    table = f'"{schema}".workspace_creations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return
    connection.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
    if connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
        raise RuntimeError('Nonempty legacy workspace_creations requires explicit ownership reconciliation')
    connection.execute(f'DROP TABLE {table}')


class WorkspaceCreationStore:
    def __init__(self):
        self.workspaces = get_workspace_store()

    def create(self, *, path, owner_id, actor, operation_id, title='', origin='created'):
        if not actor or not operation_id:
            raise ValueError('Workspace creation requires actor and operation ID')
        if origin not in {'created', 'adopted'}:
            raise ValueError('Workspace origin must be created or adopted')
        raw_path = Path(path).expanduser()
        validate_filename(raw_path)
        location = cleanup_location(raw_path)
        target = cleanup_path(location)
        # Stable candidate identity, without allocating a formal object or
        # introducing a second durable creation state machine.
        identity = hashlib.sha256((actor + '\0' + operation_id).encode()).hexdigest()
        workspace_id = 'workspace_' + identity[:12]
        creation = Creation(workspace_id, actor=actor, operation_id='workspace:' + operation_id,
                            payload={'location': location, 'owner_id': owner_id or workspace_id,
                                     'title': title, 'origin': origin})
        with creation.prepare([(target, 'adopted' if origin == 'adopted' else 'directory')]):
            if creation.completed:
                return self.workspaces.rows([workspace_id])[workspace_id]
            if not target.parent.is_dir():
                raise FileNotFoundError('Workspace parent directory must already exist')
            creation.publish(lambda _index, _stage: None)
            with creation.db.transaction() as connection, creation.registration(connection):
                self.workspaces.ensure(workspace_id, str(target), title=title, origin=origin,
                                       owner_id=owner_id or workspace_id, operation_id=creation.key,
                                       connection=connection)
                result = self.workspaces.rows([workspace_id], connection=connection)[workspace_id]
            return result
