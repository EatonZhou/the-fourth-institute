"""ResourceService projection of committed SocietyService collaboration objects."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterable

from .timing import current as current_timer, timed

if TYPE_CHECKING:
    from .service import ResourceService


# A collaboration bundle that takes longer is reported with its step timings.
SLOW_COLLABORATION_SYNC_SECONDS = 0.5


@timed
def sync_collaboration_resources(
    service: "ResourceService",
    resources: Iterable[Mapping[str, Any]],
    *, domain_identity: str = "",
) -> dict[str, Any]:
    if domain_identity not in {"", "society-service"}:
        raise PermissionError("Internal domain projection identity required")
    timer = current_timer()
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in resources:
        if not isinstance(item, Mapping):
            raise ValueError("collaboration resources must be objects")
        descriptor = item.get("descriptor")
        if set(item) != {"descriptor"} or not isinstance(descriptor, Mapping):
            raise ValueError("collaboration projection requires only a descriptor object")
        clean_descriptor = service._collaboration.normalize(descriptor)
        resource_id = clean_descriptor["resource_id"]
        if resource_id in seen:
            raise ValueError(
                f"collaboration bundle contains duplicate resource_id: {resource_id}"
            )
        seen.add(resource_id)
        prepared.append(clean_descriptor)
    if not prepared:
        raise ValueError("collaboration bundle must contain at least one resource")
    mark = timer.add("prepare", timer.started)

    results: list[dict[str, Any]] = []
    notifications: list[tuple[str, str]] = []
    from .domain_projection_evidence import (
        EVIDENCE_COLLECTION, EVIDENCE_SCHEMA, SOURCE_COLLECTION,
        projection_fingerprint, tombstone_source, same_descriptor, matches_tombstone_source,
        historical_tombstone_retry,
    )
    from management_portal import permission_system
    from management_portal.society_service.business_fact_views import projection_transaction, live_projection
    with projection_transaction(prepared, service._collaboration._registry) as connection, \
            service._collaboration.projection_scope(connection=connection) as writer:
        # Validate all deletion/Workspace lifecycle events before any live
        # domain metadata. Invalid mixed bundles never partially commit.
        if any(descriptor["resource_id"].startswith("workspace_") for descriptor in prepared):
            raise ValueError("Workspace mutations belong to the Workspace store")
        lifecycle = {
            descriptor["resource_id"]: service._collaboration.tombstone(
                descriptor, writer, source=tombstone_source(descriptor),
                trusted_domain=domain_identity == "society-service")
            for descriptor in prepared if descriptor["status"] == "deleted"
        }
        def project_live(descriptor):
            # The domain validates canonical facts and exact retry identity.
            # Reuse its original accepted event only when the format prefix
            # is the sole difference, without rewriting that event.
            accepted = writer.get("domain_live_sources", descriptor["resource_id"])
            event = accepted if same_descriptor(accepted, descriptor) else descriptor
            return live_projection(event, writer, service._collaboration)

        projected = [
            (lifecycle[descriptor["resource_id"]] if descriptor["resource_id"] in lifecycle else
             project_live(descriptor),
             descriptor["status"] != "deleted")
            for descriptor in prepared
        ]
        for descriptor, active in projected:
            resource_id = descriptor["resource_id"]
            resource_type = service._identity.parse(resource_id).resource_type
            saved, changed = service._collaboration.put_with_change(descriptor, writer=writer)
            # The object and its place in the permission trees commit together.
            permissions_changed = permission_system.sync_resource(
                saved if active else {**saved, "status": "deleted"}, resource_type=resource_type,
                connection=connection)
            if changed or permissions_changed:
                notifications.append((resource_id, resource_type))
            results.append({"resource_id": resource_id, "descriptor": saved})
        for incoming in prepared:
            if incoming["status"] != "deleted":
                accepted = writer.get("domain_live_sources", incoming["resource_id"])
                if not same_descriptor(accepted, incoming):
                    writer.put("domain_live_sources", incoming["resource_id"], incoming)
        # The public RPC cannot supply this identity. Its authenticated
        # dispatcher supplies it after verifying the service assertion.
        # A shape/writer label alone never creates provenance evidence.
        for incoming, result in zip(prepared, results):
            if incoming["status"] != "deleted" or result["descriptor"]["status"] != "deleted":
                continue
            rid = incoming["resource_id"]
            source = tombstone_source(incoming)
            accepted = writer.get(SOURCE_COLLECTION, rid)
            receipt = writer.get(EVIDENCE_COLLECTION, rid)
            if not (matches_tombstone_source(accepted, incoming)
                    or historical_tombstone_retry(accepted, receipt, incoming)):
                writer.put(SOURCE_COLLECTION, rid, source)
            if domain_identity:
                evidence = {
                    "schema": EVIDENCE_SCHEMA, "domain_identity": domain_identity,
                    "resource_id": rid, "incoming_descriptor": incoming,
                    "descriptor_version": projection_fingerprint(result["descriptor"]),
                }
                existing = writer.get(EVIDENCE_COLLECTION, rid)
                # A retry must never replace the first authenticated proof
                # with later descriptor values. Historical comparison
                # will reject that receipt if its target/source changed.
                same_event = (existing is not None
                              and same_descriptor(existing.get("incoming_descriptor"), incoming))
                if not same_event:
                    writer.put(EVIDENCE_COLLECTION, rid, evidence)
        mark = timer.add("descriptor", mark)

    for resource_id, resource_type in notifications:
        service._notify_mutation(
            path=service._collaboration.change_key(resource_id),
            resource_id=resource_id,
            resource_type=resource_type,
            kind="updated",
        )
    timer.add("notify", mark)
    timer.report_if_slow(
        "collaboration sync", SLOW_COLLABORATION_SYNC_SECONDS,
        resources=len(prepared), changed=len(notifications),
    )

    return {
        "schema": "tfi.collaboration-sync-result.v1",
        "resources": results,
    }
