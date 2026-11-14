"""Templates remain owned by SocietyService; resources expose its existing read contract."""
from __future__ import annotations

import json

from .common import canonical_record


def install_resolver(service):
    from management_portal import isa_client

    def resolve(address, *, actor="", **_context):
        template_id, separator, _suffix = str(address).partition("/")
        if separator:
            raise ValueError("Templates have no nested resource addresses")
        # Use the owning service's existing ISA channel, including GetTemplate's
        # shared-viewer check. No template database access in ResourceService.
        template = isa_client.call(actor, "GetTemplate", {"template_id": template_id})["template"]
        return {
            "address": template_id, "root_id": template_id, "resource_type": "template",
            "exists": True, "title": template["title"], "summary": template.get("description", ""),
            "record": canonical_record(template_id, {
                "resource_type": "template", "title": template["title"],
                "summary": template.get("description", ""),
                "attributes": {key: template.get(key) for key in
                               ("kind", "created_by", "builtin_key", "updated_at", "version")},
            }),
            "template": template,
        }

    def read_content(resolved, *, max_bytes=4 * 1024 * 1024, **_context):
        data = json.dumps(resolved["template"], ensure_ascii=False).encode("utf-8")
        if len(data) > max_bytes:
            raise ValueError("template resource exceeds max_bytes")
        return data, "application/json", resolved

    service.register_resolver("template", resolve, read_content=read_content)
