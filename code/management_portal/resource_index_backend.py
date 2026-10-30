"""The resource index service: its default adapters, its change notifications and the process-wide index."""
from __future__ import annotations

import os
import threading
from typing import Any, Iterable

from management_portal.portal_access_control import (
    PortalAccessStore,
    get_portal_access_store,
)
from management_common.text_values import stripped_text
from management_portal.resources.index_runtime import ResourceIndexRuntime
from management_portal import portal_features
from management_portal.notifications import (
    get_notification_publisher,
    resource_projection_subject,
)

from management_portal.resource_index_adapters import ResourceAdapter
from management_portal.resource_index_knowledge import ProjectKnowledgeAdapter, SkillPackageAdapter
from management_portal.resource_index_refresh import AdapterLayer
from management_portal.resource_index_registries import (
    ProjectRegistryAdapter, CollaborationRegistryAdapter, ResourceRegistryAdapter, DeviceRegistryAdapter,
    SavedSearchAdapter, WorkbenchAdapter,
)
from management_portal.resource_index_store import IndexStore


DEFAULT_ADAPTERS = (
    WorkbenchAdapter,
    ProjectKnowledgeAdapter,
    SkillPackageAdapter,
    ProjectRegistryAdapter,
    CollaborationRegistryAdapter,
    ResourceRegistryAdapter,
    DeviceRegistryAdapter,
    SavedSearchAdapter,
)


def _publish_projection_change(adapter: str, result: dict[str, Any]) -> None:
    if str(os.getenv("PORTAL_RESOURCE_SERVICE_PROCESS", "") or "").strip() != "1":
        return
    changed_ids = sorted(
        {
            stripped_text(resource_id)
            for resource_id in [
                *(result.get("upserted_ids") or []),
                *(result.get("deleted_ids") or []),
            ]
            if stripped_text(resource_id)
        }
    )
    snapshot_required = len(changed_ids) > 128
    get_notification_publisher().publish(
        resource_projection_subject(adapter),
        {
            "kind": "projection_updated",
            "adapter": adapter,
            "upserted": int(result.get("upserted") or 0),
            "deleted": int(result.get("deleted") or 0),
            "resource_ids": [] if snapshot_required else changed_ids,
            "snapshot_required": snapshot_required,
        },
    )


class ResourceIndexService(ResourceIndexRuntime):
    def __init__(
        self,
        *,
        store: IndexStore | None = None,
        adapters: Iterable[ResourceAdapter] | None = None,
        start_worker: bool = True,
        portal_access: PortalAccessStore | None = None,
    ) -> None:
        store_instance = store or IndexStore(
            portal_access=portal_access,
        )
        selected_adapters = (
            list(adapters)
            if adapters is not None
            else [adapter() for adapter in (
                *DEFAULT_ADAPTERS[:3],
                *(adapter for _feature, adapters in portal_features.contributions("resource.index_adapters")
                  for adapter in adapters),
                *DEFAULT_ADAPTERS[3:],
            )]
        )
        if adapters is None:
            store_instance.discard_inactive_adapters(
                adapter.name for adapter in selected_adapters
            )
        super().__init__(
            store=store_instance,
            adapters=selected_adapters,
            adapter_layer_factory=AdapterLayer,
            start_worker=start_worker,
            on_projection_changed=_publish_projection_change,
        )


_SERVICE_LOCK = threading.Lock()
_SERVICE: ResourceIndexService | None = None


def get_service() -> ResourceIndexService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = ResourceIndexService(
                    portal_access=get_portal_access_store(),
                )
    return _SERVICE
