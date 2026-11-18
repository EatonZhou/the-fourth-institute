"""Verified historical registration facts for same-ID Article recovery.

This evidence reader does not restore registrations, files, authors or ACLs.
The caller supplies only a locator. Society owns retained history authorization;
ResourceService independently checks its original registration ledger.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .mutation import mutation_payload
from .registration import ResourceRegistrationStore


class ToolEvidenceUnavailable(RuntimeError):
    """The history owner could not expose an item; this is not proof of loss."""

    def __init__(self, availability):
        gap = availability.get('gap') if isinstance(availability, dict) else None
        self.reason = gap[:512] if isinstance(gap, str) and gap else 'owner_no_items_without_absence_proof'
        self.status = 'unsupported' if gap in (
            'external_or_unproven_source_requires_separate_authorization',
            'unsupported_segment_type', 'no_supported_retained_tool_items',
        ) else 'indeterminate'
        super().__init__(self.reason)


def _object(text: Any) -> dict:
    if not isinstance(text, str):
        raise ValueError('Original tool evidence is not JSON text')
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Original tool evidence is not an object')
    return value


def read_tool_registration(*, article_id: str, actor: str, resident_id: str,
                               ordinal: int, tool_index: int, tool_item_id: str,
                               expected_hash: str) -> dict:
    """Resolve one genuine RegisterResource result through its owning service.

    Article authority does not grant deleted Resident history or AMA access.
    Missing original fields stay missing; a temporary caller grant is never
    interpreted as a historical owner or persistent ACL.
    """
    from management_portal.society_service.resident_deleted_history import read_history

    if not re.fullmatch(r'article_[0-9a-f]{12}', article_id):
        raise ValueError('Recovery requires an exact Article ID')
    if (not re.fullmatch(r'resident_[0-9a-f]{12}', resident_id)
            or type(ordinal) is not int or not 0 <= ordinal <= 2**63-1
            or type(tool_index) is not int or not 0 <= tool_index <= 10000
            or not isinstance(expected_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash)
            or not isinstance(tool_item_id, str) or not 1 <= len(tool_item_id) <= 256):
        raise ValueError('Recovery requires one exact retained tool item')
    page = read_history(resident_id, actor, section='tool_evidence', ordinal=ordinal,
                        tool_index=tool_index, tool_item_id=tool_item_id, expected_hash=expected_hash)
    if (page.get('resident_id') != resident_id or page.get('section') != 'tool_evidence'
            or page.get('identity', {}).get('resident_id') != resident_id
            or not page.get('identity', {}).get('deleted_at')):
        raise ValueError('Original history target does not match recovery evidence')
    if not page.get('items'):
        # Empty projections can reflect source authorization, format or budget
        # limits. Only the owner can establish absence; its exceptions propagate.
        raise ToolEvidenceUnavailable(page.get('availability'))
    if len(page['items']) != 1:
        raise ValueError('Original history requires exactly one tool item')
    item = page['items'][0]
    expected_item = {'ordinal': ordinal, 'tool_index': tool_index, 'tool_item_id': tool_item_id,
                     'content_sha256': expected_hash, 'segment_type': 'tool',
                     'status': 'completed', 'format': 'canonical_tool',
                     'input_omitted': False, 'output_omitted': False}
    if (any(item.get(key) != value for key, value in expected_item.items())
            or item.get('tool_name') not in {'mcp__portal__hatg_instruction', 'hatg_instruction'}):
        raise ValueError('Original registration requires an exact unomitted canonical HATG tool')
    if any(not isinstance(item.get(name), str) or len(item[name].encode()) > 512 * 1024
           for name in ('input_text', 'output_text')):
        raise ValueError('Original registration evidence exceeds the parsing budget')
    outer = _object(item['input_text'])
    call = outer.get('arguments')
    if not isinstance(call, dict) or call.get('instruction') != 'RegisterResource':
        raise ValueError('Original tool did not call RegisterResource')
    arguments = call.get('arguments')
    if not isinstance(arguments, dict) or arguments.get('resource_type') != 'article':
        raise ValueError('Original request did not register an Article')
    fields = mutation_payload(arguments.get('fields'))
    wrapper = _object(item['output_text'])
    blocks = wrapper.get('output')
    if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict) or blocks[0].get('type') != 'text':
        raise ValueError('Original tool did not return a single service receipt')
    receipt = _object(blocks[0].get('text'))
    if (receipt.get('schema') != 'tfi.hatg-instruction-result.v2'
            or receipt.get('instruction') != 'RegisterResource' or receipt.get('status') != 'committed'
            or not isinstance(receipt.get('instruction_id'), str) or not receipt['instruction_id']):
        raise ValueError('Original RegisterResource did not commit')
    result = receipt.get('result')
    record = result.get('resource') if isinstance(result, dict) else None
    if (not isinstance(record, dict) or result.get('resource_id') != article_id
            or record.get('resource_id') != article_id or record.get('schema') != 'tfi.article.v1'
            or record.get('type') != 'article' or not record.get('created_at')
            or not fields.get('path') or record.get('canonical_root') != fields['path']
            or not isinstance(fields.get('project_ids'), list) or not fields['project_ids']
            or sorted(record.get('project_ids', [])) != sorted(fields['project_ids'])
            or (fields.get('primary_file') and record.get('primary_file') != fields['primary_file'])):
        raise ValueError('Original registration result conflicts with its Article request')
    key = call.get('idempotency_key')
    if key is not None and not isinstance(key, str):
        raise ValueError('Original registration request key is invalid')
    operation_id = 'RegisterResource:' + key if key else receipt['instruction_id']
    return {'record': record, 'fields': fields, 'registration_actor': resident_id,
            'instruction_id': receipt['instruction_id'], 'operation_id': operation_id,
            'evidence': {'resident_id': resident_id, 'ordinal': ordinal, 'tool_index': tool_index,
                         'tool_item_id': tool_item_id, 'expected_hash': expected_hash},
            'missing': [name for name in ('created_by', 'acl') if name not in record]}


def read_registration_preimage(*, article_id: str, actor: str, connection=None, **selector) -> dict:
    """Strict evidence consumer; preview keeps the two proofs separate instead."""
    result = read_tool_registration(article_id=article_id, actor=actor, **selector)
    fields = result.pop('fields')
    result['ledger'] = ResourceRegistrationStore().verify_deleted(
        'article', fields, actor=result['registration_actor'], operation_id=result['operation_id'],
        resource_id=article_id, connection=connection)
    return result
