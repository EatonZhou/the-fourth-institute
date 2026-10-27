"""Resolve only unconfigured Residents; bound sessions retain their account."""
from copy import deepcopy
import secrets

from management_portal.execution.account_registrations import role_configuration
from management_portal.hatg.contracts import InstructionError


def configuration(record, binding):
    if record.get("backend") in {"portal", "kimi"} or record.get("backend_route_id"):
        return None
    if any(binding.get(key) for key in ("backend_route_id", "backend_session_id", "provider_session_ref")):
        return None
    requested = (record.get("execution_settings") or {}).get("backend_role") or "default"
    try:
        return role_configuration(requested)
    except InstructionError as error:
        if error.details.get("reason") == "backend_role.default_missing":
            return None
        raise


def bind_before_execution(record, actor):
    from . import resident_backend, resident_catalog, resident_execution_work
    from .resident_execution_storage import queue_input
    from management_portal.execution import execution_lifecycle
    from . import resident_runtime_domain
    from .resident_settings_contract import stage_runtime_settings
    from . import resident_conversation_stream
    from management_portal.execution.resident_runtime_settings import merge_settings
    from . import resident_domain_common, resident_record_domain
    configured = configuration(record, resident_backend.session_record(record))
    candidate = deepcopy(record)
    # The role was configured separately. Resolve no remote model catalog while
    # the execution caller holds this Resident's state lock.
    # Imported/unconfigured Residents can retain the source site's model and
    # effort. Resolve these together with the new account; absent role values
    # mean the new Provider's defaults, never the source account's settings.
    if configured:
        current = resident_runtime_domain.read_resident_runtime_settings(record)
        candidate.update(backend=configured["backend"], backend_route_id=configured["backend_route_id"])
        settings = merge_settings({**current, "backend": configured["backend"]},
                                  {"model": "", "reasoning_effort": "", "service_tier": "",
                                   **configured["runtime_settings"]}, actor=actor, updated_at=resident_domain_common.now_iso())
        stage_runtime_settings(candidate, settings)
    # Commit the Provider and settings together. The ordinary settings writer
    # correctly rejects changing backend before the Resident transition commits.
    def commit(connection):
        job = resident_execution_work.read(candidate)
        retargeted = False
        if resident_execution_work.is_active(job):
            replacement = 'execution-' + secrets.token_hex(12)
            now = resident_domain_common.now_iso()
            retargeted = execution_lifecycle.retarget_preparation(connection,
                    execution_id=job['execution_id'], replacement_execution_id=replacement,
                    agent_id=candidate['resident_id'], actor=resident_execution_work.request_actor(job),
                    provider=resident_backend.execution_provider(candidate), at=now)
        # Check durable admission even when the local submit acknowledgement
        # exists. A submitted placeholder must keep its original Provider.
        if configured:
            resident_record_domain.save_resident_record(candidate, commit_configuration=True)
        if retargeted:
            item = queue_input(job)
            for key in ('actual_sent_prompt', 'prompt_built_at', 'prompt_builder_version', 'runtime_context'):
                item.pop(key, None)
            resident_execution_work.promote_queue_item(job, item,
                resident_execution_work.normalized_queue(job), now, retain_continuation=True)
            job['execution_id'] = replacement
            resident_execution_work.write(candidate, job)
        if configured:
            resident_conversation_stream.get_repository().execution_settings_changed(candidate['resident_id'], settings)

    # This also repairs the unsubmitted placeholder left by earlier versions
    # that saved the first API binding before updating the pending preparation.
    resident_catalog.get_catalog().state_store.database.write(commit)
    record.update(candidate)
