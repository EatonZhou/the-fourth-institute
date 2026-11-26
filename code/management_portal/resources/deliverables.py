"""Read-only compatibility for retained deliverable IDs and immutable documents.

No writer remains. Keep this reader while historical references exist;
retirement never broadens their Project or draft access.
"""
from copy import deepcopy
from contextvars import ContextVar
import hashlib
import re

from management_common.principal import is_user_or_resident
from management_portal import permission_system
from .registry_store import get_registry_store

COLLECTION = 'deliverable'
REVISIONS = 'deliverable_revisions'
_SOURCE_READS = ContextVar('deliverable_source_reads', default=())

class DeliverableStore:
    def __init__(self, registry=None, authorize=None, service=None):
        self.registry = registry or get_registry_store()
        self.authorize = authorize or permission_system.allows
        self.service = service

    def _allowed(self, actor, instruction, arguments):
        return is_user_or_resident(actor) and bool(self.authorize(actor, instruction, arguments))

    def _private_reader(self, record, actor):
        # A published object's project-inherited admin grant says nothing about
        # the authority to see its earlier private drafts.
        return (actor in (record['author_id'], record.get('created_for'))
                or permission_system.administrators.is_administrator(actor))

    def _resource_service(self):
        if self.service is not None:
            return self.service
        from . import get_resource_service
        return get_resource_service()

    def _readable(self, record, actor, status=None):
        if not self._allowed(actor, 'GetProject', {'project_id': record['project_id']}):
            return False
        if not self._allowed(actor, 'ReadResource', {'address': record['resource_id']}):
            return False
        if record['status'] == 'draft' or status == 'draft':
            return self._private_reader(record, actor)
        return True

    def _references(self, references, actor):
        if not references:
            return []
        service = self._resource_service()
        result = []
        for ref in references:
            stack = _SOURCE_READS.get()
            if ref['address'] in stack or len(stack) >= 8:
                result.append({'status': 'restricted'})
                continue
            token = _SOURCE_READS.set((*stack, ref['address']))
            try:
                resolved = service.resolve(ref['address'], actor=actor)
                changed = ('version' in ref and str(resolved.get('version', '')) != ref['version'])
                if 'sha256' in ref:
                    data, _, _ = service.read_content(ref['address'], actor=actor, max_bytes=4 * 1024 * 1024)
                    changed = changed or hashlib.sha256(data).hexdigest() != ref['sha256']
            except Exception:
                # No address, version, digest, title or underlying exception leaks.
                result.append({'status': 'restricted'})
            else:
                result.append({**deepcopy(ref), **({'status': 'version_changed'} if changed else {})})
            finally:
                _SOURCE_READS.reset(token)
        return result

    @staticmethod
    def _validate_id(resource_id):
        if not re.fullmatch(r'deliverable_[0-9a-f]{12}', resource_id):
            raise ValueError('invalid deliverable resource_id')

    def get(self, resource_id, actor, revision=None):
        self._validate_id(resource_id)
        self.registry._ensure()
        with self.registry.database.snapshot():
            record = self.registry.get(COLLECTION, resource_id)
            if not record or not self._readable(record, actor):
                raise PermissionError('Deliverable is not accessible')
            number = record['revision'] if revision is None else revision
            if isinstance(number, str) and re.fullmatch(r'[1-9][0-9]*', number):
                number = int(number)
            if type(number) is not int or number < 1:
                raise ValueError('revision must be a positive integer')
            version = self.registry.get(REVISIONS, f'{resource_id}/{number}')
            if version is None:
                raise FileNotFoundError('Deliverable revision not found')
            if not self._readable(record, actor, version['status']):
                raise PermissionError('Deliverable revision is not accessible')
            content = deepcopy(version['snapshot'])
            content['references'] = self._references(content['references'], actor)
            return dict(record=deepcopy(record), snapshot=content, revision=number,
                        latest_revision=record['revision'], can_edit=False)

def permission_records(*, read_only: bool = False):
    store = get_registry_store()
    documents = store.readonly_snapshot(COLLECTION)[1] if read_only else store.documents(COLLECTION)
    return list(documents.values())
