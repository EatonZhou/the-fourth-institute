from __future__ import annotations

import hashlib

from management_common.principal import validate_user_id
from management_portal.resident_handoff import deliver_resident_message, cancel_resident_invocation
from management_portal.portal_access_control import get_portal_access_store
from management_portal.semantic_search_surfaces import canonical_surface, search_binding
from management_portal.service_invocation_contract import (
    PLAN_SCHEMA, digest as _digest, input_fingerprint,
    plan_fingerprint, plan_fingerprint_matches, validate_manifest,
)

from . import resident_catalog, resident_protocol, resident_input_scope
from .domain_ports import resource_port
from .service_invocation_store import TERMINAL_STATUSES, ServiceInvocationConflict, get_store as _store
from . import lineage_aggregate


def _job_identity(job, surface):
    actor = validate_user_id(job.get("created_by"))
    selected = canonical_surface(surface)
    if job.get("surface") != selected:
        raise ValueError("authoritative search job surface mismatch")
    search_id = job.get("search_id")
    generation = job.get("run_generation")
    if not isinstance(search_id, str) or not search_id or len(search_id) > 200:
        raise ValueError("stable search_id required")
    if type(generation) is not int or generation < 1:
        raise ValueError("persisted positive run_generation required")
    return {"created_by": actor, "surface": selected, "search_id": search_id, "run_generation": generation}


def _job(job, surface):
    normalized = _job_identity(job, surface)
    if not get_portal_access_store().is_active(normalized["created_by"]):
        raise PermissionError("search creator is not active")
    return normalized


def _unavailable(record):
    return (record.get("deleted_at") or record.get("archived_at") or record.get("enabled") is False
            or str(record.get("status") or "").lower() in {"deleted", "archived", "disabled", "inactive", "suspended", "paused"})


def _authorize_new(job, binding):
    if binding.get("surface") != job["surface"]:
        raise ValueError("published service snapshot belongs to another surface")
    actor = job["created_by"]
    if not get_portal_access_store().is_active(actor):
        raise PermissionError("search creator is not active")
    lineage_id, resident_id = binding["lineage_id"], binding["resident_id"]
    descriptor = resource_port.get(lineage_id, actor=actor)
    attributes = descriptor.get("attributes") or {}
    if (descriptor.get("resource_id") != lineage_id or descriptor.get("exists") is False
            or _unavailable(descriptor) or _unavailable(attributes)):
        raise PermissionError("registered search service Lineage is unavailable")
    lineage = lineage_aggregate.read_lineage_state(lineage_id)
    resident = resident_catalog.get_catalog().get(resident_id)
    nodes = [node for node in lineage.get("nodes", []) if node.get("id") == resident_id]
    if (_unavailable(lineage) or _unavailable(resident) or resident.get("lineage_id") != lineage_id
            or not nodes or any(_unavailable(node) for node in nodes)):
        raise PermissionError("registered service target is inactive or no longer a Lineage member")
    return {"actor": actor, "invoke_policy": "active_portal_users", "lineage_revision": lineage.get("revision"),
            "resident_id": resident_id, "lineage_id": lineage_id, "membership_verified": True,
            "target_lifecycle_verified": True, "definition_metadata_actor": actor}


def _cancel_pending(job, surface):
    failures = []
    for batch in _store().cancellation_targets(job, surface):
        try:
            cancel_resident_invocation(batch["resident_id"], batch["created_by"], batch["message_id"])
        except KeyError:
            continue
        except Exception as exc:
            failures.append(str(exc))
            continue
        _store().mark_cancelled(batch, surface, batch["batch_id"], batch["plan_fingerprint"])
    if failures:
        raise RuntimeError("parent fenced but Resident input cancellation is pending: " + "; ".join(failures))


def prepare_batches(*, job: dict, surface: str, batches: list[dict]) -> dict:
    job = _job(job, surface)
    batches = validate_manifest(batches)
    fingerprint = plan_fingerprint(job, batches)
    existing = _store().get(job, surface)
    if existing:
        if existing.get("is_current_generation") is False:
            raise ServiceInvocationConflict("historical search generation is observable but cannot be invoked again")
        if not plan_fingerprint_matches(job, batches, existing["plan_fingerprint"]):
            raise ServiceInvocationConflict("same search generation has a different batch plan")
        _cancel_pending(job, surface)
        return existing
    binding = search_binding(surface)
    authority = _authorize_new(job, binding)
    record = {"schema": PLAN_SCHEMA, **job,
              "resident_id": binding["resident_id"], "lineage_id": binding["lineage_id"],
              "binding": binding, "binding_fingerprint": _digest(binding), "authorization": authority,
              "plan_fingerprint": fingerprint, "batch_count": len(batches),
              "status": "prepared", "cancel_requested": False, "fenced": False}
    members = []
    for batch in batches:
        from .resident_message_contract import derived_message_identity
        message_id = derived_message_identity("search-input", fingerprint + ":" + batch["batch_id"])
        members.append({**record, **batch, "message_id": message_id})
    record["batches"] = members
    if len(members) == 1:
        for field in ("input_fingerprint", "message_id"):
            record[field] = members[0][field]
    record = _store().reserve(job, surface, record)
    _cancel_pending(job, surface)
    return record


