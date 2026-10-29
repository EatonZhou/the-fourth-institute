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
    capability_assembly: dict[str, Any] | None = None


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
        self._enter_execution_slot(request.execution_id, runtime_context)
        try:
            return self._execute_once(request, runtime_context)
        finally:
            with self._execution_slot_changed:
                self._execution_slots.discard(request.execution_id)
                self._execution_slot_changed.notify_all()

    def _enter_execution_slot(
        self,
        execution_id: str,
        runtime_context: RuntimeContext,
    ) -> None:
        """Wait until an earlier local attempt of this Execution has ended."""

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

    def _execute_once(
        self,
        request: ExecutionRequest,
        context: RuntimeContext,
    ) -> BackendResult:
        service = self._service(request)
        recovery_checkpoint = dict(context.recovery_checkpoint or {})
        if isinstance(recovery_checkpoint.get("abandoned"), dict):
            self._end_abandoned(service, request, context, recovery_checkpoint)
        if context.is_cancelled():
            raise ExecutionCancelled(
                "execution was cancelled before ExecutionRuntime started"
            )
        session = self._prepared_session(request, recovery_checkpoint)
        attempt = _ExecutionAttempt(
            self,
            request=request,
            context=context,
            service=service,
            session=session,
            recovery_checkpoint=recovery_checkpoint,
        )
        with self._lock:
            self._active[request.execution_id] = attempt.active_state()
        try:
            return attempt.run()
        except ExecutionRecoveryDeferred:
            # The Scheduler keeps the durable checkpoint and a later claim
            # resumes the same Provider turn. Do not publish a terminal Agent
            # Data fact for this local attempt.
            raise
        except BaseException as exc:
            if not context.is_handover():
                self._interrupt_kept_turn(request, context, exc)
                self._bind_started_session(
                    request,
                    session,
                    context_attached=attempt.context_attached_now,
                    context_injected_at=attempt.injected_at,
                )
            raise
        finally:
            self.agent_data_projector.discard(request.execution_id)
            with self._lock:
                self._active.pop(request.execution_id, None)

    def _service(self, request: ExecutionRequest) -> ExecutionServiceAdapter:
        try:
            return self.services[request.service_type]
        except KeyError as exc:
            raise ValueError(
                f"unsupported AI execution service: {request.service_type}"
            ) from exc

    def _prepared_session(
        self,
        request: ExecutionRequest,
        recovery_checkpoint: dict[str, Any],
    ) -> Any:
        """The Execution's session; a recovered attempt resumes the checkpointed one."""

        session = self.sessions.prepare(
            request,
            recovery=bool(recovery_checkpoint),
        )
        if not recovery_checkpoint:
            return session
        if str(recovery_checkpoint.get("provider") or "") != request.provider:
            raise ValueError(
                "execution checkpoint belongs to another provider"
            )
        recovered_session_id = str(
            recovery_checkpoint.get("backend_session_id") or ""
        )
        if not recovered_session_id:
            return session
        return replace(
            session,
            backend_session_id=recovered_session_id,
            backend_route_id=(
                str(recovery_checkpoint.get("backend_route_id") or "")
                or session.backend_route_id
            ),
        )

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
        (see ``LeaseRecovery.requeue_abandoned_provider_work``): nothing is run
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


