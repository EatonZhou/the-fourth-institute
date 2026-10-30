"""The Adapter of Providers whose agent speaks Portal's native agent socket.

``NativeSocketBackend`` runs a session's operations through the Provider's
client; ``NativeTurnRun`` runs one turn: its events, input during the turn,
continuation after a Provider failure, and the compactions and usage it
leaves.  A Provider's own Adapter subclasses ``NativeSocketBackend`` and
supplies what differs: its client, session alignment, compaction threshold
and where its session record is read.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import nullcontext
from typing import Any, Mapping

from management_common.provider_turn_artifacts import ToolEditArtifacts
from management_portal.native_agent_transport import NativeAgentDetached, NativeAgentUnavailable
from management_portal.resident_shell_environment import may_inherit_portal_database

from .backend_contracts import (
    BackendContext,
    BackendInvocation,
    BackendResult,
    _live_context_usage,
    _positive_int,
    full_system_context,
)
from .context_seed import realize_portable_seed
from .contracts import ExecutionRecoveryDeferred
from .harness_capabilities import SUBAGENTS
from .native_session_compaction import NativeCompaction
from .native_tool_events import NativeToolEventTranslator, provider_activity
from .provider_catalog import AGENT_PROVIDERS
from .provider_continuation import (
    INTERRUPTED_TURN_CONTINUATION,
    continuation_prompt_of,
    provider_error_continuation,
    report_recovery_input,
    run_provider_continuation,
)
from .provider_failures import is_egress_block_failure, is_network_failure
from .provider_turn_usage import ObservedTurnUsage
from .transport import ExecutionClaimOwnershipLost, ExecutionResponseUnknown, ExecutionServiceUnavailable
from .turn_input_pump import NO_TURN_INPUT as _NO_TURN_INPUT, TurnInputPump, text_input

LOGGER = logging.getLogger(__name__)


def record_native_compaction(
    context: BackendContext,
    *,
    provider: str,
    backend_session_id: str,
    backend_route_id: str,
    compaction: NativeCompaction,
) -> None:
    """Record a compaction that a Provider committed in its own session files."""

    context.record_compaction(
        provider=provider,
        backend_session_id=backend_session_id,
        backend_route_id=backend_route_id,
        content={"replacement_history": [dict(item) for item in compaction.replacement_history]},
        created_at=compaction.created_at,
        retain_from=compaction.retain_from,
        provider_compaction_ref=compaction.ref,
    )


def report_request_usage(context: BackendContext, observed: ObservedTurnUsage | None) -> None:
    """Record each Provider request in the usage ledger under its own identity.

    This runs whether the turn completed, failed or was cancelled; a recovered
    attempt reports the same requests again and the ledger counts each once.
    """

    for request in observed.requests if observed is not None else ():
        try:
            context.emit("token_usage_delta", {
                "event_id": request.request_id,
                "usage": dict(request.usage),
                # Read after the turn: the ledger hour is when the request was made.
                "occurred_at": request.occurred_at,
            })
        except Exception:  # noqa: BLE001 - usage reporting never fails a turn
            LOGGER.warning("Token usage of request %s could not be recorded", request.request_id, exc_info=True)


def request_scoped_item_event(
    event: dict[str, Any],
    turn_id: str,
    request_key: str,
) -> dict[str, Any]:
    """Make item IDs that a native Provider counts per request unique.

    The Claude daemon names text and thinking items ``<turn>:<message>:...``,
    counting from the start of each Provider request.  A recovered Execution
    can send a second request under the same turn; without a request key its
    items would reuse the first request's IDs for different content, which
    AgentData rightly rejects as a conflicting replay.
    """

    params = event.get("params")
    if not isinstance(params, dict):
        return event
    if event.get("method") == "subagent/event" and isinstance(params.get("event"), dict):
        return {**event, "params": {**params, "event": request_scoped_item_event(
            params["event"], turn_id, request_key,
        )}}
    item = params.get("item")
    if not isinstance(item, dict):
        return event
    item_id = str(item.get("id") or "")
    prefix = f"{turn_id}:"
    if not turn_id or not request_key or not item_id.startswith(prefix):
        return event
    scoped_id = f"{turn_id}:{request_key}:{item_id[len(prefix):]}"
    return {**event, "params": {**params, "item": {**item, "id": scoped_id}}}


def _unphased_agent_message(event: Mapping[str, Any]) -> bool:
    params = event.get("params")
    item = params.get("item") if isinstance(params, Mapping) else None
    return (
        event.get("method") == "item/completed"
        and isinstance(item, Mapping)
        and item.get("type") == "agentMessage"
        and not str(item.get("phase") or "").strip()
    )


def _with_answer_phase(event: dict[str, Any], phase: str) -> dict[str, Any]:
    params = event["params"]
    return {**event, "params": {**params, "item": {**params["item"], "phase": phase}}}


# A Worker's own call to ExecutionService that failed, such as an event of the
# turn it was recording: never a Provider failure to continue after.
_EXECUTION_SERVICE_FAILURES = (ExecutionServiceUnavailable, ExecutionResponseUnknown)


def _report_tool_events(
    invocation: BackendInvocation,
    events: list[dict[str, Any]],
) -> None:
    """Hand common tool events to the Harness observer of this turn."""

    processor = getattr(invocation, "provider_event_processor", None)
    if processor is None:
        return
    for event in events:
        try:
            processor.process_event(event)
        except Exception:  # noqa: BLE001 - observation never fails a turn
            LOGGER.warning("Harness event observer failed", exc_info=True)


def _observe_tool_edit_artifacts(
    context: BackendContext, tool_edits: ToolEditArtifacts, *,
    provider: str, session_id: str, turn_id: str, status: str,
) -> None:
    """Record the turn's 本轮修改 before its turn/completed, as Codex's artifacts arrive."""

    artifacts = tool_edits.finish(thread_id=session_id, turn_id=turn_id, status=status)
    if artifacts is not None:
        context.observe_provider_event(
            {"method": "item/completed", "params": artifacts},
            provider=provider, backend_session_id=session_id,
        )


def _report_context_usage(invocation: BackendInvocation, usage: Mapping[str, Any]) -> None:
    """Tell the Harness observer how full the context was after the turn."""

    context_usage = usage.get("context")
    tokens = _positive_int(context_usage.get("inputTokens")) if isinstance(context_usage, Mapping) else 0
    window = _positive_int(usage.get("modelContextWindow"))
    if tokens and window:
        _report_tool_events(invocation, [{
            "method": "thread/tokenUsage/updated",
            "params": {"tokenUsage": {"last": {"inputTokens": tokens}, "modelContextWindow": window}},
        }])


def nothing_to_compact(outcome: Mapping[str, Any]) -> bool:
    """Whether a compaction did not run because the history is too short."""

    reason = str(outcome.get("reason") or "")
    return reason in {"nothing_to_compact", "summary_not_smaller", "no_session"} or "not enough messages" in reason.casefold()


def compaction_note(outcome: Mapping[str, Any]) -> str:
    """What an on-demand compaction did, for the person who asked for it."""

    if not outcome.get("compacted"):
        return "没有足够的历史可以压缩，会话未改变。"
    before = _positive_int(outcome.get("pre_tokens"))
    after = _positive_int(outcome.get("context_tokens"))
    if before and after:
        return f"上下文从约 {before:,} token 压缩到约 {after:,} token。"
    items = _positive_int(outcome.get("shadowed_items"))
    removed = _positive_int(outcome.get("shadowed_tokens"))
    if items:
        note = f"较早的 {items} 条历史已换成一份摘要（约 {removed:,} token）"
        return note + (f"，压缩后上下文约 {after:,} token。" if after else "。")
    return "压缩已完成。"


class NativeSocketBackend:
    provider_id: str
    client_factory: Any
    # Whether the Provider marks its agent messages as commentary or as the
    # turn's final answer.  Without that mark the adapter decides: the last
    # agent message of a completed turn is the answer, every other one is
    # commentary.
    marks_answer_phases: bool = True
    scopes_item_ids: bool = True

    def prepare_turn_tools(self, invocation, context):
        from .native_harness_tools import prepare_tools
        return prepare_tools(invocation, context)

    def turn_scope(self, session_id, catalog, dispatch):
        return nullcontext()

    def client_turn_options(self, invocation, context, catalog):
        return self.turn_options(invocation.request.runtime_options)

    def media_options(self, invocation: BackendInvocation) -> dict[str, Any]:
        if invocation.request.input_media:
            raise ValueError("Native adapter does not support direct input media")
        return {}

    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        """What the Adapter's client needs to set the Provider's compaction threshold."""

        return {}

    def turn_options(self, options: Mapping[str, Any]) -> dict[str, Any]:
        """What else the Adapter's client is given for this turn.

        A Provider whose model can start subagents is given the Resident's
        "允许开启 sub agent": off takes its subagent tool away.
        """

        definition = AGENT_PROVIDERS[self.provider_id]
        return {"allow_subagents": definition.allows_subagents(dict(options))} if SUBAGENTS in definition.harness else {}

    def compaction_control(self, context):
        return {}

    def handover_options(self, context: BackendContext) -> dict[str, Any]:
        """Let a retiring Worker leave the turn running (``handover_keeps_turn``)."""

        if AGENT_PROVIDERS[self.provider_id].handover_keeps_turn:
            return {"detach_requested": context.is_handover}
        return {}

    def prepare_context_seed(
        self,
        invocation: BackendInvocation,
    ) -> BackendInvocation:
        return realize_portable_seed(invocation)

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        request = invocation.request
        session = invocation.session
        options = request.runtime_options
        operation = request.operation
        media_options = self.media_options(invocation)
        route_id = str(options.get("provider_profile") or session.backend_route_id or AGENT_PROVIDERS[self.provider_id].default_route)
        if session.backend_route_id and session.backend_route_id != route_id:
            raise ValueError("Provider route changes require a session migration")
        session_key = str(
            options.get("session_key")
            or request.session.binding_id
            or request.execution_id
        )
        context.set_interrupt_target(session.backend_session_id)
        if operation == "release":
            return self._release_session(invocation, session_key=session_key)
        if operation == "fork":
            return self._fork_session(invocation, route_id=route_id)
        if operation == "compact":
            return self.compact_native_session(invocation, context, session_key=session_key, route_id=route_id)
        if operation not in {"turn"}:
            raise ValueError(
                f"unsupported native backend operation: {operation}"
            )
        return NativeTurnRun(
            self, invocation, context,
            route_id=route_id, session_key=session_key, media_options=media_options,
        ).run()

    def _release_session(self, invocation: BackendInvocation, *, session_key: str) -> BackendResult:
        closed = self.client_factory().close_session(
            session_key,
            invocation.session.backend_session_id,
        ) or {}
        if "active turn" in str(closed.get("reason") or ""):
            # The live session would carry on into the key's next turn
            # (its daemon reuses it), so it is not released yet; the
            # release is retried once that turn has ended.
            raise RuntimeError(f"{self.provider_id} session has an active turn; release it when the turn ends")
        return BackendResult(
            output={
                "message": "",
                "provider": self.provider_id,
                "operation": invocation.request.operation,
            },
        )

    def _fork_session(self, invocation: BackendInvocation, *, route_id: str) -> BackendResult:
        request, session = invocation.request, invocation.session
        options = request.runtime_options
        forked = self.client_factory().fork_session(
            source_session_id=session.backend_session_id,
            cwd=request.cwd,
            config_dir=str(options.get("config_dir") or ""),
            title=str(options.get("title") or ""),
            provider_profile=route_id,
        )
        forked_id = str(forked.get("session_id") or "")
        if not forked_id:
            raise RuntimeError("Native session fork returned no session id")
        return BackendResult(
            output={
                "message": "",
                "provider": self.provider_id,
                "operation": request.operation,
                "forked_from_session_id": session.backend_session_id,
            },
            backend_session_id=forked_id,
            backend_route_id=route_id,
        )

    def compact_native_session(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
        *,
        session_key: str,
        route_id: str,
    ) -> BackendResult:
        """Have the Provider compact the session's history now (``/compact``).

        Runs between turns.  The Provider replaces older history with a
        summary written by the session's model and keeps the newest messages;
        the compaction is recorded from the Provider's own session file, as
        after a turn.  The summary's own request is not reported by either
        Provider, so the operation records no token use; the size of the
        context afterwards updates the session's context meter.
        """

        request, session = invocation.request, invocation.session
        options = request.runtime_options
        if not session.backend_session_id:
            raise ValueError("The session has no Provider history to compact yet")
        if context.is_cancelled():
            raise InterruptedError("Compaction cancelled before it started")
        self.prepare_native_session(
            session_key=session_key, backend_session_id=session.backend_session_id, options=options,
        )
        outcome = self.client_factory().compact_session(
            session_key=session_key,
            session_id=session.backend_session_id,
            cwd=request.cwd,
            config_dir=str(options.get("config_dir") or ""),
            provider_profile=route_id,
            model=str(options.get("model") or ""),
            effort=str(options.get("effort") or ""),
            is_owner=bool(options.get("is_owner", False)),
            permission_profile=request.permission_profile,
            permission_mode=str(options.get("permission_mode") or ""),
            add_dirs=[str(item) for item in options.get("add_dirs") or [] if str(item)],
            resident_environment=dict(options.get("resident_environment") or {}),
            inherit_portal_database=may_inherit_portal_database(request),
            request_id=request.execution_id,
            **self.compaction_control(context),
            **self.turn_options(options),
            **self.compaction_options(options, session.backend_session_id),
        )
        session_id = str(outcome.get("session_id") or session.backend_session_id)
        compacted = bool(outcome.get("compacted"))
        if not compacted and not nothing_to_compact(outcome):
            raise RuntimeError(f"{AGENT_PROVIDERS[self.provider_id].label} did not compact: {outcome.get('reason') or 'unknown'}")
        if compacted:
            self.record_provider_compaction(
                context=context,
                backend_session_id=session_id,
                backend_route_id=route_id,
                config_dir=str(options.get("config_dir") or ""),
            )
        context_tokens = _positive_int(outcome.get("context_tokens"))
        return BackendResult(
            output={
                "message": compaction_note(outcome),
                "provider": self.provider_id,
                "operation": "compact",
                "compacted": compacted,
            },
            backend_session_id=session_id,
            backend_route_id=route_id,
            usage=(
                {"context": {"inputTokens": context_tokens, "totalTokens": context_tokens}}
                if context_tokens else {}
            ),
        )

    def deliver_turn_input(
        self,
        *,
        session_key: str,
        session_id: str,
        turn_id: str,
        items: list[dict[str, Any]],
        control_id: str,
    ) -> tuple[bool, str]:
        """Hand input to the running turn ``turn_id``; whether the Provider took it.

        Called beside the blocking turn call, from the turn's input pump.
        A ``subagent_stop`` item stops one subagent run instead.
        """

        if len(items) == 1 and items[0].get("type") == "subagent_stop":
            client = self.client_factory()
            if not hasattr(client, "stop_subagent"):
                return False, "subagent_stop_unsupported"
            result = client.stop_subagent(session_key, session_id, tool_use_id=str(items[0].get("tool_use_id") or ""))
            return bool(result.get("stopped")), str(result.get("reason") or "")
        text = text_input(items)
        if not text:
            return False, "turn_input_not_text"
        result = self.client_factory().steer(
            session_key, session_id, turn_id=turn_id, text=text, control_id=control_id,
        )
        return bool(result.get("steered")), str(result.get("reason") or "")

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        session_key = str(runtime_options.get("session_key") or "")
        if not session_key and not backend_session_id:
            return False
        result = self.client_factory().interrupt(session_key, backend_session_id, turn_id=turn_id)
        return bool(result.get("interrupted"))

    def observe_turn_usage(self, *, context, backend_session_id, config_dir):
        since = str(getattr(context, "execution_started_at", "") or "")
        if not backend_session_id or not since:
            return None
        try:
            return self.read_turn_usage(backend_session_id, config_dir, since)
        except Exception:
            LOGGER.warning("%s usage could not be read for session %s", self.provider_id, backend_session_id, exc_info=True)
            return None

    def read_turn_usage(self, session_id, config_dir, since):
        return None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        """The turn's usage as Portal records it (see ``provider_turn_usage``)."""

        return dict(result.usage) if isinstance(result.usage, dict) else {}

    def record_provider_compaction(self, *, context, backend_session_id, backend_route_id="", config_dir=""):
        if not backend_session_id:
            return
        try:
            compaction = self.latest_compaction(backend_session_id, config_dir)
            if compaction is not None:
                record_native_compaction(context, provider=self.provider_id, backend_session_id=backend_session_id,
                                         backend_route_id=backend_route_id, compaction=compaction)
        except Exception:
            LOGGER.warning("%s compaction could not be recorded for session %s", self.provider_id, backend_session_id, exc_info=True)

    def latest_compaction(self, session_id, config_dir):
        return None

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Adapters whose daemon keeps a live session per key align it here."""


class NativeTurnRun:
    """One turn of a native-socket Provider, from its first input to its answer.

    The run delivers the Provider's events (with answer phases and
    request-scoped item ids where the Provider needs them), hands Send-now
    input to the running turn, continues a turn the Provider failed after
    taking it, and records the turn's compactions and usage whatever its end.
    """

    def __init__(
        self,
        backend: NativeSocketBackend,
        invocation: BackendInvocation,
        context: BackendContext,
        *,
        route_id: str,
        session_key: str,
        media_options: dict[str, Any],
    ) -> None:
        self.backend = backend
        self.invocation = invocation
        self.context = context
        self.request = invocation.request
        self.session = invocation.session
        self.options = self.request.runtime_options
        self.route_id = route_id
        self.session_key = session_key
        self.media_options = media_options
        self.provider = AGENT_PROVIDERS[backend.provider_id]
        self.request_key = secrets.token_hex(6)
        self.observed_session_id = self.session.backend_session_id
        self.observed_turn_id = ""
        # An agent message held until the next event shows whether it was the answer.
        self.pending_message: dict[str, Any] | None = None
        self.turn_started = False
        # The Harness reads the Provider's own tool calls in the common shape.
        self.tool_events = NativeToolEventTranslator(self.request.cwd)
        # Without a native turn diff, 本轮修改 comes from the file tools' calls.
        self.tool_edits = ToolEditArtifacts(self.request.cwd)
        self.turn_id = str(self.options.get("turn_id") or self.request.execution_id)
        if not backend.scopes_item_ids and invocation.recovery_checkpoint.get("turn_id"):
            self.turn_id = str(invocation.recovery_checkpoint["turn_id"])
        self.running_turn_id = self.turn_id
        # Send now and the loop governor's reminders reach a running turn
        # only where the Provider takes input between the steps of a turn.
        self.input_pump = TurnInputPump(
            context=context,
            event_processor=getattr(invocation, "provider_event_processor", None),
            deliver=self._deliver_turn_input,
        ) if self.provider.active_turn_input else _NO_TURN_INPUT
        self.tool_catalog, self.tool_callback = backend.prepare_turn_tools(invocation, context)
        self.client_options = backend.client_turn_options(invocation, context, self.tool_catalog)
        context.set_interrupt_target(
            self.session.backend_session_id,
            self.turn_id,
        )
        backend.prepare_native_session(
            session_key=session_key,
            backend_session_id=self.session.backend_session_id,
            options=self.options,
        )

    def _deliver_turn_input(self, run_turn_id: str, items: list[dict[str, Any]], control_id: str) -> tuple[bool, str]:
        return self.backend.deliver_turn_input(
            session_key=self.session_key,
            session_id=str(self.observed_session_id or self.session.backend_session_id or ""),
            turn_id=run_turn_id,
            items=items,
            control_id=control_id,
        )

    def run(self) -> BackendResult:
        backend, context, invocation = self.backend, self.context, self.invocation
        session = self.session
        # A turn an earlier attempt started stays that turn.  Under the same
        # turn id the Provider's process attaches to it while it runs (a
        # Worker handover left it running); a turn that is no longer running
        # is continued in the same session with "continue" once the Provider
        # had taken its input.  Only an input it never took is sent.
        recovery_checkpoint = getattr(invocation, "recovery_checkpoint", None) or {}
        reattaching = self._reattaching(recovery_checkpoint)
        running_prompt, running_media = invocation.user_prompt, self.media_options
        continue_prompt = INTERRUPTED_TURN_CONTINUATION if recovery_checkpoint.get("turn_id") else ""
        if continue_prompt:
            recovery_state = recovery_checkpoint.get("provider_recovery_state")
            continued = int((recovery_state or {}).get("provider_error_continue_count") or 0) if isinstance(recovery_state, dict) else 0
            if continued:
                # The turn running then was a continuation after a Provider
                # error, whose input was a continuation too.
                if backend.scopes_item_ids:
                    self.running_turn_id = f"{self.turn_id}:continue{continued}"
                running_prompt, running_media = continuation_prompt_of(invocation), {}
        observed_usage: ObservedTurnUsage | None = None
        detached = False
        try:
            with self.input_pump, backend.turn_scope(session.backend_session_id, self.tool_catalog, self.tool_callback):
                if recovery_checkpoint.get("turn_id") and context.is_cancelled():
                    raise InterruptedError("execution cancelled before attaching to its turn")
                if reattaching:
                    self._attach_kept_turn()
                try:
                    result = self._run_provider_turn(
                        running_prompt, self.running_turn_id, session.backend_session_id, running_media, continue_prompt,
                    )
                except (NativeAgentDetached, ExecutionClaimOwnershipLost, *_EXECUTION_SERVICE_FAILURES):
                    raise
                except Exception as exc:  # noqa: BLE001 - re-raised unless the turn may be continued
                    result = self._continue_failed_turn(exc)
            self.observed_session_id = str(result.session_id or self.observed_session_id or "")
            self._flush_pending("final_answer")
        except NativeAgentDetached as exc:
            # The Scheduler requeues the Execution; the next Worker attaches.
            detached = True
            raise ExecutionRecoveryDeferred(
                f"Worker handover: the {backend.provider_id} turn keeps running for the next Worker"
            ) from exc
        except ExecutionClaimOwnershipLost:
            # A write of this turn found the claim handed over first.
            detached = True
            raise
        except _EXECUTION_SERVICE_FAILURES as exc:
            # ExecutionService did not take an event of the turn (10-29: it
            # restarted with a publish). Portal failed, not the Provider: a
            # continuation would meet the turn still running in its session.
            # The Provider's process keeps the turn and its events, and the
            # next attempt attaches under the same turn id.
            detached = True
            raise ExecutionRecoveryDeferred(
                f"ExecutionService unavailable: the {backend.provider_id} turn keeps running for the next attempt"
            ) from exc
        finally:
            # A turn left running has not ended, and this Worker's claim is
            # released: the Worker that attaches records what it leaves.
            if not detached:
                observed_usage = self._record_turn_effects()
        usage = backend.turn_usage(result, observed_usage)
        _report_context_usage(invocation, usage)
        return BackendResult(
            output={
                "message": str(result.response or ""),
                "provider": backend.provider_id,
                "turn_id": str(result.turn_id or ""),
            },
            backend_session_id=str(
                result.session_id or session.backend_session_id
            ),
            backend_route_id=self.route_id,
            usage=usage,
            available_harness_tool_names=frozenset(tool["name"] for tool in self.tool_catalog),
        )

    def _reattaching(self, recovery_checkpoint: Mapping[str, Any]) -> bool:
        """Whether a Worker handover left this turn running for this attempt to attach to."""

        handover = recovery_checkpoint.get("worker_handover")
        return bool(
            self.provider.handover_keeps_turn
            and isinstance(handover, dict)
            and handover.get("turn_kept_running")
            and handover.get("turn_id") == self.turn_id
        )

    def _attach_kept_turn(self) -> None:
        self.turn_started = True
        self.input_pump.turn_started(self.running_turn_id, self.turn_id, self.request.execution_id)
        self.context.emit("worker_handover_continuing", {
            "provider": self.backend.provider_id, "previous_turn_id": self.running_turn_id,
            "input_delivery": "kept_running",
        })

    def _continue_failed_turn(self, exc: Exception) -> Any:
        """Carry the turn on in the same session after the Provider failed it, as the recovery policy allows."""

        backend, context = self.backend, self.context
        self.input_pump.turn_ended()
        failure: Exception = exc
        while True:
            prompt, attempt = provider_error_continuation(
                failure, invocation=self.invocation, context=context,
                provider_id=backend.provider_id, turn_started=self.turn_started,
            )
            self._flush_pending("commentary")
            client = backend.client_factory()
            continuation_turn_id = f"{self.turn_id}:continue{attempt}"
            report_recovery_input(
                context, provider=backend.provider_id, reason="provider_error", prompt=prompt,
                input_delivery="continued", turn_id=continuation_turn_id,
            )
            try:
                return run_provider_continuation(
                    lambda: self._run_provider_turn(
                        prompt, continuation_turn_id,
                        str(self.observed_session_id or self.session.backend_session_id), {},
                    ),
                    context=context,
                    unavailable=(getattr(client, "unavailable_type", NativeAgentUnavailable),),
                )
            except Exception as again:  # noqa: BLE001 - only an unavailable egress goes on
                # The egress can go away again before the
                # continuation is answered; the recovery policy
                # decides whether another one goes out.
                if not (is_egress_block_failure(again) or is_network_failure(again)):
                    raise
                failure = again

    def _record_turn_effects(self) -> ObservedTurnUsage | None:
        """Record what the turn left, however it ended; the usage the Provider recorded."""

        backend, options = self.backend, self.options
        # A message still held when the turn failed was not an answer.
        self._flush_pending("commentary")
        # A long turn can compact several times before it fails; the
        # Provider session keeps those compactions either way.
        backend.record_provider_compaction(
            context=self.context,
            backend_session_id=str(self.observed_session_id or ""),
            backend_route_id=self.route_id,
            config_dir=str(options.get("config_dir") or ""),
        )
        # What the Provider's own record of the turn holds beyond the
        # events Portal received.
        # Every request the turn made is used, also when the turn failed.
        observed_usage = backend.observe_turn_usage(
            context=self.context,
            backend_session_id=str(self.observed_session_id or ""),
            config_dir=str(options.get("config_dir") or ""),
        )
        report_request_usage(self.context, observed_usage)
        return observed_usage

    def _run_provider_turn(
        self, prompt: str, run_turn_id: str, session_id: str, media: dict[str, Any], continue_prompt: str = "",
    ) -> Any:
        backend, invocation, request, options = self.backend, self.invocation, self.request, self.options
        return backend.client_factory().run_turn(
            session_key=self.session_key,
            session_id=session_id,
            prompt=prompt,
            cwd=request.cwd,
            config_dir=str(options.get("config_dir") or ""),
            provider_profile=self.route_id,
            model=str(options.get("model") or ""),
            effort=str(options.get("effort") or ""),
            is_owner=bool(options.get("is_owner", False)),
            permission_profile=request.permission_profile,
            permission_mode=str(options.get("permission_mode") or ""),
            system_prompt_append=(
                full_system_context(invocation)
                if self.provider.system_context_every_turn
                else invocation.system_context
                if invocation.attach_system_context
                else ""
            ),
            add_dirs=[
                str(item)
                for item in options.get("add_dirs") or []
                if str(item)
            ],
            turn_id=run_turn_id,
            event_callback=self.observe_event,
            tools=self.tool_catalog,
            tool_callback=self.tool_callback,
            resident_environment=dict(options.get("resident_environment") or {}),
            inherit_portal_database=may_inherit_portal_database(request),
            **media,
            **backend.compaction_options(options, self.session.backend_session_id),
            **self.client_options,
            **backend.handover_options(self.context),
            **({"continue_prompt": continue_prompt} if continue_prompt else {}),
        )

    def _flush_pending(self, phase: str) -> None:
        held = self.pending_message
        self.pending_message = None
        if held is not None:
            self._deliver(_with_answer_phase(held, phase))

    def observe_event(self, event: dict[str, Any]) -> None:
        """Take one event of the Provider's turn."""

        if self.backend.scopes_item_ids:
            event = request_scoped_item_event(event, self.turn_id, self.request_key)
        if self.backend.marks_answer_phases:
            self._deliver(event)
            return
        # Hold each agent message until the next item or the end of the
        # turn shows whether it was the answer.
        method = str(event.get("method") or "")
        if _unphased_agent_message(event):
            self._flush_pending("commentary")
            self.pending_message = event
            return
        if method in {"item/started", "item/completed"}:
            self._flush_pending("commentary")
        elif method == "turn/completed":
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            completed = str(turn.get("status") or "completed") == "completed"
            self._flush_pending("final_answer" if completed else "commentary")
        self._deliver(event)

    def _deliver(self, event: dict[str, Any]) -> None:
        backend, context, invocation = self.backend, self.context, self.invocation
        method = str(event.get("method") or "")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
        session_id = str(
            params.get("threadId")
            or params.get("sessionId")
            or thread.get("id")
            or self.session.backend_session_id
            or ""
        )
        if session_id:
            self.observed_session_id = session_id
        turn = (
            params.get("turn")
            if isinstance(params.get("turn"), dict)
            else {}
        )
        event_turn_id = str(
            turn.get("id") or params.get("turnId") or ""
        )
        if method == "turn/started":
            self.turn_started = True
            if event_turn_id:
                self.observed_turn_id = event_turn_id
            context.bind_input(
                provider=backend.provider_id,
                backend_session_id=session_id,
                turn_id=event_turn_id,
                provider_message_id=str(params.get("userMessageId") or event_turn_id),
            )
            self.input_pump.turn_started(event_turn_id or self.turn_id, self.turn_id, self.request.execution_id)
        elif method == "turn/completed":
            self.input_pump.turn_ended()
        elif method == "turn/recovered":
            # The daemon no longer had the turn and sent this input instead.
            report_recovery_input(
                context, provider=backend.provider_id, reason="turn_interrupted",
                prompt=str(params.get("prompt") or ""),
                input_delivery=str(params.get("delivery") or "continued"),
                turn_id=str(params.get("turnId") or self.turn_id),
                image_count=int(params.get("imageCount") or 0),
            )
            return
        if session_id:
            context.set_interrupt_target(session_id, self.observed_turn_id or self.running_turn_id)
        if method == "thread/tokenUsage/updated":
            from .harness_capabilities import CONTEXT_USAGE_EVENTS
            if CONTEXT_USAGE_EVENTS in self.provider.harness:
                usage, window = _live_context_usage(params.get("tokenUsage"))
                context.observe_context_usage(usage, window)
                _report_tool_events(invocation, [event])
        translated = self.tool_events.translate(event)
        for tool_event in translated:
            self.tool_edits.observe(tool_event)
        if method == "turn/completed":
            _observe_tool_edit_artifacts(
                context, self.tool_edits, provider=backend.provider_id, session_id=session_id,
                turn_id=event_turn_id or self.turn_id, status=str(turn.get("status") or "completed"),
            )
        context.observe_provider_event(
            event,
            provider=backend.provider_id,
            backend_session_id=session_id,
        )
        if getattr(invocation, "provider_event_processor", None) is not None:
            # Text and reasoning between tool calls are progress too.
            _report_tool_events(invocation, translated or [provider_activity()])
