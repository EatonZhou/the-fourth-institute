"""Resume journal of Effect script activations (decision_4adcc1ff2f2c).

One runtime record per running Effect activation, namespace ``effect_script``
and key the activation's idempotency key.  The Effect runtime writes each
completed call (with its result) before the script continues, so a re-claimed
or retried activation reinterprets the script and reuses recorded results
instead of calling again.  When the activation ends, its call summary and
outcome become an ``effect.finished`` run record, which ``GetTriggerPath`` lists and a
duplicate activation reuses, and the journal record is deleted.
"""
from __future__ import annotations

from management_common.operation_errors import OperationError

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from . import trigger_paths
from .effect_artifacts import ArtifactStore
from .state_store import get_lineage_data_store

SCHEMA = "tfi.effect-script-journal.v1"
MAX_RESULT_BYTES = 256 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
FINISHED_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
BUSINESS_IDS = frozenset({"resident_id", "source_resident_id", "lineage_id", "project_id", "message_id",
                         "trigger_path_id", "invocation_id", "execution_id", "agent_id", "trace_id", "script_run_id",
                         "resource_id", "artifact_id", "wire_id", "component_id", "event_id"})


class RecoveryPending(OperationError):
    """Recovery cannot safely advance; keep the activation and its ISA holds."""
    http_status = 409
    commit_state = "not_committed"
    code = "effect.recovery_pending"
    retryable = True


class ResolutionRequired(RecoveryPending):
    """A call's outcome stays unknown however often it is retried.

    The retry was answered from the owner's final receipt for the same
    idempotency key, so every later retry reads that receipt again. The
    activation waits for ResolveEffectActivation; ``details`` names it.
    """
    code = "effect.resolution_required"


