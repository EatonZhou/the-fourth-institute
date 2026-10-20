"""Read-only execution protection, distinct from the manual intake switch.

Execution owns account protection and acknowledged binding overrides. Society
owns Resident visibility, manual intake and the current session route. This
projection grants no admission and never changes queues or restarts work.
"""
from management_portal.execution.accounts import shared_account_configuration

from . import family_facade, resident_backend, resident_catalog
from .domain_ports import execution_port


HINTS = {
    'manual': 'Agent 已关闭接单，发送的消息将排队等待开启。',
    'quota_used_up': '账号额度已用尽，发送的消息将排队等待额度恢复或人工放行。',
    'quota_reserved': '账号额度保护已暂停执行，发送的消息将排队等待额度恢复或人工放行。',
    'quota_recovery_unconfirmed': '账号额度恢复尚未确认，等待新的额度读数；请勿重复发送。',
    'quota_unknown': '账号额度暂不可确认，发送的消息将排队等待额度确认。',
    'account_disabled': '执行账号已停用，发送的消息将排队等待账号恢复。',
    'account_unavailable': '执行账号状态暂不可确认，发送不会绕过执行保护。',
    'execution_unavailable': '执行服务暂不可达，无法确认额度状态；请勿重复发送。',
}


def project_state_sequence(resident_id, states, *, resident_record):
    """Project one Resident's event page with one current account observation.

    Manual intake belongs to each historical state. Cache only the execution
    gate for each intake category in this read, never across requests.
    """
    gates, projected = {}, []
    for state in states:
        manual = state.get('intake_state') == 'closed'
        if manual not in gates:
            gates[manual] = project_states(
                {resident_id: {'state': state}},
                resident_records={resident_id: resident_record},
            )[resident_id]['state']['execution_gate']
        projected.append({**state, 'execution_gate': dict(gates[manual])})
    return projected


def project_states(snapshots, *, resident_records=None):
    if not snapshots:
        return snapshots
    records = resident_records
    if records is None:
        records = {r['resident_id']: r for r in resident_catalog.get_catalog().get_many(list(snapshots))}
    gates, targets = {}, {}
    for rid, snapshot in snapshots.items():
        if snapshot['state'].get('intake_state') == 'closed':
            gates[rid] = {'state': 'closed', 'reason': 'manual'}
        elif not records.get(rid):
            # The identity can disappear between the state and catalog reads.
            # Never substitute the installation's default account for it.
            gates[rid] = {'state': 'unknown', 'reason': 'account_unavailable'}
        elif (family_facade.is_facade(records[rid])
              or resident_backend.execution_backend(records[rid]) == 'portal'):
            # A facade never submits model execution. Its retained backend is
            # not an admission gate; Family role health is projected separately.
            gates[rid] = {'state': 'open', 'reason': ''}
        else:
            targets[rid] = records.get(rid, {})
    requests = {}
    if targets:
        routes = resident_backend.session_routes_for_bindings('resident:' + rid for rid in targets)
        with shared_account_configuration():
            for rid, record in targets.items():
                binding = 'resident:' + rid
                try:
                    account = resident_backend.account_id(record, binding=routes.get(binding, {}))
                except (ValueError, KeyError):
                    gates[rid] = {'state': 'unknown', 'reason': 'account_unavailable'}
                    continue
                requests[binding] = account
        entries = list(requests.items())
        for offset in range(0, len(entries), 500):
            batch = dict(entries[offset:offset + 500])
            try:
                states = execution_port.service().binding_quota_states(batch)
            except ValueError as exc:
                # A rolling deployment can still reach the previous reader.
                # Keep its missing capability unknown; do not hide bad inputs.
                if str(exc) != 'unsupported ExecutionService method: scheduler.quota_states':
                    raise
                states = {}
            except (OSError, RuntimeError):
                states = {}
            for binding in batch:
                gates[binding.removeprefix('resident:')] = states.get(binding) or {
                    'state': 'unknown', 'reason': 'execution_unavailable'}
    return {rid: {**snapshot, 'state': {**snapshot['state'], 'execution_gate': gates[rid]}}
            for rid, snapshot in snapshots.items()}
