"""Shared knowledge-tag configuration; Project registry owns storage and CAS."""
from __future__ import annotations

import json

from management_portal import permission_system, project_registry
from ..errors import ResourceVersionConflict


UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["content", "expected_version"],
    "description": "替换知识标签单例配置；当前共享 admin 与配置 CAS 在所属领域核验。",
    "properties": {
        "content": {"type": "string", "description": "完整 JSON 标签数组正文。各项 id 为唯一小写标签名、description 为非空说明；可选 folder 默认 id 且须为唯一单层目录名，可选 param 沿标签配置规则。领域解析 JSON 并检查内容。"},
        "expected_version": {"type": "string", "minLength": 1,
                             "description": "ReadResource 返回的配置版本字符串，原样提交。"},
    },
}


def _authorize(address: str, actor: str, role: str) -> None:
    if address != project_registry.KNOWLEDGE_TAGS_ID:
        raise KeyError(address)
    if not permission_system.allows_roles(actor, [(role, "@shared")]):
        raise PermissionError("知识标签配置需要共享资源上的 " + role + " 权限")


def _record(configuration):
    return {"resource_id": project_registry.KNOWLEDGE_TAGS_ID, "resource_type": "knowledge_tags",
            "title": "知识标签", "version": configuration["version"], "project_ids": [],
            "attributes": {"singleton": True}}


def install_resolver(service):
    def resolve(address, *, actor="", **_context):
        _authorize(address, actor, "@viewer")
        configuration = project_registry.knowledge_tags_configuration()
        return {"address": address, "root_id": address, "resource_type": "knowledge_tags",
                "target_kind": "knowledge_tags", "record": _record(configuration),
                "exists": True, "content_kind": "json", "detail": configuration["tags"]}

    def read(resolved, *, actor="", max_bytes=4 * 1024 * 1024, **_context):
        current = resolve(resolved["address"], actor=actor)
        data = json.dumps(current["detail"], ensure_ascii=False).encode("utf-8")
        if len(data) > max_bytes:
            raise ValueError("Knowledge tag content exceeds byte limit")
        return data, "application/json", current

    service.register_resolver("knowledge_tags", resolve, read_content=read)


def install_mutation_provider(service):
    def update(resource_id, payload, *, actor="", **_context):
        _authorize(resource_id, actor, "@admin")
        if set(payload) != {"content", "expected_version"} or not isinstance(payload["content"], str):
            raise ValueError("知识标签只支持完整 JSON 正文 content 和必填 expected_version")
        expected = payload["expected_version"]
        if not isinstance(expected, str) or not expected:
            raise ValueError("expected_version 必须为读取配置时返回的版本字符串")
        try:
            tags = project_registry.replace_tags(json.loads(payload["content"]), expected_version=expected)
        except project_registry.TagsChanged as exc:
            raise ResourceVersionConflict(
                "知识标签已被修改；请重新读取配置后再提交", expected_version=expected,
                current_version=exc.current_version,
            ) from None
        return _record({"version": project_registry.tags_version(tags)})

    service.register_mutation_provider("knowledge_tags", update=update, update_contract=UPDATE_CONTRACT,
                                       content_updatable=True)
