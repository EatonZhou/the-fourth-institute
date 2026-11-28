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

from management_common.json_columns import json_value
from psycopg.errors import UniqueViolation

from .agent_data_store import get_agent_data_store, stable_segment_id
from .context_seed import CONTEXT_SEED_SCHEMA


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


def _trace_seed(store, agent_id):
    snapshot = store.agent_context_snapshot(agent_id)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("segments"), list):
        raise ValueError("Cannot preserve context: canonical effective history is unavailable")
    segments = snapshot["segments"]
    if not segments and not snapshot.get("empty_context"):
        raise ValueError("Cannot preserve context: empty history has no explicit boundary")
    return {
        "schema": CONTEXT_SEED_SCHEMA, "source_agent_id": agent_id,
        "empty_context": not segments,
        "segments": [{"segment_id": item["segment_id"], "provider_turn_id": item.get("provider_turn_id") or ""}
                     for item in segments],
    }


def validate_context_preservation(prepared, *, store=None):
    """Verify the receipt and unchanged trace revision before accepting its seed."""
    if prepared.get("schema") != SCHEMA:
        raise ValueError("Invalid context preservation receipt")
    store = store or get_agent_data_store()
    agent_id = prepared["agent_id"]
    source = _source(store, agent_id, prepared["source_binding"])
    with store.database.read() as connection:
        row = connection.execute(
            "SELECT data_json::jsonb#>>'{metadata,prepared_sha256}' AS digest FROM agent_operations "
            "WHERE operation_id=%s AND agent_id=%s AND kind='artifact'",
            (prepared["receipt_id"], agent_id),
        ).fetchone()
        if row is None or row["digest"] != _digest(prepared):
            raise ValueError("Invalid context preservation receipt identity")
        expected = prepared["source"]
        if prepared.get("native_boundary") and prepared["seed"]["segments"]:
            # Older releases appended a replacement boundary at commit, changing
            # the trace revision. Only that recorded commit can justify the change.
            commit_id = stable_segment_id("session-preservation-commit", prepared["receipt_id"])
            commit = connection.execute(
                "SELECT data_json::jsonb->'source' AS source FROM agent_operations WHERE operation_id=%s "
                "AND agent_id=%s AND kind='session_preserved' AND data_json::jsonb->>'receipt_id'=%s",
                (commit_id, agent_id, prepared["receipt_id"]),
            ).fetchone()
            if commit is None:
                raise ValueError("Legacy native preservation boundary was never committed to the Agent trace")
            expected = json_value(commit["source"])
    if source != expected:
        raise ValueError("Context preservation source history changed")
    if prepared.get("history_source") != "agent_trace":
        # Legacy receipts could contain a session-scoped window or a native
        # archive. Only reuse it when every reference matches the effective trace.
        actual = _trace_seed(store, agent_id)
        seed = prepared["seed"]
        if ([r["segment_id"] for r in seed["segments"]] != [r["segment_id"] for r in actual["segments"]]
                or seed["empty_context"] != actual["empty_context"]):
            raise ValueError("Legacy context preservation receipt differs from the effective Agent trace")
        if _source(store, agent_id, prepared["source_binding"]) != source:
            raise ValueError("Context preservation source history changed")


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
    """Return the validated references already present in the Agent trace."""
    validate_context_preservation(prepared, store=store)
    return prepared["seed"]


def prepare_context_preservation(agent_id, *, actor, operation_id, source_binding, store=None):
    """Freeze the full effective Agent trace, retaining its revision for admission."""
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
    seed = _trace_seed(store, agent_id)
    prepared = {"schema": SCHEMA, **identity, "source": source, "seed": seed,
                "history_source": "agent_trace", "receipt_id": key}
    if _source(store, agent_id, source_binding) != source:
        raise ValueError("Context preservation source history changed")
    try:
        store.put_operation_record(segment_id=key, segment_type="harness",
            content={"prepared": prepared}, content_type="application/json",
            created_by_agent_id=agent_id, created_by_actor_ref=actor,
            metadata={"kind": "session_preservation", "source": "execution",
                      "prepared_sha256": _digest(prepared)})
    except (ValueError, UniqueViolation) as exc:
        # A concurrent retry may have committed the same operation. Its first
        # immutable receipt wins; never overwrite a different actor/source.
        winner = _replay(store, key, identity)
        if winner is None:
            raise exc
        return winner
    return prepared
