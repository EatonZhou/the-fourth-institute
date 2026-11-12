"""Plan preserving session operations without running a model or rebinding."""
from __future__ import annotations

import hashlib
import json

from .domain_ports import execution_port


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configuration_version(record):
    # Lifecycle timestamps/intake/work changes are not configuration changes.
    return digest({key: record.get(key) for key in (
        "backend", "backend_route_id", "execution_settings", "harness_config",
    )})


def request_signature(body):
    return digest({**{key: value for key, value in body.items() if key != "idempotency_key"},
                   "operation": body.get("operation") or "restart",
                   "context_strategy": body.get("context_strategy") or "preserve",
                   "active_execution_policy": body.get("active_execution_policy") or "wait",
                   "resume_failed_input": body.get("resume_failed_input", False)})


def check_replay(state, body):
    if state.get("request_signature") != request_signature(body):
        raise ValueError("Session operation id refers to a different request")


def preservation_capabilities():
    return execution_port.context_preservation_capabilities()


def preserve_route_error(source, target_provider, target_route, *, has_context):
    """The same context-preservation admission rule used by the form and submit."""
    if not has_context:
        return ""
    if source["provider"] != target_provider:
        return "这两个 Provider 之间无法完整保留上下文；原会话已保留。请选择明确的交接方式或取消切换。"
    capability = preservation_capabilities().get(target_provider, {})
    same_route = source["account_id"] == target_route
    if (same_route and source["has_session"] and capability.get("same_account_native_fork")) or capability.get("canonical_import"):
        return ""
    if capability.get("context_owner") == "portal":
        # A stateless API account never owns the transcript. Changing its API
        # route leaves the same Resident's canonical history in Portal.
        return ""
    return "目标账号不支持完整上下文导入；原会话已保留。请选择明确的交接方式或取消切换。"


def context_options(source, target_provider, target_route, *, has_context):
    error = preserve_route_error(source, target_provider, target_route, has_context=has_context)
    handoff = target_provider != "provider_api"
    return [
        {"id": "preserve", "label": "完整保留当前有效上下文", "enabled": not error,
         "description": error or "不压缩、不删减上下文；无法完整保留时拒绝切换，继续使用原会话。"},
        {"id": "compact", "label": "精简交接（可能遗漏细节）", "enabled": handoff,
         "description": "调用模型根据已有摘要和少量近期对话整理交接内容，可能遗漏细节和工具过程；新会话成功后才切换，旧会话及历史记录保留。"},
        {"id": "visible", "label": "近期可见对话交接（非完整上下文）", "enabled": handoff,
         "description": "将近期可见对话及已有摘要交给目标模型整理，内容有长度限制，不含完整工具过程和内部上下文；新会话成功后才切换，旧会话及历史记录保留。"},
        {"id": "fresh", "label": "空白开始（不携带当前上下文）", "enabled": True,
         "description": "目标账号从空白上下文开始，不携带当前对话内容；旧会话及历史记录保留。"},
    ]
