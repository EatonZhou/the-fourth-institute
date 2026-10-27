"""Recover a Fork's initialization without resubmitting any business input.

The creation Job owns the durable attempt. An uncertain native compact never
reuses its Session: a separate candidate compacts the verified inherited prefix,
then AgentData promotes its binding and compaction in one transaction.
"""
from __future__ import annotations

import copy
import hashlib
import secrets
from typing import Any, Mapping

from . import resident_backend, resident_catalog, resident_conversation_stream, resident_execution_work, resident_state
from .domain_ports import execution_port
from .resident_fork_context import requires_initial_context_compaction, initial_context_compaction_completed


def blocked(record: Mapping[str, Any]) -> bool:
    return (requires_initial_context_compaction(record) and not initial_context_compaction_completed(record)) or (
        bool(record.get('fork_mode')) and record.get('session_state') in {'creating', 'failed'}
        and not resident_backend.record_session_id(record))


def waiting_inputs(record: Mapping[str, Any], job: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recover only accepted, unexecuted inputs displaced by the old init bug."""
    queue = resident_execution_work.normalized_queue(job)
    if not blocked(record):
        return queue
    rid = str(record['resident_id'])
    known = {str(i.get('user_message_id') or '') for i in queue}
    repository = resident_conversation_stream.get_repository()
    entries = resident_catalog.get_catalog().state_store.mailbox_entries([rid], places=('pending',))
    for entry in entries:
        mid = entry['message_id']
        item = dict(entry['item'])
        # Facade pending messages or arbitrary mailbox entries aren't a queue.
        if mid in known or entry['group_id'] or item.get('user_message_id') != mid or not item.get('enqueued_at'):
            continue
        state = repository.input_states_by_id([(rid, mid)]).get((rid, mid), {})
        if state.get('state') != 'queued':
            continue
        item.pop('input_message_in_mailbox', None)
        item['input_message'] = entry['message']
        queue.append(item)
        known.add(mid)
    return sorted(queue, key=lambda i: str(i.get('enqueued_at') or ''))


def _record(resident_id: str, actor: str) -> dict[str, Any]:
    from . import resident_record_domain
    from .resident_access import can_manage_resident
    record = resident_record_domain.find_resident(resident_id)
    if not record:
        raise KeyError('resident not found')
    if not can_manage_resident(record, actor):
        raise PermissionError('没有管理此 Resident 的权限')
    return record


def view(resident_id: str, actor: str) -> dict[str, Any]:
    record = _record(resident_id, actor)
    job = resident_execution_work.read(record)
    attempt = job.get('initialization_recovery') or {}
    if initial_context_compaction_completed(record) and not attempt:
        receipt = execution_port.initialization_session_fact(resident_id, resident_backend.execution_binding_id(record))['receipt']
        attempt = {'id': receipt.get('initialization_id', ''), 'phase': 'complete',
                   'source_binding': receipt.get('source_session', {}),
                   'candidate_agent_id': receipt.get('candidate_agent_id', ''),
                   'execution_ids': {'compact': receipt['compaction_execution_id']} if receipt else {}}
    status = ('completed' if initial_context_compaction_completed(record) else
              'running' if resident_execution_work.is_active(job) and job.get('operation') == 'context_fork_create' else
              'failed' if job.get('outcome') in {'failed', 'cancelled'} or record.get('last_error') else 'pending')
    if not requires_initial_context_compaction(record) and job.get('operation') != 'context_fork_create':
        status = 'not_required'
    return {'resident_id': resident_id, 'initialization_id': str(attempt.get('id') or job.get('execution_id') or ''),
            'status': status, 'phase': str(attempt.get('phase') or ('complete' if status == 'completed' else 'initialization')),
            'error': str(job.get('error') or record.get('last_error') or '') if status == 'failed' else '',
            'queued_count': len(waiting_inputs(record, job)),
            'source_session': {key: value for key, value in (attempt.get('source_binding') or {}).items()
                               if key in {'provider', 'backend_route_id', 'backend_session_id', 'provider_session_ref', 'binding_id'}},
            'candidate_agent_id': str(attempt.get('candidate_agent_id') or ''),
            'candidate_binding_id': str(attempt.get('candidate_binding_id') or ''),
            'execution_ids': dict(attempt.get('execution_ids') or {})}


def retry(resident_id: str, actor: str) -> dict[str, Any]:
    from . import resident_domain_common, resident_execution_domain, resident_record_domain
    # Provider/AgentData reads happen outside the Resident state lock.
    record = _record(resident_id, actor)
    prior = resident_execution_work.read(record)
    if resident_execution_work.is_active(prior) and prior.get('operation') == 'context_fork_create':
        resident_execution_domain.resume_resident_execution(resident_id)
        return view(resident_id, actor)
    if not requires_initial_context_compaction(record):
        if prior.get('operation') != 'context_fork_create' or resident_backend.record_session_id(record):
            return view(resident_id, actor)
        facts = None  # Ordinary failed Session creation uses its existing owner.
    else:
        if initial_context_compaction_completed(record):
            return view(resident_id, actor)
        facts = execution_port.initialization_context(resident_id, resident_backend.execution_binding_id(record))
    previous_attempt = prior.get('initialization_recovery') or {}
    prior_execs = [execution_port.initialization_execution(eid) for eid in (previous_attempt.get('execution_ids') or {}).values()]
    # Transport failures and process restarts resume the same accepted request.
    # Only a canonical terminal failed/cancelled attempt allocates a new candidate.
    reuse = bool(previous_attempt) and not any(e.get('status') in {'failed', 'cancelled'} for e in prior_execs)
    with resident_record_domain.state_lock(resident_id):
        record = _record(resident_id, actor)
        job = resident_execution_work.read(record)
        if resident_record_domain.lifecycle_operations().pending(resident_id):
            raise ValueError('Resident 生命周期操作尚未完成')
        if resident_execution_work.is_active(job):
            if job.get('operation') != 'context_fork_create':
                raise ValueError('Resident 当前有执行中的输入，不能替换初始化')
            return view(resident_id, actor)
        if str(job.get('execution_id') or '') != str(prior.get('execution_id') or ''):
            raise ValueError('初始化状态已变化，请重新读取')
        if facts and any(eid != prior.get('execution_id') for eid in facts['active_execution_ids']):
            raise ValueError('原初始化 Execution 仍在执行，请等待其真实结果')
        queue = waiting_inputs(record, job)
        now = resident_domain_common.now_iso()
        attempt = copy.deepcopy(job.get('initialization_recovery') or {})
        # A failed candidate can be abandoned, but never operated on again by
        # a different attempt. Its Session, Execution and fork receipt remain.
        identity = 'initialization_' + secrets.token_hex(12)
        if facts and not reuse:
            if not facts['segment_ids']:
                raise ValueError('压缩继承缺少冻结上下文')
            attempt = {'id': identity, 'phase': 'prepare', 'source_binding': facts['binding'],
                       'segment_ids': facts['segment_ids'], 'candidate_agent_id': 'agent_' + secrets.token_hex(12),
                       'candidate_binding_id': 'initialization:' + identity,
                       'existing_compaction': facts['compaction_segment_id'],
                       'execution_ids': {op: 'execution-' + hashlib.sha256((identity + ':' + op).encode()).hexdigest()[:24]
                                         for op in ('create', 'compact')}}
        fresh = {'operation': 'context_fork_create', 'actor': str(job.get('actor') or actor) if reuse else actor, 'resident_id': resident_id,
                 'started_at': now, 'updated_at': now, 'queue': queue,
                 'initialization_recovery': attempt,
                 resident_state.INTAKE_FIELD: resident_state.normalize_intake_state(job.get(resident_state.INTAKE_FIELD)),
                 # Explicit initialization is admitted, but queued business
                 # inputs must still respect the unchanged intake state.
                 'intake_admitted_at': now}
        resident_execution_work.ensure_execution_id(fresh)
        resident_execution_work.write(record, fresh)
        record.update(session_state='creating', last_error='', updated_at=now)
        resident_record_domain.save_resident_record(record)
    result = view(resident_id, actor)
    resident_execution_domain.resume_resident_execution(resident_id)
    return result


def run(record: dict[str, Any], job: dict[str, Any], continuation_id: str) -> dict[str, Any]:
    from . import (
        resident_execution_domain,
        resident_record_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
    )
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    attempt = job['initialization_recovery']
    rid = record['resident_id']
    actor = resident_execution_work.request_actor(job)
    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS[backend]
    adopted = execution_port.initialization_session_fact(rid, resident_backend.execution_binding_id(record))['receipt']
    if adopted.get('initialization_id') == attempt['id']:
        return {'thread': {'id': adopted['target_session']['backend_session_id'],
                           'backend_route_id': adopted['target_session']['backend_route_id']}, 'usage': {}}
    route = default_execution_accounts().canonical_route(definition.provider,
        attempt['source_binding'].get('backend_route_id') or record.get('backend_route_id'))
    if attempt.get('existing_compaction'):
        # This fact includes a completed canonical Execution, not just a native notification.
        current = resident_backend.session_record(record)
        if current.get('provider_session_ref') != attempt['source_binding'].get('provider_session_ref'):
            raise ValueError('初始化会话绑定已变化')
        return {'thread': {'id': current['backend_session_id'], 'backend_route_id': route}, 'usage': {}}

    def phase(name: str) -> None:
        with resident_record_domain.state_lock(rid):
            current = resident_record_domain.find_resident(rid)
            saved = resident_execution_work.read(current)
            if not resident_execution_work.matches_continuation(saved, continuation_id) or saved.get('initialization_recovery', {}).get('id') != attempt['id']:
                raise ValueError('初始化恢复已被替换')
            saved['initialization_recovery']['phase'] = name
            resident_execution_work.write(current, saved)

    execution_port.fork_agent_data(rid, attempt['candidate_agent_id'], actor=actor, segment_ids=attempt['segment_ids'])
    from . import resident_domain_common
    options = {**resident_execution_domain.resident_execution_runtime_options(record, job, is_owner=True), 'timeout_seconds': resident_domain_common.CODEX_COMPACT_TIMEOUT_SECONDS}
    common = dict(backend=backend, actor=actor, cwd=resident_runtime_domain.resident_working_directory(record),
                  binding_id=attempt['candidate_binding_id'], backend_route_id=route,
                  runtime_options=options, agent_id=attempt['candidate_agent_id'])
    phase('create')
    created = execution_port.initialization_execution(attempt['execution_ids']['create'])
    if created.get('status') != 'completed':
        resident_session_backend_domain.run_backend_session_operation(**common, operation='create', operation_id=attempt['id'] + ':create',
                                      metadata={'resident_id': rid})
    phase('compact')
    compacted = execution_port.initialization_execution(attempt['execution_ids']['compact'])
    result = compacted if compacted.get('status') == 'completed' else resident_session_backend_domain.run_backend_session_operation(
        **common, operation='compact', operation_id=attempt['id'] + ':compact',
        metadata={'resident_id': rid, 'initial_context_policy': 'compact_before_first_turn',
                  'fork_context_input': {'segments': execution_port.frozen_initialization_segments(attempt['candidate_agent_id'])}})
    phase('promote')
    with resident_record_domain.state_lock(rid):
        current = resident_record_domain.find_resident(rid)
        saved = resident_execution_work.read(current)
        if not resident_execution_work.matches_continuation(saved, continuation_id) or saved.get('initialization_recovery', {}).get('id') != attempt['id']:
            raise ValueError('初始化恢复已被替换')
        receipt = execution_port.promote_initialization_session(agent_id=rid,
            candidate_agent_id=attempt['candidate_agent_id'], binding_id=resident_backend.execution_binding_id(current),
            prepared_binding_id=attempt['candidate_binding_id'], source_binding=attempt['source_binding'],
            segment_ids=attempt['segment_ids'], compaction_execution_id=attempt['execution_ids']['compact'],
            initialization_id=attempt['id'], actor_ref=actor)
    return {'thread': {'id': receipt['target_session']['backend_session_id'], 'backend_route_id': route},
            'usage': result.get('usage') or {}}
