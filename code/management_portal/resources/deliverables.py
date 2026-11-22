"""Authoritative deliverable records, immutable revisions and atomic PAR facts."""
from copy import deepcopy
from datetime import datetime, timezone
from contextvars import ContextVar
import hashlib
import re

from management_common.principal import is_user_or_resident
from management_portal import permission_system
from .deliverable_content import FIELDS, snapshot, text
from .errors import ResourceVersionConflict
from .registry_store import get_registry_store

COLLECTION = 'deliverable'
REVISIONS = 'deliverable_revisions'
_SOURCE_READS = ContextVar('deliverable_source_reads', default=())


class DeliverableStore:
    def __init__(self, registry=None, authorize=None, service=None):
        self.registry = registry or get_registry_store()
        self.authorize = authorize or permission_system.allows
        self.service = service
        self._custom_authorize = authorize is not None

    def _allowed(self, actor, instruction, arguments):
        return is_user_or_resident(actor) and bool(self.authorize(actor, instruction, arguments))

    def _manager(self, record, actor):
        # Turning PAR off does not turn every authenticated reader into an administrator.
        if not self._custom_authorize and permission_system.mode() == permission_system.OPEN:
            return False
        return self._allowed(actor, 'UnregisterResource', {'resource_id': record['resource_id']})

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

    def _project(self, project, actor):
        if not self._allowed(actor, 'GetProject', {'project_id': project}):
            raise PermissionError('Project is not accessible')
        record = self._resource_service().get(project, actor=actor)
        if record.get('resource_type') != 'project' or record.get('resource_id') != project:
            raise ValueError('project_id must identify an existing Project')

    def _readable(self, record, actor, status=None):
        if not self._allowed(actor, 'GetProject', {'project_id': record['project_id']}):
            return False
        if not self._allowed(actor, 'ReadResource', {'address': record['resource_id']}):
            return False
        if record['status'] == 'draft' or status == 'draft':
            return self._private_reader(record, actor)
        return True

    def _can_edit(self, record, actor):
        return ((actor == record['author_id'] or self._manager(record, actor))
                and self._allowed(actor, 'RegisterResource',
                    {'resource_type': 'deliverable', 'fields': {'project_id': record['project_id']}})
                and self._allowed(actor, 'UpdateResource', {'resource_id': record['resource_id']}))

    def _references(self, references, actor, *, creating=False):
        if not references:
            return []
        service = self._resource_service()
        result = []
        for ref in references:
            stack = _SOURCE_READS.get()
            if ref['address'] in stack or len(stack) >= 8:
                if creating:
                    raise ValueError('Cyclic or excessively nested source references')
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
                if creating:
                    raise PermissionError('Source reference is not accessible') from None
                result.append({'status': 'restricted'})
            else:
                if creating and changed:
                    raise ResourceVersionConflict('Source reference version or SHA-256 changed')
                result.append({**deepcopy(ref), **({'status': 'version_changed'} if changed else {})})
            finally:
                _SOURCE_READS.reset(token)
        return result

    @staticmethod
    def _validate_id(resource_id):
        if not re.fullmatch(r'deliverable_[0-9a-f]{12}', resource_id):
            raise ValueError('invalid deliverable resource_id')

    @staticmethod
    def _status(payload, previous=None):
        status = payload.get('status', (previous or {}).get('status', 'draft'))
        if status not in {'draft', 'published', 'withdrawn'}:
            raise ValueError('invalid deliverable status')
        if previous and previous['status'] != 'draft' and status == 'draft':
            raise ValueError('Published or withdrawn deliverables cannot become drafts')
        reason = text(payload.get('withdrawal_reason', (previous or {}).get('withdrawal_reason', '')), 'withdrawal_reason', 16384)
        if status == 'withdrawn' and not reason.strip():
            raise ValueError('withdrawal_reason is required')
        return status, reason if status == 'withdrawn' else ''

    def _save(self, writer, record, content):
        key = f"{record['resource_id']}/{record['revision']}"
        if writer.get(REVISIONS, key) is not None:
            raise ResourceVersionConflict('immutable revision already exists')
        writer.put(REVISIONS, key, {'status': record['status'], 'snapshot': content})
        writer.put(COLLECTION, record['resource_id'], record)
        permission_system.sync_resource(record, resource_type='deliverable', connection=writer.connection)

    def create(self, resource_id, payload, actor):
        self._validate_id(resource_id)
        if not isinstance(payload, dict) or set(payload) - (FIELDS | {'project_id', 'status', 'withdrawal_reason'}):
            raise ValueError('unsupported deliverable creation fields')
        project = text(payload.get('project_id'), 'project_id', 512).strip()
        if not project:
            raise ValueError('project_id is required')
        content = snapshot(payload)
        status, reason = self._status(payload)
        with self.registry.writer(COLLECTION, REVISIONS) as writer:
            if not self._allowed(actor, 'RegisterResource', {'resource_type': 'deliverable', 'fields': {'project_id': project}}):
                raise PermissionError('Project creation permission required')
            self._project(project, actor)
            existing = writer.get(COLLECTION, resource_id)
            if existing:
                if existing['author_id'] != actor or existing['project_id'] != project or not self._readable(existing, actor):
                    raise PermissionError('Deliverable is not accessible')
                return deepcopy(existing)
            self._references(content['references'], actor, creating=True)
            now = datetime.now(timezone.utc).isoformat()
            record = dict(resource_id=resource_id, resource_type='deliverable', author_id=actor,
                **permission_system.creation_fields(actor), project_id=project, projects=[project],
                title=content['title'], summary=content['summary'], status=status, withdrawal_reason=reason,
                revision=1, created_at=now, updated_at=now, portal_url=f'/results?id={resource_id}')
            record.setdefault('created_for', '')
            self._save(writer, record, content)
        return deepcopy(record)

    def update(self, resource_id, payload, actor):
        self._validate_id(resource_id)
        if not isinstance(payload, dict) or set(payload) - (FIELDS | {'expected_revision', 'status', 'withdrawal_reason'}):
            raise ValueError('unsupported deliverable update fields')
        expected = payload.get('expected_revision')
        if type(expected) is not int or expected < 1:
            raise ValueError('expected_revision must be a positive integer')
        with self.registry.writer(COLLECTION, REVISIONS) as writer:
            record = writer.get(COLLECTION, resource_id)
            if not record or not self._can_edit(record, actor):
                raise PermissionError('Deliverable edit permission required')
            self._project(record['project_id'], actor)
            if record['revision'] != expected:
                raise ResourceVersionConflict('Deliverable revision changed')
            previous = writer.get(REVISIONS, f'{resource_id}/{expected}')
            content = snapshot(payload, previous['snapshot'])
            if 'references' in payload:
                self._references(content['references'], actor, creating=True)
            status, reason = self._status(payload, record)
            record.update(title=content['title'], summary=content['summary'], status=status,
                withdrawal_reason=reason, revision=expected + 1, updated_at=datetime.now(timezone.utc).isoformat())
            self._save(writer, record, content)
        return deepcopy(record)

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
                        latest_revision=record['revision'], can_edit=self._can_edit(record, actor))

    def list(self, actor, project_id='', author_id='', offset=0, limit=30):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid pagination')
        items = [deepcopy(record) for record in self.registry.documents(COLLECTION).values()
                 if (not project_id or record['project_id'] == project_id)
                 and (not author_id or record['author_id'] == author_id) and self._readable(record, actor)]
        items.sort(key=lambda item: (item['updated_at'], item['resource_id']), reverse=True)
        return dict(items=items[offset:offset + limit], total=len(items), offset=offset, limit=limit)


def permission_records():
    return list(get_registry_store().documents(COLLECTION).values())
