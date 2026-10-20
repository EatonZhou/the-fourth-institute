from __future__ import annotations

"""Reusable Resident input delivery and service bindings."""

import hashlib
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from management_common.principal import actor_principal
from management_portal.execution.provider_catalog import provider_name

from .hatg.contracts import InstructionError
from .society_service import get_society_service, SocietyServiceUnavailable
from .resources.errors import ResourceServiceUnavailable
from .society_service.resident_protocol import (
    CONTEXT_TEXT_FIELD,
    INSTRUCTION_SKILLS_FIELD,
    WORKING_DIRECTORY_FIELD,
)
from .notifications import ensure_notification_listener, get_notification_hub
from .portal_access_control import get_portal_access_store
from .portal_ui_state import get_portal_ui_state_store


TERMINAL_INPUT_STATES = frozenset({"completed", "failed", "cancelled"})
INVOCATION_ASSOCIATION_NAMESPACE = "resident-invocations"


def release_resident(*, actor: str, resident_id: str, lineage_id: str,
                     forget_associations: bool = True) -> None:
    """Finish deletion before forgetting any invocation scope or creation receipt."""
    from .lineage_workflows import WorkflowBindings

    WorkflowBindings().delete_resident(actor=actor, lineage_id=lineage_id, resident_id=resident_id)
    if forget_associations:
        forget_resident_associations(resident_id)


def forget_resident_associations(resident_id: str) -> None:
    """Discard derived scope/creation records after canonical deletion succeeds."""
    store = get_portal_ui_state_store()
    for namespace in (INVOCATION_CREATION_NAMESPACE, INVOCATION_ASSOCIATION_NAMESPACE):
        for entry in store.list_namespace(namespace):
            if entry['value'].get('resident_id') != resident_id:
                continue
            with store.association_lock(entry['actor'], INVOCATION_ASSOCIATION_NAMESPACE, entry['key']):
                current = store.get(entry['actor'], namespace, entry['key'])
                if current and current['value'].get('resident_id') == resident_id:
                    store.remove(entry['actor'], namespace, entry['key'])


INVOCATION_LINEAGE_NAMESPACE = "resident-invocation-lineages"
INVOCATION_CREATION_NAMESPACE = "resident-invocation-creations"
_LIFECYCLE_FINALIZING_ERROR = "Resident 生命周期操作正在完成，请稍后编辑"


class ResidentInputTerminated(RuntimeError):
    """A definitive input failure/cancellation, distinct from observation errors."""
    def __init__(self, state: Mapping[str, Any]):
        self.status = str(state["state"])
        self.input_state = dict(state)
        error = state.get("error") if isinstance(state.get("error"), Mapping) else {}
        super().__init__(str(error.get("message") or f"Resident input ended with {self.status}"))


def _retry_after_lifecycle_finalization(operation: Callable[[], Any]) -> Any:
    for attempt in range(40):
        try:
            return operation()
        except ValueError as exc:
            if str(exc) != _LIFECYCLE_FINALIZING_ERROR or attempt == 39:
                raise
            time.sleep(0.25)
    raise AssertionError("unreachable")


def deliver_resident_message(
    *,
    actor: str,
    identity: Mapping[str, Any],
    message: Mapping[str, Any],
    activation_policy: str = "enqueue_and_trigger",
    idempotency_key: str = "",
    business_ref: str = "",
) -> dict[str, Any]:
    """Deliver one immutable Resident message with ``DeliverMessage``."""
    from . import isa_client

    resident_id = str(identity.get("resident_id") or "").strip()
    if not resident_id:
        raise ValueError("resident_id is required")
    arguments: dict[str, Any] = {"resident_id": resident_id, "message": dict(message)}
    clean_policy = str(activation_policy or "").strip()
    if clean_policy:
        arguments["activation_policy"] = clean_policy
    if business_ref:
        arguments["business_ref"] = business_ref
    return dict(isa_client.call(actor, "DeliverMessage", arguments, key=str(idempotency_key or "").strip()) or {})


def _manages_lineage(graph: Mapping[str, Any], actor: str) -> bool:
    """Whether UpdateLineage would pass on the Lineage; a personal AMA is its owner's."""
    from management_portal import permission_system

    lineage_id = str(graph.get("lineage_id") or "")
    owners = {lineage_id: str(graph.get("created_by") or "")} if graph.get("lineage_membership") == "personal" else None
    return lineage_id in permission_system.permitted_ids(actor, "UpdateLineage", "lineage_id", [lineage_id], owners=owners)


