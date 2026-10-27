"""Resident domain responsibilities: registration."""
from __future__ import annotations

from management_common.principal import is_user_or_resident

from . import (
    identity_contract,
    invariant_locks,
    resident_backend,
    resident_catalog,
    resident_execution_provider,
    resident_lineage,
    resident_store,
    resource_client,
    resident_record_domain,
    resident_project_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resident_workspace_domain,
)
from .domain_ports import resident_lineage_host_port
from .resident_access import can_manage_resident as _can_manage_resident, manage_refusal
from . import resident_domain_common, resident_fork_domain
from contextlib import ExitStack
from management_common.actor_identity import canonical_actor_id, same_actor as _same_actor
from management_portal.execution.accounts import resolve_account_configuration
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from pathlib import Path
from typing import Any
import copy


def _confirm_registered_resident_resources(
    record: dict[str, Any], *, committed_revision: int | None = None,
) -> None:
    from management_common.projection_receipts import project_committed
    from .resident_resource_projection import ResidentResourceProjectionWorker

    store = resident_catalog.get_catalog().state_store
    project_committed(
        resident_record_domain.resident_registration_projection_receipt(record, committed_revision=committed_revision),
        lambda: ResidentResourceProjectionWorker(store, resource_client).confirm(record["resident_id"]),
    )


def confirm_resident_registration(resident_id: str, actor: str) -> dict[str, Any]:
    """Retry publication of an existing identity without registering or launching it."""
    record = resident_catalog.get_catalog().get(resident_id)
    if record.get("deleted_at"):
        raise KeyError(resident_id)
    if not _can_manage_resident(record, actor):
        raise manage_refusal(record)
    resident_catalog.get_catalog().state_store.ensure_resident_registration_projection(resident_id)
    _confirm_registered_resident_resources(record)
    return resident_catalog.get_catalog().get(resident_id)


def register_resident(
    payload: dict, actor: str, *, lineage_node: dict[str, Any] | None = None,
    creation_key: str = "",
) -> dict:
    requested = dict(payload)
    lineage_id = str(requested.get("lineage_id") or "").strip()
    if not lineage_id:
        lineage_id = resident_domain_common.new_lineage_id()
        requested["lineage_id"] = lineage_id

    record, _lineage = register_resident_and_lineage(requested, actor, lineage_node=lineage_node,
                                        **({"creation_key": creation_key} if creation_key else {}))
    return record


