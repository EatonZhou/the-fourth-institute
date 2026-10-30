"""Explicit admin disposition of work that cannot finish by itself.

ResolveEffectActivation settles one Effect activation whose external outcome
is unknown. ResolveTriggerPath closes a whole stalled Trigger Path as failed.
Both keep the evidence, record who decided and why, and retry nothing.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

import psycopg

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from . import lineage_effect_script_journal as journal
from .state_store import get_lineage_data_store


@contextmanager
def activation_guard(lineage_id: str, activation_key: str):
    """One activation may execute or be disposed, never both.

    A session lock on a separate autocommit connection is released on process
    death. No transaction or Lineage definition lock spans external work.
    """
    database = get_lineage_data_store().database
    key = int.from_bytes(hashlib.sha256(
        f"{database.schema}:effect-activation:{lineage_id}:{activation_key}".encode()
    ).digest()[:8], "big", signed=True)
    with psycopg.connect(database.dsn, autocommit=True, connect_timeout=30) as connection:
        acquired = connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        yield acquired


def has_unknown_calls(record: dict) -> bool:
    return any(call.get("status") in {"executing", "unresolved"}
               for call in (record.get("calls") or {}).values())


def can_resolve(lineage_id: str, key: str, record: dict) -> bool:
    if not has_unknown_calls(record):
        return False
    with activation_guard(lineage_id, key) as acquired:
        return bool(acquired)


def _dispose(lineage_id: str, key: str, existing: dict, disposition: dict) -> None:
    from . import lineage_program_wire_queue, trigger_paths

    # Terminal evidence, journal removal and this occurrence's queue release
    # commit together. Other occurrences on the wire remain pending.
    with get_lineage_data_store().database.transaction():
        journal.finish(lineage_id, key, status=disposition["status"], outputs={}, disposition=disposition)
        lineage_program_wire_queue.settle_effect_delivery(lineage_id, existing)
        trigger_paths.settle_disposed_delivery(lineage_id, key)
    journal.cleanup(lineage_id, key)


def resolve(lineage_id: str, key: str, status: str, actor: str, *, note: str = "") -> dict:
    from . import trigger_paths

    if status not in {"succeeded", "failed"} or not isinstance(note, str) or len(note) > 4000:
        raise invalid("处置结果须为 succeeded 或 failed，说明最长 4000 字符")
    with activation_guard(lineage_id, key) as acquired:
        if not acquired:
            raise InstructionError("instruction.conflict", "Effect 激活仍在执行，请等待执行结束")
        existing = journal.load(lineage_id, key)
        if not existing:
            raise not_found("Effect 激活不存在")
        if existing.get("status") in journal.FINISHED_STATES:
            if existing.get("disposition", {}).get("status") != status:
                raise InstructionError("instruction.conflict", "Effect 已终结，不能改写处置结果")
            # Older dispositions released the queue but left its graph step
            # waiting. Reconcile that same occurrence without changing evidence.
            trigger_paths.settle_disposed_delivery(lineage_id, key)
            journal.cleanup(lineage_id, key)
            return existing
        if not has_unknown_calls(existing):
            raise InstructionError("instruction.conflict", "Effect 没有结果未知的调用")
        disposition = {"actor": actor, "status": status, "note": note, "occurred_at": journal._now()}
        _dispose(lineage_id, key, existing, disposition)
        return journal.load(lineage_id, key)


def resolve_path(lineage_id: str, trigger_path_id: str, actor: str, *, note: str) -> dict:
    """Close every open step of a stalled path as failed; the path then finishes failed.

    Every check runs before the first change. An Effect call with an unknown
    outcome keeps its own disposition (ResolveEffectActivation); a running
    Effect, a Resident still handling the input, an unfinished Execution or a
    queued delivery refuses the request.
    """
    from . import lineage_program_runtime, trigger_paths

    if not isinstance(note, str) or not note.strip() or len(note) > 4000:
        raise invalid("须写明关闭原因，最长 4000 字符")
    path = trigger_paths.get(lineage_id, trigger_path_id)
    if path is None:
        raise not_found("触发流程不存在")
    if path["status"] != "active":
        return path
    open_steps = [step for step in path["steps"] if step["status"] in {"pending", "running", "waiting"}]
    effects = {step["activation_key"]: journal.load(lineage_id, step["activation_key"])
               for step in open_steps if step["kind"] == "effect"}
    if any(has_unknown_calls(record) for record in effects.values()):
        raise InstructionError("instruction.conflict", "流程中有结果未知的 Effect 调用，请先用 ResolveEffectActivation 逐个处置")
    receipts = trigger_paths.input_receipts(step for step in open_steps if step["kind"] == "resident")
    if any(receipt.get("state") and receipt["state"] not in trigger_paths.TERMINAL for receipt in receipts.values()):
        raise InstructionError("instruction.conflict", "Resident 仍在处理流程中的输入，请等它结束或先取消这条输入")
    if any(execution["status"] not in trigger_paths.TERMINAL for execution in path["executions"]):
        raise InstructionError("instruction.conflict", "流程关联的执行尚未结束")
    # An Effect's own queued occurrence is released with its disposition.
    owned = {(str(record.get("source_wire_id") or ""), str(record.get("event_id") or "")) for record in effects.values()}
    queued = sorted({wire_id for wire_id, wire in lineage_program_runtime.read_runtime(lineage_id)["event_wires"].items()
                     for item in wire.get("pending_events") or []
                     if ((item.get("event") or {}).get("payload") or {}).get("trigger_path_id") == trigger_path_id
                     and (wire_id, str((item.get("event") or {}).get("id") or "")) not in owned})
    if queued:
        raise InstructionError("instruction.conflict", "流程仍有排队的投递，请先用 DeletePendingDelivery 处理：" + "、".join(queued))
    # The scope tells failure notification that a person already handled the path.
    disposition = {"actor": actor, "status": "failed", "note": note, "scope": "trigger_path", "occurred_at": journal._now()}
    for key, record in effects.items():
        if not record:
            continue  # A step without its journal is closed with the other steps.
        with activation_guard(lineage_id, key) as acquired:
            if not acquired:
                raise InstructionError("instruction.conflict", "Effect 激活仍在执行，请等待执行结束")
            _dispose(lineage_id, key, journal.load(lineage_id, key), disposition)
    trigger_paths.dispose_open_steps(lineage_id, trigger_path_id, disposition)
    return trigger_paths.get(lineage_id, trigger_path_id)
