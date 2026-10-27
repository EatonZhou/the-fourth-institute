"""Classification a test gets when neither it nor its file declares one (see catalog_source)."""
from __future__ import annotations


def module_for(path: str) -> str:
    value = path.casefold()
    mappings = (
        ("test_agent_data_payload_migration", "platform.data"),
        ("scheduled_triggered", "execution.scheduled-triggered"),
        ("test_codex_handover_keeps_turn", "execution.service"),
        ("test_explicit_worker_restart", "execution.service"),
        ("test_portal_instance_storage", "operations.deployment"),
        ("test_authorization", "security.identity-access"),
        ("workbench_projections", "workbench.panels"),
        ("workbench_projection_live", "workbench.panels"),
        ("workbench_framework_live", "workbench.panels"),
        ("workbench_resources_live", "workbench.panels"),
        ("workbench_native_live", "workbench.panels"),
        ("wall", "workflow.wall"),
        ("test_test_framework", "operations.test-framework"),
        ("test_test_api_auth", "operations.test-framework"),
        ("test_tau_runtime", "operations.test-framework"),
        ("test_application_target_configuration", "lineage.service"),
        ("test_tau_admission", "operations.test-framework"),
        ("test_tau_observation_retention", "operations.test-framework"),
        ("tau_browser_observability", "operations.test-framework"),
        ("test_lab", "operations.test-workbench"),
        ("release", "operations.deployment"),
        ("runtime", "operations.runtime"),
        ("lineage_program", "lineage.hatg"),
        ("hatg", "lineage.hatg"),
        ("lineage", "lineage.service"),
        ("resident_conversation", "resident.conversation"),
        ("resident_context", "resident.workspace-memory"),
        ("resident_fork", "resident.fork-handoff"),
        ("resident", "resident.identity"),
        ("knowledge", "workflow.knowledge"),
        ("resource_id", "resource.identity"),
        ("resource", "resource.service"),
        ("search", "resource.index-search"),
        ("paper", "workflow.paper"),
        ("article", "workflow.article"),
        ("project", "workflow.project"),
        ("repo", "workflow.repository"),
        ("execution", "execution.service"),
        ("harness", "execution.harness"),
        ("agent", "execution.scheduled-triggered"),
        ("portal_access", "security.identity-access"),
        ("password", "security.identity-access"),
        ("passkey", "security.device-credentials"),
        ("two_factor", "security.device-credentials"),
        ("security", "security.network-boundary"),
        ("device", "security.device-credentials"),
        ("portal_workbench", "workbench.panels"),
        ("workspace_surface", "workbench.preview"),
        ("preview", "workbench.preview"),
        ("portal_chat", "workbench.chat"),
        ("portal_nav", "workbench.shell"),
        ("design_system", "workbench.components"),
        ("settings", "workbench.shell"),
        ("notification", "workbench.notifications"),
        ("skill", "execution.providers"),
        ("collaboration", "resident.conversation"),
        ("data", "platform.data"),
    )
    for token, module in mappings:
        if token in value:
            return module
    return "platform.core"


def importance_for(path: str, title: str) -> str:
    value = f"{path} {title}".casefold()
    if "test_lab" in value or "test_local_development" in value:
        return "P0"
    if any(token in value for token in ("access_control", "identity_bootstrap", "security", "release", "runtime_status")):
        return "P1"
    if any(token in value for token in ("benchmark", "experiment", "soak", "visual")):
        return "P3"
    return "P2"


def aspects_for(path: str) -> list[str]:
    value = path.casefold()
    result = ["functional"]
    if "performance" in value or "benchmark" in value:
        result.append("performance")
    if "architecture" in value or "_boundaries" in value:
        result.append("architecture-conformance")
    return result
