"""Upload writer/consumer hooks; unused is an explicit lifecycle fact."""
from datetime import datetime

from .directory_retention import RetentionStore
from .resource_lifecycle import record_allocation


def created(directory, *, upload_id, created_at, connection=None):
    stamp = datetime.fromisoformat(created_at.replace('Z', '+00:00')) if isinstance(created_at, str) else created_at
    RetentionStore().declare(directory, category='unused_upload', owner_id=upload_id,
                             created_at=stamp, connection=connection)
    record_allocation(upload_id)


def consumed(directory, *, owner_id, connection=None):
    RetentionStore().used(directory, owner_id=owner_id, connection=connection)


def deleted(directory, *, connection=None):
    removed = RetentionStore().release_path(directory, connection=connection)
    if not removed:
        raise ValueError('Upload retention registration is missing; complete the offline migration first')
    return True
