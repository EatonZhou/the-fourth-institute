"""Resident domain responsibilities: runtime."""
from __future__ import annotations

from . import (
    identity_contract,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_execution_provider,
    resident_execution_work,
    resident_lineage,
)
from .domain_ports import resident_lineage_host_port
from .resident_access import can_manage_resident as _can_manage_resident
from .resident_runtime_payload import runtime_settings_updates_from_payload as _runtime_settings_updates_from_payload
from .resident_settings_contract import runtime_settings_from_parts, stage_runtime_settings
from management_common.actor_identity import canonical_actor_id
from management_common.portal_paths import PORTAL_ROOT
from management_common.product_environment import mirror_product_env
from management_portal.execution.accounts import default_execution_accounts
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from pathlib import Path
from typing import Any, Iterable, Mapping
import copy
import os
from management_portal.execution import resident_runtime_settings

from . import resident_domain_common


def resident_execution_session_key(record: dict[str, Any]) -> str:
    from . import resident_domain_common
    return f"resident:{resident_domain_common.record_resident_id(record)}"


def resident_backend_capabilities(record: dict[str, Any]) -> dict[str, Any]:
    return resident_execution_provider.capability_projection(record)


def _execution_snapshot_account_id(backend: str, job: dict[str, Any] | None) -> str:
    value = job if isinstance(job, dict) else {}
    accounts = default_execution_accounts()
    account_id = accounts.canonical_route(backend, value.get("backend_route_id"))
    if account_id:
        return account_id
    if not str(value.get("execution_id") or ""):
        return ""
    snapshot = resident_execution_work.execution_snapshot(value)
    request = (
        snapshot.get("request")
        if isinstance(snapshot.get("request"), dict)
        else {}
    )
    session = (
        request.get("session")
        if isinstance(request.get("session"), dict)
        else {}
    )
    result = (
        snapshot.get("result")
        if isinstance(snapshot.get("result"), dict)
        else {}
    )
    return accounts.canonical_route(backend,
        session.get("backend_route_id")
        or result.get("backend_route_id")
    )


def resident_session_account_id(
    record: dict[str, Any],
    job: dict[str, Any] | None = None,
    *,
    session_record: dict[str, Any] | None = None,
) -> str:
    backend = resident_backend.execution_backend(record)
    if not default_execution_accounts().resident_selection(backend)["enabled"]:
        return ""
    binding = (
        session_record
        if session_record is not None
        else resident_backend.session_record(record)
    )
    account_id = default_execution_accounts().canonical_route(backend,
        binding.get("backend_route_id")
        or record.get("backend_route_id")
    )
    if account_id:
        return account_id
    account_id = _execution_snapshot_account_id(backend, job)
    if account_id:
        return account_id
    return ""


def account_selection_payload(
    record: dict[str, Any],
    source_account_id: str = "",
) -> dict[str, Any]:
    return default_execution_accounts().resident_selection(
        resident_backend.execution_backend(record), source_account_id,
    )


def assign_derived_resident_account_route(
    target_record: dict[str, Any],
    resident: dict[str, Any],
    source_record: dict[str, Any],
    requested_account_id: str = "",
) -> str:
    backend = resident_backend.execution_backend(resident)
    same_provider = backend == resident_backend.execution_backend(source_record)
    source_route = (resident_session_account_id(source_record) or str(
        resident_backend.execution_account(source_record).get("backend_route_id") or ""
    )) if same_provider else ""
    # Workspace authority was already applied while preparing this Fork.
    # Account selection validates the staged configuration; it must not infer
    # a second permission policy from an absent actor.
    inherited = (
        runtime_settings_from_parts(resident["execution_settings"], resident["harness_config"])
        if isinstance(resident.get("execution_settings"), Mapping)
        and isinstance(resident.get("harness_config"), Mapping)
        else read_resident_runtime_settings(resident)
    )
    requested = str(requested_account_id or "").strip()
    if backend == "provider_api":
        from management_portal.execution.resident_configuration import API_RUNTIME_SETTINGS
        route = requested or source_route
        if not route:
            raise ValueError("切换到 Model API 时必须选择账号")
        # Model API owns validation, defaults and conversation-history policy.
        updates = {key: inherited[key] for key in API_RUNTIME_SETTINGS if key in inherited}
        if not same_provider or route != source_route:
            updates.pop("model", None)
            updates.pop("backend_options", None)
        settings = resident_runtime_settings_from_register_payload(
            {**resident, "backend_route_id": route}, {"runtime_settings": updates},
            str(inherited.get("updated_by") or ""), str(inherited.get("updated_at") or ""),
        )
        selection = {"backend_route_id": route, "runtime_settings": settings}
    else:
        selection = default_execution_accounts().derive_resident_account(
            AGENT_PROVIDERS[backend], source_route,
            same_provider and bool(resident_backend.record_session_id(source_record)),
            requested, inherited,
        )
    settings = selection.pop("runtime_settings", None)
    selection.pop("provider_profile", None)
    if isinstance(settings, dict):
        stage_runtime_settings(resident, settings)
    resident.update(selection)
    return str(selection["backend_route_id"])


