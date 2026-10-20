from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SOCIETY_SERVICE_SCHEMA = "tfi.society-service.v1"


@dataclass(frozen=True)
class Operation:
    """Domain-owned RPC semantics, independent of transport and argument order.

    owner scope delegates transactions (including multi-target ones) to the handler.
    Other scopes select an aggregate from an explicitly named bound argument.
    Notifications always originate in committed domain writes, never RPC inference.
    ``in_process`` reads run in the service process instead of the read pool,
    for reads that use process-wide resources such as the embedding model.
    """

    mode: str
    authority: str
    scope: str = "owner"
    target: str = ""
    result: str = ""
    implementation: str = field(kw_only=True)
    in_process: bool = field(default=False, kw_only=True)

    def target_id(self, arguments: Mapping[str, Any]) -> str:
        if not self.target:
            return ""
        value: Any = arguments
        for part in self.target.split("."):
            if not isinstance(value, Mapping) or part not in value:
                raise ValueError(f"missing RPC scope argument: {self.target}")
            value = value[part]
        if not isinstance(value, str):
            raise ValueError(f"RPC scope argument must be a string: {self.target}")
        return value.strip()


def _catalog(*groups: tuple[Operation, str]) -> dict[str, Operation]:
    result: dict[str, Operation] = {}
    for operation, names in groups:
        for name in names.split():
            if name in result:
                raise RuntimeError(f"duplicate RPC operation: {name}")
            result[name] = operation
    return result


# The sole operation inventory: no name heuristic or positional/payload scan.
DOMAIN_OPERATIONS = {
    "resident": _catalog(
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_policy_facts', in_process=True), """
            is_resident_input_open
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_directory_domain'), """
            get_resident_session_projection get_resident_resource_projection
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_workspace_domain'), """
            resolve_resident_identity
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_restart_domain'), """
            get_resident_restart_options get_resident_runtime_route
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_watchboard_domain'), """
            get_resident_watchboard
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_protocol'), """
            get_resident_conversation_snapshot get_resident_state get_resident_events get_resident_input_state
            get_resident_message get_resident_artifact
        """),
        (Operation('read', 'resident', 'resident', 'resident_id', 'resident', implementation='management_portal.society_service.resident_protocol'), """
            get_resident
        """),
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_intake_domain'), """
            get_account_resident_summary
        """),
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_directory_domain'), """
            list_resident_forward_targets list_resident_conversation_activity
        """),
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_protocol'), """
            get_resident_states
        """),
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_access'), """
            filter_visible_resident_ids
        """),
        (Operation('write', 'resident', 'resident', 'resident_id', implementation='management_portal.society_service.resident_protocol'), """
            cancel_resident_service_input
        """),
        # Resident discovery (resident_context): searches use the embedding
        # model held by the service process.
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_context.domain', in_process=True), """
            search_residents resident_routing_candidates
        """),
        (Operation('read', 'resident', implementation='management_portal.society_service.resident_context.domain'), """
            search_resident_directory get_resident_contexts
        """),
        (Operation('write', 'resident', implementation='management_portal.society_service.resident_context.domain'), """
            request_resident_summary_rebuild submit_routing_eval_correction set_resident_embeddings_enabled
        """),
    ),
    "workspace": _catalog(
        (Operation('read', 'workspace', implementation='management_portal.society_service.workspace_domain'), """
            get_workspace_link_file resolve_workspace_link_file_path resolve_workspace_pasted_image
        """),
        (Operation('write', 'workspace', implementation='management_portal.society_service.workspace_domain'), """
            save_workspace_attachment save_workspace_pasted_image
        """),
    ),
    "project": _catalog(
        (Operation('read', 'project', implementation='management_portal.society_service.project_domain'), """
            validate_project_deletion
        """),
        (Operation('write', 'project', implementation='management_portal.society_service.project_domain'), """
            delete_project
        """),
    ),
    "lineage": _catalog(
        (Operation('read', 'lineage', implementation='management_portal.society_service.lineage_domain'), """
            get_family_resource_projection
        """),
        (Operation('read', 'lineage', 'lineage', 'lineage_id', implementation='management_portal.society_service.lineage_domain'), """
            authorize_lineage_resident_principal get_lineage_resource_projection get_lineage_program_resource_projection
            get_lineage_node_metadata
        """),
        (Operation('read', 'lineage', 'lineage', 'lineage_id', 'lineage_graph', implementation='management_portal.society_service.lineage_domain'), """
            get_lineage
        """),
        (Operation('read', 'lineage', 'lineage', 'lineage_id', 'message_counts', implementation='management_portal.society_service.lineage_domain'), """
            get_node_message_counts
        """),
        (Operation('read', 'lineage', implementation='management_portal.society_service.lineage_domain'), """
            get_lineage_monitor_definitions list_lineages list_principal_hierarchy list_lineage_human_notifications list_resident_discovery_catalog
            list_removed_discovery_residents
        """),
        (Operation('write', 'resident', 'lineage', 'lineage_id', implementation='management_portal.society_service.lineage_domain'), """
            create_lineage_resident
        """),
        # Creates (and, with a deleted account, deletes) the User's personal Lineage; its domain serializes per User.
        (Operation('write', 'lineage', implementation='management_portal.society_service.lineage_domain'), """
            ensure_personal_ama delete_personal_ama ensure_personal_assistant provision_project_principal retry_lineage_projections
        """),
    ),
    "projection": _catalog(
        (Operation('read', 'projection', implementation='management_portal.society_service.status_projection'), """
            collect_active_agents collect_resident_usage read_context
        """),
    ),
    "service_invocation": _catalog(
        (Operation('read', 'service_invocation', implementation='management_portal.society_service.service_invocation_domain'), """
            read_plan
        """),
        # Observation reconciles durable state/cancellation, even for get_*.
        (Operation('write', 'service_invocation', implementation='management_portal.society_service.service_invocation_domain'), """
            prepare_batches submit_batch get_batch get_invocation cancel_invocation fail_invocation
        """),
    ),
}

DOMAIN_METHODS = {domain: frozenset(operations) for domain, operations in DOMAIN_OPERATIONS.items()}


def operation_contract(domain: str, method: str) -> Operation:
    try:
        return DOMAIN_OPERATIONS[domain][method]
    except KeyError:
        raise ValueError(f"unsupported SocietyService call: {domain}.{method}") from None
