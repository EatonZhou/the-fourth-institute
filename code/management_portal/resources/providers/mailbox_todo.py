"""Generic resource adapters for the single Mailbox Todo domain owner."""
import json
from management_portal import mailbox_todos as owner


def query_resources(*, actor, user_id, q, limit, offset, readable_ids):
    """Scoped discovery: owner authorizes before filtering, counting and paging."""
    result = owner.search_resources(actor, user_id=user_id, q=q, limit=limit,
                                    cursor=str(offset), readable_ids=readable_ids)
    return {"resources": result["items"], "total": result["total"],
            "pending_count": result["pending_count"], "next_cursor": result["next_cursor"]}


def install_resolver(service):
    def resolve(address, *, actor="", **_context):
        item = owner.get(actor, address)
        return {"address": address, "root_id": address, "resource_type": "mailbox_todo",
                "target_kind": "mailbox_todo", "record": owner.resource_record(item),
                "detail": item, "content_kind": "json", "exists": True}

    def read(resolved, *, actor="", max_bytes=4 * 1024 * 1024, **_context):
        current = resolve(resolved["address"], actor=actor)
        data = json.dumps(current["detail"], ensure_ascii=False).encode()
        if len(data) > max_bytes:
            raise ValueError("Todo content exceeds byte limit")
        return data, "application/json", current

    service.register_resolver("mailbox_todo", resolve, read_content=read, query=query_resources)


def install_mutation_provider(service):
    def register(payload, *, actor="", resource_id="", **_context):
        if not isinstance(payload, dict) or not {"text", "user_id"} <= set(payload) or set(payload) - {"text", "user_id", "date"}:
            raise ValueError("Todo 注册字段为必填 user_id、text 及可选 date")
        return owner.resource_record(owner.create(actor, payload["text"], user_id=payload["user_id"],
                                                 date=payload.get("date"), resource_id=resource_id))

    def update(resource_id, payload, *, actor="", **_context):
        changes = dict(payload)
        version = changes.pop("expected_version", None)
        return owner.resource_record(owner.update(actor, resource_id, changes, expected_version=version))

    def unregister(resource_id, *, actor="", **_context):
        return owner.resource_record(owner.delete(actor, resource_id))

    service.register_mutation_provider("mailbox_todo", register=register, update=update,
                                      unregister=unregister, service_managed_registration=True)