def resident_backend_binding(record: dict[str, Any]) -> dict[str, Any]:
    backend = resident_backend.backend_name(record)
    binding = resident_backend.session_record(record) if record.get("resident_id") else {}
    return {"backend": backend, "binding_role": "main",
            "backend_route_id": str(binding.get("backend_route_id") or record.get("backend_route_id") or ""),
            "provider_profile": str(record.get("backend_route_id") or ""),
            "model": str(read_resident_runtime_settings(record).get("model") or "")}


def resident_folder_for_id(record: dict, target_resident_id: str) -> Path:
    from . import resident_domain_common, resident_record_domain
    return resident_record_domain.resident_workspace_path(record) / "residents" / resident_domain_common.safe_id(target_resident_id)


def _resident_workspace_scope_record(
    record: dict,
) -> dict[str, Any]:
    return record


def resident_media_workspace_roots(
    record: dict,
) -> tuple[Path, ...]:
    from . import resident_record_domain
    workspace_record = _resident_workspace_scope_record(record)
    roots: list[Path] = []
    seen: set[str] = set()
    for item in resident_record_domain.resident_workspace_roots(workspace_record):
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve(strict=False)
        key = str(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        roots.append(path)
    return tuple(roots)


def resident_working_directory(
    record: dict,
) -> Path:
    from . import resident_record_domain
    workspace_record = _resident_workspace_scope_record(record)
    candidate = resident_record_domain.resident_default_cwd_path(workspace_record)
    if not candidate:
        raise ValueError("Resident 没有有效的默认 Workspace Binding")
    path = Path(candidate).expanduser().resolve(strict=False)
    if not path.is_dir():
        raise ValueError(f"Resident 默认 Workspace 不可访问: {path}")
    return path


def resident_cli_environment(record: dict[str, Any]) -> dict[str, str]:
    resident_id = resident_lineage.resident_id(record)
    identity_contract.validate_id(resident_id, "resident")
    environment: dict[str, str] = {}
    mirror_product_env(environment, "PORTAL_RESIDENT_ID", resident_id)
    mirror_product_env(environment, "PORTAL_LINEAGE_ID", resident_lineage.lineage_id(record))
    return environment


def resident_runtime_identity_block(record: dict[str, Any]) -> str:
    """Who runs this turn and how it calls HATG ISA as itself.

    A ``provider_api`` Resident answers through a model API with no tools, so
    it is told that instead: told to call tools, its model writes commands and
    results it never ran.
    """
    identity = resident_cli_environment(record)
    if resident_backend.execution_backend(record) == "provider_api":
        how = (
            "本 Resident 通过模型 API 直接作答，没有任何工具：不能读写文件、运行命令、调用 HATG ISA 指令或启动子 agent，"
            "规则中要求读取文件或调用工具的步骤不适用。每轮只看到本次请求中的固定指令、已选会话历史及当前输入；未携带的历史不可访问。"
            "需要文件内容或命令结果时，说明需要什么，不要写出假装已经执行的命令或结果。\n"
        )
    else:
        how = (
            "调用 Portal HATG ISA 指令时，使用 hatg_instruction 工具；没有该工具或在脚本中批量调用时，"
            f"使用命令行客户端 {PORTAL_ROOT / 'scripts' / 'resident_hatg_instruction.py'}，"
            f"在子命令前传入 --resident-id {identity['PORTAL_RESIDENT_ID']}。\n"
            "不得使用消息发送者、owner 或历史上下文中其他 Resident 的身份；"
            "服务端按当前 Resident 检查权限。\n"
        )
    return (
        "<resident-runtime-identity>\n"
        f"resident_id: {identity['PORTAL_RESIDENT_ID']}\n"
        f"lineage_id: {identity['PORTAL_LINEAGE_ID']}\n"
        + how
        + "</resident-runtime-identity>"
    )

def resident_title_map(
    identities: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    from . import resident_domain_common
    requested = list(dict.fromkeys(
        (
            str(anchor_resident_id or "").strip(),
            str(exact_resident_id or "").strip(),
        )
        for anchor_resident_id, exact_resident_id in identities
    ))
    exact_ids = {
        exact_resident_id or anchor_resident_id
        for anchor_resident_id, exact_resident_id in requested
        if identity_contract.is_valid_id(
            exact_resident_id or anchor_resident_id,
            "resident",
        )
    }
    records = {
        resident_domain_common.record_resident_id(record): record
        for record in resident_catalog.get_catalog().get_many(exact_ids)
    }
    return {
        identity: str(records[identity[1] or identity[0]].get("title") or identity[1] or identity[0])
        for identity in requested
        if (identity[1] or identity[0]) in records
    }


def _resident_activity_sort_value(resident: Mapping[str, Any]) -> str:
    return str(
        resident.get("last_interaction_at")
        or resident.get("updated_at")
        or resident.get("created_at")
        or ""
    )


def _resident_is_available(resident: Mapping[str, Any]) -> bool:
    return not str(resident.get("deleted_at") or "").strip() and str(
        resident.get("session_state") or ""
    ).strip().lower() != "deleted"


def lineage_identity_record(record: dict[str, Any]) -> dict[str, Any]:
    try:
        lineage_id = resident_lineage.lineage_id(record)
    except ValueError:
        return {}
    cached = resident_lineage.cached_access_projection(lineage_id)
    if cached is not None:
        return cached
    try:

        payload = resident_lineage_host_port.lineage_access_projection(lineage_id)
    except (KeyError, OSError, ValueError):
        identity: dict[str, Any] = {}
    else:
        identity = dict(payload) if isinstance(payload, dict) else {}
    identity = _compile_lineage_identity(identity)
    resident_lineage.remember_access_projection(lineage_id, identity)
    return identity


def _compile_lineage_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Compile the membership fields reused by every Resident member check."""
    identity = dict(identity)
    identity["_access_resident_ids"] = frozenset(
        str(item or "").strip()
        for item in (
            identity.get("lineage_node_ids")
            or identity.get("resident_ids")
            or []
        )
        if identity_contract.is_valid_id(str(item or "").strip(), "resident")
    )
    identity["_access_leader_resident_id"] = (
        str(identity.get("leader_resident_id") or "").strip()
        if identity_contract.is_valid_id(
            str(identity.get("leader_resident_id") or "").strip(),
            "resident",
        )
        else ""
    )
    return identity


def _lineage_graph_resident_ids(record: dict[str, Any]) -> set[str]:
    identity = lineage_identity_record(record)
    return set(identity.get("_access_resident_ids") or ())


def _is_direct_family_resident(
    record: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    from . import resident_domain_common
    candidate_id = resident_lineage.resident_id(candidate)
    source_id = str(candidate.get("source_resident_id") or "").strip()
    return bool(
        candidate_id
        and source_id == resident_domain_common.record_resident_id(record)
    )


def managed_derived_residents(
    record: dict,
    registry: dict[str, Any],
    actor: str,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        return []
    residents = [
        item
        for item in registry.get("residents") or []
        if isinstance(item, dict)
        and _is_direct_family_resident(record, item)
        and _can_manage_resident(item, actor_id)
        and (include_deleted or _resident_is_available(item))
    ]
    residents.sort(key=_resident_activity_sort_value, reverse=True)
    return residents


def _default_resident_sandbox(is_owner: bool) -> str:
    return os.getenv(
        "RESIDENT_AGENT_OWNER_CODEX_SANDBOX",
        os.getenv("PORTAL_OWNER_CODEX_SANDBOX", "danger-full-access"),
    ).strip() or "danger-full-access"


def resident_runtime_defaults(record: dict) -> dict[str, Any]:
    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS.get(backend)
    if definition:
        return definition.runtime_adapter().defaults(definition, record)
    return resident_runtime_settings.now_settings(model="", reasoning_effort="", service_tier="", permissions="",
        approvals_reviewer="", sandbox="", backend=backend)


def read_resident_runtime_settings(record: dict) -> dict[str, Any]:
    from . import resident_domain_common

    settings = resident_catalog.get_catalog().state_store.read_resident_execution_settings(
        resident_domain_common.record_resident_id(record),
    )
    if settings is None:
        # Only a not-yet-registered identity may use staged registration data.
        # Registered identities always read their current database configuration.
        settings = (
            runtime_settings_from_parts({**record["execution_settings"], "backend": record.get("backend", "")}, record["harness_config"])
            if isinstance(record.get("execution_settings"), Mapping)
            and isinstance(record.get("harness_config"), Mapping)
            else resident_runtime_defaults(record)
        )
    settings.setdefault("allow_subagents", False)
    if (
        resident_execution_provider.provider_for(record).capabilities.native_permissions
        and not str(settings.get("sandbox") or "").strip()
        and not str(settings.get("permissions") or "").strip()
    ):
        settings["sandbox"] = _default_resident_sandbox(True)
    return settings


def write_resident_runtime_settings(record: dict, settings: dict[str, Any]) -> dict[str, Any]:
    from . import resident_domain_common

    persisted = stage_runtime_settings(record, settings)
    store = resident_catalog.get_catalog().state_store
    resident_id = resident_domain_common.record_resident_id(record)
    if store.read_resident_execution_settings(resident_id) is not None:
        persisted = store.write_resident_execution_settings(resident_id, persisted)
        resident_conversation_stream.get_repository().execution_settings_changed(resident_id, persisted)
    return persisted


def reconcile_resident_codex_runtime_configuration(
    record: dict[str, Any],
) -> dict[str, Any]:
    """Persist adapter-normalized settings using one account and CAS path."""
    from . import resident_domain_common, resident_record_domain
    definition = AGENT_PROVIDERS.get(resident_backend.execution_backend(record))
    if not definition:
        return record
    resident_id = resident_domain_common.record_resident_id(record)
    for _attempt in range(2):
        account = resident_backend.execution_account(record)
        route = str(account.get("backend_route_id") or "")
        before = read_resident_runtime_settings(record)
        resolved = definition.runtime_adapter().reconcile(definition, route, before)
        if resolved == before:
            return record
        with resident_record_domain.state_lock(resident_id):
            current = copy.deepcopy(resident_record_domain.find_resident(resident_id) or {})
            if not current:
                raise RuntimeError("Resident disappeared before runtime configuration validation")
            if resident_backend.execution_account(current) != account or read_resident_runtime_settings(current) != before:
                record = current
                continue
            current["backend_route_id"] = route
            current["updated_at"] = resident_domain_common.now_iso()

            def commit_configuration():
                write_resident_runtime_settings(current, resolved)
                resident_record_domain.save_resident_record(current, commit_configuration=True)

            resident_catalog.get_catalog().state_store.transaction(commit_configuration)
            return current
    raise RuntimeError("Resident runtime configuration changed during Provider validation; retry")


def validate_backend_runtime_updates(
    record: dict[str, Any],
    current: object,
    updates: dict[str, Any],
) -> dict[str, Any]:
    if resident_backend.execution_backend(record) == "provider_api":
        from management_portal.execution.resident_configuration import validate_api_settings
        return validate_api_settings(str(record.get("backend_route_id") or ""), current or {}, updates)
    definition = AGENT_PROVIDERS.get(resident_backend.execution_backend(record))
    if not definition:
        if updates:
            raise ValueError("该 Resident ExecutionProvider 不支持运行设置")
        return {}
    binding = resident_backend.session_record(record) if record.get("resident_id") else {}
    route = str(binding.get("backend_route_id") or record.get("backend_route_id") or definition.default_route)
    return definition.runtime_adapter().validate_updates(definition, route, current, updates)


def resident_runtime_settings_from_register_payload(record: dict, payload: dict, actor_id: str, now: str) -> dict[str, Any]:
    settings = resident_runtime_defaults(record)
    updates = _runtime_settings_updates_from_payload(payload.get("runtime_settings"))
    if not resident_execution_provider.provider_for(record).capabilities.runtime_settings:
        if updates:
            raise ValueError("该 Resident ExecutionProvider 不支持运行设置")
        return settings
    if resident_backend.execution_backend(record) == "provider_api":
        from management_portal.ai_provider_registry import shared_ai_provider_registry
        from management_portal.execution.model_api_protocols import protocol_adapter
        entry = shared_ai_provider_registry.get(resident_backend.execution_provider(record))
        if entry:
            updates.setdefault("model", entry.get("default_model") or "")
        context = dict(updates.get("prompt_context") or {})
        context.setdefault("history", {"enabled": protocol_adapter(entry["kind"]).default_history, "max_tokens": 8000})
        updates["prompt_context"] = context
    updates = validate_backend_runtime_updates(record, settings, updates)
    settings = resident_runtime_settings.merge_settings(settings, updates, actor=actor_id, updated_at=now)
    return resident_runtime_settings.mark_portal_manual_authority(settings, actor=actor_id, updated_at=now)


def _derived_resident_runtime_base_defaults(
    record: Mapping[str, Any],
    resident: Mapping[str, Any],
    runtime_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return resident_runtime_settings.merge_settings(
        dict(runtime_settings)
        if isinstance(runtime_settings, Mapping)
        else read_resident_runtime_settings(dict(record)),
        {"backend": str(resident.get("backend") or record.get("backend") or "codex")},
    )


def derived_resident_runtime_defaults(
    record: dict,
    resident: dict[str, Any],
    actor: str = "",
    runtime_settings: dict[str, Any] | None = None,
    *,
    workspace_authority: bool | None = None,
    base_runtime_settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    from . import resident_access
    actor_id = canonical_actor_id(actor)
    has_workspace_authority = (
        bool(workspace_authority)
        if workspace_authority is not None
        else resident_access.has_resident_workspace_authority(record, actor_id, resident)
    )
    if isinstance(resident.get("execution_settings"), Mapping) and isinstance(resident.get("harness_config"), Mapping):
        settings = runtime_settings_from_parts(resident["execution_settings"], resident["harness_config"])
    else:
        settings = (
            dict(base_runtime_settings)
            if base_runtime_settings is not None
            else _derived_resident_runtime_base_defaults(record, resident, runtime_settings)
        )
    if has_workspace_authority and not str(settings.get("sandbox") or "").strip() and not str(settings.get("permissions") or "").strip():
        settings["sandbox"] = _default_resident_sandbox(True)
    if not has_workspace_authority:
        settings = resident_runtime_settings.read_only_runtime_settings(settings)
    return settings


_FORK_RUNTIME_METADATA_KEYS = frozenset({
    "backend",
    "updated_at",
    "updated_by",
    "runtime_settings_authority",
    "runtime_settings_authority_at",
    "runtime_settings_authority_by",
})


def fork_runtime_settings(
    source_effective: Mapping[str, Any],
    target_settings: Mapping[str, Any],
    *,
    actor: str,
    updated_at: str,
    workspace_authority: bool,
) -> dict[str, Any]:
    """Runtime settings of a Fork: every effective setting of the source.

    Only record metadata stays the target's own.  An actor without workspace
    authority over the new Resident gets read-only settings.
    """

    inherited = resident_runtime_settings.merge_settings(
        dict(target_settings),
        {
            key: value
            for key, value in source_effective.items()
            if key not in _FORK_RUNTIME_METADATA_KEYS
        },
        actor=actor,
        updated_at=updated_at,
    )
    if not workspace_authority:
        inherited = resident_runtime_settings.read_only_runtime_settings(inherited)
    return inherited


def stamp_derived_resident_runtime_defaults(
    record: dict,
    resident: dict[str, Any],
    *,
    actor: str = "",
    workspace_authority: bool | None = None,
) -> None:
    defaults = derived_resident_runtime_defaults(
        record,
        resident,
        actor,
        workspace_authority=workspace_authority,
    )
    stage_runtime_settings(resident, defaults)


def resident_session_prompt_controls(
    runtime_settings: object,
) -> dict[str, bool]:
    """The per-input snapshot of a Resident's prompt controls."""
    settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    return {"allow_subagents": bool(settings.get("allow_subagents"))}
