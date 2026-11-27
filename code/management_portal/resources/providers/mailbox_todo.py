"""Generic resource adapters for the single Mailbox Todo domain owner."""
import json
from management_portal import mailbox_todos as owner


_TEXT = {"type": "string", "pattern": r"^\s*\S(?:[\s\S]{0,98}\S)?\s*$", "description": "待办正文；去首尾空白后 1–100 字符。"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "YYYY-MM-DD 日期；真实日历日期由领域检查。"}
_ANCHOR = {"type": "string", "pattern": r"^mailbox_todo_[0-9a-f]{12}$", "description": "同用户的锚点待办 ID；跨日期移动会使用锚点日期，权限和位置在锁内检查。"}
REGISTER_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "创建用户私有待办；领域核对真实 caller 的用户管理权限。",
    "required": ["user_id", "text"], "additionalProperties": False,
    "properties": {
        "user_id": {"type": "string", "pattern": r"\S", "description": "已配置且活跃的目标用户 ID。"},
        "text": _TEXT,
        "date": {**_DATE, "type": ["string", "null"], "description": "YYYY-MM-DD；省略或 null 使用站点当天日期。"},
    },
}
UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "更新私有待办；order 独立于内容修改，原子校验原行和锚点。",
    "required": ["expected_version"], "minProperties": 2, "additionalProperties": False,
    "properties": {
        "expected_version": {"type": "integer", "minimum": 1, "description": "严格整数版本，来自当前待办；可由信封提供。"},
        "text": _TEXT, "date": _DATE,
        "completed": {"type": "boolean", "description": "是否完成。"},
        "indent": {"type": "integer", "minimum": 0, "maximum": 4, "description": "缩进级别 0–4。"},
        "order": {"type": "object", "minProperties": 1, "maxProperties": 1, "additionalProperties": False,
                  "description": "仅选 direction、before_id、after_id 之一。",
                  "properties": {"direction": {"type": "string", "enum": ["up", "down"], "description": "向上或向下移动。"},
                                 "before_id": _ANCHOR, "after_id": _ANCHOR}},
    },
    "allOf": [{"if": {"required": ["order"]}, "then": {"maxProperties": 2}}],
}


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
                                      register_contract=REGISTER_CONTRACT, update_contract=UPDATE_CONTRACT,
                                      unregister=unregister, service_managed_registration=True)
