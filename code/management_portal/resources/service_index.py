"""ResourceService discovery: index queries and gets with current collaboration facts,
warm-up, and routing source changes to the index."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Iterable

from .changes import ResourceChange
from .collaboration_registry import (
    RESOURCE_TYPES as COLLABORATION_RESOURCE_TYPES,
    descriptor_resource_record,
)
from .contracts import ResourceQuery
from .errors import ResourceServiceUnavailable
from .timing import lap
from .query import query_records

if TYPE_CHECKING:
    from .service import ResourceService


def query(service: "ResourceService", **kwargs: Any) -> dict[str, Any]:
    query = ResourceQuery.from_kwargs(kwargs)
    enabled_types = service.resource_types.enabled_types()
    if query.resource_types and not enabled_types.intersection(query.resource_types):
        return query_records((), query, portal_access=service._portal_access)
    if query.q:
        try:
            parsed = service.parse_address(query.q)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.value == query.q.strip("/"):
            if parsed.resource_type not in enabled_types:
                return query_records((), query, exact_address=True, portal_access=service._portal_access)
            try:
                reference = service.reference(
                    parsed.value,
                    actor=query.actor,
                )
            except (KeyError, FileNotFoundError):
                return query_records(
                    (),
                    query,
                    exact_address=True,
                    portal_access=service._portal_access,
                )
            return query_records(
                (reference,),
                query,
                exact_address=True,
                portal_access=service._portal_access,
            )
    return service._index().query(
        records_projector=service._collaboration_descriptors,
        records_projector_types=COLLABORATION_RESOURCE_TYPES,
        **kwargs,
    )


def get(service: "ResourceService", resource_id: str, *, actor: str = "") -> dict[str, Any]:
    try:
        resource_types = (service._identity.parse(resource_id).resource_type,)
    except ValueError:
        resource_types = service._identity.id_exception_types
    if len(resource_types) == 1 and resource_types[0] in COLLABORATION_RESOURCE_TYPES:
        try:
            descriptor = service._collaboration.get(resource_id)
        except KeyError:
            if resource_types[0] == "workspace":
                # A removed Workspace cannot expose file bytes through a
                # stale discovery record. Its descriptor owns its path.
                raise
            # Pre-descriptor index records retain their existing contract.
            # Registered objects never depend on discovery refresh progress.
            pass
        else:
            record = descriptor_resource_record(descriptor)
            # A Workspace descriptor already is one authoritative row:
            # do not mix its title/version with attributes from a later read.
            if resource_types[0] != "workspace":
                record = service._with_collaboration_descriptor(record)
            from management_portal.society_service.lineage_access_policy import hidden_private_ids

            if not service._access.can_read(record, actor=str(actor or "")) or hidden_private_ids(actor, [record]):
                raise KeyError(resource_id)
            record["capabilities"] = {name: True for name in ("discover", "read", "manage")}
            record.pop("search_terms", None)
            return record
    record = service._index().get_resource(
        resource_id,
        resource_types=resource_types,
        actor=actor,
    )
    service._identity.validate(resource_id, str(record.get("resource_type") or ""))
    service._access.authorize_read(record, actor=str(actor or ""))
    return service._with_collaboration_descriptor(record)


def with_collaboration_descriptors(
    service: "ResourceService",
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    from management_portal.society_service.business_fact_views import project_resources as domain_facts
    return domain_facts(service._collaboration_descriptors(records))


def collaboration_descriptors(
    service: "ResourceService",
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    items = list(records)
    attributes = service._collaboration.attributes_for_records(
        record
        for record in items
        if record.get("resource_type") in COLLABORATION_RESOURCE_TYPES
    )
    return [
        {
            **record,
            "attributes": attributes[record["resource_id"]],
        }
        if record.get("resource_id") in attributes
        else record
        for record in items
    ]


def warm(service: "ResourceService") -> None:
    try:
        index = service._index()
    except RuntimeError:
        # A service built without an index (test servers, tools) has
        # nothing more to load.
        return
    deadline = time.monotonic() + 360
    while True:
        try:
            index.query(actor="", limit=1)
            return
        except ResourceServiceUnavailable:
            if time.monotonic() >= deadline:
                return
            time.sleep(2)


def route_change(service: "ResourceService", item: ResourceChange) -> list[str]:
    mark = time.perf_counter()
    try:
        return service._route_change_untimed(item)
    finally:
        lap("notify.route", mark)


def route_change_untimed(service: "ResourceService", item: ResourceChange) -> list[str]:
    mark = time.perf_counter()
    index = service._index()
    detected_type = item.resource_type
    if item.resource_id and detected_type:
        service._identity.validate(item.resource_id, detected_type)
    elif item.resource_id:
        detected_type = service._identity.parse(item.resource_id).resource_type
    if detected_type:
        if not service._identity.is_registered(detected_type):
            raise ValueError(f"unregistered resource type: {detected_type}")
    lap("notify.route.identity", mark)
    return index.change_targets(
        path=item.path,
        resource_id=item.resource_id,
        resource_type=detected_type,
    )


def enqueue_change(
    service: "ResourceService",
    targets: tuple[str, ...],
    item: ResourceChange,
) -> list[str]:
    mark = time.perf_counter()
    try:
        return service._index().queue_change(
            targets,
            path=item.path,
        )
    finally:
        lap("notify.enqueue", mark)


def notify_source_changed(
    service: "ResourceService",
    *,
    path: str = "",
    resource_id: str = "",
    resource_type: str = "",
    kind: str = "unknown",
    wait: bool = False,
    timeout: float = 90.0,
) -> dict[str, object]:
    change = ResourceChange(
        kind=kind,
        path=path,
        resource_id=resource_id,
        resource_type=resource_type,
    )
    notification = service._changes.notify(change)
    result = notification.as_dict()
    if not wait:
        return result
    finished = (
        service._index().wait_for_refresh(
            notification.targets,
            timeout=max(0.0, float(timeout)),
        )
        if notification.targets
        else True
    )
    return {
        **result,
        "waited": True,
        "finished": bool(finished),
    }
