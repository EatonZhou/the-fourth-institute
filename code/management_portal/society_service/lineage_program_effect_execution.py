"""Run one Effect activation: interpret its script and report the outcome.

Each call of the script goes through the same router as every other entry
(``management_portal.hatg.routing``) with entry ``lineage``, the run's
initiator as caller and an idempotency key derived from the activation and
the call's position.  Completed calls are written to the resume journal
before the script continues, so a re-claimed activation reuses them.  Every
``DeliverMessage`` is registered as a Resident Invocation of the run, exactly
like a direct Event or Packet delivery.  Success emits ``effect.succeeded``
with the script's outputs; failure emits ``effect.failed``
(decision_4adcc1ff2f2c).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol

from . import (
    lineage_effect_script,
    lineage_instruction_context,
    lineage_effect_script_journal,
    lineage_primitives,
    lineage_program,
    lineage_program_runtime,
)
from .lineage_program_contracts import _program_event_source_contract_fingerprint
from .lineage_program_effect_rules import (
    DELIVER_MESSAGE,
    effect_call_key,
    effect_definition,
    effect_failure_outputs,
    effect_idempotency_key,
    effect_script_inputs,
    prepare_call_arguments,
    validate_effect_route,
)
from .lineage_values import _text

MAX_OUTPUT_BYTES = lineage_primitives.MAX_INVOCATION_ARGUMENT_BYTES


class ProgramEffectAuthority(Protocol):
    """Callbacks the Effect runtime needs from the Lineage aggregate."""

    def register_program_agent_invocation(self, lineage_id: str, target_id: str, message_id: str, event: lineage_primitives.LineageEvent, source_wire_id: str, actor: str) -> dict[str, Any]: ...
    def dispatch_lineage_program_event(self, lineage_id: str, event_source_id: str, event: lineage_primitives.LineageEvent, actor: str, *, cursor: str, expected_event_source_contract: str) -> dict[str, Any]: ...


# What a Family's Router Effect does to its own Router Resident besides asking
# it: look whether the brief it sent is still in the Router's context.
FAMILY_ROUTER_CALLS = frozenset({"GetInputState"})
# What it does to the Family's own State Cells (family_familize.FAMILY_STATE_PREFIXES).
FAMILY_STATE_CALLS = frozenset({"GetSharedState", "UpdateSharedState"})


def _family_note_context(
    lineage: dict[str, Any], effect_id: str, instruction: str, arguments: object, context: Any, *, lineage_id: str,
    event: lineage_primitives.LineageEvent | None = None,
) -> Any:
    """A Family component's calls to its own steward, Router Resident or State Cells are made as the facade (decision_44cf587c1721)."""

    if not isinstance(arguments, dict):
        return context
    from dataclasses import replace
    from .family_familize import facade_for_family_state, facade_for_internal_note

    if instruction in {"RelayFamilyMessage", "DispatchFamilyTask"}:
        from .family_familize import facade_for_arrival_call
        facade = facade_for_arrival_call(lineage, effect_id, instruction, arguments, event, context.caller,
                                         lineage_id=lineage_id) if context.entry == "lineage" else ""
        return replace(context, caller=facade) if facade else context
    if instruction in FAMILY_STATE_CALLS:
        if str(arguments.get("lineage_id") or "") != lineage_id:
            return context
        facade = facade_for_family_state(lineage, effect_id, str(arguments.get("key") or ""))
        return replace(context, caller=facade) if facade else context
    if instruction in {"CheckFamilyRecovery", "CheckFamilyProgress", "CheckFamilyRoleAvailability"}:
        from .family_familize import _component_family, _role
        block = _component_family(lineage, effect_id)
        if (block and arguments.get("lineage_id") == lineage_id
                and arguments.get("family_id") == block["id"]):
            return replace(context, caller=_role(block, "facade"))
        return context
    if instruction in {"CheckResidentWorkSummary", "GetFamilySummaryInputs"}:
        from .family_familize import _component_family, _role, member_ids
        block = _component_family(lineage, effect_id)
        target = str(arguments.get("resident_id") or "")
        allowed = (member_ids(block) + [_role(block, r) for r in ("facade", "router", "steward")]) if block else []
        facade = _role(block, "facade") if block and target in allowed else ""
        return replace(context, caller=facade) if facade else context
    if instruction == DELIVER_MESSAGE:
        roles: tuple[str, ...] = ("steward", "router")
    elif instruction in FAMILY_ROUTER_CALLS:
        roles = ("router",)
    else:
        return context
    facade = facade_for_internal_note(lineage, effect_id, str(arguments.get("resident_id") or ""), roles=roles)
    return replace(context, caller=facade) if facade else context