def register_resident_and_lineage(
    payload: dict, actor: str, *,
    lineage_node: dict[str, Any] | None = None,
    project_principal: bool = False,
    personal_owner: str = "",
    creation_key: str = "",
) -> tuple[dict, dict | None]:
    """Prepare an unpublished identity outside state locks, then commit its graph membership.

    ``personal_owner`` registers that User's own Ask Me Anything: a new
    personal Lineage outside any Project, created by the User alone.
    """
    from .resident_projection import _optional_absolute_paths, _resident_lineage_node_projection
    from . import project_domain
    actor_id = canonical_actor_id(actor)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title 为必填")

    project_id = str(payload.get("project_id") or "").strip()
    personal_member_owner = ""
    if lineage_node is not None:
        home = resident_lineage_host_port.lineage_access_projection(str(payload.get("lineage_id") or ""))
        if home.get("lineage_membership") == "personal":
            from .lineage_access_policy import actor_can_manage_lineage
            if not actor_can_manage_lineage(home, actor_id):
                raise PermissionError("个人 Lineage 访问被拒绝")
            personal_member_owner = str(home.get("created_by") or "")
    if not project_id and not personal_owner and not personal_member_owner:
        raise ValueError("Resident Lineage 必须选择 Project")
    if personal_owner:
        if (project_id or project_principal
                or lineage_node is not None or str(payload.get("lineage_folder_path") or "").strip()):
            raise ValueError("个人 Lineage 不属于 Project，只能新建")
        if not _same_actor(actor_id, personal_owner):
            raise PermissionError("个人 Lineage 只能由用户本人创建")
    elif project_principal:
        if not is_user_or_resident(actor_id):
            raise PermissionError("Project principal creation requires Project management permission")
    elif identity_contract.is_valid_id(actor_id, "resident"):
        target_lineage_id = str(payload.get("lineage_id") or "").strip()
        resident_lineage_host_port.authorize_lineage_resident_access(target_lineage_id, actor_id)
        if not is_user_or_resident(actor_id):
            raise PermissionError("Target Lineage management permission is required")
        lineage_identity = resident_lineage_host_port.lineage_access_projection(
            target_lineage_id
        )
        if project_id != str(
            lineage_identity.get("project_id") or ""
        ):
            raise PermissionError("Resident 只能在所属 Lineage 的当前 Project 内创建成员")
    else:
        pass
    awaiting_default = False
    if not payload.get("backend"):
        from management_portal.execution.account_registrations import role_configuration
        from management_portal.hatg.contracts import InstructionError
        try:
            configured = role_configuration()
        except InstructionError as error:
            if error.details.get("reason") != "backend_role.default_missing":
                raise
            # User enrollment creates an AMA before the first AI is added.
            # Keep its identity and mailbox available until a default exists.
            awaiting_default = True
            configured = {"backend": "codex", "backend_route_id": "", "runtime_settings": {"backend_role": "default"}}
        payload = {**payload, **configured, "runtime_settings": {
            **configured["runtime_settings"], **(payload.get("runtime_settings") or {})}}
    backend = str(payload.get("backend") or "codex").strip() or "codex"
    mode = str(payload.get("mode") or "new_session").strip().lower()
    execution_provider = resident_execution_provider.validate_registration(
        backend,
        mode,
    )
    requested_backend_route_id = str(
        payload.get("backend_route_id") or ""
    ).strip()
    definition = AGENT_PROVIDERS.get(backend)
    backend_route_id = ""
    if backend == "provider_api":
        from management_portal.execution.resident_configuration import validate_api_settings
        validate_api_settings(requested_backend_route_id, {}, {})
        backend_route_id = requested_backend_route_id
    if definition and not awaiting_default:
        account = resolve_account_configuration(definition.provider, requested_backend_route_id)
        backend_route_id = str(account.get("backend_route_id") or "")
    supplied_session_fields = sorted(
        {
            str(key)
            for key in payload
            if str(key) in resident_backend.PERSISTED_SESSION_FIELDS
            or str(key).lower() == "session_id"
            or str(key).lower().endswith("_session_id")
            or str(key).lower().endswith("_thread_id")
        }
    )
    if supplied_session_fields:
        raise ValueError(
            "Resident registration does not accept backend Session IDs: "
            + ", ".join(supplied_session_fields)
        )
    workspace_paths = _optional_absolute_paths(payload.get("workspace_paths"))
    if project_id:
        project_path = str(project_domain.project_root_for_id(project_id).resolve(strict=False))
        if project_path not in workspace_paths:
            workspace_paths.append(project_path)
    resident_id = unique_resident_id()
    lineage_id = str(payload.get("lineage_id") or "").strip()
    if lineage_id and not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("lineage_id must be a canonical lineage_* ID")
    if not lineage_id:
        lineage_id = resident_domain_common.new_lineage_id()
    source_resident_id = str(payload.get("source_resident_id") or "").strip()
    if source_resident_id:
        identity_contract.validate_id(source_resident_id, "resident")
    lineage_folder_raw = str(payload.get("lineage_folder_path") or "").strip()

    if lineage_folder_raw:
        workspace_path = safe_absolute_path(lineage_folder_raw)
        workspace_root = workspace_path.parent
    else:

        workspace_path = resident_lineage_host_port.canonical_lineage_folder(
            lineage_id
        ).resolve(strict=False)
        workspace_root = workspace_path.parent
    requested_workspace_bindings = [
        copy.deepcopy(item)
        for item in payload.get("workspace_bindings") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]

    now = resident_domain_common.now_iso()
    session_state = (
        "active"
        if execution_provider.capabilities.synchronous_delivery
        else "pending_create"
    )

    record = {
        "resident_id": resident_id,
        "folder_schema": resident_domain_common.LINEAGE_FOLDER_SCHEMA_VERSION,
        "lineage_id": lineage_id,
        "lineage_title": str(payload.get("lineage_title") or title),
        "lineage_project_id": str(payload.get("lineage_project_id") or project_id),
        "title": title,
        "project_id": project_id,
        "backend": backend,
        "backend_route_id": backend_route_id,
        "workspace_root": str(workspace_root),
        "workspace_path": str(workspace_path),
        "workspace_paths": workspace_paths,
        "workspace_bindings": requested_workspace_bindings,
        "session_state": session_state,
        "registration_state": "initializing",
        "created_at": now,
        "updated_at": now,
        "created_by": actor_id,
    }
    if project_principal:
        record["principal_hierarchy"] = {"level": "project", "category": "project_leader", "scope_ids": [project_id]}
    if personal_owner or personal_member_owner:
        registered_owner = personal_owner or personal_member_owner
        record["principal_hierarchy"] = {"level": "user", "category": "personal_ama", "scope_ids": [registered_owner]}
        record["personal_owner_id"] = registered_owner

    if source_resident_id:
        record["source_resident_id"] = source_resident_id
    resident_record_domain.apply_resident_resource_identity(record)
    resident_record_domain.set_resident_owned_workspace_binding(record)
    resident_workspace_domain.set_resident_reference_bindings(record, [*workspace_paths, *[item["path"] for item in requested_workspace_bindings]])
    if awaiting_default:
        from management_portal.execution.resident_runtime_settings import normalize_settings
        runtime_settings = normalize_settings({"backend": backend, **payload["runtime_settings"]})
    else:
        runtime_settings = resident_runtime_domain.resident_runtime_settings_from_register_payload(record, payload, actor_id, now)

    resident_folder = resident_record_domain.resident_folder_path(record)
    # Existing files are never owned by an aborted registration, even when
    # its Lineage Folder was explicitly supplied by the caller.
    owned_folder = not (resident_folder.exists() or resident_folder.is_symlink())
    operations = resident_record_domain.lifecycle_operations()
    with operations.prepare(resident_id, lambda operation_id: [{
        "kind": "registration_abort", "record": dict(record), "actor": actor_id,
        "source": str(resident_folder) if owned_folder else "",
        "target": str(resident_folder.with_name(f".aborted-{resident_id}-{operation_id}")),
    }]) as preparation_id:
        workspace_path.mkdir(parents=True, exist_ok=bool(lineage_folder_raw))
        resident_workspace_domain.ensure_resident_workspace(record)
        resident_runtime_domain.write_resident_runtime_settings(record, runtime_settings)

        if bool(payload.get("initialize_from_source")):
            if not source_resident_id:
                raise ValueError(
                    "initialize_from_source requires source_resident_id"
                )
            resident_fork_domain.initialize_resident_context_from_source(
                record,
                source_resident_id,
                str(payload.get("source_context_mode") or "full"),
                actor_id,
                preparation_id=preparation_id,
            )
            resident_session_backend_domain.materialize_pending_agent_data_fork(record, actor_id)

        # Only the publication of identity and membership is serialized. No
        # workspace preparation, native Fork or Provider wait holds this scope.
        registered_lineage = None
        def commit_registration() -> None:
            nonlocal registered_lineage
            if not lineage_folder_raw:
                projection = _resident_lineage_node_projection(record)
                if personal_owner:
                    projection["lineage_membership"] = "personal"
                registered_lineage = resident_lineage_host_port.initialize_resident_lineage(
                    projection, actor_id,
                )
            record["registration_state"] = "resource_pending"
            if lineage_node is not None:
                registered_lineage = resident_lineage_host_port.add_registered_resident_node(
                    _resident_lineage_node_projection(record), actor_id,
                    node_options=lineage_node,
                )
                if registered_lineage is None:
                    raise ValueError("Target Lineage is no longer available")
            resident_record_domain.save_resident_record(record)
            if creation_key:
                # Identity, graph membership, publication outbox and request
                # association must become visible in the same domain commit.
                with resident_catalog.get_catalog().state_store.resident_creation_operations.transaction(lineage_id, creation_key) as receipt:
                    receipt["resident_id"] = resident_id
            operations.commit_preparation(preparation_id)

        with invariant_locks.lineage_scope(lineage_id, operation="resident.register.commit"):
            # Serialize only this identity; unrelated registrations remain independent.
            registration_locks = {f"resident-registration:{resident_id}"}
            with ExitStack() as lock_stack:
                for lock_name in sorted(registration_locks):
                    lock_stack.enter_context(
                        invariant_locks.catalog_lock(
                            lock_name,
                            operation="resident.register.commit",
                        )
                    )
                resident_catalog.get_catalog().state_store.transaction(commit_registration)

    # Resource acknowledgement is outside state locks. A failed publication
    # retains the committed identity and outbox for exact-identity recovery.
    _confirm_registered_resident_resources(
        record, committed_revision=int(registered_lineage["revision"]),
    )
    with resident_record_domain.state_lock(resident_id):
        current = resident_lineage.find(resident_id)
        if current is None:
            raise KeyError("Resident deleted after registration")
    return current, registered_lineage


def unique_resident_id() -> str:
    catalog = resident_catalog.get_catalog()
    while True:
        candidate = resident_domain_common.new_resident_id()
        if not catalog.get_many([candidate]):
            return candidate


def safe_absolute_path(raw: str) -> Path:
    if not raw.strip():
        raise ValueError("workspace_root 不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("workspace_root 必须是绝对路径")
    resolved = path.resolve(strict=False)
    allowed = [
        resident_domain_common.AGENTS_ROOT.resolve(strict=False),
        resident_domain_common.PROJECTS_ROOT.resolve(strict=False),
        resident_project_domain.CODES_ROOT.resolve(strict=False),
        resident_domain_common.STAGING_ROOT.resolve(strict=False),
    ]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError("workspace_root 只能位于当前实例的目录：" + "、".join(map(str, allowed)))
    return resolved
