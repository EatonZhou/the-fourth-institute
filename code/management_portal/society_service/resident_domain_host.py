from __future__ import annotations

from pathlib import Path
from typing import Any

from . import (
    family_quota,
    resident_directory_domain,
    resident_execution_domain,
    resident_fork_domain,
    resident_intake_domain,
    resident_lifecycle_domain,
    resident_lifecycle_steps_domain,
    resident_lineage,
    resident_management_domain,
    resident_media_domain,
    resident_record_domain,
    resident_registration_domain,
    resident_restart_domain,
    resident_workspace_domain,
)


class ResidentDomainHost:
    """Application adapter for capabilities owned by the Resident domain."""

    _delegates = {
        name: module
        for module, names in (
            (family_quota, ("ensure_role_reserve", "reconcile_role_reserves")),
            (resident_directory_domain, (
                "get_resident_user_message_count", "get_resident_user_message_counts",
                "get_resident_context_pressures", "get_lineage_node_metadata",
            )),
            (resident_execution_domain, ("resume_resident_execution",)),
            (resident_fork_domain, (
                "create_resident_full_context_fork", "create_resident_compacted_context_fork",
                "create_resident_fork_from_message",
                "create_resident_context_fork",
            )),
            (resident_management_domain, (
                "rename_resident_identity", "rename_resident_title", "update_resident",
                "move_resident_identities",
            )),
            (resident_intake_domain, ("deliver_resident_message_content",)),
            (resident_lifecycle_domain, (
                "observe_committed_resident_transfer",
            )),
            (resident_lifecycle_steps_domain, ("apply_resident_lifecycle_step",)),
            (resident_media_domain, ("cleanup_expired_input_images",)),
            (resident_record_domain, ("sync_lineage_member_credentials",)),
            (resident_registration_domain, (
                "register_resident", "confirm_resident_registration",
            )),
            (resident_restart_domain, (
                "restart_resident_session", "get_resident_session_restart",
                "cancel_resident_session_restart",
            )),
            (resident_workspace_domain, (
                "delete_resident_identities", "confirm_resident_deletion_projection",
                "prepare_lineage_project_member_updates", "persist_lineage_project_member_updates",
                "get_resident_workspace_roots",
                "add_resident_reference_workspace", "remove_resident_reference_workspace",
                "resolve_resident_identity",
            )),
        )
        for name in names
    }

    def __getattr__(self, name: str) -> Any:
        module = self._delegates.get(name)
        if module is None:
            raise AttributeError(name)
        return getattr(module, name)

    @staticmethod
    def invalidate_lineage_access_projection(lineage_id: str) -> None:
        resident_lineage.invalidate_access_projection(lineage_id)

    @staticmethod
    def delete_registered_lineage(
        lineage_id: str,
        preferred_resident_id: str,
        actor: str,
    ) -> dict[str, Any] | None:
        records = [
            item
            for item in resident_lineage.for_lineage(lineage_id)
            if isinstance(item, dict)
            and str(item.get("lineage_id") or "") == lineage_id
        ]
        if not records:
            resident_record_domain.lifecycle_operations().require_lineage_transfer_settled(lineage_id)
            return None
        target = next(
            (
                item
                for item in records
                if preferred_resident_id
                and str(item.get("resident_id") or "")
                == preferred_resident_id
                and resident_lineage.is_lineage_leader(item)
            ),
            None,
        )
        if target is None:
            target = next(
                (
                    item
                    for item in records
                    if resident_lineage.is_lineage_leader(item)
                ),
                None,
            )
        if target is None:
            # A formal Lineage may have members without a designated leader.
            # This record locates the aggregate; it does not acquire a role.
            target = min(records, key=lambda item: str(item.get("resident_id") or ""))
        return resident_workspace_domain.delete_resident_lineage(
            str(target.get("resident_id") or ""),
            actor,
        )

    @staticmethod
    def authorize_workspace_path(
        path: Path,
        actor: str,
        *,
        write: bool = False,
    ) -> dict[str, Any]:
        """Whether an absolute path may be read (or written): the object holding it decides (decision_b820faf42218 §5.5).

        The path belongs to the deepest live Workspace or personal AMA folder
        holding it, else to the shared collaboration folder it is in; reading
        asks ReadResource and writing UpdateWorkspaceFile on that object.
        """
        from .lineage_access_policy import file_reaches, path_resource
        from .workspace_domain import PERSONAL_ROOTS, SHARED_COLLABORATION_ROOTS

        found = path_resource(str(path), shared_roots=SHARED_COLLABORATION_ROOTS, private_roots=PERSONAL_ROOTS)
        if found is None:
            raise PermissionError("actor has no accessible Workspace for link path")
        root, resource_id, owner = found
        if not file_reaches(actor, resource_id, write=write, owner=owner):
            raise PermissionError("actor has no accessible Workspace for link path")
        can_write = write or file_reaches(actor, resource_id, write=True, owner=owner)
        return {"root": Path(root), "can_write": can_write, "source": "shared" if resource_id == root else "workspace"}
