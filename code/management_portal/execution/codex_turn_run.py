"""One Codex turn of an execution, and its recovery.

``CodexSession`` is the thread an execution works on and the app-server
that holds it.  ``CodexTurnRun`` runs a turn or native goal command on it,
follows its events and carries it on after a Worker handover, a lost
connection or a Provider failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from management_common.format_tags import schema_matches
from management_portal import codex_subagent_cards
from management_portal.codex_app_server_client import codex_token_usage_delta
from management_portal.codex_app_server_protocol import (
    CodexAppServerError,
    CodexAppServerUnavailable,
    CodexCompactionInProgress,
    CodexThreadNotFound,
    CodexThreadWriterBusy,
    CodexTurnConnectionLost,
    CodexTurnContinuationRequired,
    CodexTurnDetached,
    CodexTurnInterrupted,
    CodexTurnRetryableFailure,
    codex_final_agent_response,
    is_context_compaction_item,
    terminal_turn_error,
)
from management_portal.codex_app_server_threads import codex_thread_execution_lock

from .account_adapters import CodexAccountRoute
from .backend_contracts import BackendContext, BackendInvocation, BackendResult, _live_context_usage
from .codex_history_delivery import CodexHistoryDelivery
from .codex_recovery import CodexRecoveryBudget
from .codex_rollout_history import CodexCompaction, read_codex_turn_error, read_codex_turn_input_recorded
from .contracts import ExecutionRecoveryDeferred, ExecutionRequest
from .control_messages import report_harness_control
from .provider_continuation import (
    INTERRUPTED_TURN_CONTINUATION,
    provider_error_continuation,
    report_recovery_input,
)

if TYPE_CHECKING:
    from .codex_backend import CodexBackend

LOGGER = logging.getLogger(__name__)


def codex_goal_command(options: Mapping[str, Any]) -> dict[str, Any]:
    """The native goal command a Codex turn carries (``runtime_options.goal``), or ``{}``."""

    goal = options.get("goal")
    return dict(goal) if isinstance(goal, dict) else {}


@dataclass
class CodexSession:
    """The Codex thread one execution works on and the app-server that holds it."""

    invocation: BackendInvocation
    context: BackendContext
    route: CodexAccountRoute
    client: Any
    thread_id: str
    # The thread started with the session's system context as its developer instructions.
    system_context_bound_to_thread: bool

    @property
    def request(self) -> ExecutionRequest:
        return self.invocation.request

    def provider_cancelled(self) -> bool:
        # Planned detachment is separate from user cancellation. In-flight native
        # commands keep running and the next owner follows this exact turn.
        return self.context.is_cancelled() and not (self.request.operation == "turn" and self.context.is_handover())

    def available_harness_tool_names(self) -> frozenset[str]:
        """The Portal tools the thread offers; a new thread has this turn's tools."""

        names = set(self.invocation.available_harness_tool_names)
        if self.invocation.is_new_backend_session:
            names.update(
                str(tool.name or "").strip()
                for tool in self.invocation.harness_tools
                if bool(getattr(tool, "expose_schema", True))
                and str(tool.name or "").strip()
            )
        return frozenset(names)

    def history_delivery(
        self,
        references: Any,
        *,
        agent_id: str | None = None,
        store: Any = None,
    ) -> CodexHistoryDelivery:
        request = self.request
        return CodexHistoryDelivery(
            references=references, route_id=self.route.account_id,
            agent_id=agent_id or request.agent_id, actor=request.actor,
            execution_id=request.execution_id, store=store,
            send=lambda target, items: self.client.request(
                "thread/inject_items", {"threadId": target, "items": items},
                cancellation_requested=self.provider_cancelled,
            ),
        )


CODEX_TURN_OPTION_KEYS = (
    "model",
    "effort",
    "summary",
    "service_tier",
    "collaboration_mode",
    "permissions",
    "approval_policy",
    "approvals_reviewer",
)


