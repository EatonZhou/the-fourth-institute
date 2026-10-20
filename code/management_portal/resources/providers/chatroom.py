from __future__ import annotations

import json

from .common import canonical_record
from ..portal import portal_url_for_address


def install_mutation_provider(service):
    from management_portal import isa_client

    def unregister(resource_id, *, actor="", **_context):
        result = isa_client.call(actor, "DeleteChatroom", {"chatroom_id": resource_id},
                                 key=f"resource-unregister:{resource_id}")
        return canonical_record(resource_id, result)

    service.register_mutation_provider("chatroom", unregister=unregister)


def install_resolver(service):
    """Chatrooms resolve to their summary; the Chatroom domain validates membership after ISA authorization."""

    from management_portal import chatroom_backend

    def resolve(address, *, actor="", **_context):
        resource_id = str(address).split("/", 1)[0]
        summary = chatroom_backend.resource_summary(resource_id)
        return {
            "address": address,
            "root_id": resource_id,
            "resource_type": "chatroom",
            "target_kind": "chatroom",
            "record": canonical_record(resource_id, summary),
            "detail": summary,
            "content_kind": "json",
            "filename": f"{resource_id}.json",
            "portal_url": portal_url_for_address(resource_id, "chatroom"),
            "exists": True,
        }

    def read(resolved, **_context):
        return json.dumps(resolved["detail"], ensure_ascii=False).encode(), "application/json", {"filename": resolved["filename"]}

    service.register_resolver("chatroom", resolve, read_content=read)
