"""Deliver a frozen PostgreSQL window, with a durable per-thread import receipt.

The caller owns the provider thread execution lock. An unacknowledged import
is ambiguous: the append-only provider operation must not be retried blindly.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from .agent_data_store import AgentDataStore, get_agent_data_store
from .codex_history import response_items_from_segments


class CodexHistoryDelivery:
    def __init__(
        self, *, references: Sequence[Mapping[str, Any]], route_id: str,
        agent_id: str, actor: str, execution_id: str,
        send: Callable[[str, list[dict[str, Any]]], None],
        store: AgentDataStore | None = None,
    ) -> None:
        self.store = store or get_agent_data_store()
        self.references = list(references)
        self.route_id = route_id
        self.agent_id = agent_id
        self.actor = actor
        self.execution_id = execution_id
        self.send = send

    def _read(self, identity: str) -> dict[str, Any] | None:
        try:
            return self.store.operation_record(identity)
        except KeyError:
            return None

    def initialize(self, thread_id: str) -> None:
        if not self.references:
            return
        # Resolve and validate every part before recording an external intent.
        items = response_items_from_segments(self.store.fork_context_segments(self.references))
        digest = hashlib.sha256(json.dumps(
            items, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        key = hashlib.sha256(f"codex\0{self.route_id}\0{thread_id}".encode()).hexdigest()[:32]
        intent_id = f"segment_fork_import_{key}"
        receipt_id = f"segment_fork_import_ack_{key}"
        receipt = self._read(receipt_id)
        if receipt is not None:
            if receipt["content"]["history_sha256"] != digest:
                raise ValueError("Codex thread already imported a different PostgreSQL Fork window")
            return
        if self._read(intent_id) is not None:
            raise RuntimeError(
                "Codex Fork history import outcome is unknown; refusing duplicate history injection"
            )
        evidence = {
            "provider": "codex", "backend_route_id": self.route_id,
            "backend_session_id": thread_id, "history_sha256": digest,
            "segments": self.references,
        }

        def record(identity: str, state: str) -> None:
            self.store.put_operation_record(
                segment_id=identity, segment_type="harness",
                content={**evidence, "state": state}, content_type="application/json",
                metadata={"kind": "fork_history_delivery", "source": "execution"},
                created_by_actor_ref=self.actor, created_by_agent_id=self.agent_id,
                execution_id=self.execution_id,
            )

        record(intent_id, "requested")
        self.send(thread_id, items)
        record(receipt_id, "acknowledged")
