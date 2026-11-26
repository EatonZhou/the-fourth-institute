"""Same-ID partial Article restoration, using verified owner evidence and Creation.

The historical registration ledger is never reconstructed. This operation has
its own audit, and retains unknown authorship, ACL and final-version gaps.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from management_common.clock import utc_now_iso
from management_common.directory_retention import RetentionStore
from management_portal import permission_system

from . import article_restore_decision as decision
from . import article_restore_preview as observation
from .article_restore import read_tool_registration
from .article_restore_source import snapshot, prepare_files, verify_prepared
from .creation import Creation, abort_creations
from .directory_registrations import directory_writer, is_deleted
from .registration import ResourceRegistrationStore, RegistrationEvidenceMissing
from .portal import portal_url_for_address
from .registry_store import get_registry_store, announce_registry_changes, RegistryWriter

JOURNAL = 'article_restorations'
# Retain the journal namespace used by already accepted recovery operations.
CREATION_PREFIX = 'RestoreArticle:'
SELECTORS = ('resident_id', 'ordinal', 'tool_index', 'tool_item_id', 'expected_hash')
EXTRA = frozenset({'inheritance_digest', 'decision_resident_id', 'proposal_message_id', 'decision_message_id'})


def _registered(connection, collection, rid):
    return RegistryWriter(connection, (collection,), get_registry_store().database.schema).get(collection, rid)


def _arguments(arguments, service):
    if set(arguments) != observation.REQUIRED | EXTRA:
        raise ValueError('Article restore apply requires the exact partial restoration parameters')
    source_root = observation._validate({k: arguments[k] for k in observation.REQUIRED}, service)
    if arguments['primary_file'] != 'main.md':
        raise ValueError('Partial restoration requires main.md as its explanatory primary')
    for key, pattern in (('inheritance_digest', r'[0-9a-f]{64}'),
                         ('decision_resident_id', r'resident_[0-9a-f]{12}'),
                         ('proposal_message_id', r'msg_[0-9a-f]+'), ('decision_message_id', r'msg_[0-9a-f]+')):
        if (not isinstance(arguments[key], str) or len(arguments[key]) > 100
                or not re.fullmatch(pattern, arguments[key])):
            raise ValueError('Invalid exact decision selector: ' + key)
    return source_root


def _authorize(arguments, actor, source_root):
    observation._authorize(actor, arguments, source_root)


def current_sharing(actor, arguments):
    """Freeze inherited rules and existing direct denies; mint no permit rows."""
    inherited = observation._inheritance(actor, arguments['project_id'])
    query = {'r': arguments['article_id'], 'r_scope': 'exact', 'temporary': False, 'limit': 256}
    if not permission_system.allows(actor, 'ListPermissionEntries', query):
        raise PermissionError('Current Article entries are not readable')
    rows = permission_system.manage('ListPermissionEntries', query, actor=actor)
    if rows.get('next_cursor'):
        raise ValueError('Direct Article permission observation is incomplete')
    entries = [{k: row.get(k) for k in ('p', 'a', 'r', 'effect', 'disabled', 'entry_id', 'version')}
               for row in rows['entries']]
    if any(row['effect'] == 'permit' for row in entries):
        raise ValueError('Existing direct Article permits require separate review; restoration will not discard or recreate them')
    inherited.pop('digest')
    inherited['current_direct_entries'] = entries
    inherited['digest'] = observation.digest(inherited)
    return inherited


def _source(arguments, actor, service):
    description, files = snapshot(service, arguments['source_address'], actor)
    primary = files.get('main.md', b'')
    if description['version'] != arguments['source_version']:
        raise ValueError('Recursive recovery source version changed')
    if hashlib.sha256(primary).hexdigest() != arguments['primary_version']:
        raise ValueError('Partial recovery primary version changed')
    if not all(word in primary.decode('utf-8') for word in ('部分恢复', '最终版本未找齐')):
        raise ValueError('Recovery primary must disclose the incomplete final manuscript')
    return description, files


def _evidence(arguments, actor, receipt_store, connection=None):
    tool = read_tool_registration(article_id=arguments['article_id'], actor=actor,
                                 **{k: arguments[k] for k in SELECTORS})
    if tool['record']['project_ids'] != [arguments['project_id']]:
        raise ValueError('Recovery cannot silently change the original Project')
    try:
        proof = ResourceRegistrationStore().verify_deleted('article', tool['fields'],
            actor=tool['registration_actor'], operation_id=tool['operation_id'],
            resource_id=arguments['article_id'], connection=connection)
    except RegistrationEvidenceMissing as exc:
        ledger = {'status': 'not_found', 'reason': exc.reason, 'retention_reason': 'unknown'}
    else:
        ledger = {'status': 'verified', 'proof': proof}
    receipt = observation._receipt(tool, receipt_store)
    if receipt['status'] == 'conflict':
        raise ValueError('Independent HATG receipt conflicts with retained registration')
    return tool, ledger, receipt


def scope_for(arguments, tool, inheritance):
    return {'mode': 'partial_restore', 'article_id': arguments['article_id'],
            'title': tool['record']['title'], 'original_created_at': tool['record']['created_at'],
            'source_address': arguments['source_address'], 'source_version': arguments['source_version'],
            'primary_file': 'main.md', 'primary_version': arguments['primary_version'],
            'project_id': arguments['project_id'], 'target_relative_path': arguments['target_relative_path'],
            'inheritance_digest': inheritance['digest'], 'visibility': 'current_project_shared_with_applicable_denies',
            'original_author': 'unknown', 'historical_acl': 'unknown', 'final_manuscript': 'incomplete',
            'evidence': {k: arguments[k] for k in SELECTORS}}


class _ArticleRecoveryCreation(Creation):
    """Private owner integration. No generic API can enable tombstone reuse."""
    def __init__(self, *args, verify_identity, **kwargs):
        self.verify_identity = verify_identity
        super().__init__(*args, **kwargs)

    def _check_identity(self, connection):
        if getattr(self, 'completed', False):
            record = _registered(connection, JOURNAL, self.resource_id)
            if (record and record.get('creation_key') == self.key
                    and not is_deleted(self.resource_id, connection=connection)
                    and _registered(connection, 'article', self.resource_id) == record.get('stored_document')):
                return
            raise ValueError('Completed restoration identity changed')
        self.verify_identity(connection)

    def _complete_registration(self, connection):
        # The original RegisterResource ledger remains deleted or absent. The
        # Article recovery journal is the completion authority for this intent.
        record = _registered(connection, JOURNAL, self.resource_id)
        if not record or record.get('creation_key') != self.key:
            raise RuntimeError('Article restoration lacks its atomic completion receipt')


def restore(*, arguments, actor, service, receipt_store, operation_id):
    source_root = _arguments(arguments, service)
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError('Restoration requires a stable operation identity')
    rid = arguments['article_id']
    _authorize(arguments, actor, source_root)
    request = {'actor': actor, 'arguments': dict(arguments), 'operation_id': operation_id}
    request_hash = observation.digest(request)
    registry = get_registry_store()

    def replay(writer):
        old = writer.get(JOURNAL, rid)
        if old is None:
            return None
        if old.get('request_hash') != request_hash:
            raise ValueError('Another restoration operation already owns this Article ID')
        if is_deleted(rid, connection=writer.connection) or writer.get('article', rid) is None:
            raise ValueError('The restored Article was subsequently deleted or replaced')
        if writer.get('article', rid) != old.get('stored_document'):
            raise ValueError('The restored Article registration changed')
        # Keep the historical receipt intact; resolve its navigation URL through
        # the current route owner, including receipts written before this fix.
        return {**old['result'], 'portal_url': portal_url_for_address(rid, 'article')}

    # Replays never recopy files or mint another recovery record.
    with directory_writer('article', JOURNAL) as writer:
        previous = replay(writer)
        if previous is not None:
            return previous

    from management_portal.portal_features import contributions
    sources = contributions('resource.article_registration')
    if len(sources) != 1 or not callable(getattr(sources[0][1], 'prepare_restored_document', None)):
        raise ValueError('Article restoration requires one installed registration contribution')
    article_source = sources[0][1]

    tool, ledger, receipt = _evidence(arguments, actor, receipt_store)
    inheritance = current_sharing(actor, arguments)
    if inheritance['status'] != 'observed' or inheritance['digest'] != arguments['inheritance_digest']:
        raise ValueError('Project sharing changed or is incompletely observed')
    scope = scope_for(arguments, tool, inheritance)
    approval = decision.verify(arguments, actor, scope)
    source, files = _source(arguments, actor, service)
    target = observation._target(service, actor, arguments)
    if target['blocked_parent']:
        raise ValueError('Project target parent is unavailable')
    destination = Path(target['path'])
    original = tool['record']['canonical_root']
    cleanup_proof = None

    def check_identity(connection):
        nonlocal cleanup_proof
        from .deleted_identity_boundary import require_live_identity
        require_live_identity(connection, arguments['project_id'])
        if not is_deleted(rid, connection=connection):
            raise ValueError('Restoration requires the original Article tombstone')
        if _registered(connection, 'article', rid) is not None:
            raise ValueError('A live Article registration already exists')
        current_cleanup = creation.queue.verify_manuscript_cleanup(owner_id=rid,
            original_path=original, connection=connection)
        if cleanup_proof is not None and cleanup_proof != current_cleanup:
            raise ValueError('Original manuscript cleanup evidence changed')
        cleanup_proof = current_cleanup
        if Path(original).exists() or Path(original).is_symlink():
            raise ValueError('The removed original manuscript path is occupied')
        creation.queue.assert_available(Path(original), connection=connection)

    creation = _ArticleRecoveryCreation(rid, actor=actor, operation_id=CREATION_PREFIX + operation_id,
        payload={**request, 'parent_id': arguments['project_id']}, verify_identity=check_identity)
    prepared = False
    try:
        with creation.prepare([(destination, 'directory')]):
            prepared = True
            if creation.completed:
                with directory_writer('article', JOURNAL) as writer:
                    return replay(writer)
            creation.publish(lambda _index, stage: prepare_files(stage, files, source['manifest']['directories']))
            with directory_writer('article', JOURNAL) as writer:
                connection = writer.connection
                # Same owner transaction: permission mutations cannot commit
                # between this final authorization and the resource switch.
                _scheme, engine, instance = permission_system.active().current()
                instance._lock(connection)
                engine.store.lock_version(connection)
                _authorize(arguments, actor, source_root)
                latest_inheritance = current_sharing(actor, arguments)
                if latest_inheritance != inheritance:
                    raise ValueError('Project sharing changed before commit')
                if decision.verify(arguments, actor, scope) != approval:
                    raise ValueError('Recovery decision changed before commit')
                latest_tool, ledger, receipt = _evidence(arguments, actor, receipt_store, connection)
                if latest_tool != tool:
                    raise ValueError('Original registration evidence changed')
                latest_target = observation._target(service, actor, arguments)
                if latest_target['path'] != str(destination) or latest_target['blocked_parent']:
                    raise ValueError('Target Project changed before commit')
                _source(arguments, actor, service)
                with creation.registration(connection):
                    verify_prepared(destination, source)
                    now = utc_now_iso()
                    recovery = {'mode': 'partial_restore', 'restored_by': actor, 'restored_at': now,
                        'original_author': None, 'historical_acl': None, 'final_manuscript_verified': False,
                        'source_version': source['version']}
                    document, stored = article_source.prepare_restored_document(destination,
                        article_id=rid, title=tool['record']['title'],
                        created_at=tool['record']['created_at'], project_id=arguments['project_id'],
                        recovery=recovery)
                    result = {'resource_id': rid, 'mode': 'partial_restore', 'primary_file': 'main.md',
                              'final_version_recovered': False, 'restored_at': now,
                              'portal_url': portal_url_for_address(rid, 'article')}
                    audit = {'resource_id': rid, 'mode': 'partial_restore', 'request_hash': request_hash,
                        'creation_key': creation.key, 'scope': scope, 'approval': approval,
                        'registration_ledger': ledger, 'hatg_receipt': receipt, 'tool_registration': tool,
                        'manuscript_cleanup': cleanup_proof, 'source': source, 'inheritance': inheritance,
                        'stored_document': stored, 'result': result}
                    writer.restore_article(rid, stored, audit)
                    permission_system.sync_resource({**document, 'resource_id': rid},
                        resource_type='article', connection=connection)
                    RetentionStore(connection=connection).declare(destination, category='object_data',
                        owner_id=rid, bytes=source['bytes'], connection=connection)
        announce_registry_changes('article', {rid: 'created'}, resource_type='article')
        return result
    except Exception:
        # Abort only this intent's proven inodes. No generic owner unregister
        # callback: failure cannot erase another live registration.
        if prepared:
            abort_creations(actor=actor, operation_ids=[CREATION_PREFIX + operation_id], unregister=None)
        raise
