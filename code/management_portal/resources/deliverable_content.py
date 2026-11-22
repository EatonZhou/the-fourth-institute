"""Bounded, version-owned UTF-8 content for Resident deliverables."""
import hashlib
import re
from copy import deepcopy

MAX_TEXT_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
FIELDS = {'title', 'summary', 'body', 'validation', 'limitations', 'documents', 'references'}


def text(value, name, limit=MAX_TEXT_BYTES):
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError(f'{name} must be UTF-8 text')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError as error:
        raise ValueError(f'{name} must be UTF-8 text') from error
    if size > limit:
        raise ValueError(f'{name} exceeds {limit} bytes')
    return value


def snapshot(payload, previous=None):
    result = deepcopy(previous or {})
    result.update({key: deepcopy(value) for key, value in payload.items() if key in FIELDS})
    for key in ('title', 'summary', 'body', 'validation', 'limitations'):
        result[key] = text(result.get(key, ''), key, 4096 if key == 'title' else MAX_TEXT_BYTES)
    if not result['title'].strip():
        raise ValueError('title is required')
    result['body_sha256'] = hashlib.sha256(result['body'].encode()).hexdigest()
    documents = result.get('documents', [])
    references = result.get('references', [])
    if not isinstance(documents, list) or len(documents) > 32:
        raise ValueError('documents must be an array of at most 32 documents')
    if not isinstance(references, list) or len(references) > 64:
        raise ValueError('references must be an array of at most 64 references')
    names = set()
    result['documents'] = []
    for doc in documents:
        if not isinstance(doc, dict) or set(doc) - {'name', 'content', 'sha256'}:
            raise ValueError('invalid document')
        name = text(doc.get('name'), 'document name', 255)
        if name in {'.', '..'} or not name or any(c in name for c in '/\\') or any(ord(c) < 32 for c in name):
            raise ValueError('document name must be a single filename')
        if name in names:
            raise ValueError('duplicate document name')
        names.add(name)
        content = text(doc.get('content'), 'document content')
        digest = hashlib.sha256(content.encode()).hexdigest()
        if doc.get('sha256') and doc['sha256'] != digest:
            raise ValueError('document sha256 does not match content')
        result['documents'].append(dict(name=name, content=content, sha256=digest))
    result['references'] = []
    for ref in references:
        if not isinstance(ref, dict) or set(ref) - {'address', 'version', 'sha256'}:
            raise ValueError('invalid reference')
        address = text(ref.get('address'), 'reference address', 2048)
        if not address or address.startswith('/') or '://' in address:
            raise ValueError('reference requires a formal resource address')
        clean = {'address': address}
        if 'version' in ref:
            clean['version'] = text(str(ref['version']), 'reference version', 256)
        if 'sha256' in ref:
            if not isinstance(ref['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256']):
                raise ValueError('invalid reference sha256')
            clean['sha256'] = ref['sha256']
        result['references'].append(clean)
    size = sum(len(result[key].encode()) for key in ('title', 'summary', 'body', 'validation', 'limitations'))
    size += sum(len(doc['content'].encode()) for doc in result['documents'])
    if size > MAX_SNAPSHOT_BYTES:
        raise ValueError('deliverable snapshot exceeds 4 MiB')
    return result
