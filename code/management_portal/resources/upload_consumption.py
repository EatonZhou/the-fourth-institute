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
    from management_portal.resources.isa import upload_root, _upload_dir, read_upload
    from management_portal.hatg.contracts import InstructionError
    from management_common.upload_retention import consumed
    path = Path(path).absolute()
    try:
        relative = path.relative_to(upload_root().absolute())
    except ValueError:
        return
    if len(relative.parts) < 2 or not relative.parts[0].startswith('upload_'):
        return
    identifier = relative.parts[0]
    try:
        current = read_upload(identifier)
    except InstructionError as error:
        if error.code == 'instruction.not_found':
            return
        raise
    if path == Path(current['path']).absolute():
        consumed(_upload_dir(identifier), owner_id=owner_id, connection=connection)
