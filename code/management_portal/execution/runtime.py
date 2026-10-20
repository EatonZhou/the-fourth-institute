from __future__ import annotations

from .contracts import TOKEN_USAGE_KEYS, execution_timestamp

from .provider_catalog import AGENT_PROVIDERS

import logging
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from .backends import (
    AgentSessionService,
    Backend,
    BackendContext,
    BackendInvocation,
    BackendResult,
    ExecutionServiceAdapter,
    default_execution_services,
)
from .contracts import ExecutionRecoveryDeferred, ExecutionRequest
from .injection_plan import plan_injection
from .config import execution_state_root
from .harness import AgentHarness, default_agent_harness
from .session_manager import SessionManager
from .agent_data_store import utc_now
from .agent_data_projection import AgentDataProjector
from .recovery_messages import RECOVERY_INPUT_EVENT, stamp_recovery_input

LOGGER = logging.getLogger(__name__)


class ExecutionCancelled(InterruptedError):
    pass




def _positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _clean_context_usage(value: object) -> dict[str, int]:
    data = value if isinstance(value, dict) else {}
    return {
        key: number
        for key in TOKEN_USAGE_KEYS
        if (number := _positive_int(data.get(key))) > 0
    }




@dataclass
class RuntimeContext:
    accepted_at: str = ""
    emit_event: Callable[[str, dict[str, Any]], None] | None = None
    cancellation_requested: Callable[[], bool] | None = None
    handover_requested: Callable[[], bool] | None = None
    reassign_backend_route: Callable[[str], dict[str, Any]] | None = None
    checkpoint_provider: Callable[[dict[str, Any]], None] | None = None
    recovery_checkpoint: dict[str, Any] | None = None
    turn_control_provider: (
        Callable[[], dict[str, Any] | None] | None
    ) = None
    turn_control_result_callback: (
        Callable[[dict[str, Any], bool, str], None] | None
    ) = None

    def emit(self, event_type: str, details: dict[str, Any] | None = None) -> None:
        if self.emit_event is not None:
            self.emit_event(str(event_type or ""), dict(details or {}))

    def is_cancelled(self) -> bool:
        return bool(
            self.cancellation_requested and self.cancellation_requested()
        )

    def is_handover(self) -> bool:
        return bool(
            self.handover_requested and self.handover_requested()
        )

    def checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if self.checkpoint_provider is not None:
            self.checkpoint_provider(dict(checkpoint))


@dataclass
class _ActiveExecution:
    service: ExecutionServiceAdapter
    provider: str
    runtime_options: dict[str, Any]
    backend_session_id: str = ""
    backend_route_id: str = ""
    turn_id: str = ""
    checkpoint_target: tuple[str, str, str] = ("", "", "")
    context_usage: dict[str, int] = field(default_factory=dict)
    model_context_window: int = 0
    context_usage_at: str = ""
    provider_recovery_state: dict[str, Any] = field(default_factory=dict)
    turn_start_reason: str = "initial"
    # The Harness features chosen for this Execution (None: not planned yet).
    # A recovered attempt runs the same plan, so the Provider gets the same
    # prompt and tools again.
    harness_plan: dict[str, Any] | None = None