class _ExecutionAttempt:
    """One attempt of an Execution in this Worker.

    It plans the context injection, gives the Backend its callbacks, keeps
    the live Execution state that ``ExecutionRuntime.interrupt`` reads, and
    checkpoints every change a later attempt needs to resume the turn.
    """

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        request: ExecutionRequest,
        context: RuntimeContext,
        service: ExecutionServiceAdapter,
        session: Any,
        recovery_checkpoint: dict[str, Any],
    ) -> None:
        self._runtime = runtime
        self.request = request
        self.context = context
        self.service = service
        self.session = session
        self.recovery = recovery_checkpoint
        self.injection, self.injected_at = self._plan_injection()
        # Any due part, system or prompt, counts as this session's injection.
        self.context_attached_now = self.injection.attached
        self.turn_start_reason = str(recovery_checkpoint.get("turn_start_reason") or (
            "provider_recovery" if recovery_checkpoint.get("turn_id") else "initial"
        ))
        self.invocation = BackendInvocation(
            request=request,
            session=session,
            user_prompt=self.injection.user_prompt,
            system_context=self.injection.system_context,
            session_system_context=self.injection.session_system_context,
            attach_system_context=self.injection.attach_system_context,
            is_new_backend_session=session.is_new_backend_session,
            recovery_checkpoint=recovery_checkpoint,
            available_harness_tool_names=frozenset(
                session.harness_tool_names
            ),
        )

    def active_state(self) -> _ActiveExecution:
        recovery = self.recovery
        return _ActiveExecution(
            service=self.service,
            provider=self.request.provider,
            runtime_options=dict(self.request.runtime_options),
            backend_session_id=self.session.backend_session_id,
            backend_route_id=self.session.backend_route_id,
            turn_id=str(recovery.get("turn_id") or ""),
            turn_start_reason=self.turn_start_reason,
            harness_plan=(
                dict(recovery["harness_plan"])
                if isinstance(recovery.get("harness_plan"), dict)
                else None
            ),
            capability_assembly=(
                dict(recovery["capability_assembly"])
                if isinstance(recovery.get("capability_assembly"), dict)
                else None
            ),
            checkpoint_target=(
                str(recovery.get("backend_session_id") or ""),
                str(recovery.get("backend_route_id") or ""),
                str(recovery.get("turn_id") or ""),
            ),
            context_usage=_clean_context_usage(recovery.get("context_usage")),
            model_context_window=_positive_int(
                recovery.get("model_context_window")
            ),
            context_usage_at=str(recovery.get("context_usage_at") or ""),
            provider_recovery_state=dict(
                recovery.get("provider_recovery_state")
                if isinstance(recovery.get("provider_recovery_state"), dict)
                else {}
            ),
        )

    def run(self) -> BackendResult:
        runtime, request = self._runtime, self.request
        runtime.agent_data_projector.begin(
            request,
            self.session,
            created_at=self.context.accepted_at,
            recovery=bool(self.recovery.get("resumable")),
        )
        if request.service_type == "chat_completion":
            runtime.agent_data_projector.observe_invocation(
                request.execution_id,
                self.invocation,
                [],
            )
        if not self.recovery:
            self._emit_start_events()
        if bool(self.recovery.get("provider_completed")):
            result = self._recovered_result()
        else:
            result = self.service.execute(self.invocation, self._backend_context())
            self.context.checkpoint(self._completed_checkpoint(result))
        if self.context.is_handover():
            raise ExecutionRecoveryDeferred(
                "worker handover deferred terminal reconciliation"
            )
        runtime.agent_data_projector.complete(
            execution_id=request.execution_id,
            backend_route_id=result.backend_route_id,
            backend_session_id=result.backend_session_id,
            turn_id=str(
                result.output.get("turn_id")
                or self.recovery.get("turn_id")
                or ""
            ),
            response=str(result.output.get("message") or ""),
            usage=result.usage,
        )
        if self.context.is_cancelled():
            raise ExecutionCancelled("execution cancelled during Backend call")
        runtime.sessions.complete(
            request,
            self.session,
            result.backend_session_id,
            backend_route_id=result.backend_route_id,
            context_fingerprint=(
                self.injection.fingerprint
                or str(self.recovery.get("context_fingerprint") or "")
            ),
            context_attached=(
                self.context_attached_now
                or bool(self.recovery.get("context_attached"))
            ),
            context_injected_at=self.injected_at,
            harness_tool_names=result.available_harness_tool_names,
        )
        self.emit(
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

    def emit(self, event_type: str, details: dict[str, Any]) -> None:
        if event_type == RECOVERY_INPUT_EVENT:
            details = stamp_recovery_input(self.request.execution_id, details)
        self._runtime.agent_data_projector.observe_runtime_event(
            self.request.execution_id,
            event_type,
            details,
        )
        self.context.emit(event_type, details)

    def set_interrupt_target(
        self,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
    ) -> None:
        def change(active: _ActiveExecution) -> bool:
            if backend_session_id:
                active.backend_session_id = backend_session_id
            if backend_route_id:
                active.backend_route_id = backend_route_id
            if turn_id:
                active.turn_id = turn_id
            checkpoint_target = (
                active.backend_session_id,
                active.backend_route_id,
                active.turn_id,
            )
            if checkpoint_target == active.checkpoint_target:
                return False
            active.checkpoint_target = checkpoint_target
            return True

        self._checkpoint_change(change)

    def update_context_usage(
        self,
        usage: dict[str, int],
        model_context_window: int,
    ) -> None:
        normalized = _clean_context_usage(usage)
        context_window = _positive_int(model_context_window)
        if not normalized and not context_window:
            return

        def change(active: _ActiveExecution) -> bool:
            if (
                active.context_usage == normalized
                and active.model_context_window == context_window
            ):
                return False
            active.context_usage = normalized
            active.model_context_window = context_window
            active.context_usage_at = execution_timestamp()
            return True

        self._checkpoint_change(change)

    def set_provider_recovery_state(self, state: dict[str, Any]) -> None:
        normalized = dict(state or {})

        def change(active: _ActiveExecution) -> bool:
            if active.provider_recovery_state == normalized:
                return False
            active.provider_recovery_state = normalized
            return True

        self._checkpoint_change(change)

    def set_harness_plan(self, plan: dict[str, Any]) -> None:
        normalized = dict(plan or {})

        def change(active: _ActiveExecution) -> bool:
            if active.harness_plan == normalized:
                return False
            active.harness_plan = normalized
            return True

        self._checkpoint_change(change)

    def set_capability_assembly(self, assembly: dict[str, Any]) -> None:
        normalized = dict(assembly)

        def change(active: _ActiveExecution) -> bool:
            if active.capability_assembly == normalized:
                return False
            if active.capability_assembly is not None:
                raise ValueError("Execution capability assembly is immutable")
            active.capability_assembly = normalized
            return True

        self._checkpoint_change(
            change,
            ended_error="Execution ended before capability assembly",
        )

    def set_turn_start_reason(self, reason: str) -> None:
        def change(active: _ActiveExecution) -> bool:
            if active.turn_start_reason == reason:
                return False
            active.turn_start_reason = reason
            return True

        self._checkpoint_change(change)

    def _plan_injection(self) -> tuple[Any, str]:
        """This attempt's injection plan and when the injection is made."""

        request, session = self.request, self.session
        injection_parts = request.system_instructions.parts
        first_injection = session.context_injection_count == 0 or session.is_new_backend_session
        compacted_at = (
            self._runtime.sessions.context_compacted_at(session, agent_id=request.agent_id)
            if not first_injection and any(
                part.timing == "session_start_and_after_compaction" for part in injection_parts
            )
            else ""
        )
        injected_at = utc_now()
        provider = AGENT_PROVIDERS.get(request.provider)
        return plan_injection(
            injection_parts,
            request.prompt,
            generation=(
                request.service_type in {"agent_session", "chat_completion"}
                and request.operation in {"turn", "generate"}
            ),
            first_injection=first_injection,
            injected_at=session.context_injected_at,
            compacted_at=compacted_at,
            system_already_attached=bool(self.recovery.get("context_attached")),
            system_context_survives_compaction=bool(
                provider and provider.system_context_survives_compaction
            ),
        ), injected_at

    def _emit_start_events(self) -> None:
        request, session = self.request, self.session
        self.emit(
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
        self.emit(
            "context_prepared",
            {
                "attached": self.injection.attach_system_context,
                "fingerprint": (
                    self.injection.fingerprint
                    if self.injection.attach_system_context
                    else session.context_fingerprint
                ),
                "parts": list(self.injection.attached_parts),
            },
        )
        self.emit(
            "service_started",
            {
                "service_type": request.service_type,
                "provider": request.provider,
                "operation": request.operation,
            },
        )

    def _backend_context(self) -> BackendContext:
        context = self.context
        return BackendContext(
            emit_event=self.emit,
            execution_started_at=context.accepted_at,
            turn_start_reason=self.turn_start_reason,
            turn_start_reason_changed=self.set_turn_start_reason,
            cancellation_requested=context.is_cancelled,
            handover_requested=context.is_handover,
            set_interrupt_target=self.set_interrupt_target,
            reassign_backend_route=context.reassign_backend_route,
            update_context_usage=self.update_context_usage,
            set_provider_recovery_state=self.set_provider_recovery_state,
            harness_plan_observer=self.set_harness_plan,
            capability_assembly_observer=self.set_capability_assembly,
            turn_control_provider=context.turn_control_provider,
            turn_control_result_callback=context.turn_control_result_callback,
            provider_event_observer=self._observe_provider_event,
            invocation_observer=self._observe_invocation,
            compaction_observer=self._observe_compaction,
        )

    def _observe_provider_event(
        self,
        provider: str,
        backend_session_id: str,
        backend_route_id: str,
        event: Any,
    ) -> None:
        self._runtime.agent_data_projector.observe(
            provider=provider,
            backend_route_id=backend_route_id,
            backend_session_id=backend_session_id,
            execution_id=self.request.execution_id,
            event=event,
        )

    def _observe_invocation(self, effective: Any, reports: Any) -> None:
        self._runtime.agent_data_projector.observe_invocation(
            self.request.execution_id,
            effective,
            reports,
        )

    def _observe_compaction(
        self,
        provider: str,
        backend_session_id: str,
        backend_route_id: str,
        content: Any,
        created_at: Any,
        retain_from: Any,
        provider_compaction_ref: Any,
    ) -> None:
        self._runtime.agent_data_projector.observe_compaction(
            execution_id=self.request.execution_id,
            provider=provider,
            backend_session_id=backend_session_id,
            backend_route_id=backend_route_id,
            content=content,
            created_at=created_at,
            retain_from=retain_from,
            provider_compaction_ref=provider_compaction_ref,
        )

    def _recovered_result(self) -> BackendResult:
        """The result an earlier attempt checkpointed after its Provider finished."""

        recovery = self.recovery
        result = BackendResult(
            output=dict(
                recovery.get("output")
                if isinstance(recovery.get("output"), dict)
                else {}
            ),
            backend_session_id=str(recovery.get("backend_session_id") or ""),
            backend_route_id=str(recovery.get("backend_route_id") or ""),
            usage=dict(
                recovery.get("usage")
                if isinstance(recovery.get("usage"), dict)
                else {}
            ),
            available_harness_tool_names=frozenset(
                str(name or "").strip()
                for name in (recovery.get("available_harness_tool_names") or ())
                if str(name or "").strip()
            ),
        )
        self.emit(
            "provider_result_recovered",
            {
                "provider": self.request.provider,
                "turn_id": str(recovery.get("turn_id") or ""),
            },
        )
        return result

    def _completed_checkpoint(self, result: BackendResult) -> dict[str, Any]:
        """The checkpoint that lets a later attempt finish without the Provider."""

        runtime = self._runtime
        with runtime._lock:
            active = runtime._active.get(self.request.execution_id)
            active_turn_id = active.turn_id if active else ""
            live_context_usage = dict(active.context_usage) if active else {}
            live_context_window = active.model_context_window if active else 0
            live_context_usage_at = active.context_usage_at if active else ""
        checkpoint: dict[str, Any] = {
            "provider": self.request.provider,
            "operation": self.request.operation,
            "backend_session_id": result.backend_session_id,
            "backend_route_id": result.backend_route_id,
            "turn_id": active_turn_id,
            "context_attached": (
                self.context_attached_now
                or bool(self.recovery.get("context_attached"))
            ),
            "context_fingerprint": (
                self.injection.fingerprint
                if self.context_attached_now
                else str(
                    self.recovery.get("context_fingerprint")
                    or self.session.context_fingerprint
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
            checkpoint["context_usage"] = live_context_usage
            checkpoint["context_usage_at"] = live_context_usage_at
        if live_context_window:
            checkpoint["model_context_window"] = live_context_window
        return checkpoint

    def _checkpoint_change(
        self,
        change: Callable[[_ActiveExecution], bool],
        *,
        ended_error: str = "",
    ) -> None:
        """Apply ``change`` to the live Execution and checkpoint what it changed.

        A change to an Execution that has already ended is dropped, unless
        ``ended_error`` says it must not be.
        """

        runtime = self._runtime
        with runtime._lock:
            active = runtime._active.get(self.request.execution_id)
            if active is None:
                if ended_error:
                    raise RuntimeError(ended_error)
                return
            if not change(active):
                return
            checkpoint = self._checkpoint_payload(active)
        self.context.checkpoint(checkpoint)

    def _checkpoint_payload(self, active: _ActiveExecution) -> dict[str, Any]:
        request = self.request
        provider = AGENT_PROVIDERS.get(request.provider)
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
                    self.context_attached_now
                    or self.recovery.get("context_attached")
                )
            ),
            "context_fingerprint": (
                self.injection.fingerprint
                if active.turn_id and self.context_attached_now
                else self.session.context_fingerprint
            ),
            "resumable": bool(
                (
                    request.operation == "turn"
                    and active.turn_id
                    and (
                        active.backend_session_id
                        or (provider and provider.resume_without_session)
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
            payload["model_context_window"] = active.model_context_window
        if active.provider_recovery_state:
            payload["provider_recovery_state"] = dict(
                active.provider_recovery_state
            )
        if active.harness_plan is not None:
            payload["harness_plan"] = dict(active.harness_plan)
        if active.capability_assembly is not None:
            payload["capability_assembly"] = dict(active.capability_assembly)
        return payload
