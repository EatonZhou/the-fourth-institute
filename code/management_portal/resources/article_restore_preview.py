"""Read-only, bounded evidence for a possible same-ID Article recovery.

No result from this module authorizes a future commit. Each owning service
supplies current facts; the preview does not acquire locks or reserve paths.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .article_restore import read_tool_registration, ToolEvidenceUnavailable
from .registration import ResourceRegistrationStore, RegistrationEvidenceMissing, RegistrationEvidenceConflict

ACTION = 'CheckArticleRestore'
SOURCE_BUDGET = 8 * 1024 * 1024
OUTPUT_BUDGET = 128 * 1024
REQUIRED = frozenset({'article_id', 'resident_id', 'ordinal', 'tool_index', 'tool_item_id', 'expected_hash',
    'source_address', 'source_version', 'project_id', 'target_relative_path', 'primary_file', 'primary_version'})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value
            or any(ord(c) < 32 for c in value) or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('A normalized, non-empty relative path is required')
    return PurePosixPath(value)


def _validate(arguments, service):
    if set(arguments) != REQUIRED:
        raise ValueError('Preview accepts only the exact evidence and proposal parameters; no apply or approval input')
    for key, pattern in (('article_id', r'article_[0-9a-f]{12}'), ('resident_id', r'resident_[0-9a-f]{12}'),
                         ('expected_hash', r'[0-9a-f]{64}'), ('source_version', r'[0-9a-f]{64}'),
                         ('primary_version', r'[0-9a-f]{64}')):
        if not isinstance(arguments[key], str) or not re.fullmatch(pattern, arguments[key]):
            raise ValueError('Invalid exact selector: ' + key)
    if (type(arguments['ordinal']) is not int or not 0 <= arguments['ordinal'] <= 2**63-1
            or type(arguments['tool_index']) is not int or not 0 <= arguments['tool_index'] <= 10000
            or not isinstance(arguments['tool_item_id'], str) or not 1 <= len(arguments['tool_item_id']) <= 256
            or not isinstance(arguments['project_id'], str) or not 1 <= len(arguments['project_id']) <= 80):
        raise ValueError('Invalid bounded selector')
    for key in ('target_relative_path', 'primary_file'):
        relative_path(arguments[key])
    address = arguments['source_address']
    if not isinstance(address, str) or len(address) > 2048:
        raise ValueError('Source must be an exact registered folder address')
    parsed = service.parse_address(address)
    if not parsed.suffix or parsed.suffix[0] != 'file':
        raise ValueError('Source must identify a registered folder')
    return parsed.root_id


def _authorize(actor, arguments, source_root):
    from management_portal import permission_system
    checks = {**arguments, 'source_address': source_root}
    if not permission_system.allows(actor, ACTION, checks):
        raise PermissionError('Article admin, history admin, source read and target project write are required')


def _source_content(service, actor, arguments):
    address = arguments['source_address']
    data, _mime, resolved = service.read_content(address, actor=actor, max_bytes=SOURCE_BUDGET)
    if len(data) > SOURCE_BUDGET:
        return {'status': 'unsupported', 'reason': 'source_budget'}
    if resolved.get('target_kind') != 'directory':
        return {'status': 'conflict', 'reason': 'source_not_directory'}
    listing = json.loads(data)
    version = hashlib.sha256(data).hexdigest()
    primary_address = address.rstrip('/') + '/' + arguments['primary_file']
    primary, mime, primary_resolved = service.read_content(primary_address, actor=actor, max_bytes=SOURCE_BUDGET)
    primary_version = hashlib.sha256(primary).hexdigest()
    conflicts = []
    if version != arguments['source_version']:
        conflicts.append('source_version')
    if primary_version != arguments['primary_version']:
        conflicts.append('primary_version')
    if primary_resolved.get('target_kind') != 'file':
        conflicts.append('primary_not_file')
    if len(primary) > SOURCE_BUDGET:
        return {'status': 'unsupported', 'reason': 'primary_budget'}
    if mime != 'application/pdf' or not primary.startswith(b'%PDF-'):
        conflicts.append('primary_not_pdf')
    truncated = bool(listing.get('truncated')) if isinstance(listing, dict) else False
    return {'status': 'conflict' if conflicts else ('indeterminate' if truncated else 'observed'),
            'address': address, 'version': version, 'version_scope': 'direct_directory_listing_only',
            'recursive_tree_verified': False, 'truncated': truncated, 'conflicts': conflicts,
            'primary': {'address': primary_address, 'version': primary_version, 'size': len(primary)},
            'final_deleted_version_verified': False}


def _source(service, actor, arguments):
    try:
        return _source_content(service, actor, arguments)
    except FileNotFoundError:
        return {'status': 'not_found', 'reason': 'source_file_not_found'}
    except ValueError as exc:
        # The content owner reports its byte budget separately from ACL denial.
        if 'limit' not in str(exc).lower():
            raise
        return {'status': 'unsupported', 'reason': 'source_content_budget'}


def _target(service, actor, arguments):
    project = service.get(arguments['project_id'], actor=actor)
    attributes = project.get('attributes') or {}
    if project.get('resource_type') != 'project' or attributes.get('status') == 'deleted':
        raise ValueError('Target must be a live registered Project')
    raw_root = attributes.get('path')
    if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
        raise ValueError('Target Project has no registered absolute root')
    root = Path(raw_root).resolve(strict=False)
    target = root.joinpath(*relative_path(arguments['target_relative_path']).parts)
    current = root
    blocked_parent = not root.is_dir()
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Target path must not traverse a symlink')
        if current != target and current.exists() and not current.is_dir():
            blocked_parent = True
    if not target.resolve(strict=False).is_relative_to(root):
        raise ValueError('Target path escapes the Project')
    return {'status': 'conflict' if target.exists() or blocked_parent else 'observed', 'project_id': arguments['project_id'],
            'project_root': str(root), 'relative_path': arguments['target_relative_path'],
            'path': str(target), 'exists': target.exists(), 'blocked_parent': blocked_parent, 'project_write_checked': True}


def _inheritance(actor, project_id):
    from management_portal import permission_system
    scheme = permission_system.active().current()[0]
    parent = scheme.placement('R', 'article', {'project': project_id})
    query = {'r': parent, 'r_scope': 'ancestors', 'temporary': False, 'limit': 256}
    if not permission_system.allows(actor, 'ListPermissionEntries', query):
        raise PermissionError('Current inherited permission entries are not readable')
    rows = permission_system.manage('ListPermissionEntries', query, actor=actor)
    entries = [{k: row.get(k) for k in ('p', 'a', 'r', 'effect', 'disabled', 'entry_id', 'version')}
               for row in rows['entries']]
    result = {'status': 'indeterminate' if rows.get('next_cursor') else 'observed',
              'scheme_id': scheme.scheme_id, 'scheme_digest': scheme.digest, 'placement_parent': parent,
              'project_id': project_id, 'entries': entries, 'truncated': bool(rows.get('next_cursor')),
              'scope': 'current_project_ancestor_entries', 'historical_acl_verified': False,
              'private_to_requester': False, 'temporary_grants_excluded': True,
              'note': 'Inherited permits apply to their listed principals; applicable denies still win. '
                      'This is not an enumeration of all users or a restored ACL.'}
    result['digest'] = digest(result)
    return result


def _receipt(tool, store):
    from psycopg.errors import UndefinedTable, InvalidSchemaName
    try:
        row = store.get(tool['instruction_id'])
    except (UndefinedTable, InvalidSchemaName):
        return {'status': 'not_found', 'reason': 'owner_storage_missing', 'retention_reason': 'unknown'}
    if row is None:
        return {'status': 'not_found', 'reason': 'no_retained_record', 'retention_reason': 'unknown'}
    checks = {'instruction': row.get('instruction') == 'RegisterResource',
              'status': row.get('status') == 'committed',
              'caller': row.get('caller') == tool['registration_actor'],
              'resource_id': (row.get('references') or {}).get('resource_id') == tool['record']['resource_id']}
    return {'status': 'observed' if all(checks.values()) else 'conflict',
            'instruction_id': tool['instruction_id'], 'checks': checks,
            'request_fingerprint_available': False, 'substitute_commit_evidence': False}


def preview(*, arguments, actor, service, receipt_store):
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    from .registry_store import get_registry_store

    source_root = _validate(arguments, service)
    _authorize(actor, arguments, source_root)
    selector = {key: arguments[key] for key in ('resident_id', 'ordinal', 'tool_index', 'tool_item_id', 'expected_hash')}
    # Read from Society before parsing. A denied AMA/history request propagates.
    try:
        tool = read_tool_registration(article_id=arguments['article_id'], actor=actor, **selector)
    except ToolEvidenceUnavailable as exc:
        tool = None
        tool_result = {'status': exc.status, 'reason': exc.reason}
    except ValueError as exc:
        tool = None
        tool_result = {'status': 'conflict', 'reason': str(exc)[:512]}
    else:
        record = tool['record']
        tool_result = {'status': 'observed', 'instruction_id': tool['instruction_id'],
            'record': {key: record.get(key) for key in ('resource_id', 'type', 'title', 'canonical_root',
                       'project_ids', 'primary_file', 'created_at')}, 'locator': selector,
            'registration_actor': tool['registration_actor'], 'missing': tool['missing'],
            'author_verified': False, 'historical_acl_verified': False, 'final_state_verified': False}
    ledger = {'status': 'unsupported', 'reason': 'tool_registration_unverified'}
    receipt = dict(ledger)
    if tool:
        try:
            proof = ResourceRegistrationStore().verify_deleted('article', tool['fields'],
                actor=tool['registration_actor'], operation_id=tool['operation_id'], resource_id=arguments['article_id'])
        except RegistrationEvidenceMissing as exc:
            ledger = {'status': 'not_found', 'reason': exc.reason, 'retention_reason': 'unknown'}
        except RegistrationEvidenceConflict as exc:
            ledger = {'status': 'conflict', 'fields': exc.fields}
        else:
            ledger = {'status': 'verified', 'proof': proof}
        receipt = _receipt(tool, receipt_store)
    source = _source(service, actor, arguments)
    target = _target(service, actor, arguments)
    identity = get_registry_store().inspect_article_identity('article', arguments['article_id'])
    cleanup = DirectoryCleanupStore(create=False).inspect_restore(owner_id=arguments['article_id'], path=target['path'])
    sharing = _inheritance(actor, arguments['project_id'])
    conflicts = []
    if tool and tool['record']['project_ids'] != [arguments['project_id']]:
        conflicts.append('target_project_differs_from_original')
    if tool and Path(tool['record']['canonical_root']).resolve(strict=False) == Path(target['path']):
        conflicts.append('target_must_be_a_new_path')
    # Recheck the four current grants before returning; no decision is persisted.
    _authorize(actor, arguments, source_root)
    result = {'schema': 'tfi.article-restore-preview.v1', 'mode': 'dry_run',
        'apply_supported': False, 'can_apply': False, 'observed_at': datetime.now(timezone.utc).isoformat(),
        'article_id': arguments['article_id'], 'tool_evidence': tool_result, 'registration_ledger': ledger,
        'hatg_receipt': receipt, 'identity': identity, 'cleanup': cleanup,
        'source': source, 'target': target, 'current_inheritance': sharing, 'conflicts': conflicts,
        'decision': {'status': 'unknown', 'reason': 'proposal_answer_relationship_not_verified'},
        'missing': ['historical_author', 'historical_acl', 'final_deleted_version', 'verified_user_decision'],
        'unsupported': ['apply', 'resident_revival'],
        'scope_digest': digest({'arguments': arguments, 'sharing': sharing['digest'], 'source': source, 'target': target}),
        'limitations': ['point_in_time_owner_observations_not_a_commit_lock', 'no_authorization_or_restore_token']}
    if len(json.dumps(result, ensure_ascii=False).encode()) > OUTPUT_BUDGET:
        raise ValueError('Preview output exceeds the bounded response budget')
    return result
