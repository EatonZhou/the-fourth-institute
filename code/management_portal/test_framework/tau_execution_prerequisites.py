"""Read execution prerequisites through public ISA, without widening credits."""
from __future__ import annotations

import re


def read_execution_prerequisite(instruction, resident_id: str, *, new_context: bool = False):
    if not re.fullmatch(r'resident_[0-9a-f]{12}', resident_id):
        raise ValueError('Expected an exact Resident ID')
    resident = instruction('GetResident', {'resident_id': resident_id})['resident']
    initialization = instruction('GetResidentInitialization', {'resident_id': resident_id})
    rows = instruction('ListResidentStates', {'resident_ids': [resident_id], 'include_viewer_state': False})['states']
    states = [row['state'] for row in rows if row.get('resident_id') == resident_id]
    if resident.get('resident_id') != resident_id or initialization.get('resident_id') != resident_id or len(states) != 1:
        raise ValueError('Execution prerequisite identity is missing or changed')
    account_id = resident.get('account')
    if not account_id:
        raise ValueError('Resident has no declared execution account')
    state = states[0]
    gate = state.get('execution_gate') or {}
    reason = ''
    recovery = None
    if initialization.get('status') not in {'completed', 'not_required'}:
        reason = 'initialization_required'
        recovery = {'instruction': 'RetryResidentInitialization',
                    'arguments': {'resident_id': resident_id,
                                  'expected_initialization_id': initialization.get('initialization_id', '')},
                    'account_id': account_id, 'scope': 'initialization_and_target_session'}
    elif state.get('intake_state') != 'open':
        reason = 'intake_closed'
    elif new_context:
        # Automatic time.reached work starts independently. The current
        # Session's paid exception is deliberately irrelevant to this check.
        accounts = instruction('ListProviderAccounts', {'account_id': account_id})['accounts']
        matches = [a for a in accounts if a.get('account_id') == account_id]
        account = matches[0] if len(matches) == 1 else {}
        quota = account.get('quota_reserve') or {}
        if (not account or (account.get('registration') or {}).get('enabled') is False
                or not (account.get('quota_protection_enabled') is False or quota.get('automatic_paused') is False)):
            reason = 'independent_context_quota_unavailable'
    elif gate.get('account_id') != account_id or gate.get('state') != 'open':
        reason = gate.get('reason') or 'execution_gate_unavailable'
    return {'resident_id': resident_id, 'account_id': account_id,
            'scope': 'independent_context' if new_context else 'current_session',
            'ready': not reason, 'reason': reason, 'initialization': initialization,
            'execution_gate': gate, 'recovery': recovery}


def prepare_execution_prerequisite(instruction, resident_id: str, *, new_context=False, interact=None):
    """An optional authenticated interaction owns any human decision.

    No confirmed flag, alternate account or inferred human identity is emitted
    here. A callback may open the actual Session/init UI; its return value is
    not evidence of success. Always re-read the authoritative state afterwards.
    """
    before = read_execution_prerequisite(instruction, resident_id, new_context=new_context)
    if before['ready'] or interact is None:
        return before
    interact(before)
    after = read_execution_prerequisite(instruction, resident_id, new_context=new_context)
    if after['account_id'] != before['account_id']:
        return {**after, 'ready': False, 'reason': 'execution_account_changed'}
    return after
