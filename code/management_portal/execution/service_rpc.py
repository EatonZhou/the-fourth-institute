"""ExecutionService RPC methods: one handler per wire method, looked up by name.

A handler takes the ExecutionServiceServer and the request params and returns
the reply's result.  Business owners call the ``execution.*`` methods; Workers
call the ``scheduler.*`` methods with their claim.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from . import provider_default_efforts
from .activity_redaction import redact_sensitive_mapping
from .contracts import (
    ExecutionInputControl,
    ExecutionRequest,
    ExecutionResult,
    ExecutionServiceHealth,
    ExecutionTurnSettingsControl,
)
from management_portal.notifications import business_projection_subject

LOGGER = logging.getLogger(__name__)

EXECUTION_SERVICE_SCHEMA = "tfi.execution-service.v1"

RpcHandler = Callable[[Any, dict[str, Any]], Any]


def required(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _capabilities(params: dict[str, Any]) -> list[str]:
    return [str(item) for item in params.get("capabilities") or [] if str(item)]


def _bounded_timeout(params: dict[str, Any], *, default: float, maximum: float) -> float:
    return min(maximum, max(0.1, float(params.get("timeout_seconds") or default)))


def _claim(params: dict[str, Any]) -> dict[str, str]:
    """The Worker and claim that fence a ``scheduler.*`` write."""

    return {
        "worker_id": required(params, "worker_id"),
        "claim_token": required(params, "claim_token"),
    }


def _performed_by(params: dict[str, Any], actor: str) -> str:
    return str(params.get("performed_by") or actor)


def _authorize_settings(params: dict[str, Any], instruction: str) -> None:
    from management_portal import permission_system

    if not permission_system.allows(required(params, "actor"), instruction):
        raise PermissionError(f"无权执行 {instruction}")


def get_settings(server: Any, params: dict[str, Any]) -> Any:
    _authorize_settings(params, "GetExecutionSettings")
    return server.request_timing.settings.get()


def update_settings(server: Any, params: dict[str, Any]) -> Any:
    _authorize_settings(params, "UpdateExecutionSettings")
    return server.request_timing.configure(params.get("model_request_timing_enabled"))


def health(server: Any, params: dict[str, Any]) -> ExecutionServiceHealth:
    scheduler_health = server.scheduler.health()
    return {
        "ok": True,
        "schema": EXECUTION_SERVICE_SCHEMA,
        "instance": server.instance,
        "build_id": server.build_id,
        "scheduler": scheduler_health,
        "notifications": server.notification_publisher.health(),
        "agent_data": server.agent_data.health(),
    }


def account_usage(server: Any, params: dict[str, Any]) -> Any:
    return server.quota_monitor.usage_snapshot()


def list_accounts(server: Any, params: dict[str, Any]) -> Any:
    if params.get("force_refresh"):
        server.quota_monitor.request_refresh()
    return server.quota_monitor.snapshot()


def resolve_account(server: Any, params: dict[str, Any]) -> Any:
    return server.accounts.configuration(
        required(params, "provider"),
        str(params.get("backend_route_id") or ""),
        params.get("runtime_options") or {},
    )


def runtime_catalog(server: Any, params: dict[str, Any]) -> Any:
    catalog = server.accounts.runtime_catalog(
        str(params.get("account_id") or ""),
        backend=str(params.get("backend") or ""),
        include_backends=bool(params.get("include_backends")),
    )
    try:
        return provider_default_efforts.apply(server.scheduler.changes, catalog)
    except Exception:  # noqa: BLE001 - the catalog stands without the observed defaults
        LOGGER.warning("Observed default efforts unavailable", exc_info=True)
        return catalog


def submit_execution(server: Any, params: dict[str, Any]) -> Any:
    request = ExecutionRequest.from_payload(params)
    return server.publish_change(
        server.scheduler.submit(request, prepare=server.preparation.prepare)
    )


def execution_states(server: Any, params: dict[str, Any]) -> Any:
    executions = params.get("executions")
    if not isinstance(executions, dict) or len(executions) > 500:
        raise ValueError("execution.states requires at most 500 execution/actor pairs")
    if not all(
        isinstance(key, str) and key and isinstance(actor, str) and actor
        for key, actor in executions.items()
    ):
        raise ValueError("execution.states requires execution and actor IDs")
    return server.scheduler.states(executions)


def execution_status(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = str(params.get("actor") or "")
    result = server.scheduler.snapshot(
        execution_id,
        after_sequence=int(params.get("after_sequence") or 0),
        include_events=bool(params.get("include_events", True)),
        include_result=bool(params.get("include_result", True)),
    )
    request = (
        result.get("request")
        if isinstance(result.get("request"), dict)
        else {}
    )
    if actor and str(
        request.get("actor") or result.get("actor") or ""
    ) != actor:
        raise KeyError(execution_id)
    return result


def execution_record(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = required(params, "actor")
    result = server.agent_data.execution(execution_id)
    if result is None or result["actor_ref"] != actor:
        raise KeyError(execution_id)
    return result


def execution_token_usage(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.token_usage(
        start_hour=required(params, "start_hour"),
        end_hour=required(params, "end_hour"),
        provider=required(params, "provider"),
        include_accounts=params.get("include_accounts") is True,
    )


def wait_for_execution(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = required(params, "actor")
    # The row's actor is the submitted request's actor. Reading only it
    # keeps each poll from parsing the whole request under the Scheduler lock.
    if server.scheduler.actor_of(execution_id) != actor:
        raise KeyError(execution_id)
    return server.scheduler.wait_for_change(
        execution_id,
        after_updated_at=str(params.get("after_updated_at") or ""),
        after_sequence=int(params.get("after_sequence") or 0),
        include_events=bool(params.get("include_events", True)),
        include_result=bool(params.get("include_result", True)),
        timeout_seconds=_bounded_timeout(params, default=30.0, maximum=60.0),
        quiet_reply=params.get("quiet_reply") is True,
    )


def append_input(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.submit_input_control(
        ExecutionInputControl.from_payload(params)
    )


def update_turn_settings(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.submit_turn_settings_control(
        ExecutionTurnSettingsControl.from_payload(params)
    )


def input_control(server: Any, params: dict[str, Any]) -> Any:
    control_id = required(params, "control_id")
    agent_id = required(params, "agent_id")
    receipt = server.scheduler.input_control(control_id)
    if receipt is None:
        return None
    execution = server.agent_data.execution(str(receipt["execution_id"]))
    if not execution or execution.get("agent_id") != agent_id:
        raise KeyError(control_id)
    return receipt


def withdraw_unstarted(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.withdraw_unstarted(**{
        key: required(params, key) for key in (
            "execution_id", "actor", "binding_id", "operation_id",
            "replacement_execution_id", "performed_by",
        )
    })


def cancel_execution(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = required(params, "actor")
    return server.publish_change(server.scheduler.cancel(
        execution_id,
        actor=actor,
        reason=str(params.get("reason") or ""),
        performed_by=_performed_by(params, actor),
        **({"queued_only": True} if params.get("queued_only") is True else {}),
    ))


def set_priority(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = required(params, "actor")
    priority = int(params.get("priority") or 0)
    return server.publish_change(server.scheduler.set_priority(
        execution_id,
        actor=actor,
        priority=priority,
        performed_by=_performed_by(params, actor),
    ))


def reassign_queued_route(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    actor = required(params, "actor")
    backend_route_id = required(params, "backend_route_id")
    return server.publish_change(server.scheduler.reassign_queued_route(
        execution_id,
        actor=actor,
        backend_route_id=backend_route_id,
        performed_by=_performed_by(params, actor),
    ))


def acknowledge_execution(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.acknowledge(
        required(params, "execution_id"),
        actor=required(params, "actor"),
    )


def register_worker(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.register_worker(
        worker_id=required(params, "worker_id"),
        build_id=required(params, "build_id"),
        capabilities=_capabilities(params),
        pid=int(params.get("pid") or 0),
        state=required(params, "state"),
    )


def activate_worker(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.activate_worker(
        required(params, "worker_id"),
        previous_worker_id=str(params.get("previous_worker_id") or ""),
    )


def set_worker_state(server: Any, params: dict[str, Any]) -> Any:
    server.scheduler.set_worker_state(
        required(params, "worker_id"),
        required(params, "state"),
    )
    return {"ok": True}


def handover_worker(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.handover_worker(required(params, "worker_id"))


def unregister_worker(server: Any, params: dict[str, Any]) -> Any:
    return {
        "ok": True,
        "removed": server.scheduler.unregister_worker(
            required(params, "worker_id")
        ),
    }


def wait_for_claim(server: Any, params: dict[str, Any]) -> Any:
    claimed = server.scheduler.claim_wait(
        worker_id=required(params, "worker_id"),
        capabilities=_capabilities(params),
        lease_seconds=int(params.get("lease_seconds") or 270),
        timeout_seconds=_bounded_timeout(params, default=15.0, maximum=30.0),
    )
    if isinstance(claimed.get("execution"), dict):
        server.publish_change(claimed["execution"])
    return claimed


def start_execution(server: Any, params: dict[str, Any]) -> Any:
    execution_id = required(params, "execution_id")
    worker_id = required(params, "worker_id")
    return server.publish_change(server.scheduler.start(
        execution_id,
        worker_id=worker_id,
        claim_token=required(params, "claim_token"),
    ))


def heartbeat(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.heartbeat(
        required(params, "execution_id"),
        **_claim(params),
        lease_seconds=int(params.get("lease_seconds") or 270),
    )


def prepare_handover(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.prepare_handover(
        required(params, "execution_id"),
        **_claim(params),
    )


def requeue_claim(server: Any, params: dict[str, Any]) -> Any:
    return server.publish_change(server.scheduler.requeue_claim(
        required(params, "execution_id"),
        **_claim(params),
        reason=str(params.get("reason") or ""),
        retry_after_seconds=float(params.get("retry_after_seconds") or 0),
    ))


def wait_for_worker_control(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.wait_for_worker_control(
        required(params, "execution_id"),
        **_claim(params),
        timeout_seconds=_bounded_timeout(params, default=30.0, maximum=60.0),
    )


def complete_input_control(server: Any, params: dict[str, Any]) -> Any:
    receipt = server.scheduler.complete_input_control(
        required(params, "control_id"),
        execution_id=required(params, "execution_id"),
        **_claim(params),
        delivered=bool(params.get("delivered")),
        error=str(params.get("error") or ""),
    )
    if receipt.get("status") == "delivered":
        from .appended_input_projection import materialize_delivered_input

        materialize_delivered_input(server.agent_data, receipt)
    return receipt


def record_event(server: Any, params: dict[str, Any]) -> Any:
    details = params.get("details")
    execution_id = required(params, "execution_id")
    worker_id = required(params, "worker_id")
    event_type = required(params, "event_type")
    event_details = dict(details) if isinstance(details, dict) else {}
    if event_type == "token_usage_delta":
        return server.scheduler.record_token_usage_delta(
            execution_id,
            worker_id=worker_id,
            claim_token=required(params, "claim_token"),
            event_id=required(event_details, "event_id"),
            usage=event_details.get("usage"),
            # Adapters that read usage from a Provider session file after
            # the turn report when each request was made.
            occurred_at=str(event_details.get("occurred_at") or ""),
        )
    if event_type == "provider_event":
        event_details = redact_sensitive_mapping(event_details)
    activity = server.scheduler.append_event(
        execution_id,
        worker_id=worker_id,
        claim_token=required(params, "claim_token"),
        event_type=event_type,
        details=event_details,
    )
    server.project_activity(execution_id, event_type, activity)
    return {"ok": True}


def checkpoint(server: Any, params: dict[str, Any]) -> Any:
    saved = params.get("checkpoint")
    if not isinstance(saved, dict):
        raise ValueError("execution checkpoint must be an object")
    return server.publish_change(server.scheduler.checkpoint(
        required(params, "execution_id"),
        **_claim(params),
        checkpoint=saved,
    ))


def reassign_capacity(server: Any, params: dict[str, Any]) -> Any:
    return server.publish_change(server.scheduler.reassign_capacity(
        required(params, "execution_id"),
        **_claim(params),
        backend_route_id=required(params, "backend_route_id"),
    ))


def quota_states(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.binding_quota_states(params.get("bindings"))


def configure_quota_overrides(server: Any, params: dict[str, Any]) -> Any:
    return server.publish_change(
        server.scheduler.configure_binding_quota_overrides(params.get("policies"))
    )


def forget_quota_overrides(server: Any, params: dict[str, Any]) -> Any:
    return server.scheduler.forget_binding_quota_overrides(params.get("binding_ids"))


def configure_capacity(server: Any, params: dict[str, Any]) -> Any:
    account = server.accounts.get(required(params, "account_id"))
    for key in (
        "max_concurrency",
        "dispatch_interval_min_seconds",
        "dispatch_interval_max_seconds",
    ):
        if key not in params:
            raise ValueError(f"{key} is required")
    setting = server.scheduler.configure_capacity(
        account.capacity_key,
        params.get("max_concurrency"),
        float(params.get("dispatch_interval_min_seconds") or 0),
        float(params.get("dispatch_interval_max_seconds") or 0),
        actor=required(params, "actor"),
        close_threshold=params.get("close_threshold"),
    )
    return {
        "ok": True,
        "account_id": account.account_id,
        "close_threshold": setting.get("close_threshold"),
        "max_concurrency": int(setting["limit"]),
        "dispatch_interval_min_seconds": float(
            setting["dispatch_interval_min_seconds"]
        ),
        "dispatch_interval_max_seconds": float(
            setting["dispatch_interval_max_seconds"]
        ),
        "updated_by": str(setting["updated_by"]),
        "updated_at": str(setting["updated_at"]),
    }


def finish_execution(server: Any, params: dict[str, Any]) -> Any:
    result = params.get("result")
    if not isinstance(result, dict):
        raise ValueError("execution result must be an object")
    execution_result = ExecutionResult.from_payload(result)
    finished = server.scheduler.finish(
        execution_result,
        **_claim(params),
    )
    server.record_accounted_usage(finished)
    server.record_provider_default_effort(finished)
    snapshot = server.publish_change(finished)
    if execution_result.usage:
        server.notification_publisher.publish(
            business_projection_subject("token-usage", "snapshot"),
            {"kind": "changed", "fields": ["usage"]},
        )
    return snapshot


RPC_HANDLERS: dict[str, RpcHandler] = {
    "settings.get": get_settings,
    "settings.update": update_settings,
    "health": health,
    "accounts.usage": account_usage,
    "accounts.list": list_accounts,
    "accounts.resolve": resolve_account,
    "runtime.catalog": runtime_catalog,
    "execution.submit": submit_execution,
    "execution.states": execution_states,
    "execution.status": execution_status,
    "execution.record": execution_record,
    "execution.token_usage": execution_token_usage,
    "execution.wait": wait_for_execution,
    "execution.input.append": append_input,
    "execution.turn_settings.update": update_turn_settings,
    "execution.input.get": input_control,
    "execution.unstarted.withdraw": withdraw_unstarted,
    "execution.cancel": cancel_execution,
    "execution.priority": set_priority,
    "execution.route.reassign": reassign_queued_route,
    "execution.acknowledge": acknowledge_execution,
    "scheduler.worker.register": register_worker,
    "scheduler.worker.activate": activate_worker,
    "scheduler.worker.state": set_worker_state,
    "scheduler.worker.handover": handover_worker,
    "scheduler.worker.unregister": unregister_worker,
    "scheduler.claim.wait": wait_for_claim,
    "scheduler.start": start_execution,
    "scheduler.heartbeat": heartbeat,
    "scheduler.handover.prepare": prepare_handover,
    "scheduler.requeue": requeue_claim,
    "scheduler.control.wait": wait_for_worker_control,
    "scheduler.input.complete": complete_input_control,
    "scheduler.event": record_event,
    "scheduler.checkpoint": checkpoint,
    "scheduler.capacity.reassign": reassign_capacity,
    "scheduler.quota_states": quota_states,
    "scheduler.quota_overrides.configure": configure_quota_overrides,
    "scheduler.quota_overrides.forget": forget_quota_overrides,
    "scheduler.capacity.configure": configure_capacity,
    "scheduler.finish": finish_execution,
}
