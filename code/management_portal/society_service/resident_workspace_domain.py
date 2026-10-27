"""Resident domain responsibilities: workspace."""
from __future__ import annotations
from . import business_fact_views

from . import (
    identity_contract,
    invariant_locks,
    resident_backend,
    resident_catalog,
    resident_lifecycle_domain,
    resident_lineage,
    resident_record_domain,
    resident_store,
)
from .domain_ports import resident_lineage_host_port, resource_port
from .resident_access import ensure_resident_visible_to_actor, manage_refusal
from management_common.actor_identity import canonical_actor_id
from pathlib import Path
from typing import Any, Iterable, Mapping
import copy
import os

from . import resident_domain_common
from .project_domain import project_record_for_id


LINEAGE_INSTRUCTIONS_FILE = "LINEAGE.md"


@resident_lineage.access_snapshot_scope()
def resolve_resident_identity(
    resident_id: str,
    actor: str = "",
) -> dict[str, str]:
    """Resolve the exact visible Resident and its Lineage identity."""

    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    return {
        "resident_id": target.resident_id,
        "lineage_id": resident_lineage.lineage_id(target.record),
    }


def _rebase_lineage_managed_path(
    value: object,
    *,
    source_folder: Path,
    canonical_folder: Path,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return raw
    try:
        relative = path.resolve(strict=False).relative_to(
            source_folder.resolve(strict=False)
        )
    except ValueError:
        return raw
    return str(canonical_folder / relative)


def prepare_lineage_project_member_updates(
    lineage_id: str,
    project_id: str,
    *,
    canonical_folder: Path,
    source_folder: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project one Lineage project change into every canonical Resident fact."""

    clean_lineage_id = str(lineage_id or "").strip()
    identity_contract.validate_id(clean_lineage_id, "lineage")
    canonical = Path(os.path.abspath(canonical_folder.expanduser()))
    source = source_folder.expanduser().resolve(strict=False)
    previous_records: list[dict[str, Any]] = []
    next_records: list[dict[str, Any]] = []
    now = resident_domain_common.now_iso()
    for current in resident_catalog.get_catalog().snapshot():
        if (
            str(current.get("lineage_id") or "").strip() != clean_lineage_id
            or str(current.get("deleted_at") or "").strip()
        ):
            continue
        previous_records.append(copy.deepcopy(current))
        projected = copy.deepcopy(current)
        projected.update({
            "project_id": str(project_id or "").strip(),
            "lineage_project_id": str(project_id or "").strip(),
            "workspace_root": str(canonical.parent),
            "workspace_path": str(canonical),
            "updated_at": now,
        })
        pre_normalized_bindings = []
        for binding in projected.get("workspace_bindings") or []:
            if not isinstance(binding, dict):
                continue
            item = copy.deepcopy(binding)
            item["path"] = _rebase_lineage_managed_path(
                item.get("path"), source_folder=source, canonical_folder=canonical,
            )
            pre_normalized_bindings.append(item)
        projected["workspace_bindings"] = pre_normalized_bindings
        resident_record_domain.apply_resident_resource_identity(projected)
        for field in (
            "default_cwd",
        ):
            if field in projected:
                projected[field] = _rebase_lineage_managed_path(
                    projected.get(field),
                    source_folder=source,
                    canonical_folder=canonical,
                )
        for field in (
            "workspace_paths",
        ):
            if isinstance(projected.get(field), list):
                projected[field] = [
                    _rebase_lineage_managed_path(
                        item,
                        source_folder=source,
                        canonical_folder=canonical,
                    )
                    for item in projected[field]
                ]
        roots = []
        for root in projected.get("workspace_roots") or []:
            if not isinstance(root, dict):
                continue
            item = copy.deepcopy(root)
            item["path"] = _rebase_lineage_managed_path(
                item.get("path"),
                source_folder=source,
                canonical_folder=canonical,
            )
            roots.append(item)
        projected["workspace_roots"] = roots
        bindings = []
        for binding in projected.get("workspace_bindings") or []:
            if not isinstance(binding, dict):
                continue
            item = copy.deepcopy(binding)
            item["path"] = _rebase_lineage_managed_path(
                item.get("path"),
                source_folder=source,
                canonical_folder=canonical,
            )
            bindings.append(item)
        projected["workspace_bindings"] = bindings
        resident_record_domain.set_resident_owned_workspace_binding(projected)
        resident_record_domain.validated_resident_record_payload(projected)
        next_records.append(projected)
    return previous_records, next_records


def persist_lineage_project_member_updates(
    records: Iterable[dict[str, Any]],
) -> None:
    from .resident_workspace_storage import canonical_path
    catalog = resident_catalog.get_catalog()

    def commit() -> None:
        for record in records:
            # The canonical Lineage entry may now point to its new Project folder.
            # Keep the Workspace identity while resolving its new physical path.
            owned = record["owned_workspace"]
            owned["path"] = canonical_path(resident_record_domain.resident_owned_workspace_path(record))
            record["default_cwd"] = owned["path"]
            resident_record_domain.require_resident_owned_workspace_binding(record)
            refresh_resident_workspace_document(record)

            from management_portal.resources.workspace_store import get_workspace_store
            store = get_workspace_store()
            for workspace in [record["owned_workspace"], *record["workspace_bindings"]]:
                catalog.state_store.database.write(lambda connection, workspace=workspace:
                    store.ensure(workspace["workspace_id"], workspace["path"], move=True, connection=connection))
            resident_record_domain.save_resident_record(record)
            # Lineage and descriptor changes are already visible to _save's
            # old record read. Publish even when hydrated signatures are equal.
            catalog.state_store.enqueue_resident_resource_projection(
                record["resident_id"], catalog.get(record["resident_id"]),
            )


    catalog.state_store.transaction(commit)


def resident_workspaces_text(record: dict[str, Any]) -> str:
    lines = ["# Workspaces", "", "自有 Workspace 是默认启动目录；其他目录用于任务协作。", ""]
    for root in resident_record_domain.resident_workspace_roots(record):
        title = "自有 Workspace" if root["is_primary"] else root["label"]
        lines.extend([f"## {title}", "", f"- ID: `{root['workspace_id']}`", f"- Path: `{root['path']}`"])
        if root["purpose"]:
            lines.append(f"- 用途：{root['purpose']}")
        lines.append("")
    return "\n".join(lines)


def ensure_resident_file(
    resident_folder: Path,
    filename: str,
    *,
    initial_content: str = "",
) -> Path:
    path = resident_folder / filename
    if not path.exists():
        path.write_text(initial_content, encoding="utf-8")
    return path


def refresh_resident_workspace_document(record: dict[str, Any]) -> None:
    resident_folder = resident_record_domain.resident_folder_path(record)
    resident_folder.mkdir(parents=True, exist_ok=True)
    resident_store.replace_file_text(
        resident_folder / resident_domain_common.RESIDENT_WORKSPACES_FILE,
        resident_workspaces_text(record),
    )


def ensure_resident_workspace(record: dict) -> None:
    if record.get("deleted_at"):
        raise KeyError("resident not found")
    resident_backend.validate_resident_session_identity(record)
    workspace = resident_record_domain.resident_workspace_path(record)

    resident_lineage_host_port.bind_lineage_folder(
        resident_lineage.lineage_id(record),
        workspace,
    )
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "residents").mkdir(parents=True, exist_ok=True)
    lineage_md = workspace / LINEAGE_INSTRUCTIONS_FILE
    if not lineage_md.exists():
        lineage_md.touch()
    owned = resident_record_domain.require_resident_owned_workspace_binding(record)
    owned_path = Path(str(owned["path"])).resolve(strict=False)
    expected = resident_record_domain.resident_owned_workspace_path(record)
    if owned_path != expected:
        raise ValueError("Resident managed Workspace path must be <resident_folder>/workspace")
    owned_path.mkdir(parents=True, exist_ok=True)
    resident_folder = resident_record_domain.resident_folder_path(record)
    resident_folder.mkdir(parents=True, exist_ok=True)
    ensure_resident_file(resident_folder, resident_domain_common.RESIDENT_INSTRUCTIONS_FILE)
    ensure_resident_file(resident_folder, resident_domain_common.RESIDENT_DIARY_FILE)
    ensure_resident_file(resident_folder, resident_domain_common.RESIDENT_MAILBOX_FILE)
    ensure_resident_file(
        resident_folder,
        resident_domain_common.RESIDENT_WORKSPACES_FILE,
        initial_content=resident_workspaces_text(record),
    )
    resident_record_domain.sessions_path(record).mkdir(parents=True, exist_ok=True)


def _safe_workspace_location_for_delete(record: dict) -> Path:
    path = resident_record_domain.resident_workspace_path(record)
    allowed = [
        resident_domain_common.AGENTS_ROOT.resolve(strict=False),
        resident_domain_common.PROJECTS_ROOT.resolve(strict=False),
        resident_domain_common.STAGING_ROOT.resolve(strict=False),
    ]
    if not any(path == root or root in path.parents for root in allowed):
        raise ValueError("workspace_path 不在允许删除的根目录内")
    if path in allowed:
        raise ValueError("拒绝删除共享根目录")
    return path


def _safe_lineage_workspace_for_delete(record: dict) -> Path:
    resident_id = resident_domain_common.record_resident_id(record)
    lineage_id = resident_lineage.lineage_id(record)
    if not resident_id or not lineage_id:
        raise ValueError("删除 Lineage 需要已登记的成员及 Lineage 身份")
    path = _safe_workspace_location_for_delete(record)
    expected = resident_lineage_host_port.canonical_lineage_folder(lineage_id).resolve(strict=False)
    if path != expected:
        raise ValueError("Resident 注册的 Lineage 路径与 Lineage Folder 不匹配")
    return path


def _prepare_resident_deletion(
    resident_id: str,
    actor: str,
    *,
    whole_lineage: bool = False,
    removed_family_ids: frozenset[str] = frozenset(),
) -> dict:
    from . import resident_access
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后删除")
    with resident_lineage.access_snapshot_scope():
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if not resident_access.is_active_actor(actor_id):
            raise PermissionError("需要有效的 Portal 登录身份")

        family = record.get("family") or {}
        role = family.get("role")
        if not whole_lineage and role in {"facade", "steward", "router"} and family.get("family_id") not in removed_family_ids:
            from .errors import LineageConflictError

            label = {"facade": "门面", "steward": "steward", "router": "Router"}[role]
            raise LineageConflictError(f"这是 Family 的{label}，需要先 Unfamilize", resource=resident_id)

        deleted_at = resident_domain_common.now_iso()
        target_id = str(record.get("resident_id") or "")
        lineage_id = resident_lineage.lineage_id(record)
        delete_aggregate = whole_lineage
        if delete_aggregate:
            resident_record_domain.lifecycle_operations().require_lineage_transfer_settled(lineage_id)
            deletion_records = [
                item
                for item in lineage_registry_records_locked(lineage_id)
                if str(item.get("lineage_id") or "").strip() == lineage_id
            ]
        else:
            deletion_records = [record]

        for member in deletion_records:
            member_id = resident_domain_common.record_resident_id(member)
            if resident_lifecycle_domain.resident_has_pending_work(member_id):
                raise ValueError(
                    f"Resident {member_id} 仍有消息任务或 Fork 创建任务正在排队/处理，完成或停止后再删除"
                )

        if delete_aggregate:
            workspace_path = _safe_lineage_workspace_for_delete(record)
            mismatched_members = [
                resident_domain_common.record_resident_id(item)
                for item in deletion_records
                if resident_record_domain.resident_workspace_path(item) != workspace_path
            ]
            if mismatched_members:
                raise ValueError(
                    "同一 Lineage 的 Resident 注册指向不同 Lineage Folder，拒绝删除："
                    + ", ".join(mismatched_members[:5])
                )
        else:
            workspace_path = resident_record_domain.resident_folder_path(record)
            lineage_workspace = resident_record_domain.resident_workspace_path(record).resolve(strict=False)
            residents_root = (lineage_workspace / "residents").resolve(strict=False)
            resident_path = workspace_path.resolve(strict=False)
            if workspace_path.name != target_id or not (resident_path == residents_root / target_id or residents_root in resident_path.parents):
                raise ValueError("resident folder 与 resident_id 不匹配，拒绝移动工作区")

        archive_project_info = project_info_for_workspace(record, workspace_path)
        lineage_deletion_token: Any | None = None

        if delete_aggregate:
            lineage_deletion_token = resident_lineage_host_port.prepare_lineage_deletion(
                lineage_id, actor_id, deleted_at, workspace_path,
            )

        deleted_ids = {resident_domain_common.record_resident_id(item) for item in deletion_records}
        deleted_records = [{
            **{key: copy.deepcopy(item[key]) for key in (
                "resident_id", "lineage_id", "project_id", "title", "description",
                "created_at", "created_by", "source_resident_id", "fork_mode",
                "owned_workspace_id", "owned_workspace", "workspace_bindings",
            ) if key in item},
            "deleted_at": deleted_at,
            "deleted_by": actor_id,
        } for item in deletion_records]
        steps = []
        if lineage_deletion_token is not None:
            tombstone, unbind = resident_lineage_host_port.lineage_deletion_effects(
                lineage_deletion_token,
            )
            steps.append(tombstone)
        steps.extend(resident_lifecycle_domain.resident_release_step(member, actor_id) for member in deletion_records)
        steps.append({
            "kind": "forget_deleted",
            "binding_ids": [resident_backend.execution_binding_id(member) for member in deletion_records],
            "resident_ids": [resident_domain_common.record_resident_id(member) for member in deletion_records],
        })
        if lineage_deletion_token is not None:
            steps.append(unbind)
        if workspace_path.exists():
            # Deletion removes the folder; Portal keeps no archive to restore from.
            steps.append({"kind": "remove", "source": str(workspace_path),
                          "workspace_ids": [item["owned_workspace_id"] for item in deletion_records]})
        result = {
            "resident_id": target_id,
            "lineage_id": lineage_id,
            "deleted_resident_ids": sorted(deleted_ids),
            "deleted_at": deleted_at,
            "deleted_by": actor_id,
            "workspace_original_path": str(workspace_path),
            "deleted_project_id": archive_project_info.get("project_id", ""),
            "deleted_project_name": archive_project_info.get("project_name", ""),
            "deleted_project_path": archive_project_info.get("project_path", ""),
        }
        return {
            "result": result,
            "records": deleted_records,
            "steps": steps,
            "lineage_deletion_token": lineage_deletion_token,
        }


def _commit_resident_deletions(
    plans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Commit one deletion set and its durable effects without external I/O.

    A member deletion's graph removal is the caller's own graph transaction.
    """
    catalog = resident_catalog.get_catalog()
    deleted_ids = {record["resident_id"] for plan in plans for record in plan["records"]}
    projection_revisions: dict[str, int] = {}

    def commit() -> None:
        from .lineage_definition_scope import check_before_membership_write
        for lineage_id in sorted({plan["result"]["lineage_id"] for plan in plans}):
            check_before_membership_write(catalog.state_store, lineage_id)
        retired_paths = catalog.remove([record for plan in plans for record in plan["records"]])
        for plan in plans:
            retired_paths.extend(catalog.state_store.retire_workspaces_under(
                [plan["result"]["workspace_original_path"]],
                deleted_at=plan["result"]["deleted_at"], deleted_by=plan["result"]["deleted_by"],
            ))
        if plans:
            # Include extra Workspaces outside the deleted Resident folders.
            # Every removal re-reads live registrations before touching files.
            plans[-1]["steps"].extend({"kind": "remove", "source": path}
                                      for path in sorted(set(retired_paths)))
        for plan in plans:
            for record in plan["records"]:
                projection_revisions[record["resident_id"]] = (
                    catalog.state_store.enqueue_resident_resource_projection(
                        record["resident_id"], record
                    )
                )
            if plan["lineage_deletion_token"] is not None:
                resident_lineage_host_port.commit_lineage_deletion(plan["lineage_deletion_token"])
            resident_record_domain.lifecycle_operations().enqueue_cleanup(plan["result"]["resident_id"], plan["steps"])

    catalog.state_store.transaction(commit)
    from management_portal.society_service.changes import lineage_changed, resident_deleted

    for resident_id in sorted(deleted_ids):
        resident_deleted(resident_id)
    for lineage_id in {plan["result"]["lineage_id"] for plan in plans}:
        lineage_changed(lineage_id)
    records = [record for plan in plans for record in plan["records"]]
    return [plan["result"] for plan in plans], records, projection_revisions


def _confirm_deleted_resident_resources(
    records: list[dict[str, Any]],
    projection_revisions: Mapping[str, int],
) -> None:
    """Publish committed tombstones before deletion is reported as complete.

    The deleted Residents' bearer tokens are disabled first, so a leftover
    token file can no longer call Portal (decision_fb910c43ab07).
    """
    from management_common.projection_receipts import project_committed
    from . import resident_hatg_credentials, resource_client
    from .resident_resource_projection import ResidentResourceProjectionWorker

    for record in records:
        try:
            resident_hatg_credentials.revoke_resident_credential(record["resident_id"])
        except Exception as exc:  # noqa: BLE001 - the deletion itself is already committed
            import logging

            logging.getLogger(__name__).warning(
                "Bearer token of deleted Resident %s was not disabled: %s",
                record.get("resident_id"), type(exc).__name__,
            )

    def publish() -> None:
        store = resident_catalog.get_catalog().state_store
        worker = ResidentResourceProjectionWorker(store, resource_client)
        for record in records:
            resident_id = record["resident_id"]
            project_committed(
                {
                    "projection": "resident.resource_tombstone",
                    "resident_id": resident_id,
                    "lineage_id": record["lineage_id"],
                    "committed_revision": int(projection_revisions[resident_id]),
                },
                lambda selected=resident_id: worker.confirm_tombstone(selected),
            )

    invariant_locks.defer_until_state_unlock(publish)


def confirm_resident_deletion_projection(resident_id: str) -> None:
    """Retry the committed deletion projection without recreating its identity."""
    from management_common.projection_receipts import project_committed
    from . import resource_client, resident_hatg_credentials
    from .resident_resource_projection import ResidentResourceProjectionWorker

    store = resident_catalog.get_catalog().state_store
    with store.database.read() as connection:
        row = connection.execute(
            "SELECT revision FROM resident_resource_projection_outbox WHERE resident_id=%s", (resident_id,),
        ).fetchone()

    def publish() -> None:
        resident_hatg_credentials.revoke_resident_credential(resident_id)
        ResidentResourceProjectionWorker(store, resource_client).confirm_tombstone(resident_id)

    project_committed({
        "projection": "resident.resource_tombstone", "resident_id": resident_id,
        "committed_revision": int(row["revision"]) if row else 0,
    }, publish)


def retry_deleted_resident(resident_id: str, actor: str) -> dict[str, Any] | None:
    """Authorize a deletion replay and finish its existing tombstone publication."""
    from . import resident_access

    try:
        record = resident_catalog.get_catalog().get_historical(resident_id)
    except KeyError:
        return None
    if not record.get("deleted_at"):
        return None
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后删除")
    if not resident_access.is_active_actor(actor_id):
        raise PermissionError("需要有效的 Portal 登录身份")
    # Replays do not allocate cleanup work or replace the first deletion facts.
    invariant_locks.defer_until_state_unlock(
        lambda: confirm_resident_deletion_projection(resident_id),
    )
    return {
        "resident_id": resident_id,
        "lineage_id": str(record.get("lineage_id") or ""),
        "deleted_resident_ids": [resident_id],
        "deleted_at": record["deleted_at"],
        "deleted_by": str(record.get("deleted_by") or ""),
    }


def delete_resident_identities(
    lineage_id: str, resident_ids: list[str], actor: str, *,
    removed_family_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Delete an exact set; the caller owns the graph transaction and picks a new Leader."""
    if not resident_ids or len(set(resident_ids)) != len(resident_ids):
        raise ValueError("Resident deletion requires a nonempty set of unique IDs")
    with invariant_locks.lineage_scope(lineage_id, operation="residents.delete"):
        with invariant_locks.resident_locks(resident_ids, operation="residents.delete"):
            for resident_id in resident_ids:
                record = resident_record_domain.find_resident(resident_id)
                if not record or resident_lineage.lineage_id(record) != lineage_id:
                    raise ValueError("Resident does not belong to the target Lineage")
            plans = [_prepare_resident_deletion(resident_id, actor, removed_family_ids=removed_family_ids) for resident_id in resident_ids]
            results, records, revisions = _commit_resident_deletions(plans)
    _confirm_deleted_resident_resources(records, revisions)
    return results


def delete_resident_lineage(resident_id: str, actor: str) -> dict:
    """Delete the complete Lineage identified by an existing member."""
    previous = retry_deleted_resident(resident_id, actor)
    if previous is not None:
        return previous
    record = resident_record_domain.find_resident(resident_id)
    if not record:
        raise KeyError("resident not found")
    lineage_id = resident_lineage.lineage_id(record)
    with invariant_locks.lineage_scope(
        lineage_id,
        operation="resident.delete",
    ):
        participants = [resident_domain_common.record_resident_id(item) for item in lineage_registry_records_locked(lineage_id)]
        with invariant_locks.resident_locks(participants, operation="resident.delete"):
            previous = retry_deleted_resident(resident_id, actor)
            if previous is not None:
                return previous
            results, records, revisions = _commit_resident_deletions(
                [_prepare_resident_deletion(resident_id, actor, whole_lineage=True)],
            )
    _confirm_deleted_resident_resources(records, revisions)
    return results[0]


def prepare_resident_lineage_move(record: dict[str, Any], target_lineage_id: str, actor_id: str) -> dict[str, Any]:
    """Compute identity and durable transfer intent; never move files in this scope."""
    definition = resident_lineage_host_port.managed_lineage_definition(target_lineage_id, actor_id)
    # Existing member data supplies workspace defaults only. Identity,
    # placement and authority come from the target Lineage, including empty ones.
    source_folder = resident_record_domain.resident_folder_path(record)
    target_lineage_folder = resident_lineage_host_port.canonical_lineage_folder(target_lineage_id).resolve(strict=False)
    destination = target_lineage_folder / "residents" / resident_domain_common.record_resident_id(record)
    if destination.exists():
        raise FileExistsError(f"目标 Lineage 已存在 Resident folder: {destination}")
    owned_path = resident_record_domain.resident_owned_workspace_path(record).resolve(strict=False)
    for binding in record.get("workspace_bindings") or []:
        if Path(str(binding.get("path") or "")).resolve(strict=False) == owned_path:
            binding["path"] = str(destination / "workspace")
    record.update({
        "lineage_id": target_lineage_id,
        "lineage_title": str(definition.get("title") or ""),
        "lineage_project_id": str(definition.get("project_id") or ""),
        "workspace_root": str(target_lineage_folder.parent),
        "workspace_path": str(target_lineage_folder),
        "project_id": str(definition.get("project_id") or ""),
    })
    record["default_cwd"] = resident_record_domain.resident_default_cwd_path(record)
    record["updated_at"] = resident_domain_common.now_iso()
    return {"kind": "workspace_move", "source": str(source_folder), "target": str(destination)}


def enqueue_resident_lineage_move(record: dict[str, Any], transfer: dict[str, Any], source_id: str, previous_path: str) -> str:
    resident_id = record["resident_id"]
    target_id = resident_lineage.lineage_id(record)
    return resident_record_domain.lifecycle_operations().enqueue_transfer(resident_id, [
        transfer,
        {**transfer, "kind": "workspace_finish", "resident_id": resident_id,
         "lineage_id": target_id, "previous_workspace_path": previous_path},
        {"kind": "resident_detach", "lineage_id": source_id},
        {"kind": "resident_detach", "lineage_id": target_id},
    ], source_id, target_id)


def update_resident_record(resident_id: str, payload: dict, actor: str) -> dict:
    from . import resident_access
    from .resident_projection import _optional_absolute_paths, _resident_lineage_node_projection
    from .resident_workspace_storage import canonical_path
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后编辑")
    with resident_lineage.access_snapshot_scope():
        record = resident_record_domain.find_resident(resident_id)
        if not record or record.get("deleted_at"):
            raise KeyError("resident not found")
        if not resident_access.can_manage(record, actor_id):
            raise manage_refusal(record)

        title = str(payload.get("title") or record.get("title") or "").strip()
        if not title:
            raise ValueError("title 不能为空")
        requested_paths = _optional_absolute_paths(payload["workspace_paths"]) if "workspace_paths" in payload else None

        if resident_record_domain.lifecycle_operations().pending(resident_id):
            raise ValueError("Resident 生命周期操作正在完成，请稍后编辑")
        source_lineage_id = resident_lineage.lineage_id(record)
        previous_workspace_path = str(record.get("workspace_path") or "")
        transfer = None
        moved_lineage = False
        target_lineage_id = str(payload.get("target_lineage_id") or "").strip()
        if target_lineage_id:
            identity_contract.validate_id(target_lineage_id, "lineage")
            if target_lineage_id != resident_lineage.lineage_id(record):
                transfer = prepare_resident_lineage_move(record, target_lineage_id, actor_id)
                moved_lineage = True

        if "project_id" in payload and not moved_lineage:
            next_project_id = str(payload.get("project_id") or "").strip()
            if next_project_id != str(record.get("project_id") or "").strip():
                raise ValueError("Resident project_id 由 Lineage 派生，请通过 Lineage 修改 Project 归属")
        record["title"] = title
        workspace_inputs_changed = requested_paths is not None
        if workspace_inputs_changed:
            requested = {canonical_path(path) for path in requested_paths}
            record["workspace_bindings"] = [binding for binding in record["workspace_bindings"]
                                           if binding["path"] in requested]
            set_resident_reference_bindings(record, sorted(requested))
        if transfer is None:
            resident_record_domain.require_resident_owned_workspace_binding(record)
        if transfer is not None or workspace_inputs_changed:
            resident_record_domain.set_resident_owned_workspace_binding(record)
        record["updated_at"] = resident_domain_common.now_iso()

        resident_record_domain.apply_resident_resource_identity(record)
        if transfer is None:
            ensure_resident_workspace(record)
            refresh_resident_workspace_document(record)
        if transfer is not None:
            resident_lineage_host_port.move_resident_membership(
                source_lineage_id, _resident_lineage_node_projection(record), actor_id,
                allow_empty_source=payload.get("allow_empty_source", False),
                publish_identity=lambda: resident_record_domain.save_resident_record(record),
            )
            operation_id = enqueue_resident_lineage_move(
                record, transfer, source_lineage_id, previous_workspace_path,
            )
            return {**record, "workspace_pending": True, "operation_id": operation_id}
        resident_record_domain.save_resident_record(record)
        return record


def _save_resident_workspace_bindings(record: dict[str, Any]) -> str:
    resident_id = str(record["resident_id"])
    operations = resident_record_domain.lifecycle_operations()
    if operations.pending(resident_id):
        raise ValueError("Resident 生命周期操作正在完成，请稍后重试")

    def commit() -> str:
        resident_record_domain.save_resident_record(record)
        return operations.enqueue(resident_id, [{
            "kind": "workspace_document", "resident_id": resident_id,
        }])

    return resident_catalog.get_catalog().state_store.transaction(commit)


def _validated_reference_workspace_path(workspace_path: str) -> Path:
    raw_path = str(workspace_path or "").strip()
    if not raw_path:
        raise ValueError("Workspace 路径不能为空")
    if len(raw_path) > 4096:
        raise ValueError("Workspace 路径过长")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("Workspace 路径必须是绝对路径")
    try:
        resolved_path = candidate.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"无法解析 Workspace 路径：{exc}") from exc
    if not resolved_path.exists() or not resolved_path.is_dir():
        raise ValueError("Workspace 路径不存在或不是目录")

    return resolved_path


def set_resident_reference_bindings(record: dict, paths: list[str]) -> None:
    from .resident_workspace_storage import apply_binding_projection, canonical_path
    from .domain_ports import resource_port
    resolved = list(dict.fromkeys(canonical_path(_validated_reference_workspace_path(path)) for path in paths))
    own_path = canonical_path(record["owned_workspace"]["path"])
    previous = {item["path"]: item for item in record.get("workspace_bindings") or []}
    record["workspace_bindings"] = [
        copy.deepcopy(previous[path]) if path in previous else {
            "workspace_id": resource_port.workspace_id_for_path(path), "path": path, "purpose": ""
        } for path in resolved if path != own_path
    ]
    apply_binding_projection(record)


def add_resident_reference_workspace(
    resident_id: str,
    workspace_path: str,
    actor: str,
    *,
    workspace_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """Bind one existing directory as another Workspace for this Resident."""
    from . import resident_access
    from .resident_workspace_storage import apply_binding_projection, canonical_path
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后增加 Workspace")
    resolved_path = _validated_reference_workspace_path(workspace_path)

    operation_id = ""
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record or record.get("deleted_at"):
            raise KeyError("resident not found")
        if not resident_access.can_manage(record, actor_id):
            raise manage_refusal(record)

        resolved_text = canonical_path(resolved_path)
        existing_root = next(
            (
                root for root in resident_record_domain.resident_workspace_roots(record)
                if canonical_path(root["path"]) == resolved_text
            ),
            None,
        )
        added = existing_root is None
        existing_binding = next(
            (
                item
                for item in record.get("workspace_bindings") or []
                if isinstance(item, dict)
                and canonical_path(item["path"])
                == resolved_text
            ),
            None,
        )
        canonical_workspace_id = resource_port.workspace_id_for_path(
            resolved_text, preferred_id=str(workspace_id or (existing_binding or {}).get("workspace_id") or ""))
        if added:
            record["workspace_bindings"].append({
                "workspace_id": canonical_workspace_id, "path": resolved_text,
                "purpose": str(purpose or "").strip()[:500],
            })
            apply_binding_projection(record)
            record["updated_at"] = resident_domain_common.now_iso()
            resident_record_domain.apply_resident_resource_identity(record)
            resident_record_domain.require_resident_owned_workspace_binding(record)
            operation_id = _save_resident_workspace_bindings(record)

        result = {
            "resident_id": str(record.get("resident_id") or ""),
            "path": resolved_text,
            "added": added,
            "workspace_id": canonical_workspace_id,
            "default_cwd": resident_record_domain.resident_default_cwd_path(record),
            "workspace_paths": resident_record_domain.workspace_paths(record),
        }

    if operation_id and not resident_record_domain.run_lifecycle_operation(operation_id):
        result.update(workspace_document_pending=True, operation_id=operation_id)
    return result


def _workspace_binding_matches(
    binding: dict[str, Any],
    *,
    workspace_id: str = "",
    workspace_path: str = "",
) -> bool:
    if workspace_id and str(binding.get("workspace_id") or "") == workspace_id:
        return True
    if workspace_path:
        try:
            return (
                Path(str(binding.get("path") or ""))
                .expanduser()
                .resolve(strict=False)
                == Path(workspace_path).expanduser().resolve(strict=False)
            )
        except OSError:
            return False
    return False


def remove_resident_reference_workspace(
    resident_id: str,
    actor: str,
    *,
    workspace_id: str = "",
    workspace_path: str = "",
) -> dict[str, Any]:
    """Unbind one non-owned Workspace without deleting its files."""
    from . import resident_access

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后解除 Workspace 绑定")
    if not any((workspace_id, workspace_path)):
        raise ValueError("解除 Workspace 绑定需要 workspace_id 或 path")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record or record.get("deleted_at"):
            raise KeyError("resident not found")
        if not resident_access.can_manage(record, actor_id):
            raise manage_refusal(record)
        bindings = [
            copy.deepcopy(item)
            for item in record.get("workspace_bindings") or []
            if isinstance(item, dict)
        ]
        selected = next(
            (
                item
                for item in bindings
                if _workspace_binding_matches(
                    item,
                    workspace_id=str(workspace_id or ""),
                    workspace_path=str(workspace_path or ""),
                )
            ),
            None,
        )
        if selected is None:
            raise KeyError("workspace binding not found")
        removed_path = str(
            Path(str(selected.get("path") or ""))
            .expanduser()
            .resolve(strict=False)
        )

        record["workspace_bindings"] = [
            item for item in bindings if item is not selected
        ]
        from .resident_workspace_storage import apply_binding_projection
        apply_binding_projection(record)
        resident_record_domain.require_resident_owned_workspace_binding(record)
        record["updated_at"] = resident_domain_common.now_iso()
        resident_record_domain.apply_resident_resource_identity(record)
        operation_id = _save_resident_workspace_bindings(record)
        result = {
            "resident_id": str(record.get("resident_id") or ""),
            "workspace_id": str(selected.get("workspace_id") or ""),
            "path": removed_path,
            "removed": True,
            "files_deleted": False,
        }

    if not resident_record_domain.run_lifecycle_operation(operation_id):
        result.update(workspace_document_pending=True, operation_id=operation_id)
    return result


def _get_resident_workspace_roots(resident_id: str, actor: str = "") -> dict:
    actor_id = canonical_actor_id(actor)
    with resident_lineage.access_snapshot_scope():
        record = resident_record_domain.find_resident(resident_id)
        if not record or record.get("deleted_at"):
            raise KeyError("resident not found")
        record_snapshot = copy.deepcopy(record)

    # ResourceService and filesystem access are independent state owners. Keep
    # both outside the Lineage aggregate lock so one slow workspace request
    # cannot stall unrelated HATG reads and mutations for this Lineage.
    hydrated = business_fact_views.canonical_views(
        [record_snapshot],
        id_field="resident_id",
        actor=actor_id,
    )
    record = hydrated[0]
    ensure_resident_visible_to_actor(record, actor_id)
    workspace_roots, default_cwd = resident_record_domain.resident_workspace_roots_with_usage(record)
    roots = []
    for root_info in workspace_roots:
        root_path = Path(str(root_info.get("path") or "")).expanduser().resolve(strict=False)
        roots.append({**root_info, "path": root_path.as_posix(), "exists": root_path.exists(), "is_dir": root_path.is_dir()})
    return {
        "resident_id": resident_domain_common.record_resident_id(record),
        "workspace_id": str(record.get("owned_workspace_id") or ""),
        "default_cwd": default_cwd,
        "roots": roots,
    }


def get_resident_workspace_roots(resident_id: str, actor: str = "") -> dict[str, Any]:
    """The bound Workspaces of one exact Resident identity; files are read by ReadResource."""
    from . import resident_access

    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    if not resident_access.has_resident_workspace_authority(
        target.record,
        actor,
        None,
    ):
        raise PermissionError("无权读取该 Resident 的 Workspace")
    with resident_lineage.access_snapshot_scope():
        result = _get_resident_workspace_roots(target.resident_id, actor)
    result["resident_id"] = target.resident_id
    return result


def lineage_registry_records_locked(lineage_id: str) -> list[dict[str, Any]]:
    target = str(lineage_id or "").strip()
    if not identity_contract.is_valid_id(target, "lineage"):
        raise ValueError("lineage_id must be a canonical lineage_* ID")
    records: list[dict[str, Any]] = []
    for item in resident_lineage.for_lineage(target):
        if not isinstance(item, dict):
            continue
        try:
            item_lineage_id = resident_lineage.lineage_id(item)
        except ValueError:
            continue
        if item_lineage_id == target:
            records.append(item)
    return records


def project_info_for_workspace(record: dict, workspace_path: Path) -> dict[str, str]:
    project_id = str(record.get("project_id") or "").strip()
    project_record: dict[str, Any] = {}
    project_root: Path | None = None
    if project_id:
        try:
            project_record, project_root = project_record_for_id(project_id)
        except KeyError:
            pass
    project_name = project_root.name if project_root else ""
    return {
        "project_id": project_id,
        "project_name": project_name,
        "project_path": str(project_root or ""),
        "project_registry_id": str(project_record.get("id") or "") if project_record else "",
    }
