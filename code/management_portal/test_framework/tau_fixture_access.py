"""Temporary Workspace permissions: exact resource, instructions and recipient.

The caller supplies authenticated ISA transports. No actor is accepted as an
override and no transport is promoted to a human or administrator identity.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from management_portal.shared_state import atomic_replace_json
from .compatibility import test_actor


def _journal(root: Path):
    path = Path(root) / 'fixture-access.json'
    if path.is_symlink():
        raise ValueError('Fixture access journal cannot be a symlink')
    state = json.loads(path.read_text()) if path.exists() else {
        'evidence_dir': str(Path(root).resolve()), 'grants': [],
    }
    if state.get('evidence_dir') != str(Path(root).resolve()):
        raise ValueError('Fixture access journal belongs to another case')
    return path, state


def prepare_workspace_access(root: Path, workspace_id: str, *, check, issue=None):
    """Check as the test user; optionally borrow only its two missing actions.

    ``check`` is the recipient's authenticated ISA; ``issue`` belongs to an
    authorized controller who actually holds these actions. A missing issuer
    returns an explicit prerequisite, never a fake ready result. Each saved key
    is reused after an ambiguous reply. The framework revokes as recipient.
    """
    if not re.fullmatch(r'workspace_[0-9a-f]{12}', workspace_id):
        raise ValueError('Expected an exact Workspace ID')
    path, state = _journal(root)
    principal = test_actor()
    checks = []
    for action in ('UpdateWorkspaceFile', 'UnregisterResource'):
        arguments = {'principal_id': principal, 'a': action, 'resource_id': workspace_id}
        # A named A node is not the instruction contract: the active scheme
        # can map an instruction's arguments to other nodes/resources. Check
        # the actual call, without writing a file or unregistering anything.
        call_arguments = ({'address': workspace_id + '/file/tau-access-probe.txt', 'content': ''}
                          if action == 'UpdateWorkspaceFile' else {'resource_id': workspace_id})
        permission = {'principal_id': principal, 'instruction': action, 'arguments': call_arguments}
        grant = next((g for g in state['grants'] if g['arguments'] == arguments
                      and not g.get('rejected') and not g.get('released')), None)
        result = check('CheckPermission', permission)
        if result.get('principal_id') != principal or not isinstance(result.get('allowed'), bool):
            raise ValueError('Workspace permission check returned another principal or no decision')
        if issue is not None and (not result['allowed'] or (grant is not None and not grant.get('receipt'))):
            if grant is None:
                from uuid import uuid4
                grant = {'arguments': arguments, 'key': 'tau-workspace-access:' + uuid4().hex}
                state['grants'].append(grant)
                atomic_replace_json(path, state)
            if not grant.get('receipt'):
                from management_portal.hatg.contracts import InstructionError
                try:
                    receipt = issue('IssueTemporaryCredential', {**arguments, 'expires_in_seconds': 3600}, key=grant['key'])
                except InstructionError as error:
                    if error.code in {'instruction.forbidden', 'instruction.invalid_arguments', 'instruction.not_found'}:
                        grant['rejected'] = error.code
                        atomic_replace_json(path, state)
                    raise
                if any(receipt.get(k) != v for k, v in arguments.items()) or not receipt.get('credential_id'):
                    raise ValueError('Temporary permission receipt does not match this fixture')
                grant['receipt'] = receipt
                atomic_replace_json(path, state)
            result = check('CheckPermission', permission)
            if result.get('principal_id') != principal or not isinstance(result.get('allowed'), bool):
                raise ValueError('Workspace permission check returned another principal or no decision')
        checks.append({'instruction': action, 'allowed': result.get('allowed') is True,
                       'requests': result.get('requests', []),
                       'receipt_pending': bool(grant and not grant.get('receipt') and not grant.get('rejected'))})
    ready = all(row['allowed'] and not row['receipt_pending'] for row in checks)
    return {'workspace_id': workspace_id, 'principal_id': principal,
            'ready': ready, 'checks': checks,
            'reason': '' if ready else ('workspace_permission_receipt_pending'
                if any(row['receipt_pending'] for row in checks) else 'workspace_permission_required')}


def release_workspace_access(root: Path, *, instruction):
    """Return only this case's receipts; permission failures remain pending."""
    path, state = _journal(root)
    released = []
    active = None
    for grant in state['grants']:
        if grant.get('released') or grant.get('rejected'):
            continue
        receipt = grant.get('receipt')
        if not receipt:
            raise RuntimeError('Workspace permission request has no receipt; resume its original issuer/key before cleanup')
        if receipt.get('principal_id') != test_actor():
            raise ValueError('Fixture credential belongs to another recipient')
        cid = receipt['credential_id']
        if active is None:
            active = instruction('ListTemporaryCredentials', {'principal_id': test_actor()})['credentials']
            if not isinstance(active, list):
                raise ValueError('Temporary credential listing is incomplete')
        # A completed Workspace deletion or expiry may already remove this
        # recorded credential. Only its recipient's formal listing proves that.
        if any(item.get('credential_id') == cid for item in active):
            result = instruction('RevokeTemporaryCredential', {'credential_id': cid}, key=grant['key'] + ':revoke')
            if result.get('revoked') != cid:
                raise RuntimeError('Temporary permission revocation was not confirmed')
        grant['released'] = True
        atomic_replace_json(path, state)
        released.append(cid)
    return released
