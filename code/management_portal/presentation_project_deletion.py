"""Project deletion contribution over the shared presentation preference store."""
from management_portal import presentation_store


def delete_project_favorites(receipt, *, write=True):
    """Replay the owner's frozen Workbench identities, preserving old cursors."""
    targets = list(receipt.get('workbench_ids', ()))
    if write:
        for resource_id in targets:
            presentation_store.forget_favorite_target('workbench', resource_id)
    return targets


PROJECT_DELETION_EFFECTS = {'favorites': delete_project_favorites}
