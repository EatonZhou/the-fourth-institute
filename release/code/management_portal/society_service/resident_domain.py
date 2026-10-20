"""Stable Resident domain exports; behavior lives in focused modules."""
from __future__ import annotations

from .resident_actual_sent_domain import (
    read_actual_sent_prompt,
)

from .resident_conversation_projection_domain import (
    is_generated_derived_resident_title,
)

from .resident_directory_domain import (
    get_lineage_node_metadata,
    get_resident_context_pressures,
    get_resident_resource_projection,
    get_resident_session_projection,
    get_resident_user_message_count,
    get_resident_user_message_counts,
    list_resident_conversation_activity,
    list_resident_forward_targets,
)

from .resident_domain_common import (
    ResidentExecutionFailed,
    ResidentRuntimeTarget,
    ResidentTurnCancelled,
)

from .resident_execution_domain import (
    change_resident_runtime_settings,
    deliver_resident_message_content,
    resume_pending_resident_executions,
    resume_resident_execution,
)



from .resident_fork_domain import (
    create_resident_compacted_context_fork,
    create_resident_context_fork,
    create_resident_fork_from_message,
    create_resident_full_context_fork,
)

from .resident_intake_domain import (
    adopt_generated_resident_title,
    get_account_resident_summary,
    move_resident_identities,
    rename_resident_identity,
    rename_resident_title,
    request_resident_execution_cancellation,
    reset_resident_context,
    transition_resident_state,
    update_resident,
)

from .resident_lifecycle_domain import (
    observe_committed_resident_transfer,
    ready_resident_lifecycle_operations,
    resident_aggregate_lineage_id,
    resident_lifecycle_health,
    resume_resident_lifecycle_operation,
)

from .resident_record_domain import (
    sync_lineage_member_credentials,
)

from .resident_registration_domain import (
    confirm_resident_registration,
    register_resident,
)

from .resident_restart_domain import (
    cancel_resident_session_restart,
    get_resident_restart_options,
    get_resident_runtime_route,
    get_resident_session_restart,
    restart_resident_session,
)

from .resident_runtime_domain import (
    account_selection_payload,
    resident_title_map,
)

from .resident_watchboard_domain import (
    get_resident_watchboard,
)

from .resident_workspace_domain import (
    add_resident_reference_workspace,
    confirm_resident_deletion_projection,
    delete_resident_identities,
    delete_resident_lineage,
    get_resident_workspace_roots,
    persist_lineage_project_member_updates,
    prepare_lineage_project_member_updates,
    remove_resident_reference_workspace,
    remove_resident_reference_workspace,
    resolve_resident_identity,
)
