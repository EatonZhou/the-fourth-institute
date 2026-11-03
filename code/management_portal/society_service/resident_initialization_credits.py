"""Human consent belongs to one Fork initialization and its resulting Session."""
from __future__ import annotations

import hashlib

from . import resident_backend, resident_execution_work, resident_record_domain, resident_session_credits
from .domain_ports import execution_port


def identity(job):
    return str((job.get('initialization_recovery') or {}).get('id') or job.get('execution_id') or '')


def authorize(resident_id, initialization_id, actor, consent, request_id):
    """Attach an authenticated decision to the unchanged initialization owner."""
    from .resident_domain_common import now_iso
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        job = resident_execution_work.read(record)
        if (job.get('operation') != 'context_fork_create' or identity(job) != initialization_id
                or not resident_execution_work.is_active(job)):
            raise ValueError('初始化状态已变化，请重新读取并确认 credits')
        if resident_backend.execution_account(record).get('account_id') != consent['account_id']:
            raise ValueError('初始化目标账号已变化，请重新确认 credits')
        job['credits_authorization'] = {
            'account_id': consent['account_id'], 'operation_id': 'initialization-credits:' + request_id,
            'initialization_id': initialization_id, 'confirmed_by': actor, 'confirmed_at': now_iso(),
        }
        resident_execution_work.write(record, job)
    synchronize(resident_id, job['execution_id'])


def _preparation_target(record, job):
    attempt = job.get('initialization_recovery') or {}
    if attempt:
        phase = 'create' if attempt.get('phase') in {'prepare', 'create'} else 'compact'
        return attempt['candidate_binding_id'], attempt['execution_ids'][phase]
    operation = job.get('initial_compaction_operation_id')
    execution_id = ('execution-' + hashlib.sha256(operation.encode()).hexdigest()[:24]
                    if operation else job['execution_id'])
    return resident_backend.execution_binding_id(record), execution_id


def synchronize(resident_id, execution_id):
    """Publish only the current preparation step, including after a lost reply."""
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        job = resident_execution_work.read(record)
        authorization = job.get('credits_authorization') or {}
        if not authorization:
            return
        if (job.get('operation') != 'context_fork_create' or job.get('execution_id') != execution_id
                or authorization['initialization_id'] != identity(job)):
            raise ValueError('credits 确认不属于当前初始化')
        binding, target_execution = _preparation_target(record, job)
        previous = (resident_record_domain.resident_quota_override(record)
                    if binding == resident_backend.execution_binding_id(record)
                    else job.get('credits_preparation_policy') or {})
        scope = {'account_id': authorization['account_id'], 'operation_id': authorization['operation_id'],
                 'execution_id': target_execution}
        policy = previous if previous.get('credits_scope') == scope and previous.get('enabled') else {
            'binding_id': binding, 'enabled': True, 'revision': int(previous.get('revision') or 0) + 1,
            'credits_scope': scope,
        }
        if binding == resident_backend.execution_binding_id(record):
            record['quota_reserve_override'] = policy
            resident_record_domain.save_resident_record(record, commit_configuration=True)
        else:
            job['credits_preparation_policy'] = policy
            resident_execution_work.write(record, job)
    # No native/model operation occurs while the Resident lock is held.
    execution_port.configure_binding_quota_overrides([policy])


def promote(record, job):
    """Called under the initialization's completion lock before advancing inputs."""
    authorization = job.get('credits_authorization') or {}
    if not authorization:
        return
    if authorization['initialization_id'] != identity(job):
        raise ValueError('credits 确认不属于已完成的初始化')
    binding = resident_backend.session_record(record)
    resident_session_credits.promote(record, authorization, binding.get('provider_session_ref'))


def finish(record, job):
    """Reconcile the committed Session policy before the original queue resumes."""
    if not job.get('credits_authorization'):
        return True
    from . import resident_input_queue_domain
    if not resident_input_queue_domain.sync_resident_quota_overrides([record]):
        return False
    candidate = (job.get('credits_preparation_policy') or {}).get('binding_id')
    if candidate:
        execution_port.forget_binding_quota_overrides([candidate])

    return True
