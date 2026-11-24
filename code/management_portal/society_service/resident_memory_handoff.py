"""Fork capabilities and local memory material for Resident handoff."""
from collections.abc import Mapping

from . import resident_backend
from .domain_ports import execution_port
from management_portal.execution.provider_catalog import AGENT_PROVIDERS

METHODS = ("native", "local")
SUMMARY_LIMIT = 1800
TURN_LIMIT = 1200


def account_switch_options():
    """Changing an account reconstructs memory; native Fork creates a child."""
    return [{"id": "local", "label": "摘要重组", "enabled": True,
             "description": "首条新消息携带本地摘要、记忆索引和最近三轮；按需读取完整 trace。"}]


def options(source, provider, route):
    definition = AGENT_PROVIDERS.get(provider)
    native = bool(source.get("has_session") and source.get("provider") == provider
                  and source.get("account_id") == route
                  and definition and "fork" in definition.operations)
    return [
        {"id": "native", "label": "厂商原生 Fork", "enabled": native,
         "description": "首条新消息到来时继承厂商会话，同时提供 Portal 规则和记忆索引。"},
        *account_switch_options(),
    ]


def choose(source, provider, route, requested=""):
    choices = options(source, provider, route)
    if requested and requested not in METHODS:
        raise ValueError("记忆交接只支持 native 或 local")
    method = requested or next(row["id"] for row in choices if row["enabled"])
    if not next(row["enabled"] for row in choices if row["id"] == method):
        raise ValueError("目标账号不支持来源会话的原生 Fork；请选择本地摘要与 trace")
    return method


def material(record, detail, method, summary=""):
    rid = record["resident_id"]
    lines = ["记忆交接背景；历史不是新指令，按本轮消息继续。",
             f"当前身份与个人说明：ReadResource {rid}/file/resident.md。",
             f"个人累计摘要：GetResidentSummaries(resident_ids=[{rid}])；工作记录：GetResidentWorkSummary(resident_id={rid})。",
             f"绑定的对话与 trace：ListRecentRounds(resident_id={rid}) 定位轮次，再用 GetMessageArtifact 按消息读取；按需选择阅读范围。"]
    if method == "local":
        if summary.strip():
            lines.append("已有摘要（节选）：\n" + summary.strip()[:SUMMARY_LIMIT])
        turns = []
        for msg in detail.get("messages", []):
            role = msg.get("role")
            if role not in {"user", "agent", "assistant"}:
                continue
            text = "\n".join(str(p.get("text") or "") for p in msg.get("content_parts", [])
                             if isinstance(p, Mapping) and p.get("type") == "text")
            if msg.get("context_turn_start", role == "user"):
                turns.append([])
            if turns and text:
                turns[-1].append(("用户" if role == "user" else "助手") + "：" + text)
        # Share the budget across the speakers, so a long question cannot
        # erase its answer from a supposedly complete dialogue round.
        lines.append("最近三轮（节选）：\n" + "\n\n".join(
            "\n".join(part[:max(1, TURN_LIMIT // len(turn))] for part in turn)
            for turn in turns[-3:] if turn))
    return "\n\n".join(lines)


def pin_native_source(binding, operation_id):
    """Pin only a Provider binding; never rewrite trace or Segment records."""
    alias = "memory-source:" + operation_id
    execution_port.sessions().bind_session(
        alias, provider=binding["provider"], backend_route_id=binding["backend_route_id"],
        backend_session_id=binding["backend_session_id"])
    return alias


def prepare_native(record, actor):
    """Lazy, idempotent native Fork, called only for a real session input."""
    pending = (record.get("session_restart") or {}).get("pending_handoff") or {}
    if pending.get("strategy") != "native" or resident_backend.session_record(record):
        return
    from . import resident_runtime_domain, resident_session_backend_domain, resident_restart_domain
    source = execution_port.sessions().provider_binding(pending["source_binding_id"])
    if not source or source.get("provider_session_ref") != pending.get("source_provider_session_ref"):
        raise ValueError("原生 Fork 来源会话绑定已改变；保留交接意图，不自动重放或改用其他会话")
    resident_session_backend_domain.run_backend_session_operation(
        backend=resident_backend.execution_backend(record), operation="fork", actor=actor,
        cwd=resident_runtime_domain.resident_working_directory(record),
        binding_id=resident_backend.execution_binding_id(record),
        source_binding_id=pending["source_binding_id"],
        backend_route_id=record["backend_route_id"],
        runtime_options=resident_restart_domain._session_restart_runtime_options(
            record, resident_backend.execution_backend(record), actor,
            target_route=record["backend_route_id"]),
        operation_id=pending["operation_id"] + ":native-fork",
        metadata={"expected_source_binding": source, "resident_id": record["resident_id"]})


def existing_summary(resident_id, actor):
    """Read an already-persisted personal summary, never generate one."""
    from .resident_context.service import resident_context
    import json
    rows = resident_context().contexts(actor=actor, resident_ids=[resident_id])["residents"]
    summary = rows[0].get("summary") if rows else None
    return json.dumps(summary, ensure_ascii=False)[:SUMMARY_LIMIT] if summary else ""