def submit_batch(*, job: dict, surface: str, batch_id: str, plan_fingerprint: str, prompt: str) -> dict:
    job = _job(job, surface)
    record = _store().get_batch(job, surface, batch_id, plan_fingerprint)
    if record.get("is_current_generation") is False:
        raise ServiceInvocationConflict("historical search generation cannot be submitted as the current run")
    if record["input_fingerprint"] != input_fingerprint(job, prompt):
        raise ServiceInvocationConflict("search generation input changed")
    if record.get("fenced"):
        _cancel_pending(job, surface)
        return record
    if record["status"] in TERMINAL_STATUSES | {"queued", "running"}:
        return record
    current = search_binding(surface)
    if _digest(current) != record["binding_fingerprint"]:
        raise ServiceInvocationConflict("binding changed before first dispatch")
    _authorize_new(job, current)
    profile = {"input_sha256": hashlib.sha256(prompt.strip().encode()).hexdigest()}
    resident_input_scope.register(resident_id=record["resident_id"], message_id=record["message_id"],
                                  actor=job["created_by"], profile=profile)
    _store().claim_dispatch(job, surface, batch_id, plan_fingerprint)
    if _store().get_batch(job, surface, batch_id, plan_fingerprint).get("fenced"):
        return _store().get_batch(job, surface, batch_id, plan_fingerprint)
    try:
        # Message identity makes a lost acknowledgement/restarted observer safe to retry.
        acknowledgement = deliver_resident_message(
            actor=job["created_by"], identity=record, idempotency_key="search:" + record["message_id"],
            message={"message_id": record["message_id"], "role": "user",
                     "content_parts": [{"type": "text", "text": prompt}]},
        )
    finally:
        _cancel_pending(job, surface)
    state = acknowledgement.get("input_state", {}).get("state") or "queued"
    record = _store().observe(job, surface, batch_id, plan_fingerprint, state)
    _cancel_pending(job, surface)
    return record


def _observe_batch(job, surface, record):
    if record.get("fenced") or record["status"] == "prepared":
        return {"receipt": record, "input_state": {}}
    try:
        state = resident_protocol.get_resident_input_state(record["resident_id"], job["created_by"], record["message_id"])
    except KeyError:
        return {"receipt": record, "input_state": {}}
    if state.get("message_id") != record["message_id"]:
        raise ServiceInvocationConflict("Input state belongs to another message")
    record = _store().observe(job, surface, record["batch_id"], record["plan_fingerprint"], state["state"])
    return {"receipt": record, "input_state": {} if record.get("fenced") else state}


def get_batch(*, job: dict, surface: str, batch_id: str, plan_fingerprint: str) -> dict:
    job = _job(job, surface)
    _cancel_pending(job, surface)
    return _observe_batch(job, surface, _store().get_batch(job, surface, batch_id, plan_fingerprint))


def get_invocation(*, job: dict, surface: str) -> dict:
    job = _job(job, surface)
    _cancel_pending(job, surface)
    record = _store().get(job, surface)
    if not record:
        raise KeyError("service invocation not found")
    input_state = {}
    if len(record["batches"]) == 1:
        input_state = _observe_batch(job, surface, record["batches"][0])["input_state"]
    else:
        for batch in record["batches"]:
            if batch["status"] not in TERMINAL_STATUSES | {"prepared"}:
                _observe_batch(job, surface, batch)
    record = _store().get(job, surface)
    return {"receipt": record, "input_state": {} if record.get("fenced") else input_state}


def read_plan(*, job: dict, surface: str) -> dict | None:
    """Internal diagnostics for an authoritative, already authorized Search job."""
    job = _job_identity(job, surface)
    return _store().read_plan(job, job["surface"])


def cancel_invocation(*, job: dict, surface: str, deleted: bool = False) -> dict:
    job = _job(job, surface)
    result = _store().cancel(job, surface, deleted=deleted)
    _cancel_pending(job, surface)
    if result["receipt"] is not None:
        result["receipt"] = _store().get(job, surface)
    return {**result, "deleted": deleted}


def fail_invocation(*, job: dict, surface: str, plan_fingerprint: str = "") -> dict:
    """Fence an exact run, including failure before its plan was acknowledged."""
    job = _job(job, surface)
    result = _store().fail(job, surface, plan_fingerprint)
    _cancel_pending(job, surface)
    if result["receipt"] is not None:
        result["receipt"] = _store().get(job, surface)
    return result
