"""A research direction resolves to its place on its Project's research map."""
from __future__ import annotations

import json

from .common import canonical_record


def install_resolver(service):
    from management_portal import research_map_store

    def resolve(address, *, actor="", **_context):
        resource_id = address.split("/", 1)[0]
        identity = research_map_store.direction(resource_id)
        if identity is None:
            raise KeyError(resource_id)
        project_id = identity["project_id"]
        current = research_map_store.read(project_id)
        item = next((entry for entry in current["directions"] if entry["direction_id"] == resource_id), None)
        detail = {"project_id": project_id, "removed": item is None, "map_revision": current["revision"],
                  **(item or {"direction_id": resource_id})}
        record = canonical_record(resource_id, {key: value for key, value in detail.items() if key != "direction_id"})
        return {"address": address, "root_id": resource_id, "resource_type": "direction", "target_kind": "direction",
                "record": {**record, "resource_id": resource_id, "resource_type": "direction",
                           "title": str((item or {}).get("title") or resource_id), "project_ids": [project_id]},
                "detail": detail, "exists": item is not None, "content_kind": "json", "filename": resource_id + ".json"}

    def read(resolved, **_context):
        return json.dumps(resolved["detail"], ensure_ascii=False).encode(), "application/json", {"filename": resolved["filename"]}

    service.register_resolver("direction", resolve, read_content=read)
