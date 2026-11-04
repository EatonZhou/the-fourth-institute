"""Read execution prerequisites through public ISA, without widening credits."""
from __future__ import annotations

import re
import math


def _execution_account(instruction, account_id, *, refresh=False):
    arguments = {'account_id': account_id}
    if refresh:
        arguments['refresh'] = True
    accounts = instruction('ListProviderAccounts', arguments)['accounts']
    matches = [account for account in accounts if account.get('account_id') == account_id]
    return matches[0] if len(matches) == 1 else {}


def read_execution_prerequisite(instruction, resident_id: str, *, new_context: bool = False,
                                require_native_quota: bool = False):
    if not re.fullmatch(r'resident_[0-9a-f]{12}', resident_id):
        raise ValueError('Expected an exact Resident ID')
    resident = instruction('GetResident', {'resident_id': resident_id})['resident']
    if resident.get('resident_id') != resident_id:
        raise ValueError('Execution prerequisite identity is missing or changed')
    if (resident.get('family') or {}).get('role') == 'facade':
        lineage_id = resident.get('lineage_id')
        if not isinstance(lineage_id, str) or not re.fullmatch(r'lineage_[0-9a-f]{12}', lineage_id):
            raise ValueError('Family facade has no exact Lineage identity')
        # A facade receives and routes messages; it has no model Session gate.
        # Never turn its proxy account or absent gate into execution consent.
        return {'resident_id': resident_id, 'account_id': resident.get('account') or '',
                'scope': 'family_facade', 'ready': False, 'reason': 'family_facade_not_executing',
                'initialization': None, 'execution_gate': None,
                'recovery': {'instruction': 'GetLineage', 'arguments': {'lineage_id': lineage_id},
                             'scope': 'family_execution_members'}}
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
    native_quota = None
    account, quota = {}, {}
    native_unavailable = False
    if require_native_quota:
        # Check before offering initialization recovery too: that recovery can
        # itself prepare a model Session under an older paid exception.
        account = _execution_account(instruction, account_id, refresh=True)
        quota = account.get('quota_reserve') or {}
        native_quota = {key: quota.get(key) for key in (
            'fresh', 'remaining', 'remaining_percent', 'used_up', 'exhausted', 'observed_at')}
        remaining = quota.get('remaining')
        native_unavailable = (not account or account.get('enabled') is False
            or (account.get('registration') or {}).get('enabled') is False
            or quota.get('fresh') is not True or quota.get('used_up') is True
            or quota.get('exhausted') is True or isinstance(remaining, bool)
            or not isinstance(remaining, (int, float))
            or not math.isfinite(remaining) or remaining <= 0)
    if native_unavailable:
        reason = 'native_quota_unavailable'
    elif initialization.get('status') not in {'completed', 'not_required'}:
        reason = 'initialization_required'
        recovery = {'instruction': 'RetryResidentInitialization',
                    'arguments': {'resident_id': resident_id,
                                  'expected_initialization_id': initialization.get('initialization_id', '')},
                    'account_id': account_id, 'scope': 'initialization_and_target_session'}
    elif state.get('intake_state') != 'open':
        reason = 'intake_closed'
    else:
        if new_context:
            if not require_native_quota:
                account = _execution_account(instruction, account_id)
                quota = account.get('quota_reserve') or {}
            if (not account or (account.get('registration') or {}).get('enabled') is False
                    or not (account.get('quota_protection_enabled') is False or quota.get('automatic_paused') is False)):
                reason = 'independent_context_quota_unavailable'
        if not reason and not new_context and (gate.get('account_id') != account_id or gate.get('state') != 'open'):
            reason = gate.get('reason') or 'execution_gate_unavailable'
    result = {'resident_id': resident_id, 'account_id': account_id,
            'scope': 'independent_context' if new_context else 'current_session',
            'ready': not reason, 'reason': reason, 'initialization': initialization,
            'execution_gate': gate, 'recovery': recovery}
    if require_native_quota:
        result['native_quota'] = native_quota
    return result


def prepare_execution_prerequisite(instruction, resident_id: str, *, new_context=False,
                                   require_native_quota=False, interact=None):
    """An optional authenticated interaction owns any human decision.

    No confirmed flag, alternate account or inferred human identity is emitted
    here. A callback may open the actual Session/init UI; its return value is
    not evidence of success. Always re-read the authoritative state afterwards.
    """
    before = read_execution_prerequisite(instruction, resident_id, new_context=new_context,
                                        require_native_quota=require_native_quota)
    if before['ready'] or interact is None or before['reason'] in {
            'native_quota_unavailable', 'family_facade_not_executing'}:
        return before
    interact(before)
    after = read_execution_prerequisite(instruction, resident_id, new_context=new_context,
                                       require_native_quota=require_native_quota)
    if after['account_id'] != before['account_id']:
        return {**after, 'ready': False, 'reason': 'execution_account_changed'}
    return after
