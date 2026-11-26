from __future__ import annotations

import os

from management_common.rpc_auth import load_rpc_signing_key, sign_rpc_auth


# Bringing the active scheme's instance up to date needs every object ResourceService
# knows; SocietyService asks for it while turning the permission system on.
PERMISSION_RPC_METHODS = frozenset({"permission.prepare"})
DOMAIN_PROJECTION_METHODS = frozenset({"resource.collaboration.sync", "resource.collaboration.sync-many"})
RPC_ACTOR_METHODS = frozenset(
    {
        "resource.query",
        "resource.suggest",
        "resource.get",
        "resource.resolve",
        "resource.reference",
        "resource.read_content",
        "resource.open_content",
        "resource.read_content_range",
        "resource.register",
        "resource.update",
        "resource.execution_content_changes",
        "resource.unregister",
        "resource.create_knowledge_file",
        "resource.update_knowledge_file_name",
        "resource.move_knowledge_items",
        "resource.check_device_ssh",
        "resource.update_device_ssh_credential",
        "resource.delete_device_ssh_credential",
        "resource.run_device_command",
        "resource.update_workbench_page",
        "resource.update_workbench_reverse_plan",
        "resource.collaboration.workspace_id_for_path",
        "instruction.execute",
    }
) | PERMISSION_RPC_METHODS | DOMAIN_PROJECTION_METHODS


def rpc_auth_required() -> bool:
    return str(
        os.getenv("PORTAL_RESOURCE_SERVICE_PROCESS", "") or ""
    ).strip() == "1"