def restore_call(lineage_id: str, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    restored = deepcopy(entry)
    if "result_artifact" in restored:
        try:
            restored["result"] = ArtifactStore().read(lineage_id, key, restored["result_artifact"])
        except Exception as exc:
            raise RecoveryPending("Effect result artifact is unavailable; no call was repeated") from exc
    if restored.get("ok") and not isinstance(restored.get("result"), dict):
        raise RecoveryPending("Completed Effect call has no complete result; no call was repeated")
    return restored


def record_call_started(lineage_id: str, key: str, position: str, entry: dict[str, Any]) -> None:
    with get_lineage_data_store().effect_work.transaction(lineage_id, key) as record:
        if get_lineage_data_store().lineage_deletion_operations.get(lineage_id):
            raise RecoveryPending("Lineage deletion has fenced new Effect calls")
        if not record or record.get("status") != "running":
            raise RecoveryPending("Effect activation cannot start another instruction")
        record.setdefault("calls", {})[position] = {**entry, "status": "executing"}
        record["updated_at"] = _now()


def output_reference(lineage_id: str, key: str, outputs: dict[str, Any]) -> dict[str, Any]:
    return {"artifact": ArtifactStore().put(lineage_id, key, outputs, purpose="output")}


def cleanup(lineage_id: str, key: str) -> bool:
    """Retry the durable post-commit task. Failure retains its retry evidence."""
    from management_portal.hatg import routing
    store = get_lineage_data_store()
    # The journal transaction can be nested in a dispatch transaction. Never
    # release pins or remove files while that outer commit can still roll back.
    with store.database.read_current() as connection:
        if connection.in_transaction:
            return False
    task = store.effect_cleanup_task(lineage_id, key)
    if not task:
        return True
    try:
        from management_portal.hatg.contracts import InstructionContext
        for caller, instruction_ids in task.get("instructions", {}).items():
            routing.release_recovery(key, instruction_ids, InstructionContext(caller=caller, entry="lineage", recovery_key=key))
        ArtifactStore().clear_recovery(lineage_id, key)
        return store.complete_effect_cleanup(lineage_id, key, expected_instructions=task["instructions"])
    except Exception:
        return False


def recover_cleanup(lineage_id: str) -> dict[str, bool]:
    return {task["activation_key"]: cleanup(lineage_id, task["activation_key"]) for task in
            get_lineage_data_store().effect_cleanup_tasks(lineage_id=lineage_id)}


def ensure_deletion_can_settle(lineage_id: str, effect_ids: set[str] | None = None) -> None:
    for record in get_lineage_data_store().effect_work.list(lineage_id).values():
        if effect_ids is not None and record.get("effect_id") not in effect_ids:
            continue
        if any(entry.get("status") in {"executing", "unresolved"} for entry in (record.get("calls") or {}).values()):
            raise RecoveryPending("Confirm in-flight Effect instruction outcomes before deletion")


def cancel_removed_effects(lineage_id: str, effect_ids: set[str] | None, *, reason: str, occurred_at: str) -> None:
    """Deletion cancels settled activations; uncertain calls remain recoverable."""
    ensure_deletion_can_settle(lineage_id, effect_ids)
    for key, record in get_lineage_data_store().effect_work.list(lineage_id).items():
        if effect_ids is not None and record.get("effect_id") not in effect_ids:
            continue
        finish(lineage_id, key, status="cancelled", outputs={"error_code": "effect.cancelled", "diagnostic": reason},
               failure={"reason": reason, "occurred_at": occurred_at})


class ScriptChanged(OperationError):
    """The Effect's script differs from the one this activation started with."""
    http_status = 409
    commit_state = "not_committed"

    code = "effect.script_changed"
    retryable = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load(lineage_id: str, key: str) -> dict[str, Any]:
    return (
        get_lineage_data_store().effect_work.get(lineage_id, key)
        or trigger_paths.effect_finished(lineage_id, key)
        or {}
    )


def begin(lineage_id: str, key: str, *, effect_id: str, trigger_path_id: str, script_sha: str,
          source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open (or reopen) the journal of one activation and return it; a finished one returns its record."""

    with get_lineage_data_store().effect_work.transaction(lineage_id, key) as record:
        if not record:
            finished = trigger_paths.effect_finished(lineage_id, key)
            if finished is not None:
                return finished
        if record:
            if str(record.get("script_sha") or "") != script_sha:
                raise ScriptChanged("Effect 脚本在这次激活执行期间被修改，不在新脚本上继续执行")
            return deepcopy(record)
        record.update({
            "schema": SCHEMA,
            "effect_id": effect_id,
            "trigger_path_id": trigger_path_id,
            "script_sha": script_sha,
            "status": "running",
            "started_at": _now(),
            "calls": {},
            **(source or {}),
        })
        path_id, step_id = trigger_paths.effect_started(lineage_id, key, record)
        record["trigger_path_id"] = path_id
        record["trigger_step_id"] = step_id
        return deepcopy(record)


def record_call(lineage_id: str, key: str, position: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Persist one completed call before the script continues."""

    stored = dict(entry)
    result = stored.get("result")
    stored["business_ids"] = {name: result[name] for name in BUSINESS_IDS if isinstance(result, dict)
                              and isinstance(result.get(name), str) and 0 < len(result[name]) <= 256}
    if isinstance(result, dict) and isinstance(result.get("status"), str):
        stored["result_status"] = result["status"][:80]
    if result is not None and _size(result) > MAX_RESULT_BYTES:
        stored.pop("result", None)
        stored["result_artifact"] = ArtifactStore().put(lineage_id, key, result, purpose="recovery")
    with get_lineage_data_store().effect_work.transaction(lineage_id, key) as record:
        calls = record.setdefault("calls", {})
        calls[position] = stored
        if _size(record) > MAX_RECORD_BYTES and "result" in stored:
            stored = {key_: value for key_, value in stored.items() if key_ != "result"}
            stored["result_artifact"] = ArtifactStore().put(lineage_id, key, result, purpose="recovery")
            calls[position] = stored
        record["updated_at"] = _now()
    return stored


def mark_registered(lineage_id: str, key: str, position: str) -> None:
    with get_lineage_data_store().effect_work.transaction(lineage_id, key) as record:
        entry = (record.get("calls") or {}).get(position)
        if isinstance(entry, dict):
            entry["invocation_registered"] = True


def finish(
    lineage_id: str,
    key: str,
    *,
    status: str,
    outputs: dict[str, Any],
    failure: dict[str, Any] | None = None,
    disposition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Close the activation: record its call summary and outcome, drop the journal."""

    store = get_lineage_data_store()
    if status not in FINISHED_STATES:
        raise ValueError("Effect finish requires a confirmed terminal outcome")
    if status in {"cancelled", "timed_out"} and not (failure and failure.get("reason") and failure.get("occurred_at")):
        raise ValueError("Effect disposition requires its evidenced reason and occurred_at")
    with store.effect_work.transaction(lineage_id, key) as record:
        if not record:
            finished = trigger_paths.effect_finished(lineage_id, key) or {}
            return call_summary(finished)
        if disposition is None and any(entry.get("status") in {"executing", "unresolved"} for entry in (record.get("calls") or {}).values()):
            raise RecoveryPending("Cannot terminate an Effect with an unconfirmed instruction outcome")
        summary = call_summary(record)
        terminal = deepcopy(record)
        terminal["calls"] = {item["position"]: item for item in summary}
        terminal["status"] = status
        terminal["outputs"] = deepcopy(outputs)
        if disposition is not None:
            terminal["disposition"] = deepcopy(disposition)
        if failure:
            terminal["failure"] = deepcopy(failure)
        terminal["finished_at"] = _now()
        saved = trigger_paths.effect_settled(lineage_id, key, terminal)
        if not saved and trigger_paths.effect_finished(lineage_id, key) is None:
            raise RecoveryPending("Effect terminal record was not persisted")
        instructions: dict[str, list[str]] = {}
        for entry in (record.get("calls") or {}).values():
            if entry.get("caller") and entry.get("instruction_id"):
                instructions.setdefault(entry["caller"], []).append(entry["instruction_id"])
        store.enqueue_effect_cleanup(lineage_id, key, instructions)
        record.clear()
    cleanup(lineage_id, key)
    return summary


def call_summary(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Calls of one activation in execution order, without results.

    A running journal holds each call's ``ok`` and ``error``; a finished
    activation already holds this summary, with ``status`` and ``error_code``.
    """

    items = []
    for position, entry in (record.get("calls") or {}).items():
        if not isinstance(entry, dict):
            continue
        ok = entry["ok"] if "ok" in entry else entry.get("status") == "committed"
        items.append({
            "position": str(position),
            "index": int(entry.get("index") or 0),
            "line": int(entry.get("line") or 0),
            "instruction": str(entry.get("instruction") or ""),
            "instruction_id": str(entry.get("instruction_id") or ""),
            **({"caller": entry["caller"]} if entry.get("caller") else {}),
            "status": str(entry.get("status") or ("committed" if ok else "failed")),
            **({} if ok else {"error_code": str((entry.get("error") or {}).get("code") or entry.get("error_code") or "")}),
            **({"script_run_id": str(entry.get("script_run_id") or (entry.get("business_ids") or {}).get("script_run_id"))}
               if entry.get("instruction") == "RunScript" and (entry.get("script_run_id") or (entry.get("business_ids") or {}).get("script_run_id")) else {}),
            "sequence": int(entry.get("sequence") or 0),
            "at": str(entry.get("at") or ""),
            **({"started_at": entry["started_at"]} if entry.get("started_at") else {}),
            **({"result_status": entry["result_status"]} if entry.get("result_status") else {}),
            "business_ids": deepcopy(entry.get("business_ids") or {}),
            **({"error": {name: (str(entry["error"][name])[:2000] if name == "message" else deepcopy(entry["error"][name])) for name in ("code", "message", "retryable", "commit_state")
                           if name in entry["error"]}} if isinstance(entry.get("error"), dict) and entry["error"] else {}),
        })
    items.sort(key=lambda item: (item["sequence"], item["at"]))
    return items


def path_calls(lineage_id: str, trigger_path_id: str, *, journals: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every call of the Effect activations in one Run: finished ones from run records, running ones from journals."""

    path = trigger_paths.get(lineage_id, trigger_path_id)
    activations = {
        step["activation_key"]: {**step["result"], "effect_id": step["node_id"]}
        for step in (path or {}).get("steps", []) if step["kind"] == "effect" and step["status"] in trigger_paths.TERMINAL
    }
    if journals is None:
        journals = get_lineage_data_store().effect_work.list(lineage_id)
    for key, record in journals.items():
        if str(record.get("trigger_path_id") or "") == trigger_path_id:
            activations.setdefault(key, record)
    result = []
    for key, record in activations.items():
        for item in call_summary(record):
            result.append({"effect_id": str(record.get("effect_id") or ""), "activation_key": key, **item})
    result.sort(key=lambda item: (item["at"], item["sequence"]))
    return result
