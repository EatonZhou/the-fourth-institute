"""Shared creation semantics for ordinary discussion and application Residents.

The caller serializes its logical slot and owns the association storage.  A
request is frozen before it is sent and carries its request ID as the
creation key, so resending it after an unknown outcome returns the Resident
it already created; once a Resident ID is recorded it is reused and never
allocated again.  These Residents may use the Portal-internal provider API
backend, so they are created through SocietyService rather than the
public ``CreateResident`` instruction.
"""
from copy import deepcopy
import secrets
from management_common.operation_errors import OperationError


def create(*, actor, lineage_id, arguments, load, save, service=None):
    if service is None:
        from .society_service import get_society_service

        service = get_society_service()
    state = deepcopy(load() or {})
    request = state.get("request")
    if request:
        if request["lineage_id"] != lineage_id:
            raise ValueError("Resident creation is pending in another Lineage; reconcile the original request")
        if request["arguments"]["backend"] != arguments["backend"]:
            raise ValueError("Resident invocation backend cannot change")
    if state.get("resident_id"):
        return state["resident_id"]
    if not request:
        request = {
            "lineage_id": lineage_id,
            "request_id": f"invocation-create:{secrets.token_hex(16)}",
            "arguments": deepcopy(arguments),
        }
        state = {"request": request}
        save(state)
    try:
        attached = service.lineage.create_lineage_resident(
            lineage_id, {**request["arguments"], "creation_key": request["request_id"]}, actor,
        )
    except OperationError as exc:
        # Preserve the original request and the owner's exact receipt. A
        # pending/unknown response is not a successful Resident association.
        exc.details.setdefault("resident_creation", {
            "lineage_id": lineage_id, "request_id": request["request_id"],
        })
        save({**state, "operation_error": exc.failure_payload()})
        raise
    record = dict((attached or {}).get("resident") or {})
    resident_id = str(record.get("resident_id") or (attached or {}).get("node", {}).get("id") or "").strip()
    if not resident_id or (record and record.get("lineage_id") != lineage_id):
        raise RuntimeError("Resident registration returned no canonical identity in the requested Lineage")
    state.pop("operation_error", None)
    save({**state, "resident_id": resident_id})
    return resident_id