def diagnose_lineage_invocations(*, actor: str, lineage_id: str) -> dict[str, Any]:
    """Expose binding identities after active-account and Lineage business checks."""
    if not get_portal_access_store().is_active(actor):
        raise PermissionError("Active Portal identity required")
    service = get_society_service()
    graph = service.lineage.get_lineage(lineage_id, actor)
    if not _manages_lineage(graph, actor):
        raise PermissionError("Lineage management permission required")
    resident_ids = {node["id"] for node in graph.get("nodes") or []}
    bindings = []
    for entry in get_portal_ui_state_store().list_namespace(INVOCATION_ASSOCIATION_NAMESPACE):
        value = entry["value"]
        if not isinstance(value, dict) or value.get("resident_id") not in resident_ids:
            continue
        metadata = value.get("metadata") or {}
        bindings.append({
            "resident_id": value["resident_id"],
            "association_actor": entry["actor"],
            "resident_scope": entry["key"],
            "resource_id": str(metadata.get("resource_id") or ""),
            "recorded_resource_type": str(metadata.get("resource_type") or ""),
        })
    current = service.lineage.get_lineage(lineage_id, actor)
    if not _manages_lineage(current, actor):
        raise PermissionError("Lineage management permission revoked")
    if current.get("revision") != graph.get("revision"):
        raise ValueError("Lineage changed during diagnostic read")
    return {"lineage_id": lineage_id, "revision": graph.get("revision"), "bindings": bindings}


