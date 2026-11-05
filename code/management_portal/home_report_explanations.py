"""Register a fixed explanation reference in the Report lifecycle.

The original package remains the only owner of its page, review and media.
Report owns the identity, immutable reference snapshot and deletion receipt.
"""
from __future__ import annotations

import hashlib
import json
import fcntl
from contextlib import contextmanager

from management_portal.home_explanation_media import Media, MAX_BUNDLE, pointer
from management_portal.home_report_quality import quality
from management_portal.resources.errors import ResourceVersionConflict


def target(value):
    if not isinstance(value, dict) or set(value) != {'address', 'sha256'}:
        raise ValueError('解释包登记仅接受address和sha256')
    pointer(value)
    if len(value['address']) > 2048:
        raise ValueError('解释包地址过长')
    return dict(value)


def fields(value):
    ref = target(value)
    key = hashlib.sha256(json.dumps(['report-explanation/v1', ref['address'], ref['sha256']],
                                    ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'explanation_target': ref, 'origin_key': 'home-message/v1:' + key}


class _PackageReader(Media):
    def read(self, ref, actor, limit=512*1024):
        pointer(ref)
        try:
            return super().read(ref, actor, limit)
        except ValueError as exc:
            # A valid fixed reference whose bytes no longer match cannot be
            # silently rebound to a new version by a Report read or retry.
            raise ResourceVersionConflict(str(exc)) from exc


def load(ref, actor, *, service=None):
    if not actor:
        raise PermissionError('解释包登记需要认证主体')
    ref = target(ref)
    media = _PackageReader(service=service)
    context = media.context(**ref, actor=actor)
    # Reuse the explanation owner's declared dependency selection. No sibling
    # directory scan, author inference, review submission or media state read.
    remaining = MAX_BUNDLE
    for asset in media.review_assets(context):
        data = media.read(asset, actor, limit=remaining)
        remaining -= len(data)
    manifest = context['manifest']
    for key, maximum in (('title', 4000), ('summary', 16000)):
        value = manifest.get(key, '')
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError('解释包标题或摘要无效')
    quality(manifest.get('quality'))
    return manifest


def snapshot(ref, actor):
    from management_portal.home_report import digest
    from management_portal.home_report_store import now
    ref = target(ref)
    manifest = load(ref, actor)
    result = {'schema': 'tfi.home-report.v1', 'content_kind': 'explanation',
              'explanation_target': ref, 'explanation_manifest': manifest,
              'target': {'kind': 'explanation', **ref},
              'title': manifest.get('title') or '解释', 'goal': manifest.get('summary', ''),
              'quality': quality(manifest.get('quality')), 'format': 'explanation',
              'rows': [], 'relations': [], 'counts': {}, 'warnings': [], 'empty': False,
              'sources': [{'id': digest(ref)[:20], 'label': '固定解释包',
                           'address': ref['address'], 'version': ref['sha256'],
                           'version_kind': 'content_sha256',
                           'read': {'instruction': 'ResolveResource', 'arguments': {'address': ref['address']}}}],
              'checked_at': now()}
    result['version'] = digest({k: v for k, v in result.items() if k != 'checked_at'})
    return result


class ExplanationReports:
    def __init__(self, store):
        self.store = store

    def lookup(self, value, actor):
        from management_portal.home_report_store import COLLECTION, ORIGINS, EXPLANATIONS
        payload = fields(value)
        origin = self.store.origin_id(actor, payload['origin_key'])
        # Only this actor's explicit POST binding can carry a deletion tombstone.
        if self.store.registry.get(ORIGINS, origin):
            return self.store.origin(actor, payload['origin_key'])
        indexed = self.store.registry.get(EXPLANATIONS, payload['origin_key']) or {}
        for candidate in indexed.get('bindings', []):
            if not self.store.registry.get(COLLECTION, candidate['report_id']):
                continue
            try:
                return self.store.response(candidate['report_id'], actor, candidate['revision'])
            except (PermissionError, FileNotFoundError):
                # Neither private identities nor another principal's deletion
                # affect the visible result or permission to create a new one.
                continue
        raise FileNotFoundError('此解释包尚无可读的汇报登记')

    @contextmanager
    def creation_lock(self, value):
        # Held only inside the mutation provider, never by a Web caller across
        # RegisterResource RPC. All formal creators share this package lock.
        key = fields(value)['origin_key'].split(':', 1)[1]
        locks = self.store.root / '.locks'
        locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (locks / ('explanation-' + key)).open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def bind(self, value, actor, result):
        from management_portal.home_report_store import ORIGINS
        key = fields(value)['origin_key']
        origin = self.store.origin_id(actor, key)
        identifier = result['report_id']
        # Serialize against deletion and recheck both Report and source ACLs.
        with self.store.lock(identifier):
            result = self.store.response(identifier, actor, result['revision'])
            with self.store.writer(identifier) as writer:
                if writer.get(ORIGINS, origin):
                    return self.store.origin(actor, key)
                writer.put(ORIGINS, origin, {'report_id': identifier,
                           'revision': result['revision'], 'created_by': actor})
        return result

    def register(self, value, actor):
        from management_portal import permission_system
        from management_portal.resources import get_resource_service
        payload = fields(value)
        operation = 'home-report-explanation:' + self.store.origin_id(actor, payload['origin_key'])

        try:
            existing = self.lookup(value, actor)
        except FileNotFoundError:
            existing = None
        if existing:
            return self.bind(value, actor, existing)
        if not permission_system.allows(actor, 'RegisterResource', {'resource_type': 'report', 'fields': payload}):
            raise PermissionError('无权创建汇报')
        try:
            result = get_resource_service().register('report', payload, actor=actor, operation_id=operation)
        except FileExistsError:
            # A competing formal or HTTP registration committed while this
            # caller was reserving its identity. Formal callers receive the
            # conflict; Web can now reuse the readable canonical reference.
            return self.bind(value, actor, self.lookup(value, actor))
        identifier = result.get('resource_id') or result['resource']['resource_id']
        return self.store.response(identifier, actor, 1)
