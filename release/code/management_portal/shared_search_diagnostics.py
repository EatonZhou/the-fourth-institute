from __future__ import annotations

import json
import re
from typing import Any

from management_common.principal import validate_user_id
from management_portal import semantic_search_jobs, semantic_search_store, shared_search_adapter as adapter
from management_portal.execution.diagnostics import text_metrics
from management_portal.society_service import get_society_service
from management_portal.portal_access_control import get_portal_access_store
from management_portal.resources import get_resource_service
from management_portal.service_invocation_contract import input_fingerprint, plan_fingerprint_matches


SCHEMA = "tfi.search-run-diagnostics.v1"
IDENTITY_FIELDS = ("created_by", "surface", "search_id", "run_generation")
RECEIPT_STATUSES = {"prepared", "dispatching", "submitted", "queued", "pending", "running", "completed", "failed", "cancelled", "superseded"}


def _authorized_job(search_id: str, actor: str):
    actor = validate_user_id(actor)
    if not get_portal_access_store().is_active(actor):
        raise PermissionError("需要有效的 Portal 登录身份")
    resolved = get_resource_service().resolve(search_id, actor=actor)
    if resolved.get("root_id") != search_id or resolved.get("resource_type") != "search" or not resolved.get("exists"):
        raise KeyError("Search is not available")
    surface = adapter.canonical_surface(resolved["record"]["surface"])
    job = semantic_search_jobs.search_by_id(surface, search_id)
    if not job or job.get("search_id") != search_id or job.get("deleted"):
        raise KeyError("Search is not available")
    if job.get("surface") != surface:
        raise ValueError("Search diagnostics identity mismatch")
    return job, surface


def _validate_plan(plan: dict, identity: dict) -> list[dict]:
    if any(plan.get(key) != value for key, value in identity.items()):
        raise ValueError("Search diagnostics plan identity mismatch")
    batches = plan.get("batches")
    if (not isinstance(batches, list) or not batches or plan.get("batch_count") != len(batches)
            or any(not isinstance(batch, dict) for batch in batches)):
        raise ValueError("Search diagnostics invalid batch plan")
    manifest = [{"batch_id": batch.get("batch_id"), "input_fingerprint": batch.get("input_fingerprint")} for batch in batches]
    if not plan_fingerprint_matches(identity, manifest, plan.get("plan_fingerprint")):
        raise ValueError("Search diagnostics plan fingerprint mismatch")
    seen = set()
    for batch in batches:
        if (any(batch.get(key) != value for key, value in identity.items())
                or batch.get("plan_fingerprint") != plan["plan_fingerprint"]
                or not isinstance(batch.get("message_id"), str)
                or not re.fullmatch(r"msg_[a-zA-Z0-9_-]+", batch["message_id"])
                or batch["message_id"] in seen):
            raise ValueError("Search diagnostics batch identity mismatch")
        seen.add(batch["message_id"])
    return batches


def _input_statistics(identity: dict, batches: list[dict]) -> list[dict]:
    unknown = [{"source": "unknown", "candidate_count": None, "prompt": None} for _ in batches]
    try:
        payload = semantic_search_store.stored_run_input(identity["search_id"])
        # Only the latest run's frozen input is kept; earlier generations are not recorded.
        if payload is None or (payload.get("identity") or {}).get("run_generation") != identity["run_generation"]:
            return unknown
        if payload.get("identity") != identity:
            raise ValueError("snapshot identity mismatch")
        prompts = payload.get("prompts")
        if prompts is None and isinstance(payload.get("prompt"), str):
            prompts = [payload["prompt"]]
        if not isinstance(prompts, list) or len(prompts) != len(batches):
            raise ValueError("snapshot batch count mismatch")
        values = []
        for batch, prompt in zip(batches, prompts):
            if not isinstance(prompt, str) or input_fingerprint(identity, prompt) != batch["input_fingerprint"]:
                raise ValueError("snapshot fingerprint mismatch")
            # Measure the frozen wire-input format, not today's query, collector or batch budget.
            content = json.loads(prompt.split("\n", 1)[1])
            if (not isinstance(content, dict) or content.get("surface") != identity["surface"]
                    or not isinstance(content.get("candidates"), list)
                    or any(not isinstance(row, dict) for row in content["candidates"])):
                raise ValueError("snapshot candidates unavailable")
            values.append({"source": "verified_frozen_input", "candidate_count": len(content["candidates"]),
                           "prompt": text_metrics(prompt)})
        return values
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return [{**item, "source": "unavailable_or_invalid_snapshot"} for item in unknown]


def _receipt(record: dict) -> dict[str, Any]:
    status = record.get("status")
    return {"status": status if isinstance(status, str) and status in RECEIPT_STATUSES else "unknown",
            "fenced": bool(record.get("fenced")),
            "is_current_generation": record.get("is_current_generation") is True}


def get_search_run_diagnostics(search_id: str, *, actor: str, generation: int | None = None) -> dict[str, Any]:
    """Read one authorized Search generation, without observing or mutating its lifecycle."""
    if not isinstance(search_id, str) or not re.fullmatch(r"search_[0-9a-f]{12}", search_id):
        raise ValueError("canonical Search ID required")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise ValueError("generation must be a positive integer")
    job, surface = _authorized_job(search_id, actor)
    current_generation = adapter.run_generation(job)
    selected = current_generation if generation is None else generation
    if selected < 1 or selected > current_generation:
        raise KeyError("Search generation is not available")
    identity = {"search_id": search_id, "surface": surface, "created_by": validate_user_id(job.get("created_by")),
                "run_generation": selected}
    response = {"schema": SCHEMA, "search_id": search_id, "surface": surface,
                "run_generation": selected, "current_generation": current_generation,
                "availability": "not_recorded", "parent": None, "batches": []}
    if job.get("execution_contract") != adapter.CONTRACT:
        raise ValueError("unsupported semantic search execution contract")
    plan = get_society_service().service_invocation.read_plan(job=identity, surface=surface)
    if plan is not None:
        batches = _validate_plan(plan, identity)
        inputs = _input_statistics(identity, batches)
        values = []
        for batch, input_stats in zip(batches, inputs):
            try:
                state = get_society_service().resident.get_resident_input_state(
                    batch["resident_id"], actor, batch["message_id"],
                )
            except KeyError:
                state = {"state": "not_submitted"}
            except PermissionError:
                state = {"state": "not_authorized"}
            else:
                if state.get("message_id") != batch["message_id"]:
                    raise ValueError("Search input identity mismatch")
                state = {"message_id": state["message_id"], "state": state["state"],
                         "updated_at": state.get("updated_at", ""),
                         "reply_count": len(state.get("reply_message_ids") or [])}
            values.append({"batch_id": batch["batch_id"], "message_id": batch["message_id"],
                           "resident_id": batch["resident_id"], "receipt": _receipt(batch),
                           "input": input_stats, "input_state": state})
        response.update(availability="available", parent={**_receipt(plan), "batch_count": len(batches),
                        "plan_fingerprint": plan["plan_fingerprint"]}, batches=values)
    # Recheck revocation and identity after reads; do not expose data under stale ownership.
    latest, latest_surface = _authorized_job(search_id, actor)
    if latest.get("created_by") != identity["created_by"] or latest_surface != surface:
        raise PermissionError("Search ownership identity changed during diagnostics")
    return response
