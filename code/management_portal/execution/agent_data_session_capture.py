"""The Provider session an execution runs in: its binding, inherited context and compactions."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .agent_data_output_capture import json_safe, utc_moment
from .agent_data_projection_state import ExecutionProjection
from .agent_data_store import AgentDataStore, stable_segment_id


def persist_provider_compaction(
    store: AgentDataStore,
    *,
    provider_session_ref: str,
    agent_id: str,
    operation_id: str,
    actor_ref: str,
    provider: str,
    content: Mapping[str, Any],
    created_at: str,
    retain_from: str = "",
    provider_compaction_ref: str = "",
    skip_segment_ids: set[str] | frozenset[str] = frozenset(),
    kept_execution_id: str = "",
) -> tuple[str, bool]:
    """Record a Provider compaction where it happened in the Agent trace.

    A session's current context is read from the trace: its transcript from
    its last compaction on (``AgentDataStore.session_context_ids``).  Without
    ``retain_from`` the compaction replaces everything the session held, as
    Codex's replacement history already contains the kept recent items, and
    it goes at the end of the trace.  With it, the members recorded at or
    after that time (the messages the Provider kept verbatim, and later work)
    stay after the compaction: it goes before the first of them.  What
    ``kept_execution_id`` recorded stays after it too (a compaction before
    the Provider took that turn's input).  A compaction already recorded
    under ``provider_compaction_ref`` is not recorded again.  Returns the
    compaction Segment ID and whether this call created it.
    """

    canonical_content = json_safe(dict(content))
    if provider_compaction_ref and provider_session_ref:
        recorded = store.session_compaction(provider_session_ref, provider_compaction_ref, agent_id=agent_id)
        if recorded:
            return recorded, False
    segment_id = stable_segment_id(
        "provider-compaction",
        operation_id,
        hashlib.sha256(
            json.dumps(
                canonical_content,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    )
    if segment_id in skip_segment_ids:
        return segment_id, False
    members = (
        store.segment_members(store.session_context_ids(provider_session_ref, agent_id=agent_id))
        if provider_session_ref
        else []
    )
    retain_after = utc_moment(retain_from) if retain_from else None
    retained_ids = [
        item["segment_id"]
        for item in members
        if item["segment_type"] != "compaction" and (
            (kept_execution_id and item["execution_id"] == kept_execution_id)
            or (
                retain_after is not None
                and (utc_moment(item.get("finished_at") or item.get("started_at")) or retain_after) >= retain_after
            )
        )
    ]
    retained = set(retained_ids)
    metadata: dict[str, Any] = {
        "source": "provider_compaction",
        "kind": "compaction",
    }
    if provider_compaction_ref:
        metadata["provider_compaction_ref"] = provider_compaction_ref
    store.put_segment(
        segment_id=segment_id,
        segment_type="compaction",
        content=canonical_content,
        content_type="application/json",
        started_at=created_at,
        finished_at=created_at,
        status="completed",
        created_by_actor_ref=actor_ref,
        performed_by_actor_ref=provider,
        metadata=metadata,
        created_by_agent_id=agent_id,
        execution_id=operation_id,
    )
    store.add_relations([
        {
            "relation_type": "compressed_from",
            "source_segment_id": item["segment_id"],
            "target_segment_id": segment_id,
            "operation_id": operation_id,
            "operation_type": "provider.compact",
            "operation_version": "1",
            "actor_ref": provider,
        }
        for item in members
        if item["segment_id"] not in retained
    ])
    store.append_agent_segments(agent_id, [segment_id], actor_ref=provider)
    if retained:
        store.place_in_trace_before(agent_id, segment_id, retained, actor_ref=provider)
    return segment_id, True


class SessionCapture:
    """Binds an execution to its Provider session and records what that session holds."""

    def __init__(self, store: AgentDataStore) -> None:
        self.store = store

    def bind_provider_session(
        self,
        projection: ExecutionProjection,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str,
        source_backend_session_id: str = "",
        source_backend_route_id: str = "",
    ) -> None:
        clean_session = str(backend_session_id or "").strip()
        if projection.request.session.mode == "stateless":
            clean_session = projection.request.execution_id
        if not clean_session:
            return
        if projection.provider_session_ref:
            if (
                projection.backend_session_id
                and projection.backend_session_id != clean_session
            ):
                raise RuntimeError("Provider Session changed during one execution")
            return
        session = self.store.ensure_provider_session(
            provider=provider,
            backend_route_id=backend_route_id,
            backend_session_id=clean_session,
            agent_id=projection.agent_id,
            actor_ref=projection.request.actor,
            source_backend_session_id=source_backend_session_id,
            source_backend_route_id=source_backend_route_id,
            # A native fork for a Resident (a restart, a Fork) runs under its
            # binding's name; the inherited context is marked in the
            # Resident's own trace.
            context_agent_id=str(projection.request.metadata.get("resident_id") or ""),

        )
        projection.provider_session_ref = str(session["provider_session_ref"])
        projection.provider = provider
        projection.backend_session_id = clean_session
        projection.backend_route_id = backend_route_id
        self.store.bind_execution_provider_session(
            projection.request.execution_id,
            provider_session_ref=projection.provider_session_ref,
        )
        operation = projection.request.operation
        context_input = projection.request.metadata.get("fork_context_input") or {}
        if operation == "compact" and context_input:
            references = context_input["segments"]
            segments = self.store.fork_context_segments(references)
            self._mark_context_received(projection, [item["segment_id"] for item in segments])
        if operation not in {"turn", "generate"}:
            return
        if projection.pending_compactions:
            pending = list(projection.pending_compactions)
            projection.pending_compactions.clear()
            for content, created_at, options in pending:
                # Compacted before the Provider took this turn's input: the
                # turn's records stay after the compaction in the trace.
                self.persist_compaction(
                    projection,
                    content=content,
                    created_at=created_at,
                    kept_execution_id=projection.request.execution_id,
                    **options,
                )
        if context_input:
            # The inherited prefix already belongs to the Agent trace; the
            # mark puts it in the new session's context, before this turn's
            # input.  The new user Segment holds only the new input.
            references = context_input["segments"]
            segments = self.store.fork_context_segments(references)
            self._mark_context_received(
                projection, [item["segment_id"] for item in segments],
            )

    def _mark_context_received(
        self, projection: ExecutionProjection, segment_ids: list[str],
    ) -> None:
        """Mark in the trace where the session received a Fork's inherited context.

        The inherited Segments belong to the source's history; this mark is
        what puts them in the session's transcript, so that a Fork from a
        later turn of this session inherits them too.  A turn's inherited
        context comes before its input.
        """

        self.store.record_context_received(
            projection.agent_id,
            projection.provider_session_ref,
            segment_ids,
            actor_ref=projection.request.actor,
        )

    def persist_compaction(
        self,
        projection: ExecutionProjection,
        *,
        content: Mapping[str, Any],
        created_at: str,
        retain_from: str = "",
        provider_compaction_ref: str = "",
        kept_execution_id: str = "",
    ) -> tuple[str, bool]:
        segment_id, created = persist_provider_compaction(
            self.store,
            provider_session_ref=projection.provider_session_ref,
            agent_id=projection.agent_id,
            operation_id=projection.request.execution_id,
            actor_ref=projection.request.actor,
            provider=projection.request.provider,
            content=content,
            created_at=created_at,
            retain_from=retain_from,
            provider_compaction_ref=provider_compaction_ref,
            skip_segment_ids=projection.persisted_segments,
            kept_execution_id=kept_execution_id,
        )
        if created:
            projection.persisted_segments.add(segment_id)
            projection.compaction_segment_id = segment_id
        return segment_id, created
