"""Workspace registration policy; Creation owns all filesystem recovery."""
import hashlib
from pathlib import Path

from management_common.directory_paths import cleanup_location, cleanup_path, validate_filename

from .creation import Creation
from .workspace_store import get_workspace_store


class WorkspaceCreationStore:
    def __init__(self):
        self.workspaces = get_workspace_store()

    def create(self, *, path, owner_id, actor, operation_id, title=''):
        row, _result = self._create(path=path, owner_id=owner_id, actor=actor,
                                   operation_id=operation_id, title=title, artifact_kind='directory')
        return row

    def register(self, *, path, owner_id, actor, operation_id, title=''):
        """Register existing contents; a failed registration cannot delete them."""
        row, _result = self._create(path=path, owner_id=owner_id, actor=actor,
                                   operation_id=operation_id, title=title, artifact_kind='existing_directory')
        return row

    def materialize(self, *, path, actor, operation_id, contents, initialize, title='', parent_id='', owner_id='',
                    workspace_dependencies=None, resolve_workspace_dependencies=None):
        """Create a Workspace with explicit lifecycle ownership, self-owned by default.

        Initializers populate only Creation's private directory. Their result is
        saved with that artifact, so a retry never recaptures a changed source.
        """
        return self._create(path=path, owner_id=owner_id, actor=actor, operation_id=operation_id,
                            title=title, artifact_kind='directory', contents=contents,
                            initialize=initialize, parent_id=parent_id,
                            workspace_dependencies=workspace_dependencies,
                            resolve_workspace_dependencies=resolve_workspace_dependencies)

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

    def _create(self, *, path, owner_id, actor, operation_id, title, artifact_kind,
                contents=None, initialize=None, parent_id='', workspace_dependencies=None,
                resolve_workspace_dependencies=None):
        if not actor or not operation_id:
            raise ValueError('Workspace creation requires actor and operation ID')
        raw_path = Path(path).expanduser()
        validate_filename(raw_path)
        location = cleanup_location(raw_path)
        target = cleanup_path(location)
        # Stable candidate identity, without allocating a formal object or
        # introducing a second durable creation state machine.
        identity = hashlib.sha256((actor + '\0' + operation_id).encode()).hexdigest()
        workspace_id = 'workspace_' + identity[:12]
        payload = {'location': location, 'owner_id': owner_id or workspace_id,
                   'title': title, 'artifact_kind': artifact_kind}
        if contents is not None:
            payload['contents'] = contents
        if parent_id:
            payload['parent_id'] = parent_id
        creation = Creation(workspace_id, actor=actor, operation_id='workspace:' + operation_id,
                            payload=payload, workspace_dependencies=workspace_dependencies)
        legacy_dependencies = None
        legacy_requested = False
        if resolve_workspace_dependencies is not None:
            from .creation import _workspace_dependency_document
            if creation.workspace_dependencies is None:
                raise ValueError('A dependency resolver requires proposed Workspace dependencies')
            resolved = resolve_workspace_dependencies(workspace_id, actor=actor,
                operation_id='workspace:' + operation_id, payload=payload,
                workspace_ids=creation.workspace_dependencies['workspace_ids'])
            legacy_requested = resolved is None
            if not legacy_requested and _workspace_dependency_document(resolved) != creation.workspace_dependencies:
                raise ValueError('Workspace dependency resolver changed the proposed dependencies')
        if legacy_requested:
            from management_common.resource_lifecycle import current_lifecycle
            from .creation import creation_dependency_record
            with creation.db.transaction() as connection:
                creation.queue.lock(connection)
                old = creation_dependency_record(creation.key, connection=connection)
                if (old and not old['completed'] and not old['aborted']
                        and old['workspace_dependencies'] is None
                        and (old['operation_id'], old['resource_id'], old['actor'], old['payload'], old['scope_id'])
                        == (creation.key, workspace_id, actor, payload, current_lifecycle())):
                    legacy_dependencies = creation.workspace_dependencies['workspace_ids']
                    self._require_active_dependencies(legacy_dependencies, connection)
                    # Keep the old row unknown. prepare rechecks the exact
                    # original identity, payload, scope and NULL under its lease.
                    creation.workspace_dependencies = None
                else:
                    raise ValueError('Legacy Workspace creation recovery requires an exact unfinished NULL record')
        with creation.prepare([(target, artifact_kind)]):
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
                if legacy_dependencies is not None:
                    old = creation_dependency_record(creation.key, connection=connection)
                    if (not old or old['completed'] or old['aborted'] or old['workspace_dependencies'] is not None
                            or (old['operation_id'], old['resource_id'], old['actor'], old['payload'], old['scope_id'])
                            != (creation.key, workspace_id, actor, payload, current_lifecycle())):
                        raise ValueError('Legacy Workspace creation proof changed before commit')
                    self._require_active_dependencies(legacy_dependencies, connection)
                self.workspaces.ensure(workspace_id, str(target), title=title,
                                       owner_id=owner_id or workspace_id, operation_id=creation.key,
                                       connection=connection)
                result = self.workspaces.rows([workspace_id], connection=connection)[workspace_id]
            return result, creation.artifacts[0].get('result', {})

    def _require_active_dependencies(self, identities, connection):
        self.workspaces.cleanup.lock(connection)
        rows = self.workspaces.rows(identities, connection=connection)
        if set(rows) != set(identities):
            raise ValueError('A Workspace creation dependency is no longer active')
