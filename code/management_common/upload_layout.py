"""Upload filenames and the one compatibility boundary for persisted layouts.

New uploads keep their filename inside ``files/``, separate from internal
records. Existing flat payload/name layouts stay readable until retention
deletes them; callers never guess a layout from the filename or disk contents.
"""
import os
from contextlib import ExitStack, contextmanager
from . import secure_paths
from .secure_paths import relative_parts

NAMED_LAYOUT = 'named-v1'


def file_name(name, *, native=True):
    if not isinstance(name, str) or name in {'', '.', '..'} or any(c in name for c in '/\\\0'):
        raise ValueError('Upload payload must be one local filename')
    if native and relative_parts(name) != (name,):
        raise ValueError('Upload payload must be one local filename')
    return name


def new_payload_metadata(name):
    """Preserve the exact name or reject it under the host filesystem rules."""
    return {'file_name': file_name(name), 'storage_layout': NAMED_LAYOUT}


def payload_parts(meta):
    name = file_name(meta.get('file_name'), native=False)
    if meta.get('storage_layout') == NAMED_LAYOUT:
        return ('files', file_name(name))
    if 'storage_layout' in meta:
        raise ValueError('Unknown upload storage layout')
    name = file_name(meta.get('storage_name', name))
    metadata_alias = name == 'meta.json' or (os.name == 'nt' and name.casefold() == 'meta.json')
    if metadata_alias:
        raise ValueError('Upload payload conflicts with metadata or filesystem names')
    return (name,)


@contextmanager
def payload_parent(directory, meta):
    """Pin the payload parent relative to an already pinned upload directory."""
    parts = payload_parts(meta)
    with ExitStack() as stack:
        for name in parts[:-1]:
            directory = secure_paths.open_child(directory, name, os.O_RDONLY, is_directory=True)
            stack.callback(os.close, directory)
        yield directory, parts[-1]
