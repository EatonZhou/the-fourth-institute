from __future__ import annotations

import heapq
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .access import ResourceAccessPolicy
from . import timing
from .contracts import ResourceQuery, empty_query_result
from management_portal.portal_access_control import (
    PortalAccessStore,
)
from management_common.text_values import stripped_text


def _copy(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


# ``portal_access`` is accepted and unused: IndexStore and ResourceService
# query paths whose bytecode the business-fact consumer contracts pin still
# pass it.
def resource_is_readable(
    record: Mapping[str, Any],
    actor: str,
    *,
    portal_access: PortalAccessStore | None = None,
    access_policy: ResourceAccessPolicy | None = None,
) -> bool:
    return readable_flags([record], actor, access_policy=access_policy)[0]


def readable_flags(
    records: Sequence[Mapping[str, Any]],
    actor: str,
    *,
    access_policy: ResourceAccessPolicy | None = None,
) -> list[bool]:
    """Whether the actor may read each record, decided in one permission batch."""
    policy = access_policy or ResourceAccessPolicy()
    identifiers = [str(record.get("resource_id") or record.get("root_id") or "") for record in records]
    readable = policy.readable_ids([identifier for identifier in identifiers if identifier], actor=actor)
    return [bool(identifier) and identifier in readable for identifier in identifiers]


def _score(record: Mapping[str, Any], query: str) -> float:
    needle = query.casefold().strip()
    if not needle:
        return 1.0
    best = 0.0
    values = record.get("search_terms")
    if not isinstance(values, (list, tuple, set)):
        values = (
            record.get("resource_id"),
            record.get("title"),
            record.get("summary"),
        )
    summary = stripped_text(record.get("summary")).casefold()
    identity_terms = {
        stripped_text(record.get("resource_id")).casefold(),
        stripped_text(record.get("title")).casefold(),
    }
    for value in values:
        term = stripped_text(value).casefold()
        if not term:
            continue
        if term == summary and term not in identity_terms:
            continue
        if term == needle:
            best = max(best, 1000.0)
        elif term.startswith(needle):
            best = max(best, 700.0 - min(len(term), 300) * 0.01)
        else:
            position = term.find(needle)
            if position >= 0:
                best = max(
                    best,
                    400.0 - position - min(len(term), 300) * 0.01,
                )
    return best


def score_id_title(record: Mapping[str, Any], query: str) -> float:
    return _score(
        {"search_terms": (record.get("resource_id"), record.get("title"))},
        query,
    )


def _exact_resource_id(
    records: Iterable[Mapping[str, Any]],
    query: ResourceQuery,
) -> str:
    if not query.q:
        return ""
    return (
        query.q
        if any(stripped_text(record.get("resource_id")) == query.q for record in records)
        else ""
    )


def _matches_filters(
    attributes: Mapping[str, Any],
    query: ResourceQuery,
) -> bool:
    for key, expected in query.filters.items():
        raw_value = attributes.get(key)
        values = (
            raw_value
            if isinstance(raw_value, (list, tuple, set, frozenset))
            else (raw_value,)
        )
        actual = {stripped_text(value) for value in values if stripped_text(value)}
        if expected and not (expected & actual):
            return False
    return True


def _matches_query(
    record: Mapping[str, Any],
    query: ResourceQuery,
    *,
    exact_resource_id: str,
    include_access: bool,
    include_search: bool = True,
    portal_access: PortalAccessStore | None = None,
    access_policy: ResourceAccessPolicy | None = None,
) -> bool:
    if (
        exact_resource_id
        and stripped_text(record.get("resource_id")) != exact_resource_id
    ):
        return False
    if include_access and not resource_is_readable(
        record,
        query.actor,
        portal_access=portal_access,
        access_policy=access_policy,
    ):
        return False
    resource_type = stripped_text(record.get("resource_type"))
    if query.resource_types and resource_type not in query.resource_types:
        return False
    facets = {stripped_text(value) for value in record.get("facets") or ()}
    if query.facets and not query.facets.issubset(facets):
        return False
    attributes = (
        record.get("attributes")
        if isinstance(record.get("attributes"), Mapping)
        else {}
    )
    status = stripped_text(attributes.get("status"))
    if not query.statuses and status.casefold() == "deleted":
        return False
    projects = {stripped_text(value) for value in attributes.get("project_ids") or ()}
    if query.projects and not (query.projects & projects):
        return False
    if query.statuses and status not in query.statuses:
        return False
    if not _matches_filters(attributes, query):
        return False
    return not include_search or not query.q or _score(record, query.q) > 0


def prefilter_query_candidates(
    records: Iterable[Mapping[str, Any]],
    query: ResourceQuery,
) -> list[Mapping[str, Any]]:
    """Apply filters backed by stable index fields before dynamic projection."""
    candidates = list(records)
    exact_resource_id = _exact_resource_id(candidates, query)
    selected: list[Mapping[str, Any]] = []
    for record in candidates:
        if exact_resource_id and stripped_text(record.get("resource_id")) != exact_resource_id:
            continue
        if query.resource_types and stripped_text(record.get("resource_type")) not in query.resource_types:
            continue
        facets = {stripped_text(value) for value in record.get("facets") or ()}
        if query.facets and not query.facets.issubset(facets):
            continue
        if query.q and _score(record, query.q) <= 0:
            continue
        selected.append(record)
    return selected


def filter_query_candidates(
    records: Iterable[Mapping[str, Any]],
    query: ResourceQuery,
) -> list[Mapping[str, Any]]:
    """Apply all actor-independent filters after dynamic record projection."""
    candidates = list(records)
    exact_resource_id = _exact_resource_id(candidates, query)
    return [
        record
        for record in candidates
        if _matches_query(
            record,
            query,
            exact_resource_id=exact_resource_id,
            include_access=False,
        )
    ]


def query_records(
    records: Iterable[Mapping[str, Any]],
    query: ResourceQuery,
    *,
    index_updated_at: str = "",
    exact_address: bool = False,
    portal_access: PortalAccessStore | None = None,
    access_policy: ResourceAccessPolicy | None = None,
) -> dict[str, Any]:
    mark = time.perf_counter()
    candidates = list(records)
    exact_resource_id = _exact_resource_id(candidates, query)
    access_policy = access_policy or ResourceAccessPolicy()
    readable = access_policy.readable_ids(
        [str(record.get("resource_id") or "") for record in candidates], actor=query.actor,
    )
    ranked: list[tuple[float, Mapping[str, Any]]] = []
    for record in candidates:
        if record.get("resource_id") not in readable:
            continue
        if not _matches_query(
            record,
            query,
            exact_resource_id=exact_resource_id,
            include_access=False,
            include_search=not exact_address,
            portal_access=portal_access,
            access_policy=access_policy,
        ):
            continue
        score = 1000.0 if exact_address else _score(record, query.q)
        ranked.append((score, record))
    rank_key = lambda row: (
        -row[0],
        stripped_text(row[1].get("resource_type")),
        stripped_text(row[1].get("title")).casefold(),
        stripped_text(row[1].get("resource_id")),
    )
    result = empty_query_result(query, index_updated_at=index_updated_at)
    result["total"] = len(ranked)
    end = query.offset + query.limit
    if end < len(ranked):
        # Exact total still requires checking every candidate, but a small
        # result page does not require sorting every visible candidate.
        selected = heapq.nsmallest(end, ranked, key=rank_key)[query.offset:]
    else:
        ranked.sort(key=rank_key)
        selected = ranked[query.offset:end]
    mark = timing.lap("select_and_rank", mark)
    resources = []
    # Leave out what the result never carries before copying: the search
    # terms (and unrequested attributes) were copied and then dropped.
    omitted = {"search_terms"} if query.include_attributes else {"search_terms", "attributes"}
    for score, record in selected:
        item = _copy({key: value for key, value in record.items() if key not in omitted})
        if query.q:
            item["score"] = round(score, 3)
        resources.append(item)
    timing.lap("copy_results", mark)
    result["resources"] = resources
    return result