class CodexTurnRun:
    """One Codex turn, or native goal command, of an execution and its recovery.

    The run keeps the recovery state of the execution's earlier attempts: the
    answer of a turn that completed, the accepted turn to follow again, or
    the continuation to send after the Provider lost the turn.  Every change
    of that state is checkpointed through the context.
    """

    def __init__(self, backend: CodexBackend, session: CodexSession, usage_scope: dict[str, Any]) -> None:
        self.backend = backend
        self.session = session
        self.usage_scope = usage_scope
        self.invocation = session.invocation
        self.context = session.context
        self.request = session.request
        self.options = self.request.runtime_options
        self.goal_command = codex_goal_command(self.options)
        self.event_processor = self.invocation.provider_event_processor
        self.turn_options = self._turn_options()
        self.dynamic_tool_handler = self.turn_options.get("dynamic_tool_handler")
        checkpointed = self.invocation.recovery_checkpoint.get("provider_recovery_state")
        self.provider_recovery_state = dict(checkpointed if isinstance(checkpointed, dict) else {})
        self.recovery_budget = CodexRecoveryBudget(self.provider_recovery_state)
        self.original_input_recorded = self.provider_recovery_state.get("original_input_recorded") is True
        self.provider_error_continue_count = int(
            self.provider_recovery_state.get("provider_error_continue_count") or 0
        )
        self.continuation_count = 0
        self.reported_usage_events: set[str] = set()
        self.compaction_event_observed = False
        self.latest_token_usage: dict[str, Any] = {}
        self.subagent_cards: Any = None
        self.previous_compaction: CodexCompaction | None = None
        self.history_delivery: CodexHistoryDelivery | None = None

    def _turn_options(self) -> dict[str, Any]:
        """What ``run_turn`` and ``resume_turn`` are given besides the prompt."""

        options = self.options
        sandbox, sandbox_policy = self.backend._sandbox_options(options)
        turn_options = {
            key: options[key]
            for key in CODEX_TURN_OPTION_KEYS
            if key in options and options[key] not in (None, "")
        }
        if sandbox:
            turn_options["sandbox"] = sandbox
        if sandbox_policy is not None:
            turn_options["sandbox_policy"] = sandbox_policy
        turn_options["cwd"] = self.request.cwd
        turn_options["config"] = self.backend._runtime_thread_config(self.request)
        portal_message_id = str(
            options.get("client_user_message_id") or ""
        )
        if portal_message_id:
            turn_options["client_user_message_id"] = portal_message_id
        dynamic_tool_handler = self.backend._harness_tool_dispatcher(
            self.invocation.harness_tools,
            self.invocation.tool_batch_processor,
        )
        if dynamic_tool_handler is not None:
            turn_options["dynamic_tool_handler"] = dynamic_tool_handler
        if self.event_processor is not None or self.context.has_turn_control_provider():
            turn_options["turn_control_provider"] = self.next_turn_control
            turn_options["turn_control_result_callback"] = self.record_turn_control_result
        return turn_options

    def next_turn_control(self) -> dict[str, Any] | None:
        external = self.context.next_turn_control()
        if external is not None:
            return external
        if self.event_processor is not None:
            return self.event_processor.next_control()
        return None

    def record_turn_control_result(
        self,
        control: dict[str, Any],
        delivered: bool,
        error: str,
    ) -> None:
        if (
            schema_matches(control.get("schema"), "tfi.execution-input-control.v1")
            or schema_matches(control.get("schema"), "tfi.execution-turn-settings-control.v1")
        ):
            self.context.record_turn_control_result(
                control,
                delivered,
                error,
            )
        elif self.event_processor is not None:
            self.event_processor.record_control_result(
                control,
                delivered,
                error,
            )
            if delivered:
                report_harness_control(self.context, control)

    def run(self) -> BackendResult:
        recovered = self._recovered_result()
        if recovered is not None:
            return recovered
        session = self.session
        self.usage_scope["reported"] = self.reported_usage_events
        self.subagent_cards = codex_subagent_cards.for_thread(session.thread_id)
        self.turn_options["turn_snapshot_transform"] = self.canonical_replay_turn
        self.turn_options["detach_requested"] = self.context.is_handover
        self.previous_compaction = self.backend._latest_compaction(session.route, session.thread_id)
        fork_history = self.request.metadata.get("fork_context_input") or {}
        if fork_history and not self.invocation.recovery_checkpoint.get("turn_id"):
            self.history_delivery = session.history_delivery(fork_history["segments"])
        with self.backend._compaction_recorded_on_failure(
            context=self.context,
            route=session.route,
            thread_id=lambda: session.thread_id,
            previous_compaction=lambda: self.previous_compaction,
        ), codex_thread_execution_lock(
            f"{session.route.account_id}:{session.thread_id}",
            cancellation_requested=session.provider_cancelled,
        ):
            self.backend._write_execution_context(self.request)
            if session.provider_cancelled():
                raise InterruptedError(
                    f"Codex execution cancelled before turn start for {session.thread_id}"
                )
            recovery_turn_id = str(
                self.invocation.recovery_checkpoint.get("turn_id") or ""
            )
            if self.goal_command:
                result = self._run_goal(recovery_turn_id)
            else:
                result = self._run_turns(recovery_turn_id)
        # A resumed provider turn can replay an older contextCompaction item.
        # That notification is useful as a hint to reconcile the durable
        # rollout, but it does not prove that this Execution created a new
        # compaction.  Compaction projection is secondary to an otherwise
        # completed user turn, so a stale replay must not replace the
        # Provider's successful result with a Portal system failure.  Explicit
        # compact operations keep their strict required=True checks.
        self.backend._record_new_compaction(
            context=self.context,
            route=session.route,
            thread_id=session.thread_id,
            previous_fingerprint=self._previous_compaction_fingerprint(),
            wait_seconds=1.0 if self.compaction_event_observed else 0.0,
            required=False,
        )
        return self._result(result)

    def _recovered_result(self) -> BackendResult | None:
        """The answer an earlier attempt checkpointed for the turn it was following."""

        recovered_terminal = (
            self.provider_recovery_state
            if self.provider_recovery_state.get("kind") == "completed_turn"
            else {}
        )
        recovered_turn_id = str(
            recovered_terminal.get("turn_id") or ""
        ).strip()
        checkpoint_turn_id = str(
            self.invocation.recovery_checkpoint.get("turn_id") or ""
        ).strip()
        if not recovered_turn_id or recovered_turn_id != checkpoint_turn_id:
            return None
        return BackendResult(
            output={
                "message": str(recovered_terminal.get("response") or "").strip(),
                "provider": self.backend.provider_id,
                "turn_id": recovered_turn_id,
            },
            backend_session_id=self.session.thread_id,
            backend_route_id=self.session.route.account_id,
            usage=dict(
                recovered_terminal.get("usage")
                if isinstance(recovered_terminal.get("usage"), dict)
                else {}
            ),
            available_harness_tool_names=self.session.available_harness_tool_names(),
        )

    def _result(self, result: Any) -> BackendResult:
        usage = (
            dict(result.token_usage)
            if isinstance(result.token_usage, dict)
            else {}
        )
        return BackendResult(
            output={
                "message": str(result.response or ""),
                "provider": self.backend.provider_id,
                "turn_id": str(result.turn_id or ""),
                **(
                    {"goal": dict(result.goal) if result.goal else None}
                    if self.goal_command
                    else {}
                ),
            },
            backend_session_id=str(result.thread_id or self.session.thread_id),
            backend_route_id=self.session.route.account_id,
            usage=usage,
            available_harness_tool_names=self.session.available_harness_tool_names(),
        )

    def _previous_compaction_fingerprint(self) -> str:
        return self.previous_compaction.fingerprint if self.previous_compaction is not None else ""

    def persist_recovery_state(self, state: Mapping[str, Any] | None) -> None:
        self.provider_recovery_state = dict(state or {})
        self.provider_recovery_state["recovery_budget"] = self.recovery_budget.snapshot()
        self.provider_recovery_state["provider_error_continue_count"] = self.provider_error_continue_count
        self.provider_recovery_state["original_input_recorded"] = self.original_input_recorded
        self.context.set_provider_recovery_state(self.provider_recovery_state)

    def reserve_recovery_attempt(self, reason: object) -> None:
        self.recovery_budget.reserve_retry(reason)
        self.persist_recovery_state(self.provider_recovery_state)

    def turn_started(self, turn_id: str) -> None:
        self.recovery_budget.turn_started(turn_id)
        self.context.bind_input(
            provider=self.backend.provider_id,
            backend_session_id=self.session.thread_id,
            backend_route_id=self.session.route.account_id,
            turn_id=turn_id,
        )
        original_turn_id = str(
            self.provider_recovery_state.get("original_turn_id") or ""
        )
        if original_turn_id and str(turn_id or "") != original_turn_id:
            self.persist_recovery_state({})
        else:
            self.persist_recovery_state(self.provider_recovery_state)

    def observe_event(self, event: dict[str, Any]) -> None:
        """Observe one notification of the turn: usage, compaction, the Harness and AgentData."""

        session = self.session
        if self.recovery_budget.observe(event):
            self.provider_error_continue_count = 0
            self.persist_recovery_state(self.provider_recovery_state)
        method = str(event.get("method") or "")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if method == "thread/compacted" or (
            method == "item/completed" and is_context_compaction_item(item)
        ):
            self.compaction_event_observed = True
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = str(turn.get("id") or params.get("turnId") or "")
        if turn_id:
            self.context.set_interrupt_target(
                session.thread_id,
                turn_id,
                session.route.account_id,
            )
        if self.event_processor is not None:
            try:
                self.event_processor.process_event(event)
            except Exception:
                LOGGER.exception("Codex Harness event observation failed")
        if method == "thread/tokenUsage/updated":
            self._observe_token_usage(params)
        child_event = params.get("event") if method == "subagent/event" else None
        if isinstance(child_event, dict) and child_event.get("method") == "thread/tokenUsage/updated":
            self._observe_subagent_token_usage(params, child_event)
        # Persist the provider notification itself. Reconstructed snapshots
        # are display state, not another source of immutable reasoning.
        if not (item.get("type") == "reasoning" and event.get("origin") == "turn_snapshot"):
            self.context.observe_provider_event(event, provider=self.backend.provider_id,
                                                backend_session_id=session.thread_id,
                                                backend_route_id=session.route.account_id)
        for card_event in self.subagent_cards.observe(event):
            self.context.observe_provider_event(card_event, provider=self.backend.provider_id,
                                                backend_session_id=session.thread_id,
                                                backend_route_id=session.route.account_id)
        if method == "turn/completed" and not self.goal_command:
            self._checkpoint_completed_turn(params, turn_id)

    def _observe_token_usage(self, params: dict[str, Any]) -> None:
        if isinstance(params.get("tokenUsage"), dict):
            self.latest_token_usage = dict(params["tokenUsage"])
        usage_event_id, usage_delta = codex_token_usage_delta(
            params.get("tokenUsage")
        )
        if (
            usage_event_id
            and usage_event_id not in self.reported_usage_events
        ):
            self.reported_usage_events.add(usage_event_id)
            self.context.emit(
                "token_usage_delta",
                {
                    "event_id": usage_event_id,
                    "usage": usage_delta,
                },
            )
        live_usage, context_window = _live_context_usage(
            params.get("tokenUsage")
        )
        if live_usage or context_window:
            self.context.observe_context_usage(
                live_usage,
                context_window,
            )

    def _observe_subagent_token_usage(self, params: dict[str, Any], child_event: dict[str, Any]) -> None:
        # A subagent's requests run in its own thread: they are the
        # turn's usage too, though not the size of the turn's context.
        child_params = child_event.get("params") if isinstance(child_event.get("params"), dict) else {}
        child_event_id, child_delta = codex_token_usage_delta(child_params.get("tokenUsage"))
        child_event_id = f"{params.get('traceKey') or ''}:{child_event_id}" if child_event_id else ""
        if child_event_id and child_event_id not in self.reported_usage_events:
            self.reported_usage_events.add(child_event_id)
            self.context.emit("token_usage_delta", {"event_id": child_event_id, "usage": child_delta})

    def _checkpoint_completed_turn(self, params: dict[str, Any], turn_id: str) -> None:
        completed_turn = (
            params.get("turn")
            if isinstance(params.get("turn"), dict)
            else {}
        )
        completed_turn_id = str(
            completed_turn.get("id") or turn_id
        ).strip()
        response = codex_final_agent_response(completed_turn, [])
        if (
            completed_turn_id
            and str(completed_turn.get("status") or "")
            .strip()
            .casefold()
            == "completed"
            and response
        ):
            # The app-server can emit its terminal event and still
            # leave the transport request blocked. Persist the
            # provider result at the event boundary so a replacement
            # worker can finish without reopening the whole thread.
            self.persist_recovery_state({
                "kind": "completed_turn",
                "turn_id": completed_turn_id,
                "response": response,
                "usage": dict(self.latest_token_usage),
            })

    def canonical_replay_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.backend._canonical_replay_turn(
                self.session.route.account_id, self.session.thread_id, turn,
            )
        except ExecutionRecoveryDeferred as exc:
            # A replacement worker must not replenish this wait budget.
            # Keep observing the accepted turn; never send a continuation
            # just because its durable message identities are unavailable.
            self.reserve_recovery_attempt(exc)
            raise

    def _first_turn_prompt(self) -> str:
        """The turn's own input: the system context leads it when the thread does not hold it."""

        invocation = self.invocation
        return (f"{invocation.system_context}\n\n{invocation.user_prompt}"
                if invocation.attach_system_context and not self.session.system_context_bound_to_thread
                else invocation.user_prompt)

    def recovery_input(self, turn_id: str, continuation_prompt: str) -> str:
        """The continuation when Codex has the turn's input, otherwise the input itself."""

        if not self.original_input_recorded:
            rollout = self.backend.account_registry.find_thread_rollout(
                self.session.route.account_id, self.session.thread_id, include_archived=True,
            )
            recorded = read_codex_turn_input_recorded(rollout, turn_id, expected_prompt=self.invocation.user_prompt)
            if recorded is None:
                self.reserve_recovery_attempt("native input delivery is not yet verifiable")
                raise ExecutionRecoveryDeferred(
                    "Codex input delivery is not yet verifiable; original input retained",
                    retry_after_seconds=5,
                )
            self.original_input_recorded = recorded
        if self.original_input_recorded:
            return continuation_prompt
        return self._first_turn_prompt()

    def _continuation_state(
        self,
        *,
        turn_id: str,
        reason: str,
        prompt: str,
        input_delivery: str | None = None,
    ) -> dict[str, Any]:
        """The checkpoint of a turn that waits for its continuation."""

        state: dict[str, Any] = {
            "kind": "turn_and_continue",
            "phase": "ready_to_continue",
            "original_turn_id": turn_id,
            "reason": reason,
            "continuation_prompt": prompt,
        }
        if input_delivery is not None:
            state["input_delivery"] = input_delivery
        state.update({
            "transition_count": self.continuation_count,
            "previous_compaction_fingerprint": self._previous_compaction_fingerprint(),
            "effort": str(
                self.turn_options.get("effort") or ""
            ).strip(),
            "provider_error_continue_count": self.provider_error_continue_count,
        })
        return state

    def apply_continuation(
        self,
        continuation: CodexTurnContinuationRequired,
    ) -> str:
        """The prompt that continues a turn that ended without an answer, or raise."""

        rollout_path = self.backend.account_registry.find_thread_rollout(
            self.session.route.account_id, continuation.thread_id, include_archived=True,
        )
        terminal_error = (
            read_codex_turn_error(rollout_path, continuation.turn_id)
            if isinstance(rollout_path, (str, Path)) else None
        )
        if terminal_error:
            failure = terminal_turn_error(continuation.thread_id, {
                "id": continuation.turn_id,
                "status": "failed",
                "error": terminal_error,
            })
            if not isinstance(failure, CodexTurnRetryableFailure):
                raise failure from continuation
        provider_error = continuation.reason == "provider_system_error"
        if provider_error:
            self.reserve_recovery_attempt(continuation)
        policy = self.invocation.provider_error_recovery
        harness_continue = bool(
            provider_error
            and policy is not None
            and self.provider_error_continue_count
            < policy.max_continue_attempts
        )
        if harness_continue:
            continuation_prompt, self.provider_error_continue_count = provider_error_continuation(
                continuation.__cause__ or continuation,
                invocation=self.invocation, context=self.context,
                provider_id=self.backend.provider_id, turn_started=True,
            )
        else:
            continuation_prompt = INTERRUPTED_TURN_CONTINUATION
        continuation_prompt = self.recovery_input(continuation.turn_id, continuation_prompt)
        continuation_state = self._continuation_state(
            turn_id=continuation.turn_id,
            reason=continuation.reason,
            prompt=continuation_prompt,
            input_delivery="continued" if self.original_input_recorded else "resent",
        )
        if self.continuation_count > 2:
            if provider_error:
                # Requeue with the same outage budget. Only newly observed
                # work can replenish it, not another scheduler claim.
                self.persist_recovery_state(continuation_state)
                raise ExecutionRecoveryDeferred(
                    "Codex provider system error recovery deferred",
                    retry_after_seconds=5,
                )
            self.persist_recovery_state({})
            raise RuntimeError(
                "Codex provider continuation limit exceeded"
            )
        self.persist_recovery_state(continuation_state)
        self.context.set_turn_start_reason("provider_recovery")
        return continuation_prompt

    def recover_provider_failure(
        self,
        failure: CodexTurnInterrupted | CodexTurnRetryableFailure,
    ) -> CodexTurnContinuationRequired:
        """The continuation of a turn the Provider interrupted or failed, or raise."""

        if self.session.provider_cancelled():
            raise InterruptedError(
                f"Codex execution cancelled for {self.session.thread_id}"
            ) from failure
        policy = self.invocation.provider_error_recovery
        if policy is None or policy.max_continue_attempts < 1:
            raise failure
        if self.provider_error_continue_count >= policy.max_continue_attempts:
            self.reserve_recovery_attempt(failure)
            self.persist_recovery_state(self._continuation_state(
                turn_id=failure.turn_id,
                reason="provider_system_error",
                prompt=policy.continuation_prompt,
            ))
            raise ExecutionRecoveryDeferred(
                "Codex provider recovery deferred after bounded "
                "automatic continuation",
                retry_after_seconds=5,
            ) from failure
        return CodexTurnContinuationRequired(
            failure.thread_id or self.session.thread_id,
            failure.turn_id,
            "provider_system_error",
        )

    def resume_accepted_turn(self, turn_id: str) -> Any:
        """Follow the accepted turn again, reconnecting while app-server restarts."""

        session = self.session
        current_turn_id = str(turn_id or "").strip()
        if not current_turn_id:
            raise CodexAppServerUnavailable(
                "Codex accepted a turn but did not return its identity"
            )
        retry_delay = 0.25
        reconnect_deadline = time.monotonic() + 540.0
        while True:
            if session.provider_cancelled():
                raise InterruptedError(
                    f"Codex execution cancelled while reconnecting {session.thread_id}"
                )
            try:
                return session.client.resume_turn(
                    session.thread_id,
                    current_turn_id,
                    cwd=self.request.cwd,
                    config=self.turn_options["config"],
                    event_callback=self.observe_event,
                    turn_started_callback=self.turn_started,
                    turn_snapshot_transform=self.turn_options["turn_snapshot_transform"],
                    detach_requested=self.context.is_handover,
                    cancellation_requested=session.provider_cancelled,
                    dynamic_tool_handler=self.dynamic_tool_handler,
                    turn_control_provider=self.turn_options.get(
                        "turn_control_provider"
                    ),
                    turn_control_result_callback=self.turn_options.get(
                        "turn_control_result_callback"
                    ),
                    timeout_seconds=None,
                )
            except CodexTurnConnectionLost as exc:
                current_turn_id = str(
                    exc.turn_id or current_turn_id
                ).strip()
            except (CodexTurnInterrupted, CodexTurnRetryableFailure) as exc:
                raise self.recover_provider_failure(exc) from exc
            except (CodexAppServerUnavailable, TimeoutError):
                pass
            if time.monotonic() >= reconnect_deadline:
                self.persist_recovery_state({"kind": "provider_reconnect", "turn_id": current_turn_id})
                raise ExecutionRecoveryDeferred("Codex app-server reconnect deadline reached", retry_after_seconds=5)
            if self.context.is_handover():
                raise CodexTurnDetached(session.thread_id, current_turn_id)
            time.sleep(retry_delay)
            retry_delay = min(5.0, retry_delay * 2.0)

    def run_turn_recovering(
        self,
        prompt: str,
        *,
        images: list[str] | None = None,
        image_urls: list[str] | None = None,
    ) -> Any:
        """Run one turn; a turn app-server rejected or lost is deferred or followed again."""

        session = self.session
        state = self.provider_recovery_state
        if state.get("continuation_prompt") == prompt:
            report_recovery_input(
                self.context, provider="codex", prompt=prompt,
                reason="provider_error" if state.get("reason") == "provider_system_error" else "missing_final_response",
                input_delivery=str(state.get("input_delivery") or "continued"),
                turn_id=str(state.get("original_turn_id") or ""),
                image_count=len(images or []) + len(image_urls or []),
            )
        try:
            return session.client.run_turn(
                session.thread_id,
                prompt,
                **({"review_target": self.options["review_target"]} if self.options.get("review_target") else {}),
                event_callback=self.observe_event,
                turn_started_callback=self.turn_started,
                images=images,
                image_urls=image_urls,
                image_detail=str(self.options.get("input_image_detail") or "").strip().casefold() or None,
                cancellation_requested=session.provider_cancelled,
                **({"history_initializer": self.history_delivery.initialize} if self.history_delivery else {}),
                **self.turn_options,
            )
        except CodexCompactionInProgress as exc:
            self.persist_recovery_state({
                "kind": "thread_writer_busy",
                "thread_id": session.thread_id,
            })
            raise ExecutionRecoveryDeferred(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except CodexThreadWriterBusy as exc:
            # The Provider rejected this input before accepting a turn.
            # Reclaims must share the durable no-progress budget, otherwise
            # a writer that never releases its thread loops forever.
            self.reserve_recovery_attempt(exc)
            self.persist_recovery_state({
                "kind": "thread_writer_busy",
                "thread_id": session.thread_id,
            })
            raise ExecutionRecoveryDeferred(
                str(exc),
                retry_after_seconds=30,
            ) from exc
        except CodexTurnConnectionLost as exc:
            self.reserve_recovery_attempt(exc)
            return self.resume_accepted_turn(exc.turn_id)
        except (CodexTurnInterrupted, CodexTurnRetryableFailure) as exc:
            raise self.recover_provider_failure(exc) from exc

    def _run_goal(self, recovery_turn_id: str) -> Any:
        goal_command = self.goal_command
        goal_action = "observe" if recovery_turn_id else str(goal_command.get("action") or "")
        return self.session.client.run_goal(
            self.session.thread_id,
            action=goal_action,
            objective=str(goal_command.get("objective") or ""),
            token_budget=(
                int(goal_command["token_budget"])
                if goal_command.get("token_budget") is not None
                else None
            ),
            cwd=self.request.cwd,
            config=self.turn_options["config"],
            dynamic_tool_handler=self.dynamic_tool_handler,
            event_callback=self.observe_event,
            cancellation_requested=self.session.provider_cancelled,
            detach_requested=self.context.is_handover,
        )

    def _run_turns(self, recovery_turn_id: str) -> Any:
        """Run the turn and its continuations until one answers."""

        self.continuation_count = int(self.provider_recovery_state.get("transition_count") or 0)
        turn_prompt = self._first_turn_prompt()
        input_media = self.request.input_media
        turn_images = [item.source for item in input_media
                       if item.media_type == "image" and item.source_type == "local_path"]
        turn_image_urls = [item.source for item in input_media
                           if item.media_type == "image" and item.source_type == "url"]
        if (recovery_turn_id
            and self.provider_recovery_state.get("original_turn_id") == recovery_turn_id
            and self.provider_recovery_state.get("phase") == "ready_to_continue"):
            turn_prompt = self.recovery_input(recovery_turn_id, str(self.provider_recovery_state["continuation_prompt"]))
            self.persist_recovery_state({**self.provider_recovery_state, "continuation_prompt": turn_prompt,
                                         "input_delivery": "continued" if self.original_input_recorded else "resent"})
            recovery_turn_id = ""
            if self.original_input_recorded:
                turn_images = []
                turn_image_urls = []
            self.context.set_turn_start_reason("provider_recovery")
        fork_thread_replaceable = self.history_delivery is not None
        while True:
            try:
                if recovery_turn_id:
                    return self.resume_accepted_turn(recovery_turn_id)
                return self.run_turn_recovering(turn_prompt, images=turn_images, image_urls=turn_image_urls)
            except CodexTurnContinuationRequired as exc:
                self.continuation_count += 1
                turn_prompt = self.apply_continuation(exc)
                recovery_turn_id = ""
                if self.original_input_recorded:
                    turn_images = []
                    turn_image_urls = []
            except CodexAppServerError as exc:
                if not (
                    fork_thread_replaceable
                    and isinstance(exc, CodexThreadNotFound)
                ):
                    raise
                if not self._replace_lost_fork_thread():
                    raise
                fork_thread_replaceable = False

    def _replace_lost_fork_thread(self) -> bool:
        """Import the Fork window into a new thread when app-server lost the Fork child's.

        A Fork child's thread has no turn yet, so it exists only in the
        app-server's memory and a normal worker restart loses it.  The Fork
        window is immutable in PostgreSQL.  False when the lost thread
        already took input: it cannot be replaced.
        """

        from .session_manager import SessionManager

        session = self.session
        sessions = SessionManager(agent_data=self.history_delivery.store)
        if sessions.has_accepted_input(self.invocation.session.binding_id, agent_id=self.request.agent_id):
            return False
        lost_thread_id = session.thread_id
        session.thread_id = self.backend._start_thread(
            session.client, self.request, self.context.is_cancelled, self.invocation.harness_tools,
        )
        sessions.replace_unaccepted_session(
            self.request, self.invocation.session, backend_session_id=session.thread_id,
            backend_route_id=session.route.account_id,
        )
        self.context.emit("fork_thread_replaced", {
            "provider": self.backend.provider_id, "lost_thread_id": lost_thread_id,
            "thread_id": session.thread_id,
        })
        return True
