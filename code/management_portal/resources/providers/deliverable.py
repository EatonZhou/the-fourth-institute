"""Read-only historical deliverable identities and immutable source documents."""
import json
import re
from urllib.parse import unquote

from ..deliverables import DeliverableStore


def install_resolver(service):
    def resolve(address, *, actor='', **context):
        match = re.fullmatch(r'(deliverable_[0-9a-f]{12})(?:/revision/([1-9][0-9]*)(?:/file/([^/]+))?)?', address)
        if not match:
            raise ValueError('invalid deliverable address')
        identifier, revision, filename = match.groups()
        detail = DeliverableStore(service=service).get(identifier, actor, int(revision) if revision else None)
        result = dict(address=address, root_id=identifier, resource_type='deliverable', target_kind='deliverable',
            record=detail['record'], detail=detail, exists=True, content_kind='json',
            version=str(detail['revision']), filename='deliverable.json',
            portal_url=f"/api/resources/content?address={identifier}/revision/{detail['revision']}")
        if filename is not None:
            filename = unquote(filename)
            doc = next((doc for doc in detail['snapshot']['documents'] if doc['name'] == filename), None)
            if doc is None:
                raise FileNotFoundError('Deliverable document not found')
            result.update(content_kind='text', filename=filename, document=doc, version=doc['sha256'],
                          size=len(doc['content'].encode()))
        return result

    def read(resolved, *, actor='', max_bytes=4 * 1024 * 1024, **context):
        current = resolve(resolved['address'], actor=actor)
        if 'document' in current:
            data, mime = current['document']['content'].encode(), 'text/plain; charset=utf-8'
        else:
            data, mime = json.dumps(current['detail'], ensure_ascii=False).encode(), 'application/json'
        if len(data) > max_bytes:
            raise ValueError('Deliverable exceeds read byte budget')
        return data, mime, {'filename': current['filename']}
    service.register_resolver('deliverable', resolve, read_content=read)
