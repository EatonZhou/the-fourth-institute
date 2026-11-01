"""Upload consumer boundary; called by owners inside their accepting transaction."""
from collections.abc import Mapping


def consume_message(message, *, owner_id, connection):
    from management_common.upload_retention import consumed
    from management_portal.resources.isa import _upload_dir
    if message.get('role') != 'user':
        return
    parts = [*(message.get('content_parts') or []), *(message.get('input_media') or [])]
    identifiers = {part['upload_id'] for part in parts
                   if isinstance(part, Mapping) and part.get('upload_id')}
    for identifier in sorted(identifiers):
        consumed(_upload_dir(identifier), owner_id=owner_id, connection=connection)


def consume_file(path, *, owner_id, connection):
    """A feature successfully imported a file from an original ISA upload."""
    from pathlib import Path
    from management_portal.resources.isa import upload_root, _upload_dir
    from management_common.upload_retention import consumed
    path = Path(path).absolute()
    if path.parent.parent == upload_root().absolute() and path.parent.name.startswith('upload_'):
        consumed(_upload_dir(path.parent.name), owner_id=owner_id, connection=connection)
