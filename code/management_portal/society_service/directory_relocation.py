"""Compose resource and Society directory owners without upward dependencies.

Registration callbacks participate in the move journal's transaction. Execution
users belong to Resident lifecycle; native Git/Provider mechanics belong to the
resource owner. Recovery uses the same admission check as normal completion.
"""
from management_common.directory_move_store import DirectoryMoveStore
from management_portal import directory_move_owner
from . import directory_execution_admission, handoff_location_storage
from .resident_lifecycle_domain import require_idle_directory
from .state_store import get_lineage_data_store


def relocate_nested(connection, source, target):
    """Enlist all current directory references in the caller's transaction."""
    directory_move_owner.relocate_nested(connection, source, target)
    handoff_location_storage.relocate_references(connection, source, target)


def reconcile_dependencies(source, target):
    """Keep execution admission closed while checking users and native state."""
    with directory_execution_admission.authority():
        return _reconcile_dependencies(source, target)


def _reconcile_dependencies(source, target):
    require_idle_directory(source, target)
    return directory_move_owner.reconcile_dependencies(source, target)


def move_idle_directory(source, target, *, owner_id, actor, database, update, operation_id):
    """Move an owned directory; hold execution admission exactly once."""
    with directory_execution_admission.authority():
        require_idle_directory(source, target)
        return DirectoryMoveStore().move(source, target, owner_id=owner_id, actor=actor,
            terminate=require_idle_directory, database=database, update=update,
            dependencies=_reconcile_dependencies, operation_id=operation_id)


def recover_pending_moves():
    """Portal System retries durable journals through the ordinary completion owner."""
    return DirectoryMoveStore().recover(dependencies=reconcile_dependencies)


def rewrite_project_database(change, rewriter, *, apply):
    """Compose Project identity/resource rewrites with current Handoff storage."""
    from management_portal.project_rename import rewrite_database
    database = get_lineage_data_store().database
    with (database.transaction() if apply else database.snapshot()) as connection:
        result = rewrite_database(change, rewriter, apply=apply,
            preserve_tables=handoff_location_storage.location_tables())
        if apply and change.moves:
            handoff_location_storage.relocate_references(connection, change.old_root, change.new_root)
        return result


def relocate_area(source, target):
    """Finish a stopped area move after its manifest switches to the new root.

    Area references already resolve at the destination. Legacy absolute current
    references still enlist the same resource and Society storage owners.
    """
    with directory_execution_admission.authority():
        require_idle_directory(source, target)
        get_lineage_data_store().database.write(lambda c: relocate_nested(c, source, target))
        return directory_move_owner.reconcile_dependencies(source, target)