def effect_outputs(instruction: str, result: object, *, lineage_id: str = "", activation_key: str = "") -> dict[str, Any]:
    """Declared result fields of the called instruction, within the Event budget."""

    from management_portal.hatg.catalog import declaration

    if not isinstance(result, dict):
        return {}
    declared = [param.name for param in declaration(instruction).result]
    return _bounded_outputs(result, declared or sorted(result), lineage_id=lineage_id, activation_key=activation_key)


def _bounded_outputs(result: dict[str, Any], names: list[str], *, lineage_id: str = "", activation_key: str = "") -> dict[str, Any]:
    """Preserve all selected outputs; large outputs use a permanent artifact."""

    outputs = {name: result[name] for name in names if name in result}
    size = len(json.dumps(outputs, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        if not (lineage_id and activation_key):
            raise ValueError("Large Effect outputs require an activation artifact scope")
        return lineage_effect_script_journal.output_reference(lineage_id, activation_key, outputs)
    return lineage_primitives.normalize_argument_object(outputs)


def _dispatch_effect_watchers(
    lineage_id: str,
    plan: dict[str, Any],
    effect_id: str,
    event: lineage_primitives.LineageEvent,
    actor: str,
    *,
    host: ProgramEffectAuthority,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for watcher_id, watcher in sorted((plan.get("event_sources") or {}).items()):
        if (
            not isinstance(watcher, dict)
            or watcher.get("kind") != "event_source"
            or str(watcher.get("event_type") or "") != event.type
            or str(watcher.get("subject_effect_id") or "") != effect_id
        ):
            continue
        results.append(
            host.dispatch_lineage_program_event(
                lineage_id,
                str(watcher_id),
                event,
                actor,
                cursor=f"effect-event:{event.id}:{watcher_id}",
                expected_event_source_contract=_program_event_source_contract_fingerprint(watcher),
            )
        )
    return results


def _outcome_event(
    effect_id: str,
    instruction: str,
    invocation: lineage_primitives.EffectInvocation,
    *,
    event_type: str,
    instruction_id: str,
    body: dict[str, Any],
) -> lineage_primitives.LineageEvent:
    activation = invocation.activation
    route_path = [item for item in str(activation.payload.get("route_path") or "").split(",") if item]
    cause_step_id = str(activation.payload.get("trigger_step_id") or "")
    return lineage_primitives.make_event(
        event_type,
        payload={
            "effect_id": effect_id,
            "instruction": instruction,
            "instruction_id": instruction_id,
            "source_event_id": activation.id,
            "invocation_id": invocation.id,
            "route_path": ",".join([*route_path, effect_id]),
            **({"cause": {"kind": "step", "id": cause_step_id}} if cause_step_id else {}),
            **body,
        },
        correlation=instruction_id or f"{activation.id}:{effect_id}:{event_type}",
        identity=activation.identity or None,
    )


_PARSED_SCRIPTS: dict[str, lineage_effect_script.Script] = {}


def _parsed_script(text: str) -> lineage_effect_script.Script:
    """Parse once per script text; parsed scripts are immutable."""

    script = _PARSED_SCRIPTS.get(text)
    if script is None:
        script = lineage_effect_script.parse(text)
        if len(_PARSED_SCRIPTS) >= 256:
            _PARSED_SCRIPTS.pop(next(iter(_PARSED_SCRIPTS)), None)
        _PARSED_SCRIPTS[text] = script
    return script


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _EffectPaused(lineage_effect_script_journal.RecoveryPending):
    """Keep an admitted script at its next instruction until it is opened."""


def _require_effect_open(lineage_id: str, effect_id: str) -> None:
    if lineage_program_runtime.read_operator_state(lineage_id, effect_id).get("paused"):
        raise _EffectPaused("Effect 已关闭，打开后才能继续执行")


def _execute_program_effect_component(
    lineage: dict[str, Any], plan: dict[str, Any], effect_id: str,
    invocation: lineage_primitives.EffectInvocation, actor: str, *, host: ProgramEffectAuthority,
) -> dict[str, Any]:
    from .effect_disposition import activation_guard

    lineage_id = str(lineage.get("lineage_id") or "")
    key = effect_idempotency_key(lineage_id, effect_id, invocation)
    with activation_guard(lineage_id, key) as acquired:
        if not acquired:
            raise lineage_effect_script_journal.RecoveryPending("Effect activation is already executing")
        try:
            _require_effect_open(lineage_id, effect_id)
            return _execute_claimed_effect_component(lineage, plan, effect_id, invocation, actor, host=host)
        except _EffectPaused:
            return {"status": "paused", "effect_id": effect_id, "paused": True,
                    "reason": "Effect 已关闭", "retryable": True, "resident_invocations": []}


def _execute_claimed_effect_component(
    lineage: dict[str, Any],
    plan: dict[str, Any],
    effect_id: str,
    invocation: lineage_primitives.EffectInvocation,
    actor: str,
    *,
    host: ProgramEffectAuthority,
) -> dict[str, Any]:
    from management_portal.hatg import routing

    lineage_id = str(lineage.get("lineage_id") or "")
    event = invocation.activation
    trigger_path_id = str(event.payload.get("trigger_path_id") or "")
    call_context = lineage_instruction_context.event_context(event, actor)
    activation_key = effect_idempotency_key(lineage_id, effect_id, invocation)
    lineage_effect_script_journal.recover_cleanup(lineage_id)
    from dataclasses import replace
    call_context = replace(call_context, recovery_key=activation_key)
    registrations: list[dict[str, Any]] = []
    journal_open = False
    replayed = False
    outcome: lineage_effect_script.Outcome | None = None
    failure_error: dict[str, Any] | None = None
    try:
        validate_effect_route(effect_id, event.payload.get("route_path"))
        definition = effect_definition(plan, effect_id)
        script_text = str(definition["script"])
        script = _parsed_script(script_text)
        try:
            journal = lineage_effect_script_journal.begin(
                lineage_id,
                activation_key,
                effect_id=effect_id,
                trigger_path_id=trigger_path_id,
                script_sha=hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
                source={"event_id": event.id, "event_source_id": invocation.source_id if invocation.source_id in plan.get("event_sources", {}) else "",
                        "source_wire_id": invocation.source_wire_id,
                        "trigger_step_id": str(event.payload.get("trigger_step_id") or "")},
            )
            call_context = replace(call_context, trigger_path_id=journal["trigger_path_id"])
            event = lineage_primitives.normalize_event({
                **event.to_dict(), "payload": {**event.payload,
                    "trigger_path_id": journal["trigger_path_id"],
                    "trigger_step_id": journal.get("trigger_step_id", ""),
                },
            })
            invocation = replace(invocation, activation=event)
        except lineage_effect_script_journal.ScriptChanged:
            raise
        except Exception as exc:
            raise lineage_effect_script_journal.RecoveryPending("Effect start could not be persisted") from exc
        journal_open = True
        if journal.get("status") in lineage_effect_script_journal.FINISHED_STATES:
            lineage_effect_script_journal.cleanup(lineage_id, activation_key)
            replayed = True
            if journal.get("disposition"):
                return {"status": "executed" if journal["status"] == "succeeded" else "failed",
                        "replayed": True, "retryable": False, "disposition": journal["disposition"],
                        "calls": lineage_effect_script_journal.call_summary(journal), "typed_outputs": {},
                        "resident_invocations": []}
            return _effect_result(
                lineage_id, plan, effect_id, invocation, actor, host=host,
                status=str(journal["status"]),
                outputs=dict(journal.get("outputs") or {}),
                summary=lineage_effect_script_journal.call_summary(journal),
                registrations=registrations,
                replayed=True,
            )
        # Keep terminal replays above authoritative. For unfinished activations,
        # reject hidden fixed targets or broken endpoint references before any
        # further ISA call, including an earlier read/write in the same script.
        from .lineage_program import effect_script_analysis

        for code, message, line in effect_script_analysis(definition).diagnostics:
            if code in {"effect.target_endpoint_required", "effect.target_type_mismatch", "effect.unknown_endpoint", "effect.missing_target"}:
                raise lineage_effect_script.ScriptFailure(code, message, line=line)
        calls: dict[str, dict[str, Any]] = dict(journal.get("calls") or {})
        sequence = [len(calls)]

        def register(position: str, result: dict[str, Any]) -> None:
            message_id = _text(result.get("message_id"), 200)
            target_id = _text(result.get("resident_id"), 80)
            if not (message_id and target_id):
                return
            try:
                registered = host.register_program_agent_invocation(
                    lineage_id, target_id, message_id, event, invocation.source_wire_id, actor,
                )
            except Exception as exc:  # noqa: BLE001 - re-raised below so the dispatcher re-claims
                raise _RegistrationFailed() from exc
            registrations.append({"message_id": message_id, "resident_id": target_id, "resident_handoff": registered})
            lineage_effect_script_journal.mark_registered(lineage_id, activation_key, position)

        def execute_call(request: lineage_effect_script.CallRequest) -> lineage_effect_script.CallResult:
            # Re-read durable state at every instruction boundary, including
            # retries. The snapshot that activated this script cannot reopen it.
            _require_effect_open(lineage_id, effect_id)
            entry = calls.get(request.position)
            if entry is not None:
                entry = lineage_effect_script_journal.restore_call(lineage_id, activation_key, entry)
            if (
                entry is not None
                and "ok" in entry
                and entry.get("status") not in {"executing", "unresolved"}
                and not (entry.get("error") or {}).get("retryable")
                and ("result" in entry or not entry.get("ok"))
            ):
                if entry.get("ok") and request.instruction == DELIVER_MESSAGE and not entry.get("invocation_registered"):
                    register(request.position, dict(entry.get("result") or {}))
                return lineage_effect_script.CallResult(
                    bool(entry.get("ok")),
                    dict(entry.get("result") or {}),
                    dict(entry.get("error") or {}),
                    str(entry.get("instruction_id") or ""),
                )
            instruction_id = ""
            started_at = _now()
            context = call_context
            dispatched = False
            final_receipt = False
            try:
                arguments = prepare_call_arguments(request.instruction, request.arguments, invocation)
                context = _family_note_context(lineage, effect_id, request.instruction, arguments, call_context,
                                               lineage_id=lineage_id, event=event)
                try:
                    # Admit the call atomically with the same durable control
                    # transaction used by PauseComponent. External work starts
                    # only after this short transaction is released.
                    with lineage_program_runtime.operator_state_transaction(lineage_id, effect_id) as state:
                        if state.get("paused"):
                            raise _EffectPaused("Effect 已关闭")
                        lineage_effect_script_journal.record_call_started(lineage_id, activation_key, request.position,
                            {"instruction": request.instruction, "index": request.index, "line": request.line,
                             "caller": context.caller, "started_at": started_at, "sequence": sequence[0] + 1})
                except _EffectPaused:
                    raise
                except Exception as exc:
                    raise lineage_effect_script_journal.RecoveryPending("Effect call start could not be persisted") from exc
                from .script_runs.service import ACTIVATION
                activation_token = ACTIVATION.set({"lineage_id": lineage_id, "effect_id": effect_id, "identity": event.identity, "trigger_type": event.type})
                try:
                    dispatched = True
                    executed = routing.execute(
                        {
                            "instruction": request.instruction,
                            "arguments": arguments,
                            "idempotency_key": effect_call_key(activation_key, request.position),
                        },
                        context,
                    )
                finally:
                    ACTIVATION.reset(activation_token)
                instruction_id = executed.instruction_id
                if executed.status == "committed":
                    result = executed.result if isinstance(executed.result, dict) else {}
                    entry = {"ok": True, "result": result, "status": "committed"}
                else:
                    error = dict(executed.error or {})
                    uncertain = error.get("commit_state") in {"unknown", "committed"} or executed.status in {"executing", "projection_pending"}
                    if uncertain:
                        error["retryable"] = True
                    # Answered from the owner's final receipt: the same key reads it again
                    # on every retry. Only a pending projection can still settle by retrying.
                    final_receipt = uncertain and bool(getattr(executed, "replayed", False)) \
                        and executed.status == "failed" and error.get("code") != "instruction.projection_pending"
                    entry = {"ok": False, "error": error, "status": "unresolved" if uncertain else executed.status}
            except lineage_effect_script_journal.RecoveryPending:
                raise
            except Exception as exc:  # noqa: BLE001 - preserve unconfirmed owner outcomes
                commit_state = getattr(exc, "commit_state", "unknown") if dispatched else "not_committed"
                entry = {"ok": False, "error": {
                    "code": getattr(exc, "code", "") or "instruction.failed",
                    "message": str(exc),
                    "retryable": bool(getattr(exc, "retryable", False)) or commit_state != "not_committed",
                    "commit_state": commit_state,
                }, "status": "unresolved" if commit_state != "not_committed" else "failed"}
            sequence[0] += 1
            entry.update({
                "index": request.index,
                "line": request.line,
                "instruction": request.instruction,
                "instruction_id": instruction_id,
                "sequence": sequence[0],
                "at": _now(),
                "started_at": started_at,
                "caller": context.caller,
            })
            try:
                calls[request.position] = lineage_effect_script_journal.record_call(
                    lineage_id, activation_key, request.position, entry,
                )
            except Exception as exc:
                raise lineage_effect_script_journal.RecoveryPending("Effect call result could not be persisted") from exc
            if entry.get("status") == "unresolved":
                if final_receipt:
                    raise lineage_effect_script_journal.ResolutionRequired(
                        f"{request.instruction} 的结果未知，重试只会读回执行方保存的同一张回执，等待人工处置",
                        details={
                            "activation_key": activation_key, "effect_id": effect_id,
                            "trigger_path_id": str(journal.get("trigger_path_id") or ""),
                            "instruction": request.instruction, "instruction_id": instruction_id,
                            "receipt": str((entry.get("error") or {}).get("message") or "")[:1000],
                        },
                    )
                raise lineage_effect_script_journal.RecoveryPending("Effect instruction outcome remains unconfirmed")
            if entry["ok"] and request.instruction == DELIVER_MESSAGE:
                register(request.position, dict(entry.get("result") or {}))
            return lineage_effect_script.CallResult(
                bool(entry["ok"]),
                dict(entry.get("result") or {}),
                dict(entry.get("error") or {}),
                instruction_id,
            )

        outcome = lineage_effect_script.Interpreter(
            script, effect_script_inputs(definition, invocation), execute_call,
        ).run()
    except _RegistrationFailed as exc:
        # The delivery is committed and journaled; leave the activation open so a
        # re-claim replays the call and registers the Invocation, as before scripts.
        raise exc.__cause__ from None
    except lineage_effect_script_journal.RecoveryPending:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure becomes effect.failed
        if replayed:
            raise
        failure_error = {
            "code": getattr(exc, "code", "") or "instruction.failed",
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
            "line": int(getattr(exc, "line", 0) or 0),
        }
    if outcome is not None and outcome.status == "failed" and outcome.error is not None:
        failure_error = {
            "code": outcome.error.code,
            "message": outcome.error.message,
            "retryable": outcome.error.retryable,
            "line": outcome.error.line,
        }
    if failure_error is not None:
        summary = (
            lineage_effect_script_journal.call_summary(lineage_effect_script_journal.load(lineage_id, activation_key))
            if journal_open else []
        )
        failure_error["completed_calls"] = sum(1 for item in summary if item["status"] == "committed")
        failure = effect_failure_outputs(failure_error)
        if journal_open and not failure_error.get("retryable"):
            summary = lineage_effect_script_journal.finish(
                lineage_id, activation_key, status="failed", outputs=failure, failure=failure,
            )
        return _effect_result(
            lineage_id, plan, effect_id, invocation, actor, host=host,
            status="failed", outputs=failure, summary=summary, registrations=registrations,
        )
    assert outcome is not None
    if outcome.outputs is not None:
        outputs = lineage_primitives.normalize_argument_object(
            _bounded_outputs(outcome.outputs, list(outcome.outputs), lineage_id=lineage_id, activation_key=activation_key)
        )
    elif outcome.last_call is not None:
        outputs = effect_outputs(*outcome.last_call, lineage_id=lineage_id, activation_key=activation_key)
    else:
        outputs = {}
    summary = lineage_effect_script_journal.finish(
        lineage_id, activation_key, status="succeeded", outputs=outputs,
    )
    return _effect_result(
        lineage_id, plan, effect_id, invocation, actor, host=host,
        status="succeeded", outputs=outputs, summary=summary, registrations=registrations,
    )


def _effect_result(
    lineage_id: str,
    plan: dict[str, Any],
    effect_id: str,
    invocation: lineage_primitives.EffectInvocation,
    actor: str,
    *,
    host: ProgramEffectAuthority,
    status: str,
    outputs: dict[str, Any],
    summary: list[dict[str, Any]],
    registrations: list[dict[str, Any]],
    replayed: bool = False,
) -> dict[str, Any]:
    """Build the projection, emit the outcome Event and build the dispatch result."""

    last = summary[-1] if summary else {}
    instruction = str(last.get("instruction") or "")
    instruction_id = str(last.get("instruction_id") or "")
    failed = status != "succeeded"
    projection = lineage_program_runtime.effect_result_projection(
        lineage_id,
        effect_id,
        status="failed" if failed else "executed",
        outputs=outputs,
        instruction=instruction,
        instruction_id=instruction_id,
        error=str(outputs.get("diagnostic") or "") if failed else "",
        calls=summary,
    )
    event = _outcome_event(
        effect_id, instruction, invocation,
        event_type=lineage_program.EFFECT_FAILURE_EVENT_TYPE if failed else lineage_program.EFFECT_OUTCOME_EVENT_TYPE,
        instruction_id=instruction_id,
        body={**outputs, "call_count": len(summary)} if failed else {"outputs": outputs, "call_count": len(summary)},
    )
    response: dict[str, Any] = {
        "status": "failed" if failed else "executed",
        "invocation": invocation.to_dict(),
        "instruction": instruction,
        "instruction_id": instruction_id,
        "calls": summary,
        "resident_invocations": registrations,
        "replayed": replayed,
        "typed_outputs": outputs,
        "effect_runtime": projection,
    }
    dispatches = _dispatch_effect_watchers(lineage_id, plan, effect_id, event, actor, host=host)
    if failed:
        response.update({
            "error": str(outputs.get("diagnostic") or ""),
            "retryable": bool(outputs.get("retryable")),
            "failure_event": event.to_dict(),
            "watcher_dispatches": dispatches,
        })
    else:
        response.update({
            "outcome_event": event.to_dict(),
            "outcome_watcher_dispatches": dispatches,
        })
    return response


class _RegistrationFailed(Exception):
    """Registering a delivered message as a Resident Invocation raised."""
