from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from typing import Any

from .resident_handoff import resume_and_wait
from .society_service import get_society_service
from .service_invocation_contract import ServiceInvocationConflict, batch_manifest, input_fingerprint


SharedSearchInvocationConflict = ServiceInvocationConflict


class SharedSearchInvocationFailed(RuntimeError):
    def __init__(self, message: str, *, receipt: Mapping[str, Any] | None = None,
                 input_state: Mapping[str, Any] | None = None) -> None:
        self.receipt = copy.deepcopy(dict(receipt or {}))
        self.input_state = copy.deepcopy(dict(input_state or {}))
        self.message_id = self.receipt.get("message_id", "")
        super().__init__(message)


class SharedSearchObservationPending(TimeoutError):
    """Resume observation of this receipt; no terminal outcome was observed."""

    def __init__(self, receipt: Mapping[str, Any], *, input_state: Mapping[str, Any] | None = None) -> None:
        self.receipt = copy.deepcopy(dict(receipt))
        self.input_state = copy.deepcopy(dict(input_state or {}))
        self.message_id = self.receipt["message_id"]
        super().__init__(f"search observation budget exhausted for {self.message_id}")


def get_shared_search_invocation(*, job: Mapping[str, Any], surface: str) -> dict[str, Any]:
    return get_society_service().service_invocation.get_invocation(job=dict(job), surface=surface)


def read_shared_search_plan(*, job: Mapping[str, Any], surface: str) -> dict[str, Any] | None:
    return get_society_service().service_invocation.read_plan(job=dict(job), surface=surface)


def cancel_shared_search_invocation(*, job: Mapping[str, Any], surface: str, deleted: bool = False) -> dict[str, Any]:
    return get_society_service().service_invocation.cancel_invocation(job=dict(job), surface=surface, deleted=deleted)


def fail_shared_search_invocation(
    *, job: Mapping[str, Any], surface: str, plan_fingerprint: str = "",
) -> dict[str, Any]:
    return get_society_service().service_invocation.fail_invocation(
        job=dict(job), surface=surface, plan_fingerprint=plan_fingerprint,
    )


def prepare_shared_search_batches(
    *, job: Mapping[str, Any], surface: str, prompts: list[str],
) -> dict[str, Any]:
    return get_society_service().service_invocation.prepare_batches(
        job=dict(job), surface=surface, batches=batch_manifest(job, prompts),
    )


def run_shared_search_batch(
    *,
    job: Mapping[str, Any],
    surface: str,
    prompt: str,
    batch_id: str,
    plan_fingerprint: str,
    timeout_seconds: float = 120.0,
    cancellation_requested: Callable[[], bool] | None = None,
    on_assigned: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute one member of a frozen creator-authorized invocation plan."""
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("search timeout must be finite and positive")
    owner = get_society_service().service_invocation
    arguments = {"job": dict(job), "surface": surface}
    batch_arguments = {**arguments, "batch_id": batch_id, "plan_fingerprint": plan_fingerprint}
    if cancellation_requested is not None and cancellation_requested():
        owner.cancel_invocation(**arguments)
        raise InterruptedError("search cancelled before dispatch")
    receipt = owner.get_batch(**batch_arguments)["receipt"]
    if receipt["input_fingerprint"] != input_fingerprint(job, prompt):
        raise SharedSearchInvocationConflict("batch input does not match the frozen plan")
    if receipt.get("fenced") or receipt.get("is_current_generation") is False:
        raise SharedSearchInvocationFailed("search invocation is fenced or no longer current", receipt=receipt)
    if on_assigned is not None:
        on_assigned(copy.deepcopy(receipt))
    receipt = owner.submit_batch(**batch_arguments, prompt=prompt)
    if receipt.get("fenced") or receipt.get("is_current_generation") is False:
        raise SharedSearchInvocationFailed("search invocation is fenced or no longer current", receipt=receipt)
    cancellation_fenced = False

    def should_cancel():
        nonlocal cancellation_fenced
        if not cancellation_fenced and cancellation_requested is not None and cancellation_requested():
            owner.cancel_invocation(**arguments)
            cancellation_fenced = True
        return cancellation_fenced

    try:
        result = resume_and_wait(
            actor=job["created_by"], resident_id=receipt["resident_id"],
            message_id=receipt["message_id"], lineage_id=receipt["lineage_id"],
            timeout_seconds=timeout, cancellation_requested=should_cancel,
        )
    except TimeoutError as exc:
        observed = owner.get_batch(**batch_arguments)
        raise SharedSearchObservationPending(receipt=observed["receipt"], input_state=observed["input_state"]) from exc
    except RuntimeError as exc:
        observed = owner.get_batch(**batch_arguments)
        raise SharedSearchInvocationFailed(str(exc), receipt=observed["receipt"], input_state=observed["input_state"]) from exc
    observed = owner.get_batch(**batch_arguments)
    receipt, state = observed["receipt"], observed["input_state"]
    if receipt.get("fenced") or receipt.get("is_current_generation") is False:
        raise SharedSearchInvocationFailed("search parent no longer accepts this result", receipt=receipt, input_state=state)
    if state.get("state") != "completed" or result.get("message_id") != receipt["message_id"] or not result.get("message", "").strip():
        raise SharedSearchInvocationFailed("search input has no completed reply", receipt=receipt, input_state=state)
    return {**result, "status": "completed", "binding_fingerprint": receipt.get("binding_fingerprint", ""),
            "run_generation": receipt["run_generation"], "receipt": receipt,
            "batch_id": batch_id, "plan_fingerprint": plan_fingerprint}


def run_shared_search_turn(
    *, job: Mapping[str, Any], surface: str, prompt: str,
    timeout_seconds: float = 120.0,
    cancellation_requested: Callable[[], bool] | None = None,
    on_assigned: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("search timeout must be finite and positive")
    if cancellation_requested is not None and cancellation_requested():
        cancel_shared_search_invocation(job=job, surface=surface)
        raise InterruptedError("search cancelled before dispatch")
    plan = prepare_shared_search_batches(job=job, surface=surface, prompts=[prompt])
    return run_shared_search_batch(
        job=job, surface=surface, prompt=prompt, batch_id=plan["batches"][0]["batch_id"],
        plan_fingerprint=plan["plan_fingerprint"], timeout_seconds=timeout,
        cancellation_requested=cancellation_requested, on_assigned=on_assigned,
    )
