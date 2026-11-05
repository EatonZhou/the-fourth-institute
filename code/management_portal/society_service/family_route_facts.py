"""Bounded, read-only facts for one Router's current direct routing scope.

This is input preparation, not routing or authorization to deliver. Each
source is read in a batch; admission still checks the selected member later.
"""
from __future__ import annotations

from . import resident_access, resident_conversation_stream, resident_execution_provider
from .resident_context.service import resident_context


SUMMARY_FIELDS = ("primary_ownership", "route_when", "avoid_when", "durable_expertise",
                  "supporting_expertise", "current_focus")
SUMMARY_BUDGET = 12000
MEMBER_BUDGET = 900


def _summary(document: dict, budget: int) -> tuple[dict, bool, int]:
    source = document.get("summary") or {}
    output, used, truncated = {}, 0, False
    # Bound only optional descriptions, never the roster or original request.
    for field in SUMMARY_FIELDS:
        raw = source.get(field)
        values = raw if isinstance(raw, list) else [raw] if raw else []
        selected = []
        for value in values:
            text = str(value)
            remaining = min(240, max(0, budget - used))
            excerpt = text[:remaining]
            truncated |= len(excerpt) != len(text)
            if excerpt:
                selected.append(excerpt)
                used += len(excerpt)
        if selected:
            output[field] = selected if isinstance(raw, list) else selected[0]
    return output, truncated, used


def snapshot(facade: dict, router: dict, routing: dict) -> dict:
    """Use the real Router's read access, including private-Family boundaries."""
    actor = router["resident_id"]
    selected = list(dict.fromkeys([*routing["members"], routing["steward"]]))
    selected = [rid for rid in selected if rid]
    records = {row["resident_id"]: row for row in
               resident_access.visible_resident_records(selected, actor, "portal")}
    visible = [rid for rid in selected if rid in records]
    documents = {row["resident_id"]: row for row in
                 resident_context().store.documents(visible)} if visible else {}
    states = resident_conversation_stream.get_repository().states(visible) if visible else {}
    candidates, remaining = [], SUMMARY_BUDGET
    for rid in selected:
        candidate = {"resident_id": rid, "role": "steward" if rid == routing["steward"] else "member"}
        record = records.get(rid)
        if record is None:
            candidates.append({**candidate, "availability": "unavailable"})
            continue
        doc = documents.get(rid, {})
        summary, truncated, used = _summary(doc, min(MEMBER_BUDGET, remaining))
        remaining -= used
        capabilities = resident_execution_provider.provider_for(record).capabilities
        state = states.get(rid, {})
        candidate.update(
            title=record.get("title") or "", availability="known",
            summary=summary, summary_truncated=truncated,
            summary_revision=int(doc.get("summary_revision") or doc.get("generation") or 0),
            summary_stale=doc.get("stale") is not False,
            summary_updated_at=str(doc.get("summary_updated_at") or ""),
            work_state=state.get("work_state") or "unknown",
            intake_state=state.get("intake_state") or "unknown",
            queued_count=state.get("queued_count"),
            accepts_images=capabilities.supports_media, accepts_files=capabilities.local_tools,
        )
        candidates.append(candidate)
    # Corrections are examples, not a second roster or a wider history search.
    from . import lineage_domain
    try:
        corrections = lineage_domain.get_lineage_graph_state(facade["lineage_id"],
            {"key": "family_corrections_" + facade["resident_id"]}, actor)
    except (KeyError, PermissionError):
        corrections = None
    examples = (corrections or {}).get("value")
    recent = [item for item in examples if isinstance(item, dict)][-4:] if isinstance(examples, list) else []
    return {"candidates": candidates, "corrections": [
        {"from": item["from"].get("resident_id") if isinstance(item.get("from"), dict) else None,
         "to": item["to"].get("resident_id") if isinstance(item.get("to"), dict) else None,
         "request": str(item.get("request") or "")[:240],
         "reason": str(item.get("reason") or "")[:240]} for item in recent],
        "corrections_available": corrections is not None,
        "corrections_truncated": isinstance(examples, list) and len(examples) > len(recent)}