class ExecutionRuntime:
    """Dispatch a generic AI request to one execution service."""

    def __init__(
        self,
        *,
        services: dict[str, ExecutionServiceAdapter] | None = None,
        agent_providers: dict[str, Backend] | None = None,
        agent_harness: AgentHarness | None = None,
        session_manager: SessionManager | None = None,
        agent_data_projector: AgentDataProjector | None = None,
        state_root: Path | str | None = None,
    ) -> None:
        state_root = state_root or execution_state_root()
        resolved_agent_harness = agent_harness or default_agent_harness(
            state_root=state_root,
        )
        self.services = dict(
            services
            or default_execution_services(
                agent_harness=resolved_agent_harness,
            )
        )
        if agent_providers is not None:
            self.services["agent_session"] = AgentSessionService(
                agent_providers,
                harness=resolved_agent_harness,
            )
        self.agent_data_projector = agent_data_projector or AgentDataProjector()
        self.sessions = session_manager or SessionManager(
            agent_data=self.agent_data_projector.store,
        )
        self._active: dict[str, _ActiveExecution] = {}
        self._lock = threading.RLock()
        self._execution_slots: set[str] = set()
        self._execution_slot_changed = threading.Condition(self._lock)

    def execute(
        self,
        request: ExecutionRequest,
        context: RuntimeContext | None = None,
    ) -> BackendResult:
        request.validate()
        runtime_context = context or RuntimeContext()
        execution_id = request.execution_id
        abandoned = isinstance((runtime_context.recovery_checkpoint or {}).get("abandoned"), dict)
        waited_for_predecessor = False
        with self._execution_slot_changed:
            while execution_id in self._execution_slots:
                waited_for_predecessor = True
                if runtime_context.is_cancelled() and not abandoned:
                    raise ExecutionCancelled(
                        "execution was cancelled while waiting for its local predecessor"
                    )
                if runtime_context.is_handover():
                    raise ExecutionRecoveryDeferred(
                        "execution claim changed while waiting for its local predecessor"
                    )
                self._execution_slot_changed.wait(0.1)
            if runtime_context.is_cancelled() and not abandoned:
                raise ExecutionCancelled(
                    "execution was cancelled before ExecutionRuntime started"
                )
            if waited_for_predecessor and runtime_context.is_handover():
                raise ExecutionRecoveryDeferred(
                    "execution claim changed before ExecutionRuntime started"
                )
            self._execution_slots.add(execution_id)
        try:
            return self._execute_once(request, runtime_context)
        finally:
            with self._execution_slot_changed:
                self._execution_slots.discard(execution_id)
                self._execution_slot_changed.notify_all()

    def _execute_once(
        self,
        request: ExecutionRequest,
        context: RuntimeContext,
    ) -> BackendResult:
        request.validate()
        try:
            service = self.services[request.service_type]
        except KeyError as exc:
            raise ValueError(
                f"unsupported AI execution service: {request.service_type}"
            ) from exc
        runtime_context = context or RuntimeContext()
        recovery_checkpoint = dict(
            runtime_context.recovery_checkpoint or {}
        )
        if isinstance(recovery_checkpoint.get("abandoned"), dict):
            self._end_abandoned(service, request, runtime_context, recovery_checkpoint)
        if runtime_context.is_cancelled():
            raise ExecutionCancelled(
                "execution was cancelled before ExecutionRuntime started"
            )
        session = self.sessions.prepare(
            request,
            recovery=bool(recovery_checkpoint),
        )
        if recovery_checkpoint:
            if str(recovery_checkpoint.get("provider") or "") != request.provider:
                raise ValueError(
                    "execution checkpoint belongs to another provider"
                )
            recovered_session_id = str(
                recovery_checkpoint.get("backend_session_id") or ""
            )
            recovered_route_id = str(
                recovery_checkpoint.get("backend_route_id") or ""
            )
            if recovered_session_id:
                session = replace(
                    session,
                    backend_session_id=recovered_session_id,
                    backend_route_id=(
                        recovered_route_id or session.backend_route_id
                    ),
                )
        system_instructions = request.system_instructions
        injection_parts = system_instructions.parts
        first_injection = session.context_injection_count == 0 or session.is_new_backend_session
        compacted_at = (
            self.sessions.context_compacted_at(session, agent_id=request.agent_id)
            if not first_injection and any(
                part.timing == "session_start_and_after_compaction" for part in injection_parts
            )
            else ""
        )
        injection_at = utc_now()
        injection = plan_injection(
            injection_parts,
            request.prompt,
            generation=(
                request.service_type in {"agent_session", "chat_completion"}
                and request.operation in {"turn", "generate"}
            ),
            first_injection=first_injection,
            injected_at=session.context_injected_at,
            compacted_at=compacted_at,
            system_already_attached=bool(recovery_checkpoint.get("context_attached")),
            system_context_survives_compaction=bool(
                AGENT_PROVIDERS.get(request.provider)
                and AGENT_PROVIDERS[request.provider].system_context_survives_compaction
            ),
        )
        instructions_fingerprint = injection.fingerprint
        attach_system_context = injection.attach_system_context
        # Any due part, system or prompt, counts as this session's injection.
        context_attached_now = injection.attached
        invocation = BackendInvocation(
            request=request,
            session=session,
            user_prompt=injection.user_prompt,
            system_context=injection.system_context,
            session_system_context=injection.session_system_context,
            attach_system_context=attach_system_context,
            is_new_backend_session=session.is_new_backend_session,
            recovery_checkpoint=recovery_checkpoint,
            available_harness_tool_names=frozenset(
                session.harness_tool_names
            ),
        )
        def emit(event_type: str, details: dict[str, Any]) -> None:
            if event_type == RECOVERY_INPUT_EVENT:
                details = stamp_recovery_input(request.execution_id, details)
            self.agent_data_projector.observe_runtime_event(
                request.execution_id,
                event_type,
                details,
            )
            runtime_context.emit(event_type, details)

        def checkpoint_payload(
            active: _ActiveExecution,
        ) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "provider": request.provider,
                "operation": request.operation,
                "backend_session_id": active.backend_session_id,
                "backend_route_id": active.backend_route_id,
                "turn_id": active.turn_id,
                "turn_start_reason": active.turn_start_reason,
                "context_attached": bool(
                    active.turn_id
                    and (
                        context_attached_now
                        or recovery_checkpoint.get("context_attached")
                    )
                ),
                "context_fingerprint": (
                    instructions_fingerprint
                    if active.turn_id and context_attached_now
                    else session.context_fingerprint
                ),
                "resumable": bool(
                    (
                        request.operation == "turn"
                        and active.turn_id
                        and (
                            active.backend_session_id
                            or (AGENT_PROVIDERS.get(request.provider) and AGENT_PROVIDERS[request.provider].resume_without_session)
                        )
                    )
                    or active.provider_recovery_state.get("kind")
                    == "thread_writer_busy"
                ),
            }
            if active.context_usage:
                payload["context_usage"] = dict(active.context_usage)
                payload["context_usage_at"] = active.context_usage_at
            if active.model_context_window:
                payload["model_context_window"] = (
                    active.model_context_window
                )
            if active.provider_recovery_state:
                payload["provider_recovery_state"] = dict(
                    active.provider_recovery_state
                )
            if active.harness_plan is not None:
                payload["harness_plan"] = dict(active.harness_plan)
            return payload

        def set_interrupt_target(
            backend_session_id: str,
            turn_id: str,
            backend_route_id: str,
        ) -> None:
            with self._lock:
                active = self._active.get(request.execution_id)
                if active is None:
                    return
                if backend_session_id:
                    active.backend_session_id = backend_session_id
                if backend_route_id:
                    active.backend_route_id = backend_route_id
                if turn_id:
                    active.turn_id = turn_id
                checkpoint_session_id = active.backend_session_id
                checkpoint_route_id = active.backend_route_id
                checkpoint_turn_id = active.turn_id
                checkpoint_target = (
                    checkpoint_session_id,
                    checkpoint_route_id,
                    checkpoint_turn_id,
                )
                if checkpoint_target == active.checkpoint_target:
                    return
                active.checkpoint_target = checkpoint_target
                checkpoint = checkpoint_payload(active)
            runtime_context.checkpoint(checkpoint)

        def update_context_usage(
            usage: dict[str, int],
            model_context_window: int,
        ) -> None:
            normalized = _clean_context_usage(usage)
            context_window = _positive_int(model_context_window)
            if not normalized and not context_window:
                return
            with self._lock:
                active = self._active.get(request.execution_id)
                if active is None:
                    return
                if (
                    active.context_usage == normalized
                    and active.model_context_window == context_window
                ):
                    return
                active.context_usage = normalized
                active.model_context_window = context_window
                active.context_usage_at = execution_timestamp()
                checkpoint = checkpoint_payload(active)
            runtime_context.checkpoint(checkpoint)

        def set_provider_recovery_state(state: dict[str, Any]) -> None:
            normalized = dict(state or {})
            with self._lock:
                active = self._active.get(request.execution_id)
                if active is None or active.provider_recovery_state == normalized:
                    return
                active.provider_recovery_state = normalized
                checkpoint = checkpoint_payload(active)
            runtime_context.checkpoint(checkpoint)

        def set_harness_plan(plan: dict[str, Any]) -> None:
            normalized = dict(plan or {})
            with self._lock:
                active = self._active.get(request.execution_id)
                if active is None or active.harness_plan == normalized:
                    return
                active.harness_plan = normalized
                checkpoint = checkpoint_payload(active)
            runtime_context.checkpoint(checkpoint)

        def set_turn_start_reason(reason: str) -> None:
            with self._lock:
                active = self._active.get(request.execution_id)
                if active is None or active.turn_start_reason == reason:
                    return
                active.turn_start_reason = reason
                checkpoint = checkpoint_payload(active)
            runtime_context.checkpoint(checkpoint)

        turn_start_reason = str(recovery_checkpoint.get("turn_start_reason") or (
            "provider_recovery" if recovery_checkpoint.get("turn_id") else "initial"
        ))
        with self._lock:
            self._active[request.execution_id] = _ActiveExecution(
                service=service,
                provider=request.provider,
                runtime_options=dict(request.runtime_options),
                backend_session_id=session.backend_session_id,
                backend_route_id=session.backend_route_id,
                turn_id=str(recovery_checkpoint.get("turn_id") or ""),
                turn_start_reason=turn_start_reason,
                harness_plan=(
                    dict(recovery_checkpoint["harness_plan"])
                    if isinstance(recovery_checkpoint.get("harness_plan"), dict)
                    else None
                ),
                checkpoint_target=(
                    str(
                        recovery_checkpoint.get("backend_session_id") or ""
                    ),
                    str(
                        recovery_checkpoint.get("backend_route_id") or ""
                    ),
                    str(recovery_checkpoint.get("turn_id") or ""),
                ),
                context_usage=_clean_context_usage(
                    recovery_checkpoint.get("context_usage")
                ),
                model_context_window=_positive_int(
                    recovery_checkpoint.get("model_context_window")
                ),
                context_usage_at=str(
                    recovery_checkpoint.get("context_usage_at") or ""
                ),
                provider_recovery_state=dict(
                    recovery_checkpoint.get("provider_recovery_state")
                    if isinstance(
                        recovery_checkpoint.get("provider_recovery_state"),
                        dict,
                    )
                    else {}
                ),
            )
        try:
            self.agent_data_projector.begin(
                request,
                session,
                created_at=runtime_context.accepted_at,
                recovery=bool(recovery_checkpoint.get("resumable")),
            )
            if request.service_type == "chat_completion":
                self.agent_data_projector.observe_invocation(
                    request.execution_id,
                    invocation,
                    [],
                )
            if not recovery_checkpoint:
                emit(
                    "runtime_started",
                    {
                        "service_type": request.service_type,
                        "provider": request.provider,
                        "operation": request.operation,
                        "session_mode": session.mode,
                        "binding_id": session.binding_id,
                        "is_new_backend_session": session.is_new_backend_session,
                        "backend_route_id": session.backend_route_id,
                    },
                )
                emit(
                    "context_prepared",
                    {
                        "attached": attach_system_context,
                        "fingerprint": (
                            instructions_fingerprint
                            if attach_system_context
                            else session.context_fingerprint
                        ),
                        "parts": list(injection.attached_parts),
                    },
                )
                emit(
                    "service_started",
                    {
                        "service_type": request.service_type,
                        "provider": request.provider,
                        "operation": request.operation,
                    },
                )
            if bool(recovery_checkpoint.get("provider_completed")):
                result = BackendResult(
                    output=dict(
                        recovery_checkpoint.get("output")
                        if isinstance(
                            recovery_checkpoint.get("output"),
                            dict,
                        )
                        else {}
                    ),
                    backend_session_id=str(
                        recovery_checkpoint.get("backend_session_id")
                        or ""
                    ),
                    backend_route_id=str(
                        recovery_checkpoint.get("backend_route_id") or ""
                    ),
                    usage=dict(
                        recovery_checkpoint.get("usage")
                        if isinstance(
                            recovery_checkpoint.get("usage"),
                            dict,
                        )
                        else {}
                    ),
                    available_harness_tool_names=frozenset(
                        str(name or "").strip()
                        for name in (
                            recovery_checkpoint.get(
                                "available_harness_tool_names"
                            )
                            or ()
                        )
                        if str(name or "").strip()
                    ),
                )
                emit(
                    "provider_result_recovered",
                    {
                        "provider": request.provider,
                        "turn_id": str(
                            recovery_checkpoint.get("turn_id") or ""
                        ),
                    },
                )
            else:
                result = service.execute(
                    invocation,
                    BackendContext(
                        emit_event=emit,
                        execution_started_at=runtime_context.accepted_at,
                        turn_start_reason=turn_start_reason,
                        turn_start_reason_changed=set_turn_start_reason,
                        cancellation_requested=runtime_context.is_cancelled,
                        handover_requested=runtime_context.is_handover,
                        set_interrupt_target=set_interrupt_target,
                        reassign_backend_route=(
                            runtime_context.reassign_backend_route
                        ),
                        update_context_usage=update_context_usage,
                        set_provider_recovery_state=(
                            set_provider_recovery_state
                        ),
                        harness_plan_observer=set_harness_plan,
                        turn_control_provider=(
                            runtime_context.turn_control_provider
                        ),
                        turn_control_result_callback=(
                            runtime_context.turn_control_result_callback
                        ),
                        provider_event_observer=lambda provider, backend_session_id, backend_route_id, event: (
                            self.agent_data_projector.observe(
                                provider=provider,
                                backend_route_id=backend_route_id,
                                backend_session_id=backend_session_id,
                                execution_id=request.execution_id,
                                event=event,
                            )
                        ),
                        invocation_observer=lambda effective, reports: (
                            self.agent_data_projector.observe_invocation(
                                request.execution_id,
                                effective,
                                reports,
                            )
                        ),
                        compaction_observer=lambda provider, backend_session_id, backend_route_id, content, created_at, retain_from, provider_compaction_ref: (
                            self.agent_data_projector.observe_compaction(
                                execution_id=request.execution_id,
                                provider=provider,
                                backend_session_id=backend_session_id,
                                backend_route_id=backend_route_id,
                                content=content,
                                created_at=created_at,
                                retain_from=retain_from,
                                provider_compaction_ref=provider_compaction_ref,
                            )
                        ),
                    ),
                )
                with self._lock:
                    active = self._active.get(request.execution_id)
                    active_turn_id = active.turn_id if active else ""
                    live_context_usage = (
                        dict(active.context_usage) if active else {}
                    )
                    live_context_window = (
                        active.model_context_window if active else 0
                    )
                    live_context_usage_at = (
                        active.context_usage_at if active else ""
                    )
                completed_checkpoint = {
                    "provider": request.provider,
                    "operation": request.operation,
                    "backend_session_id": result.backend_session_id,
                    "backend_route_id": result.backend_route_id,
                    "turn_id": active_turn_id,
                    "context_attached": (
                        context_attached_now
                        or bool(
                            recovery_checkpoint.get(
                                "context_attached"
                            )
                        )
                    ),
                    "context_fingerprint": (
                        instructions_fingerprint
                        if context_attached_now
                        else str(
                            recovery_checkpoint.get(
                                "context_fingerprint"
                            )
                            or session.context_fingerprint
                        )
                    ),
                    "resumable": True,
                    "provider_completed": True,
                    "output": dict(result.output),
                    "usage": dict(result.usage),
                    "available_harness_tool_names": sorted(
                        result.available_harness_tool_names
                    ),
                }
                if live_context_usage:
                    completed_checkpoint["context_usage"] = (
                        live_context_usage
                    )
                    completed_checkpoint["context_usage_at"] = (
                        live_context_usage_at
                    )
                if live_context_window:
                    completed_checkpoint["model_context_window"] = (
                        live_context_window
                    )
                runtime_context.checkpoint(completed_checkpoint)
            if runtime_context.is_handover():
                raise ExecutionRecoveryDeferred(
                    "worker handover deferred terminal reconciliation"
                )
            self.agent_data_projector.complete(
                execution_id=request.execution_id,
                backend_route_id=result.backend_route_id,
                backend_session_id=result.backend_session_id,
                turn_id=str(
                    result.output.get("turn_id")
                    or recovery_checkpoint.get("turn_id")
                    or ""
                ),
                response=str(result.output.get("message") or ""),
                usage=result.usage,
            )
            if runtime_context.is_cancelled():
                raise ExecutionCancelled("execution cancelled during Backend call")
            self.sessions.complete(
                request,
                session,
                result.backend_session_id,
                backend_route_id=result.backend_route_id,
                context_fingerprint=(
                    instructions_fingerprint
                    or str(
                        recovery_checkpoint.get(
                            "context_fingerprint"
                        )
                        or ""
                    )
                ),
                context_attached=(
                    context_attached_now
                    or bool(
                        recovery_checkpoint.get("context_attached")
                    )
                ),
                context_injected_at=injection_at,
                harness_tool_names=(
                    result.available_harness_tool_names
                ),
            )
            emit(
                "service_completed",
                {
                    "service_type": request.service_type,
                    "provider": request.provider,
                    "operation": request.operation,
                    "backend_session_id": result.backend_session_id,
                    "backend_route_id": result.backend_route_id,
                },
            )
            return result
        except ExecutionRecoveryDeferred:
            # The Scheduler keeps the durable checkpoint and a later claim
            # resumes the same Provider turn. Do not publish a terminal Agent
            # Data fact for this local attempt.
            raise
        except BaseException as exc:
            if not runtime_context.is_handover():
                self._interrupt_kept_turn(request, runtime_context, exc)
                self._bind_started_session(
                    request,
                    session,
                    context_attached=context_attached_now,
                    context_injected_at=injection_at,
                )
            raise
        finally:
            self.agent_data_projector.discard(request.execution_id)
            with self._lock:
                self._active.pop(request.execution_id, None)

    def _bind_started_session(
        self,
        request: ExecutionRequest,
        session: Any,
        *,
        context_attached: bool,
        context_injected_at: str = "",
    ) -> None:
        """Keep a new Provider session whose turn started, even if it ended badly.

        A turn can fail or be cancelled after the Provider opened a new session
        and started the turn with its input (for a Fork, the inherited
        context).  Binding that session lets the next turn resume it and lets
        the Resident see that the input arrived.  Without the binding the next
        turn delivers the inherited context again, into the Provider's still
        live session or into a new one.
        """

        if (
            request.operation != "turn"
            or session.mode == "stateless"
            or not session.binding_id
            or not session.is_new_backend_session
        ):
            return
        with self._lock:
            active = self._active.get(request.execution_id)
            backend_session_id = active.backend_session_id if active else ""
            backend_route_id = active.backend_route_id if active else ""
            turn_started = bool(active and active.turn_id)
        if not backend_session_id or not turn_started:
            return
        try:
            self.sessions.complete(
                request,
                session,
                backend_session_id,
                backend_route_id=backend_route_id,
                context_attached=context_attached,
                context_injected_at=context_injected_at,
            )
        except Exception:  # noqa: BLE001 - the turn's own failure is what the caller reports
            LOGGER.warning(
                "Provider session %s of execution %s could not be bound",
                backend_session_id,
                request.execution_id,
                exc_info=True,
            )

    def _interrupt_kept_turn(
        self,
        request: ExecutionRequest,
        runtime_context: RuntimeContext,
        error: BaseException,
    ) -> None:
        """Stop the Provider turn of an Execution that failed in this Worker.

        A Provider that keeps a turn for the next Worker
        (``handover_keeps_turn``) keeps it running whenever the Worker's
        connection closes.  A failed Execution has no next Worker: its turn
        would work on unseen and hold the session, and the Resident's next
        input would wait behind it.  A stopped Execution was interrupted when
        it was stopped.
        """

        provider = AGENT_PROVIDERS.get(request.provider)
        if (
            provider is None
            or not provider.handover_keeps_turn
            or request.operation != "turn"
            or isinstance(error, InterruptedError)
        ):
            return
        with self._lock:
            active = self._active.get(request.execution_id)
            if active is None or not active.turn_id:
                return
            service = active.service
            target = {
                "provider": active.provider,
                "backend_session_id": active.backend_session_id,
                "turn_id": active.turn_id,
                "backend_route_id": active.backend_route_id,
                "runtime_options": dict(active.runtime_options),
            }
        interrupted = False
        failure = ""
        try:
            interrupted = bool(service.interrupt(**target))
        except Exception as exc:  # noqa: BLE001 - the Execution's own failure is what the caller reports
            failure = f"{type(exc).__name__}: {exc}"[:500]
            LOGGER.warning("Failed execution %s could not interrupt its Provider turn",
                           request.execution_id, exc_info=True)
        if interrupted or failure:
            runtime_context.emit("provider_interrupt", {
                "reason": "execution failed", "interrupted": interrupted, "turn_id": target["turn_id"],
                **({"error": failure} if failure else {}),
            })

    def _end_abandoned(
        self,
        service: Any,
        request: ExecutionRequest,
        runtime_context: RuntimeContext,
        checkpoint: dict[str, Any],
    ) -> None:
        """Interrupt the Provider work a lost Worker left running, then end.

        The scheduler hands such an execution to a new Worker only for this
        (see ``Scheduler._requeue_abandoned_provider_work``): nothing is run
        again.  The interrupt is addressed with what the lost Worker
        checkpointed, else with the session the execution is bound to; it
        ends the Provider's active turn (or operation) in that session.  The
        execution then ends as the scheduler decided.
        """

        abandoned = dict(checkpoint.get("abandoned") or {})
        backend_session_id = str(checkpoint.get("backend_session_id") or "")
        backend_route_id = str(checkpoint.get("backend_route_id") or "")
        interrupted = False
        error = ""
        try:
            if not backend_session_id:
                session = self.sessions.prepare(request, recovery=True)
                backend_session_id = session.backend_session_id
                backend_route_id = backend_route_id or session.backend_route_id
            interrupted = bool(service.interrupt(
                provider=request.provider,
                backend_session_id=backend_session_id,
                turn_id=str(checkpoint.get("turn_id") or ""),
                backend_route_id=backend_route_id,
                runtime_options={
                    **dict(request.runtime_options),
                    "interrupt_active_turn": True,
                    "abandoned_execution_id": request.execution_id,
                },
            ))
        except Exception as exc:  # noqa: BLE001 - an unreachable Provider has no turn left to stop
            error = f"{type(exc).__name__}: {exc}"[:500]
            LOGGER.warning("Abandoned execution %s could not interrupt its Provider work",
                           request.execution_id, exc_info=True)
        runtime_context.emit("provider_interrupt", {
            "reason": "worker lost", "interrupted": interrupted,
            "turn_id": str(checkpoint.get("turn_id") or ""), **({"error": error} if error else {}),
        })
        message = str(abandoned.get("error") or "execution worker was lost")
        if abandoned.get("status") == "cancelled":
            raise ExecutionCancelled(message)
        raise RuntimeError(message)

    def interrupt(self, execution_id: str) -> bool:
        with self._lock:
            active = self._active.get(str(execution_id or ""))
            if active is None:
                return False
            service = active.service
            provider = active.provider
            backend_session_id = active.backend_session_id
            backend_route_id = active.backend_route_id
            turn_id = active.turn_id
            runtime_options = dict(active.runtime_options)
        return bool(
            service.interrupt(
                provider=provider,
                backend_session_id=backend_session_id,
                turn_id=turn_id,
                backend_route_id=backend_route_id,
                runtime_options=runtime_options,
            )
        )

    def capabilities(self) -> list[str]:
        capabilities: list[str] = []
        for service_type, service in self.services.items():
            providers = getattr(service, "providers", None)
            if isinstance(providers, dict):
                capabilities.extend(
                    f"{service_type}:{provider}"
                    for provider in providers
                )
            else:
                capabilities.append(service_type)
        return sorted(capabilities)
