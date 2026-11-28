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
        # The first batch uses the original per-thread receipt key. Following
        # batches have consecutive slots under the same thread execution lock.
        key = hashlib.sha256(f"codex\0{self.route_id}\0{thread_id}".encode()).hexdigest()[:32]
        delivered = []
        batch = 0
        while True:
            slot = key if batch == 0 else f"{key}_{batch}"
            intent_id = f"segment_fork_import_{slot}"
            receipt_id = f"segment_fork_import_ack_{slot}"
            receipt = self._read(receipt_id)
            if receipt is None:
                break
            delivered.extend(ref["segment_id"] for ref in receipt["content"]["segments"])
            batch += 1
        if self._read(intent_id) is not None:
            raise RuntimeError(
                "Codex Fork history import outcome is unknown; refusing duplicate history injection"
            )

        positions = {sid: index for index, sid in enumerate(delivered)}
        pending = []
        previous = -1
        for ref in self.references:
            position = positions.get(ref["segment_id"])
            if position is None:
                pending.append(ref)
            elif pending or position <= previous:
                raise ValueError("Codex history cannot be appended in the requested order")
            else:
                previous = position
        if not pending:
            return
        items = response_items_from_segments(self.store.fork_context_segments(pending))
        digest = hashlib.sha256(json.dumps(
            items, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        evidence = {
            "provider": "codex", "backend_route_id": self.route_id,
            "backend_session_id": thread_id, "history_sha256": digest,
            "segments": pending,
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
