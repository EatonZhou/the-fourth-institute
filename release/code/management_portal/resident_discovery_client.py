"""Portal's view of Resident discovery, a module of SocietyService (``resident_context``)."""
from __future__ import annotations

from typing import Any


class ResidentDiscoveryUnavailable(RuntimeError):
    pass


class ResidentDiscoveryClient:
    """Resident search, routing recall and summaries, always as the calling actor."""

    def __init__(self, service: Any = None) -> None:
        self._service = service

    def search(
        self, *, actor: str, query: str, limit: int = 8, resident_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._call("search_residents", actor, query, limit, resident_ids or [])

    def routing_candidates(
        self, *, actor: str, query: str, limit: int = 40, resident_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._call("resident_routing_candidates", actor, query, limit, resident_ids or [])

    def search_directory(self, *, actor: str, query: str = "", limit: int = 30) -> dict[str, Any]:
        return self._call("search_resident_directory", actor, query, limit)

    def contexts(self, *, actor: str, resident_ids: list[str]) -> dict[str, Any]:
        return self._call("get_resident_contexts", actor, list(resident_ids or []))

    def request_rebuild(self, *, actor: str, resident_id: str, reason: str = "manual") -> dict[str, Any]:
        return self._call("request_resident_summary_rebuild", actor, resident_id, reason)

    def submit_routing_eval_correction(
        self, *, actor: str, query: str, predicted: list[dict[str, Any]],
        ground_truth_resident_id: str, ground_truth_action: str, rationale: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "submit_routing_eval_correction", actor, query, list(predicted or []),
            ground_truth_resident_id, ground_truth_action, rationale,
        )

    def _call(self, method: str, *args: Any) -> dict[str, Any]:
        from management_portal.society_service import (
            SocietyServiceBusy,
            SocietyServiceUnavailable,
            get_society_service,
        )

        service = self._service or get_society_service()
        try:
            result = getattr(service.resident, method)(*args)
        except (SocietyServiceUnavailable, SocietyServiceBusy, OSError, TimeoutError) as exc:
            raise ResidentDiscoveryUnavailable("Resident discovery is unavailable") from exc
        return dict(result or {})
