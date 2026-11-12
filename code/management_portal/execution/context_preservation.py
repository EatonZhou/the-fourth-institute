"""Execution-owned, model-free preparation of immutable session context.

The business caller authorizes the Agent and drains its work before preparing.
It validates the returned source token under its short admission lock before
committing. No native paths or Provider history structures cross this port.
Preparation never appends to the live Agent: failed admission leaves its
effective history untouched. Stable operation receipts make retries reusable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from management_common.json_columns import json_value
from psycopg.errors import UniqueViolation

from .agent_data_store import get_agent_data_store, stable_segment_id, utc_now
from .agent_data_traces import locked_trace, append_sequence_ids
from .agent_operations import put_operation
from .codex_preservation import native_seed
from .session_history_import import validate_codex_preservation


SCHEMA = "tfi.context-preservation.v1"
_BINDING_KEYS = ("binding_id", "provider_session_ref", "provider", "backend_route_id", "backend_session_id")


def _identity(binding):
    return {key: str(binding.get(key) or "") for key in _BINDING_KEYS}


def _digest(prepared):
    return hashlib.sha256(json.dumps(prepared, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def context_preservation_capabilities():
    from .provider_catalog import AGENT_PROVIDERS
    return {**{name: {
        "same_account_native_fork": "fork" in definition.operations,
        "canonical_import": "validated_codex_history" if name == "codex" else False,
        "cross_provider_import": False,
        "empty_session_without_model": "create" in definition.operations,
    } for name, definition in AGENT_PROVIDERS.items()},
        "provider_api": {"same_account_native_fork": False, "canonical_import": False,
                         "cross_provider_import": False, "context_owner": "portal"}}


def _source(store, agent_id, binding):
    binding_id = "resident:" + agent_id
    current = store.provider_binding(binding_id)
    expected = _identity(binding)
    if binding and expected["binding_id"] != binding_id:
        raise ValueError("Context preservation source binding belongs to another Agent")
    if _identity(current) != expected:
        raise ValueError("Context preservation source binding changed")
    with store.database.read() as connection:
        row = connection.execute(
            "SELECT sequence.sequence_id, sequence.xmin::text AS revision "
            "FROM agents agent JOIN sequences sequence ON sequence.sequence_id=agent.trace_sequence_id "
            "WHERE agent.agent_id=%s", (agent_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Context preservation source Agent is unavailable")
    return {"binding": expected, "sequence_id": str(row["sequence_id"]), "revision": str(row["revision"])}


def _validate_native(evidence):
    if evidence:
        try:
            stat = Path(evidence["path"]).stat()
        except OSError as exc:
            raise ValueError("Context preservation native source is unavailable") from exc
        if [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns] != evidence["fingerprint"]:
            raise ValueError("Context preservation native source changed")


def validate_context_preservation(prepared, *, store=None):
    """Recheck only source identity and MVCC revision; no scan or materialization."""
    if prepared.get("schema") != SCHEMA:
        raise ValueError("Invalid context preservation receipt")
    store = store or get_agent_data_store()
    source = _source(store, prepared["agent_id"], prepared["source_binding"])
    with store.database.read() as connection:
        row = connection.execute("SELECT data_json::jsonb->'metadata' AS metadata, "
            "(SELECT data_json::jsonb->'source' FROM agent_operations WHERE operation_id=%s) AS committed_source "
            "FROM agent_operations WHERE operation_id=%s",
            (_commit_id(prepared), prepared["receipt_id"])).fetchone()
    metadata = json_value(row["metadata"]) if row is not None else {}
    if metadata.get("prepared_sha256") != _digest(prepared):
        raise ValueError("Invalid context preservation receipt identity")
    committed_source = json_value(row["committed_source"]) if row["committed_source"] is not None else None
    if source not in (prepared["source"], committed_source):
        raise ValueError("Context preservation source history changed")
    _validate_native(metadata.get("native_evidence"))


def _commit_id(prepared):
    return stable_segment_id("session-preservation-commit", prepared["receipt_id"])


def _replay(store, key, identity):
    try:
        receipt = store.operation_record(key)["content"]["prepared"]
    except KeyError:
        return None
    if any(receipt.get(k) != v for k, v in identity.items()):
        raise ValueError("Context preservation operation refers to another source or actor")
    validate_context_preservation(receipt, store=store)
    return receipt


def commit_context_preservation(prepared, *, store=None):
    """Attach an already frozen native boundary and its receipt atomically.

    Called under the business admission lock after its final authorization and
    version checks. No native scan, payload read or payload materialization is
    performed here. Canonical references already belong to the source trace.
    """
    store = store or get_agent_data_store()
    validate_context_preservation(prepared, store=store)
    if not prepared["native_boundary"] or not prepared["seed"]["segments"]:
        return prepared["seed"]
    with store.database.transaction() as connection:
        sequence_id = locked_trace(connection, prepared["agent_id"], prepared["actor"])
        row = connection.execute("SELECT xmin::text AS revision FROM sequences WHERE sequence_id=%s",
                                 (sequence_id,)).fetchone()
        current = {**prepared["source"], "sequence_id": sequence_id, "revision": str(row["revision"])}
        committed = connection.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s",
                                       (_commit_id(prepared),)).fetchone()
        if committed is not None:
            if json_value(committed["data_json"])["source"] != current:
                raise ValueError("Context preservation source history changed")
            return prepared["seed"]
        if current != prepared["source"]:
            raise ValueError("Context preservation source history changed")
        append_sequence_ids(connection, sequence={"sequence_id": sequence_id},
            segment_ids=[item["segment_id"] for item in prepared["seed"]["segments"]], actor_ref=prepared["actor"])
        row = connection.execute("SELECT xmin::text AS revision FROM sequences WHERE sequence_id=%s",
                                 (sequence_id,)).fetchone()
        put_operation(connection, operation_id=_commit_id(prepared), kind="session_preserved",
            agent_id=prepared["agent_id"], created_at=utc_now(),
            data={"source": {**current, "revision": str(row["revision"])}, "receipt_id": prepared["receipt_id"]})
    return prepared["seed"]


def prepare_context_preservation(agent_id, *, actor, operation_id, source_binding, store=None, routes=None):
    """Freeze registered native history or canonical history, never visible Chat.

    A lost local caller can retry the same operation without repeating a native
    import or changing the live trace. Source changes invalidate its receipt;
    choosing a different source requires a new business operation identity.
    """
    if not actor or not operation_id or not agent_id:
        raise ValueError("Context preservation requires Agent, actor and operation identities")
    store = store or get_agent_data_store()
    store.ensure_agent(agent_id, actor_ref=actor, sequence_kind="agent_trace", parent_agent_id=None)
    source = _source(store, agent_id, source_binding)
    key = stable_segment_id("session-preservation", agent_id, operation_id)
    identity = {"agent_id": agent_id, "actor": actor, "operation_id": operation_id,
                "source_binding": _identity(source_binding) if source_binding else {}}
    receipt = _replay(store, key, identity)
    if receipt is not None:
        return receipt
    native_evidence = {}
    seed = native_seed(agent_id, source_binding, store=store, routes=routes,
                       actor_ref=actor, evidence=native_evidence)
    if seed is None:
        snapshot = store.agent_context_snapshot(agent_id, provider_session_ref=str(source_binding.get("provider_session_ref") or ""))
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("segments"), list):
            raise ValueError("Cannot preserve context: canonical effective history is unavailable")
        segments = snapshot["segments"]
        if not segments and not snapshot.get("empty_context"):
            raise ValueError("Cannot preserve context: empty history has no explicit boundary")
        validate_codex_preservation(segments)
        refs = [{"segment_id": item["segment_id"], **({"content_sha256": item["content_sha256"]}
                 if item.get("content_sha256") else {})} for item in segments]
        seed = {"schema": "tfi.provider-context-seed.v1", "segments": refs,
                "empty_context": bool(snapshot.get("empty_context")), "source_agent_id": agent_id}
    prepared = {"schema": SCHEMA, **identity, "source": source, "seed": seed,
                "native_boundary": bool(native_evidence), "receipt_id": key}
    if _source(store, agent_id, source_binding) != source:
        # A concurrent admission may already have attached this very snapshot.
        # Its immutable commit token distinguishes that from unrelated work.
        winner = _replay(store, key, identity)
        if winner is not None:
            return winner
        raise ValueError("Context preservation source history changed")
    _validate_native(native_evidence)
    try:
        store.put_operation_record(segment_id=key, segment_type="harness",
            content={"prepared": prepared}, content_type="application/json",
            created_by_agent_id=agent_id, created_by_actor_ref=actor,
            metadata={"kind": "session_preservation", "source": "execution",
                      "prepared_sha256": _digest(prepared), "native_evidence": native_evidence})
    except (ValueError, UniqueViolation) as exc:
        # A concurrent retry may have committed the same operation. Its first
        # immutable receipt wins; never overwrite a different actor/source.
        winner = _replay(store, key, identity)
        if winner is None:
            raise exc
        return winner
    return prepared
