"""Portable upload payload layout, including persisted pre-layout records.

New writers store bytes as ``payload`` and retain the supplied display name.
Existing exported instances may have records without storage_name; their bytes
stay under file_name until that upload is deleted through normal retention.
Only this owner interprets that on-disk format difference.
"""
from .secure_paths import relative_parts

PAYLOAD_NAME = 'payload'


def payload_name(meta):
    name = meta.get('storage_name', meta.get('file_name'))
    if not isinstance(name, str) or any(c in name for c in '/\\\0'):
        raise ValueError('Upload payload must be one local filename')
    if name.casefold() == 'meta.json' or relative_parts(name) != (name,):
        raise ValueError('Upload payload conflicts with metadata or filesystem names')
    return name
