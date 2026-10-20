"""Explicit admin disposition of an Effect whose external outcome is unknown."""
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
    with psycopg.connect(database.dsn, autocommit=True, connect_timeout=10) as connection:
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


def resolve(lineage_id: str, key: str, status: str, actor: str, *, note: str = "") -> dict:
    from . import lineage_program_runtime

    if status not in {"succeeded", "failed"} or not isinstance(note, str) or len(note) > 4000:
        raise invalid("处置结果须为 succeeded 或 failed，说明最长 4000 字符")
    store = get_lineage_data_store()
    with activation_guard(lineage_id, key) as acquired:
        if not acquired:
            raise InstructionError("instruction.conflict", "Effect 激活仍在执行，请等待执行结束")
        existing = journal.load(lineage_id, key)
        if not existing:
            raise not_found("Effect 激活不存在")
        if existing.get("status") in journal.FINISHED_STATES:
            if existing.get("disposition", {}).get("status") != status:
                raise InstructionError("instruction.conflict", "Effect 已终结，不能改写处置结果")
            journal.cleanup(lineage_id, key)
            return existing
        if not has_unknown_calls(existing):
            raise InstructionError("instruction.conflict", "Effect 没有结果未知的调用")
        disposition = {"actor": actor, "status": status, "note": note, "occurred_at": journal._now()}
        # Terminal evidence, journal removal and this occurrence's queue release
        # commit together. Other occurrences on the wire remain pending.
        with store.database.transaction():
            journal.finish(lineage_id, key, status=status, outputs={}, disposition=disposition)
            lineage_program_runtime.settle_effect_delivery(lineage_id, existing)
        journal.cleanup(lineage_id, key)
        return journal.load(lineage_id, key)
