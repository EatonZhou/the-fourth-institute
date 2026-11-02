"""Workspace registration policy; Creation owns all filesystem recovery."""
import hashlib
from pathlib import Path

from management_common.directory_paths import cleanup_location, cleanup_path, validate_filename

from .creation import Creation
from .workspace_store import get_workspace_store


class WorkspaceCreationStore:
    def __init__(self):
        self.workspaces = get_workspace_store()

    def create(self, *, path, owner_id, actor, operation_id, title='', origin='created'):
        row, _result = self._create(path=path, owner_id=owner_id, actor=actor,
                                   operation_id=operation_id, title=title, origin=origin)
        return row

    def materialize(self, *, path, actor, operation_id, contents, initialize, title='', parent_id=''):
        """Create a self-owned Workspace from a durable content specification.

        Initializers populate only Creation's private directory. Their result is
        saved with that artifact, so a retry never recaptures a changed source.
        """
        return self._create(path=path, owner_id='', actor=actor, operation_id=operation_id,
                            title=title, origin='created', contents=contents,
                            initialize=initialize, parent_id=parent_id)

    def abort(self, *, actor, operation_ids):
        """Unregister this attempt's new Workspaces and clean proven artifacts."""
        from .creation import abort_creations
        from .directory_registrations import unregister_tree, unregister_identity
        def unregister(row, connection, cleanup_owner):
            unregister_tree(workspace_ids=[row['resource_id']], actor=actor,
                            connection=connection, cleanup_owner=cleanup_owner)
            # Partial creations have no Workspace row yet, but their identity
            # must also be permanently retired.
            unregister_identity(row['resource_id'], connection=connection, actor=actor)
        abort_creations(actor=actor, operation_ids=['workspace:' + value for value in operation_ids],
                        unregister=unregister)

    def _create(self, *, path, owner_id, actor, operation_id, title, origin,
                contents=None, initialize=None, parent_id=''):
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
        payload = {'location': location, 'owner_id': owner_id or workspace_id,
                   'title': title, 'origin': origin}
        if contents is not None:
            payload['contents'] = contents
        if parent_id:
            payload['parent_id'] = parent_id
        creation = Creation(workspace_id, actor=actor, operation_id='workspace:' + operation_id,
                            payload=payload)
        with creation.prepare([(target, 'adopted' if origin == 'adopted' else 'directory')]):
            if creation.completed:
                return self.workspaces.rows([workspace_id])[workspace_id], creation.artifacts[0].get('result', {})
            if initialize is None and not target.parent.is_dir():
                raise FileNotFoundError('Workspace parent directory must already exist')
            def populate(index, stage):
                if initialize is not None:
                    from .creation_git import GitWorktreeCreation
                    effects = GitWorktreeCreation(creation.artifacts[index],
                        lambda: creation.db.write(creation._save), creation.key, check=creation.assert_owned)
                    creation.artifacts[index]['result'] = initialize(stage, effects)
            creation.publish(populate)
            creation.assert_owned()
            if creation.artifacts[0].get('git_dependency'):
                from .creation_git import GitWorktreeCreation
                GitWorktreeCreation(creation.artifacts[0], lambda: creation.db.write(creation._save),
                                    creation.key, check=creation.assert_owned).publish(cleanup_path(creation.artifacts[0]['stage']), target)
            with creation.db.transaction() as connection, creation.registration(connection):
                self.workspaces.ensure(workspace_id, str(target), title=title, origin=origin,
                                       owner_id=owner_id or workspace_id, operation_id=creation.key,
                                       connection=connection)
                result = self.workspaces.rows([workspace_id], connection=connection)[workspace_id]
            return result, creation.artifacts[0].get('result', {})