def cancel_resident_invocation(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    """Cancel one Resident input with CancelInput, which stops a running turn,
    withdraws a waiting one and reports a finished one as it is."""

    from . import isa_client

    service = get_society_service().resident
    scoped = service.cancel_resident_service_input(resident_id, actor, message_id)
    if scoped is not None:
        return dict(scoped.get("input_state") or {})
    for _attempt in range(3):
        # ISA owns cancellation access; a conflict means the input moved meanwhile.
        try:
            result = dict(isa_client.call(
                actor, "CancelInput", {"resident_id": resident_id, "message_id": message_id},
            ) or {})
        except InstructionError as exc:
            if exc.code != "instruction.conflict":
                raise
            continue
        return dict(result.get("input_state") or {})
    raise RuntimeError("Resident input state kept changing during cancellation")

EVENT_HEARTBEAT_SECONDS = 15.0
# Input completion and AgentData reply projection are committed independently.
# Real provider turns can leave that projection several seconds behind the
# terminal state, so keep this bounded window long enough to bridge normal
# commit lag without teaching individual callers how to re-read Resident data.
REPLY_VISIBILITY_RETRY_DELAYS = (
    0.1,
    0.2,
    0.4,
    0.8,
    1.5,
    2.0,
    3.0,
    4.0,
    5.0,
    8.0,
    12.0,
    15.0,
)


def _clean_scope(value: object, *, field: str) -> str:
    scope = str(value or "").strip()
    if not scope:
        raise ValueError(f"{field} is required")
    return scope


def _invocation_identity(
    record: Mapping[str, Any],
    *,
    resident_scope: str,
    lineage_scope: str,
    backend: str = "",
    created: bool,
) -> dict[str, Any]:
    return {
        "schema": "tfi.resident-invocation.v1",
        "resident_id": str(record.get("resident_id") or ""),
        "lineage_id": str(record.get("lineage_id") or ""),
        "resident_scope": resident_scope,
        "lineage_scope": lineage_scope,
        "backend": str(backend or record.get("backend") or ""),
        "created": bool(created),
    }


def _configure_invocation_resident(
    service: object,
    record: Mapping[str, Any],
    actor: str,
    *,
    backend: str,
    cwd: str,
) -> dict[str, Any]:
    """Keep a business Resident's title and working directory current.

    Provider, account and runtime settings belong to the Resident; they are
    changed with RestartSession and UpdateResidentSettings, never here.
    """
    resident_id = str(record.get("resident_id") or "").strip()
    if cwd and cwd != record.get("default_cwd") and cwd not in record.get("workspace_paths", []):
        _retry_after_lifecycle_finalization(
            lambda: _bind_working_directory(actor, resident_id, cwd)
        )
    return dict(record)


def _event_is_for_resident(notification: object, resident_id: str) -> bool:
    subject = str(getattr(notification, "subject", "") or "")
    return subject in {
        f"portal.resident.changed.{resident_id}.conversation",
        "portal.gateway.reconnected",
    }


def _terminal_input_state_from_events(
    events: Sequence[Mapping[str, Any]],
    message_id: str,
) -> dict[str, Any]:
    for event in events:
        if str(event.get("event_type") or "") != "resident.input.state.changed":
            continue
        event_payload = event.get("payload")
        input_state = (
            event_payload.get("input_state")
            if isinstance(event_payload, Mapping)
            else None
        )
        if not isinstance(input_state, Mapping):
            continue
        if str(input_state.get("message_id") or "") != message_id:
            continue
        if str(input_state.get("state") or "").casefold() in TERMINAL_INPUT_STATES:
            return dict(input_state)
    return {}


def _snapshot_input(
    service: object,
    *,
    actor: str,
    resident_id: str,
    message_id: str,
) -> tuple[int, dict[str, Any]]:
    snapshot = service.get_resident_conversation_snapshot(
        resident_id,
        actor,
        history_limit=1,
        input_message_id=message_id,
    )
    return (
        int((snapshot or {}).get("cursor") or 0),
        dict(service.get_resident_input_state(resident_id, actor, message_id) or {}),
    )


def _events_after(
    service: object,
    *,
    actor: str,
    resident_id: str,
    cursor: int,
    message_id: str = "",
) -> tuple[int, list[dict[str, Any]], bool]:
    current = cursor
    collected: list[dict[str, Any]] = []
    while True:
        result = dict(
            service.get_resident_events(
                resident_id,
                actor,
                current,
                limit=500,
                input_message_id=message_id,
            )
            or {}
        )
        durable_cursor = int(result.get("cursor") or current)
        events = [
            dict(item)
            for item in result.get("events") or []
            if isinstance(item, Mapping)
        ]
        expected = current + 1
        for event in events:
            event_cursor = int(event.get("cursor") or 0)
            if event_cursor != expected:
                return durable_cursor, [], True
            expected += 1
        if not events:
            return (
                durable_cursor,
                collected,
                durable_cursor > current,
            )
        collected.extend(events)
        current = int(events[-1].get("cursor") or current)
        if current >= durable_cursor:
            return current, collected, False


def wait_for_input(
    *,
    actor: str,
    resident_id: str,
    message_id: str,
    cancellation_requested: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    clean_resident_id = str(resident_id or "").strip()
    clean_message_id = str(message_id or "").strip()
    if not clean_resident_id or not clean_message_id:
        raise ValueError("resident_id and message_id are required")
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    ensure_notification_listener()
    hub = get_notification_hub()
    service = get_society_service().resident
    notification_cursor = hub.sequence
    cancel_sent = False
    cursor, state = _snapshot_input(
        service,
        actor=actor,
        resident_id=clean_resident_id,
        message_id=clean_message_id,
    )
    reconciled_at = time.monotonic()
    while True:
        status = str(state.get("state") or "").strip().casefold()
        if status in TERMINAL_INPUT_STATES:
            if status != "completed":
                raise ResidentInputTerminated(state)
            return state
        if (
            not cancel_sent
            and cancellation_requested is not None
            and cancellation_requested()
        ):
            cancel_sent = True
            cancel_resident_invocation(
                clean_resident_id,
                actor,
                clean_message_id,
            )
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Resident input observation budget exhausted")
        notification_gap = hub.cursor_has_gap(notification_cursor)
        notifications, notification_cursor = hub.wait(
            notification_cursor,
            predicate=lambda item: _event_is_for_resident(item, clean_resident_id),
            timeout=min(1.0 if cancellation_requested is not None else EVENT_HEARTBEAT_SECONDS,
                        max(0.0, deadline - time.monotonic()) if deadline is not None else EVENT_HEARTBEAT_SECONDS),
        )
        reconnect = any(
            str(getattr(item, "subject", "") or "") == "portal.gateway.reconnected"
            for item in notifications
        )
        if notification_gap or reconnect:
            cursor, state = _snapshot_input(
                service,
                actor=actor,
                resident_id=clean_resident_id,
                message_id=clean_message_id,
            )
            reconciled_at = time.monotonic()
            continue
        if not notifications:
            # Cancellation checks are local; quiet transport reconciliation has
            # its own heartbeat so cancellable callers do not poll snapshots.
            if cancellation_requested is not None and time.monotonic() - reconciled_at < EVENT_HEARTBEAT_SECONDS:
                continue
            cursor, state = _snapshot_input(
                service,
                actor=actor,
                resident_id=clean_resident_id,
                message_id=clean_message_id,
            )
            reconciled_at = time.monotonic()
            continue
        durable_cursor, events, cursor_gap = _events_after(
            service,
            actor=actor,
            resident_id=clean_resident_id,
            cursor=cursor,
            message_id=clean_message_id,
        )
        if cursor_gap:
            cursor, state = _snapshot_input(
                service,
                actor=actor,
                resident_id=clean_resident_id,
                message_id=clean_message_id,
            )
            reconciled_at = time.monotonic()
            continue
        cursor = durable_cursor
        terminal = _terminal_input_state_from_events(events, clean_message_id)
        if terminal:
            state = terminal


def submit_invocation(
    *,
    actor: str,
    message: str,
    resident_scope: str,
    lineage_scope: str = "",
    lineage_binding: str = "",
    title: str = "Embedded Resident",
    backend: str = "codex",
    backend_route_id: str = "",
    cwd: str = "",
    runtime_context: str = "",
    runtime_settings: Mapping[str, Any] | None = None,
    context_refs: object = None,
    input_media: object = None,
    additional_skills: Sequence[str] = (),
    message_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    on_assigned: Callable[[dict[str, str]], None] | None = None,
    resident_binding: Mapping[str, str] | None = None,
    workflow_binding: Mapping[str, str] | None = None,
    business_ref: str = "",
    cancellation_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    clean_scope = _clean_scope(resident_scope, field="resident_scope")
    clean_lineage_scope = str(lineage_scope or clean_scope).strip()
    clean_business_ref = str(business_ref or "")
    clean_message_id = str(message_id or "").strip()
    if not clean_message_id:
        if clean_business_ref:
            digest = hashlib.sha256(
                f"{clean_scope}\0{clean_business_ref}".encode("utf-8")
            ).hexdigest()[:32]
            clean_message_id = f"msg_{digest}"
        else:
            clean_message_id = f"msg_{secrets.token_hex(16)}"
    parts: list[dict[str, Any]] = [{"type": "text", "text": str(message or "")}]
    for item in input_media if isinstance(input_media, (list, tuple)) else []:
        if isinstance(item, Mapping):
            media = dict(item)
            part_type = str(
                media.get("type") or media.get("media_type") or ""
            ).strip().casefold()
            if part_type not in {"image", "file"}:
                raise ValueError("Resident input media must be image or file")
            media["type"] = part_type
            parts.append(media)
    preparation = dict(
        actor=actor,
        resident_scope=clean_scope,
        lineage_scope=clean_lineage_scope,
        lineage_binding=lineage_binding,
        title=title,
        backend=backend,
        backend_route_id=backend_route_id,
        cwd=cwd,
        runtime_settings=runtime_settings,
        metadata=metadata,
        resident_binding=resident_binding,
    )
    # Preparing a bound Resident may outlive an RPC observation window. Its
    # creation/migration owners retain their intents; no input has been sent at
    # this boundary. Retry preparation only, never the whole submission.
    while True:
        if cancellation_requested is not None and cancellation_requested():
            raise InterruptedError("Resident invocation cancelled before delivery")
        try:
            identity = ensure_invocation_resident(**preparation)
            break
        except (SocietyServiceUnavailable, ResourceServiceUnavailable):
            pass
        except InstructionError as error:
            if error.code != "instruction.in_progress":
                raise
        time.sleep(1.0)
    resident_id = str(identity.get("resident_id") or "").strip()
    if not resident_id:
        raise RuntimeError("Resident registration returned no resident_id")
    from .lineage_workflows import WorkflowBindings, WorkflowFailed, WorkflowPending, submission_handle, submission_error

    if workflow_binding is not None:
        if any(workflow_binding.get(key) != identity.get(key) for key in ("resident_id", "lineage_id")):
            raise ValueError("Workflow binding does not match the assigned Resident and Lineage")
        prepared = WorkflowBindings.prepare(workflow_binding, request_id=clean_message_id)
    else:
        prepared = submission_handle({"resident_id": resident_id, "lineage_id": identity.get("lineage_id"),
            "request_id": clean_message_id, "instruction": "DeliverMessage",
            "idempotency_key": f"invocation:{clean_message_id}"})
    if on_assigned is not None:
        on_assigned(dict(prepared))
    message_context = (
        dict(context_refs) if isinstance(context_refs, Mapping) else {}
    )
    clean_runtime_context = str(runtime_context or "").strip()
    if clean_runtime_context:
        message_context[CONTEXT_TEXT_FIELD] = clean_runtime_context
    clean_additional_skills = list(dict.fromkeys(
        str(item or "").strip()
        for item in additional_skills
        if str(item or "").strip()
    ))
    if clean_additional_skills:
        message_context[INSTRUCTION_SKILLS_FIELD] = clean_additional_skills
    clean_cwd = str(cwd or "").strip()
    if clean_cwd:
        message_context[WORKING_DIRECTORY_FIELD] = clean_cwd
    if message_context:
        parts.append({"type": "context_reference", "context": message_context})
    if cancellation_requested is not None and cancellation_requested():
        return submission_handle(prepared, state="not_submitted", reason="调用在发送前取消，未执行提交指令")
    if workflow_binding is not None:
        try:
            handle = WorkflowBindings().trigger(dict(workflow_binding), actor=actor,
                prompt=str(message or ""), content_parts=parts[1:], request_id=clean_message_id)
        except (WorkflowPending, WorkflowFailed) as error:
            return dict(error.handle)
        return submission_handle({**prepared, **handle}, state="submitted", message_id=str(handle["message_id"]))
    try:
        acknowledgement = deliver_resident_message(
            actor=actor, identity=identity, idempotency_key=prepared["idempotency_key"],
            business_ref=clean_business_ref,
            message={"message_id": clean_message_id, "role": "user", "content_parts": parts},
        )
    except PermissionError:
        raise
    except (InstructionError, SocietyServiceUnavailable, ResourceServiceUnavailable, OSError) as error:
        return resolve_invocation_submission(actor=actor, handle=submission_error(prepared, error))
    actual = str(acknowledgement.get("message_id") or "")
    if actual and acknowledgement.get("resident_id", resident_id) == resident_id:
        return submission_handle(prepared, state="submitted", message_id=actual)
    return resolve_invocation_submission(actor=actor, handle=prepared)


def resolve_invocation_submission(*, actor: str, handle: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the original submission through ISA reads; never send or cancel."""
    from . import isa_client
    from .lineage_workflows import WorkflowBindings, submission_execution, submission_handle, submission_was_rejected

    if handle.get("event_source_id") or handle.get("effect_id") or handle.get("instruction") == "TriggerEvent":
        return WorkflowBindings().resolve(handle, actor=actor)
    current = submission_handle(handle)
    if not current.get("resident_id"):
        return submission_handle(current, reason="恢复句柄缺少目标 Resident")
    record = {}
    try:
        record = submission_execution(current, actor=actor, instruction="DeliverMessage")
        if record:
            current["instruction_id"] = record["instruction_id"]
        references = record.get("references") or {}
        if references.get("resident_id") and references["resident_id"] != current["resident_id"]:
            return submission_handle(current, reason="提交回执目标与准备句柄不匹配")
        message_id = str(references.get("message_id") or
                         (handle.get("message_id") if handle.get("submission_state") == "submitted" else "") or
                         current.get("request_id") or "")
        if not message_id:
            return submission_handle(current, reason="缺少原始请求定位，不能选择当前活动输入")
        try:
            state = isa_client.call(actor, "GetInputState", {
                "resident_id": current["resident_id"], "message_id": message_id,
            }).get("input_state") or {}
        except InstructionError as error:
            if error.code != "instruction.not_found":
                raise
            state = {}
        if state.get("message_id") == message_id and state.get("state"):
            return submission_handle(current, state="submitted", message_id=message_id)
        error = record.get("error") or {}
        if submission_was_rejected(record):
            return submission_handle(current, state="not_submitted", reason=error.get("message") or "原指令明确未提交")
        return submission_handle(current, reason="原输入尚无确认回执；未找到不代表未受理")
    except (InstructionError, SocietyServiceUnavailable, ResourceServiceUnavailable, OSError) as error:
        return submission_handle(current, reason=f"原提交观察暂不可确认：{error}")


def lookup_invocation_resident(*, actor: str, resident_scope: str, lineage_binding: str = "") -> dict[str, str]:
    """Read an existing discussion identity without creating or configuring it."""
    scope = _clean_scope(resident_scope, field="resident_scope")
    row = get_portal_ui_state_store().get(actor, INVOCATION_ASSOCIATION_NAMESPACE, scope)
    value = (row or {}).get("value") or {}
    resident_id = str(value.get("resident_id") or "")
    if not resident_id:
        return {}
    record = get_society_service().resident.get_resident(resident_id, actor)
    lineage_id = str(record.get("lineage_id") or "")
    if not lineage_id or (lineage_binding and lineage_binding != lineage_id):
        raise ValueError("Discussion Resident is bound to a different Lineage")
    return {"resident_id": resident_id, "lineage_id": lineage_id}


def _create_invocation_resident(
    service: Any, store: Any, *, actor: str, scope: str,
    lineage_id: str, arguments: dict[str, Any],
) -> str:
    from .resident_creation import create

    return create(
        actor=actor, lineage_id=lineage_id, arguments=arguments, service=service,
        load=lambda: (store.get(actor, INVOCATION_CREATION_NAMESPACE, scope) or {}).get("value"),
        save=lambda state: store.put(actor, INVOCATION_CREATION_NAMESPACE, scope, state),
    )


def _bind_working_directory(actor: str, resident_id: str, cwd: str) -> None:
    """A bound business Resident works in the directory the business names (e.g. a WALL project root)."""
    from . import isa_client

    target = Path(cwd).expanduser().resolve(strict=False)
    resident = dict(isa_client.call(actor, "GetResident", {"resident_id": resident_id}).get("resident") or {})
    bound = {
        Path(str(item.get("path") or "")).expanduser().resolve(strict=False)
        for item in resident.get("workspaces") or []
        if isinstance(item, Mapping) and str(item.get("path") or "").strip()
    }
    if target in bound:
        return
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:24]
    isa_client.call(actor, "BindWorkspace", {"resident_id": resident_id, "path": str(target)},
                    key=f"invocation-workspace:{resident_id}:{digest}")


def ensure_invocation_resident(
    *,
    actor: str,
    resident_scope: str,
    lineage_scope: str = "",
    title: str = "Embedded Resident",
    backend: str = "codex",
    backend_route_id: str = "",
    cwd: str = "",
    runtime_settings: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    resident_binding: Mapping[str, str] | None = None,
    lineage_binding: str = "",
) -> dict[str, Any]:
    """Resolve a Resident within an existing Lineage, never create a replacement team."""

    clean_scope = _clean_scope(resident_scope, field="resident_scope")
    clean_lineage_scope = _clean_scope(
        lineage_scope or clean_scope,
        field="lineage_scope",
    )
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后创建 Resident invocation")
    # Invocation uses an existing Lineage. Its domain and HATG instructions
    # authorize the actual target; project creation eligibility is unrelated.
    clean_cwd = str(cwd or "").strip()
    store = get_portal_ui_state_store()
    service = get_society_service()
    presentation_metadata = dict(metadata or {})

    if resident_binding is not None:
        resident_id = str(resident_binding.get("resident_id") or "")
        record = service.resident.get_resident(resident_id, actor_id)
        if not record.get("lineage_id") or record["lineage_id"] != resident_binding.get("lineage_id"):
            raise ValueError("Resident 的当前 Lineage 与业务绑定不一致；请修复权威绑定")
        if clean_cwd and str(record.get("backend") or "") != "provider_api" and Path(clean_cwd).is_dir():
            _bind_working_directory(actor_id, resident_id, clean_cwd)
        return dict(resident_binding)

    clean_backend = provider_name(backend)

    with store.association_lock(
        actor_id,
        INVOCATION_ASSOCIATION_NAMESPACE,
        clean_scope,
    ):
        association = store.get(
            actor_id,
            INVOCATION_ASSOCIATION_NAMESPACE,
            clean_scope,
        )
        association_value = (
            association.get("value")
            if isinstance(association, Mapping)
            and isinstance(association.get("value"), Mapping)
            else {}
        )
        existing_resident_id = str(
            association_value.get("resident_id") or ""
        ).strip()
        if existing_resident_id:
            existing = dict(
                service.resident.get_resident(existing_resident_id, actor_id)
                or {}
            )
            if not existing.get("lineage_id"):
                raise ValueError("Resident 查询缺少当前 Lineage；不能以缓存或新团队替代")
            if lineage_binding and existing["lineage_id"] != lineage_binding:
                raise ValueError("Discussion Resident is bound to a different Lineage")
            # The Resident's own provider, account and settings decide how it
            # runs; a caller's values only seed a Resident when it is created.
            current_backend = str(existing.get("backend") or clean_backend)
            configured = _configure_invocation_resident(
                service, existing, actor_id, backend=current_backend,
                cwd=clean_cwd,
            )
            store.put(
                actor_id,
                INVOCATION_ASSOCIATION_NAMESPACE,
                clean_scope,
                {
                    "resident_id": existing_resident_id,
                    "lineage_id": str(configured.get("lineage_id") or ""),
                    "lineage_scope": clean_lineage_scope,
                    "backend": current_backend,
                    "metadata": presentation_metadata,
                },
            )
            if not lineage_binding:
                store.put(
                    actor_id,
                    INVOCATION_LINEAGE_NAMESPACE,
                    clean_lineage_scope,
                    {"lineage_id": configured["lineage_id"]},
                )
            return _invocation_identity(
                configured,
                resident_scope=clean_scope,
                lineage_scope=clean_lineage_scope,
                backend=current_backend,
                created=False,
            )

        registration: dict[str, Any] = {
            "title": str(title or "Embedded Resident"),
            "backend": clean_backend,
            "backend_route_id": str(backend_route_id or ""),
            "runtime_settings": dict(runtime_settings or {}),
            "mode": "new_session",
        }
        lineage_association = {} if lineage_binding else store.get(actor_id, INVOCATION_LINEAGE_NAMESPACE, clean_lineage_scope)
        lineage_value = (
            lineage_association.get("value")
            if isinstance(lineage_association, Mapping)
            and isinstance(lineage_association.get("value"), Mapping)
            else {}
        )
        resident_actor = actor_principal(actor_id).kind == "resident"
        if lineage_binding:
            lineage_id = lineage_binding
            service.lineage.get_lineage(lineage_id, actor_id)
        elif resident_actor:
            caller = service.resident.get_resident(actor_id, actor_id)
            lineage_id = str(caller.get("lineage_id") or "").strip()
            if not lineage_id:
                raise PermissionError("Resident invocation caller has no active Lineage")
        else:
            lineage_id = str(lineage_value.get("lineage_id") or "").strip()
        if not lineage_id:
            raise ValueError(
                "Resident invocation 缺少权威 Lineage 绑定；请由业务入口显式绑定已有团队，"
                "不会自动新建团队"
            )
        resident_id = _create_invocation_resident(
            service, store, actor=actor_id, scope=clean_scope,
            lineage_id=lineage_id, arguments=registration,
        )
        record = dict(service.resident.get_resident(resident_id, actor_id) or {})
        if lineage_binding and record.get("lineage_id") != lineage_binding:
            raise ValueError("Discussion Resident is bound to a different Lineage")
        lineage_id = str(record.get("lineage_id") or "").strip()
        if not resident_id or not lineage_id:
            raise RuntimeError("Resident registration returned no canonical identity")
        store.put(
            actor_id,
            INVOCATION_ASSOCIATION_NAMESPACE,
            clean_scope,
            {
                "resident_id": resident_id,
                "lineage_id": lineage_id,
                "lineage_scope": clean_lineage_scope,
                "backend": clean_backend,
                "metadata": presentation_metadata,
            },
        )
        if not lineage_binding:
            store.put(
                actor_id,
                INVOCATION_LINEAGE_NAMESPACE,
                clean_lineage_scope,
                {"lineage_id": lineage_id},
            )
        record = _configure_invocation_resident(
            service, record, actor_id, backend=clean_backend, cwd=clean_cwd,
            )
        return _invocation_identity(
            record,
            resident_scope=clean_scope,
            lineage_scope=clean_lineage_scope,
            backend=clean_backend,
            created=True,
        )


def _result_from_input_state(
    state: Mapping[str, Any],
    handle: Mapping[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    service = get_society_service().resident
    resident_id = str(handle.get("resident_id") or "")
    replies = []
    response: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    for reply_id in state.get("reply_message_ids") or []:
        message = dict(service.get_resident_message(resident_id, actor, str(reply_id)) or {})
        replies.append(message)
        if isinstance(message.get("usage"), Mapping):
            usage = dict(message["usage"])
        for part in message.get("content_parts") or []:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "artifact_reference" and part.get("artifact_id"):
                artifact = service.get_resident_artifact(
                    resident_id,
                    actor,
                    str(part["artifact_id"]),
                )
                if isinstance(artifact.get("value"), Mapping):
                    response = dict(artifact["value"])
    text = "\n".join(
        str(part.get("text") or "")
        for reply in replies
        for part in reply.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
    return {
        "resident_id": resident_id,
        "lineage_id": str(handle.get("lineage_id") or ""),
        "message_id": str(handle.get("message_id") or ""),
        "message": text,
        "messages": replies,
        "response": response,
        "usage": usage,
    }


def _reply_projection_is_complete(
    state: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    reply_ids = [
        str(reply_id or "").strip()
        for reply_id in state.get("reply_message_ids") or []
        if str(reply_id or "").strip()
    ]
    if not reply_ids:
        return False
    messages = result.get("messages")
    if not isinstance(messages, list) or len(messages) != len(reply_ids):
        return False
    for reply_id, message in zip(reply_ids, messages):
        if not isinstance(message, Mapping):
            return False
        if str(message.get("message_id") or "").strip() != reply_id:
            return False
        if not list(message.get("content_parts") or []):
            return False
    return True


def observe_invocation(*, actor: str, resident_id: str, message_id: str,
                       lineage_id: str = "", input_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read one input and its complete reply projection without submitting or waiting."""
    if not resident_id or not message_id:
        raise ValueError("resident_id and message_id are required")
    handle = {"resident_id": resident_id, "lineage_id": lineage_id, "message_id": message_id}
    state = dict(input_state) if input_state is not None else dict(
        get_society_service().resident.get_resident_input_state(resident_id, actor, message_id))
    status = state.get("state")
    if status in {"failed", "cancelled"}:
        raise ResidentInputTerminated(state)
    result = None
    if status == "completed":
        try:
            result = _result_from_input_state(state, handle, actor=actor)
        except KeyError:
            # Terminal state, messages and artifacts are separate projections.
            # A missing reply is pending only while the original input still
            # exists and is accessible to this observer. Never mask revocation
            # or deletion behind an observation timeout.
            state = dict(get_society_service().resident.get_resident_input_state(
                resident_id, actor, message_id))
            if state.get("state") in {"failed", "cancelled"}:
                raise ResidentInputTerminated(state)
    if result is not None and not _reply_projection_is_complete(state, result):
        result = None
    return {"input_state": state, "result": result}


def resume_and_wait(
    *,
    actor: str,
    resident_id: str,
    message_id: str,
    lineage_id: str = "",
    cancellation_requested: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    if not str(message_id or "").strip():
        raise ValueError("A confirmed message_id is required before waiting or cancellation")
    handle = {
        "resident_id": str(resident_id or ""),
        "lineage_id": str(lineage_id or ""),
        "message_id": str(message_id or ""),
    }
    state = wait_for_input(
        actor=actor,
        resident_id=handle["resident_id"],
        message_id=handle["message_id"],
        cancellation_requested=cancellation_requested,
        timeout_seconds=timeout_seconds,
    )
    observed = observe_invocation(actor=actor, **handle, input_state=state)
    for delay in REPLY_VISIBILITY_RETRY_DELAYS:
        if observed["result"] is not None:
            break
        time.sleep(delay)
        observed = observe_invocation(actor=actor, **handle)
    if observed["result"] is None:
        raise TimeoutError("Resident reply projection observation budget exhausted")
    return observed["result"]


def invoke_and_wait(
    *,
    actor: str,
    message: str,
    resident_scope: str,
    lineage_scope: str = "",
    lineage_binding: str = "",
    title: str = "Embedded Resident",
    backend: str = "codex",
    backend_route_id: str = "",
    cwd: str = "",
    runtime_context: str = "",
    runtime_settings: Mapping[str, Any] | None = None,
    context_refs: object = None,
    input_media: object = None,
    additional_skills: Sequence[str] = (),
    message_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    on_submitted: Callable[[dict[str, str]], None] | None = None,
    on_assigned: Callable[[dict[str, str]], None] | None = None,
    resident_binding: Mapping[str, str] | None = None,
    workflow_binding: Mapping[str, str] | None = None,
    business_ref: str = "",
) -> dict[str, Any]:
    handle = submit_invocation(
        workflow_binding=workflow_binding,
        business_ref=business_ref,
        resident_binding=resident_binding,
        actor=actor,
        message=message,
        resident_scope=resident_scope,
        lineage_scope=lineage_scope,
        lineage_binding=lineage_binding,
        title=title,
        backend=backend,
        backend_route_id=backend_route_id,
        cwd=cwd,
        runtime_context=runtime_context,
        runtime_settings=runtime_settings,
        context_refs=context_refs,
        input_media=input_media,
        additional_skills=additional_skills,
        message_id=message_id,
        metadata=metadata,
        on_assigned=on_assigned,
        cancellation_requested=cancellation_requested,
    )
    if handle.get("submission_state") in {"pending", "not_submitted"}:
        from .lineage_workflows import WorkflowPending, WorkflowFailed

        error_type = WorkflowPending if handle["submission_state"] == "pending" else WorkflowFailed
        raise error_type(handle, {})
    if on_submitted is not None:
        on_submitted(dict(handle))
    return resume_and_wait(
        actor=actor,
        resident_id=str(handle.get("resident_id") or ""),
        lineage_id=str(handle.get("lineage_id") or ""),
        message_id=str(handle.get("message_id") or ""),
        cancellation_requested=cancellation_requested,
    )


def run_resident_turn(
    *,
    actor: str,
    prompt: str,
    cwd: str,
    resident_scope: str,
    lineage_scope: str = "",
    title: str = "Embedded Agent",
    backend_route_id: str = "",
    additional_skills: Sequence[str] = (),
    allow_subagents: bool = False,
    runtime_options: Mapping[str, Any] | None = None,
    runtime_context: str = "",
    input_media: object = None,
    message_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    on_submitted: Callable[[dict[str, str]], None] | None = None,
    on_assigned: Callable[[dict[str, str]], None] | None = None,
    resident_binding: Mapping[str, str] | None = None,
    workflow_binding: Mapping[str, str] | None = None,
    business_ref: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """One turn of a Resident, waiting for its reply.

    With ``resident_binding`` the bound Resident runs on its own Provider,
    account and settings.  Without it, ``provider`` and the other creation
    values create the scope's embedded Resident the first time; afterwards
    that Resident's own configuration decides how it runs.
    """
    if resident_binding is None and not str(provider or "").strip():
        raise ValueError("Creating an embedded Resident needs its provider")
    settings = dict(runtime_options or {})
    settings["allow_subagents"] = bool(allow_subagents)
    return invoke_and_wait(
        workflow_binding=workflow_binding,
        business_ref=business_ref,
        resident_binding=resident_binding,
        actor=actor, message=prompt, resident_scope=resident_scope,
        lineage_scope=lineage_scope, title=title,
        backend=provider, backend_route_id=backend_route_id,
        cwd=cwd, runtime_context=runtime_context, runtime_settings=settings,
        input_media=input_media, additional_skills=additional_skills,
        message_id=message_id, metadata=metadata,
        cancellation_requested=cancellation_requested, on_submitted=on_submitted,
        on_assigned=on_assigned,
    )


def run_shared_search_turn(
    *,
    job: Mapping[str, Any],
    surface: str,
    prompt: str,
    timeout_seconds: float = 360.0,
    cancellation_requested: Callable[[], bool] | None = None,
    on_assigned: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    from .shared_search_invocation import run_shared_search_turn as run_isolated

    return run_isolated(
        job=job, surface=surface, prompt=prompt, timeout_seconds=timeout_seconds,
        cancellation_requested=cancellation_requested,
        on_assigned=on_assigned,
    )


def run_resident_completion(
    *,
    actor: str,
    user_prompt: str,
    resident_scope: str,
    lineage_scope: str = "",
    lineage_binding: str = "",
    title: str = "Embedded Model",
    parameters: Mapping[str, Any] | None = None,
    messages: Sequence[Mapping[str, Any]] | None = None,
    timeout_seconds: float = 360,
    max_retries: int = 0,
    metadata: Mapping[str, Any] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
    on_submitted: Callable[[dict[str, str]], None] | None = None,
    on_assigned: Callable[[dict[str, str]], None] | None = None,
    resident_binding: Mapping[str, str] | None = None,
    workflow_binding: Mapping[str, str] | None = None,
    business_ref: str = "",
    provider_id: str = "",
    model: str = "",
    system_prompt: str = "",
) -> dict[str, Any]:
    """A Model API completion by a Resident, waiting for its reply.

    With ``resident_binding`` the bound provider_api Resident uses its own
    execution configuration.  Without it, ``provider_id``, ``model``,
    ``system_prompt``, ``parameters`` and the other configuration values create the
    scope's embedded Resident the first time; afterwards its own configuration
    decides how it runs, so a later call's values do not apply.
    """
    if resident_binding is None and not (str(provider_id or "").strip() and str(model or "").strip()):
        raise ValueError("Creating an embedded Model API Resident needs provider_id and model")
    return invoke_and_wait(
        workflow_binding=workflow_binding,
        business_ref=business_ref,
        resident_binding=resident_binding,
        actor=actor, message=user_prompt, resident_scope=resident_scope,
        lineage_scope=lineage_scope, lineage_binding=lineage_binding,
        title=title, 
         backend="provider_api",
        backend_route_id=str(provider_id or ""),
        runtime_settings={
            "model": str(model or ""),
            "prompt_context": {"system_prompt": str(system_prompt or ""),
                               "messages": [dict(item) for item in messages or []]},
            "backend_options": {"parameters": dict(parameters or {}),
                                "timeout_seconds": float(timeout_seconds), "max_retries": int(max_retries)},
        },
        metadata=metadata,
        cancellation_requested=cancellation_requested, on_submitted=on_submitted,
        on_assigned=on_assigned,
    )
