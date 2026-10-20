"""SocietyService ``resident`` domain operations of Resident discovery.

Every read answers with the caller's own read access (``actor``); nothing is
widened here.  Searches run in the SocietyService process itself, where the
embedding model lives, rather than in the read pool.
"""
from __future__ import annotations

from typing import Any

from .service import resident_context


def _ids(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    clean = [str(value or "").strip() for value in values if str(value or "").strip()]
    return clean or None


def search_residents(actor: str, query: str, limit: int = 8, resident_ids: list[str] | None = None) -> dict[str, Any]:
    return resident_context().search(actor=actor, query=query, limit=int(limit or 8), resident_ids=_ids(resident_ids))


def set_resident_embeddings_enabled(enabled: bool) -> dict[str, bool]:
    return resident_context().set_embeddings_enabled(enabled)


def resident_routing_candidates(
    actor: str, query: str, limit: int = 40, resident_ids: list[str] | None = None,
) -> dict[str, Any]:
    return resident_context().routing_candidates(
        actor=actor, query=query, limit=int(limit or 40), resident_ids=_ids(resident_ids),
    )


def search_resident_directory(actor: str, query: str = "", limit: int = 30) -> dict[str, Any]:
    return resident_context().search_directory(actor=actor, query=query, limit=int(limit or 30))


def get_resident_contexts(actor: str, resident_ids: list[str]) -> dict[str, Any]:
    return resident_context().contexts(actor=actor, resident_ids=list(resident_ids or []))


def request_resident_summary_rebuild(actor: str, resident_id: str, reason: str = "manual") -> dict[str, Any]:
    return resident_context().request_rebuild(actor=actor, resident_id=resident_id, reason=reason or "manual")


def submit_routing_eval_correction(
    actor: str, query: str, predicted: list[dict[str, Any]], ground_truth_resident_id: str,
    ground_truth_action: str, rationale: str = "",
) -> dict[str, Any]:
    return resident_context().submit_routing_eval_correction(
        actor=actor, query=query, predicted=list(predicted or []),
        ground_truth_resident_id=ground_truth_resident_id, ground_truth_action=ground_truth_action,
        rationale=rationale,
    )
