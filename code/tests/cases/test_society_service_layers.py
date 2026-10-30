"""SocietyService modules form layers: each imports only its own row's modules and lower rows'.

``LAYERS`` lists the modules of ``management_portal.society_service`` in rows,
bottom to top: contracts, storage, the Lineage model, records and service
clients, the stored aggregate and its Program runtime, the Resident domain
with the Family facade and Program execution, the Lineage domain, the
operations built on it, and the instruction handlers and the service.  A
module may import modules of its own row and of the rows below, and the
imports form no cycle.  Every import that runs counts: at module top, inside a
function (where one is often put to dodge a cycle at load time), and by name
through ``importlib.import_module`` or an RPC ``Operation``'s
``implementation``, which ``server`` imports; only imports under
``if TYPE_CHECKING`` do not.  A new module needs a row.

``UPWARD`` lists the imports that still point up or close a cycle and what
keeps each.  Most are kept by modules a later rework owns
(``resident_protocol``, ``resident_conversation_stream``,
``resident_input_recovery``) or by the RPC operation table; an entry leaves
the list together with its import.  ``test_resident_domain_layers`` orders the
Resident domain modules inside their rows.

:module: spec.design-governance
:importance: P1
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "management_portal.society_service"
SOURCE = ROOT / "management_portal" / "society_service"

# Bottom to top.  Names are relative to the package; its own ``__init__`` is "__init__".
LAYERS = (
    # Contracts, configuration, identities, locks and value rules.
    """
    application_target_configuration changes config contracts domain_ports effect_artifacts errors
    family_definition folder_removal identity_contract invariant_locks lineage_effect_syntax
    lineage_execution_authority lineage_member_projection lineage_placement lineage_values personal_ama
    principal_hierarchy project_folders read_snapshot resident_backend resident_capability_materials
    resident_context resident_context.embedder resident_context.evidence resident_context.summary_layers
    resident_default_backend resident_hatg_credentials resident_intake_admission resident_message_contract
    resident_project_domain resident_projection resident_resource_changes resident_runtime_payload
    resident_settings_contract resident_state resident_turn_scope resident_workspace_retirement
    resident_workspace_transfer runtime runtime.contracts runtime.ports script_runs
    script_runs.clients.portal_script script_runs.runner script_runs.runtime seed_state_marker
    workspace_contracts
    """,
    # Tables, rows and stores.
    """
    definition_collections domain_work_storage edge_storage family_chat_storage family_storage
    family_task_dispatch_storage graph_storage lineage_data_columns lineage_data_definitions
    lineage_data_effect_cleanup lineage_data_executions lineage_data_mailbox lineage_data_notifications
    lineage_data_project_graphs lineage_data_projection_outbox lineage_data_residents lineage_data_runtime
    lineage_definition_contract lineage_definition_versions lineage_metadata_storage lineage_state_domains
    lineage_storage program_work_storage relation_work_storage resident_context.store
    resident_delivery_storage resident_execution_storage resident_storage resident_store
    resident_workspace_storage runtime_read_models script_runs.store service_invocation_storage
    service_invocation_store state_store trigger_path_storage wire_delivery_storage
    """,
    # Lineage definitions, Programs, Effect scripts and Graph State.
    """
    lineage_automation_identity lineage_branch_transitions lineage_effect_script lineage_effect_targets
    lineage_execution_explanation lineage_graph_authoring lineage_graph_state lineage_handoff_backend
    lineage_handoff_execution lineage_handoff_preparation lineage_instruction_context
    lineage_node_workspaces lineage_operators lineage_primitive_definitions lineage_primitives
    lineage_program lineage_program_addresses lineage_program_arguments lineage_program_bindings
    lineage_program_compilation lineage_program_components lineage_program_contracts
    lineage_program_diagnostics lineage_program_effect_rules lineage_program_event_scope
    lineage_program_fields lineage_program_graph_mutation lineage_program_identity
    lineage_program_packet_routing lineage_program_queries lineage_program_vocabulary
    lineage_program_wires lineage_recurring_schedule lineage_subgraph lineage_workspace_binding
    lineage_workspace_files result_contracts
    """,
    # Records, catalogs, the Conversation stream and the service clients.
    """
    __init__ business_fact_views execution_client input_progress lifecycle lineage_access_policy
    lineage_human_notifications lineage_recovery_policy lost_terminal_facts personal_assistant
    resident_account_policy resident_api_context resident_catalog resident_context.service
    resident_context.source resident_conversation_activity resident_conversation_events
    resident_conversation_history resident_conversation_inputs resident_conversation_messages
    resident_conversation_records resident_conversation_state resident_conversation_stream
    resident_domain_common resident_execution_provider resident_fork_context resident_input_scope
    resident_lifecycle resident_policy_facts resident_released_executions resident_resource_projection
    resident_session_preservation resident_usage_projection resource_client transport workspace_attachments
    workspace_domain
    """,
    # The stored Lineage aggregate and its Program runtime: Events, trigger paths, Effects and switches.
    """
    component_runtime effect_disposition effect_recovery_worker lineage_aggregate lineage_catalog
    lineage_definition_scope lineage_effect_script_journal lineage_event_claims lineage_event_conditions
    lineage_event_delivery lineage_event_dispatch_rules lineage_event_routing lineage_loop_runtime
    lineage_operator_runtime lineage_program_effect_results lineage_program_observations
    lineage_program_occurrences lineage_program_operator_execution lineage_program_resident_inputs
    lineage_program_runtime lineage_program_runtime_projection lineage_program_schedules
    lineage_program_wire_queue lineage_release_events resident_execution_links script_runs.service
    system_workflow_switches trigger_path_observation trigger_paths wire_delivery_budget
    """,
    # Resident records, the Family facade and messages out, which Programs deliver through.
    """
    family_activity family_facade family_familize family_owners family_tasks message_quotes outward_messages
    personal_history resident_access resident_execution_gate resident_execution_work
    resident_fork_inheritance resident_held_infos resident_input_checkpoints resident_lineage
    resident_mailbox resident_record_domain resident_unread resident_work_origin
    """,
    # Program execution and the definition rules it checks.
    """
    effect_failure_notifications family_workflows function_block_authoring lineage_definition_merge
    lineage_definition_normalization lineage_program_authoring lineage_program_backlog
    lineage_program_effect_execution lineage_program_event_dispatch lineage_program_execution
    lineage_program_terminal_reconciliation watchboard_lineage
    """,
    # The Resident domain and the Events about Resident messages.
    """
    family_chat_preferences family_recovery lineage_event_observers lineage_event_reconciliation
    lineage_failure_notifications lineage_message_events lineage_program_polling resident_actual_sent_domain
    resident_context.work_summary resident_context_evidence resident_conversation_event_relay
    resident_conversation_projection_domain resident_deferred_restart resident_delivery_work
    resident_directory_domain resident_execution_domain resident_execution_engine_domain
    resident_fork_domain resident_initialization resident_input_queue_domain resident_input_recovery
    resident_intake_domain resident_lifecycle_domain resident_lifecycle_steps_domain
    resident_management_domain resident_media_domain resident_prompt_domain resident_protocol
    resident_questions resident_queue_control_domain resident_registration_domain resident_restart_domain
    resident_restart_handoff resident_runtime_domain resident_session_backend_domain
    resident_watchboard_domain resident_work_recovery resident_workspace_domain
    """,
    # The Lineage domain.
    """
    lineage_branches lineage_domain lineage_graph_edits lineage_handoffs lineage_lifecycle lineage_moves
    lineage_program_edits lineage_program_services lineage_read_cache lineage_reads
    lineage_resident_controls lineage_resident_nodes lineage_resident_workspaces lineage_state
    lineage_view_projection
    """,
    # Family, template, Function Block, Project and principal operations.
    """
    family_progress family_quota family_reassign family_resource_projection family_supervision
    family_task_dispatches function_block_domain lineage_resident_operations principal_hierarchy_domain
    project_domain resident_domain templates templates.builtins templates.explore templates.familize
    templates.kinds templates.prompt templates.registry templates.rewrite templates.script templates.store
    templates.structure templates.values
    """,
    # Instruction handlers, hosts, projections and the service.
    """
    active_agent_projection application conversation_projection coordinator isa isa.common isa.conversation
    isa.definition isa.execution isa.families isa.function_blocks isa.organization isa.residents isa.runs
    isa.script_runs isa.shared_state isa.system_workflows isa.templates resident_context.domain
    resident_context.worker resident_domain_host resident_lineage_host runtime.backend_adapter
    script_runs.events script_runs.worker server service_invocation_domain status_projection
    """,
)
LAYER = {name: row for row, names in enumerate(LAYERS) for name in names.split()}

UPWARD = {
    ("lineage_data_executions", "resident_execution_links"):
        "admitting an Execution links the trigger path steps of its inputs in the same transaction",
    ("lineage_data_mailbox", "state_store"):
        "kept by resident_input_recovery: it reads STORED_INPUT_MESSAGE from state_store",
    ("lineage_data_residents", "resource_client"):
        "Resident rows read and commit their Workspace rows through resource_client, which also publishes from the store",
    ("resident_delivery_storage", "resident_conversation_stream"):
        "kept by resident_conversation_stream: an admitted delivery writes its input receipt there, above state_store",
    ("__init__", "server"): "get_society_service builds the in-process service in local mode",
    ("resident_conversation_events", "resident_mailbox"):
        "the events a reader receives carry the inputs' Mailbox state",
    ("resident_conversation_events", "resident_unread"):
        "recording an event projects the unread count of its Conversation in the same transaction",
    ("resident_conversation_state", "resident_execution_work"):
        "the queue and work state are read from the Mailbox and the Resident's execution work",
    ("script_runs.service", "isa.common"): "it resolves a Resident's Lineage with isa.common.lineage_of_resident",
    ("family_facade", "family_activity"):
        "kept by resident_protocol: it reads both project_snapshot and family_activity.project_states",
    ("family_facade", "family_quota"):
        "kept by resident_protocol: project_snapshot shows the Family roles' availability",
    ("family_facade", "family_recovery"):
        "kept by resident_protocol: project_messages shows the recoveries of facade requests",
    ("family_facade", "resident_execution_gate"):
        "kept by resident_protocol: project_snapshot shows the Family roles' execution gates",
    ("family_facade", "resident_protocol"):
        "kept by resident_protocol: a relayed input's state is the state of the member input holding it",
    ("outward_messages", "resident_protocol"):
        "kept by resident_protocol: a message to a Resident is admitted by deliver_resident_input",
    ("resident_input_recovery", "resident_work_recovery"):
        "kept by resident_input_recovery: it calls up from functions",
    ("lineage_domain", "principal_hierarchy_domain"):
        "the RPC operation table names lineage_domain for the principal operations it forwards",
    ("lineage_domain", "templates.explore"): "a Resident's finished turn syncs its exploration candidates",
    ("lineage_state", "templates.familize"):
        "normalizing a definition reconciles Family wiring",
    ("lineage_moves", "templates.familize"):
        "a moved member's steward hears of it",
}


def _modules() -> dict[str, tuple[tuple[str, ...], ast.Module]]:
    """Package-relative module name -> (its package, its tree)."""
    found = {}
    for path in sorted(SOURCE.rglob("*.py")):
        parts = path.relative_to(SOURCE).with_suffix("").parts
        package = parts[:-1]
        name = ".".join(package if parts[-1] == "__init__" else parts) or "__init__"
        found[name] = (package, ast.parse(path.read_text(encoding="utf-8")))
    return found


def _relative(absolute: str) -> str | None:
    if absolute == PACKAGE:
        return "__init__"
    return absolute.removeprefix(PACKAGE + ".") if absolute.startswith(PACKAGE + ".") else None


def _runtime_nodes(tree: ast.AST):
    """Every node except those under ``if TYPE_CHECKING:``, which only name types."""
    pending = [tree]
    while pending:
        node = pending.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            test = child.test if isinstance(child, ast.If) else None
            if getattr(test, "id", getattr(test, "attr", "")) == "TYPE_CHECKING":
                pending.extend(child.orelse)
            else:
                pending.append(child)


def _imports() -> dict[str, dict[str, int]]:
    """Module -> {imported package module: first line}, function bodies and named imports included."""
    modules = _modules()
    edges: dict[str, dict[str, int]] = {name: {} for name in modules}

    def add(source: str, target: str | None, line: int) -> None:
        if target in modules and target != source:
            edges[source].setdefault(target, line)

    for name, (package, tree) in modules.items():
        for node in _runtime_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(name, _relative(alias.name), node.lineno)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level - 1 > len(package):
                        continue
                    base = ".".join([*package[:len(package) - node.level + 1], *([node.module] if node.module else [])])
                elif node.module and _relative(node.module) is not None:
                    base = "" if node.module == PACKAGE else _relative(node.module)
                else:
                    continue
                for alias in node.names:
                    submodule = f"{base}.{alias.name}" if base else alias.name
                    add(name, submodule if submodule in modules else base or "__init__", node.lineno)
            elif isinstance(node, ast.Call):
                function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if function == "import_module" and node.args and isinstance(node.args[0], ast.Constant):
                    add(name, _relative(str(node.args[0].value)), node.lineno)
                if function == "Operation":
                    for keyword in node.keywords:
                        if keyword.arg == "implementation" and isinstance(keyword.value, ast.Constant):
                            add("server", _relative(str(keyword.value.value)), node.lineno)
    return edges


def test_every_module_has_one_row():
    """SocietyService 每个模块恰好属于一层"""
    names = [name for row in LAYERS for name in row.split()]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    assert not duplicated, f"Modules listed twice: {duplicated}"
    modules = set(_modules())
    assert not modules - set(LAYER), f"Give these modules a row in LAYERS: {sorted(modules - set(LAYER))}"
    assert not set(LAYER) - modules, f"Remove these names from LAYERS: {sorted(set(LAYER) - modules)}"


def test_imports_point_to_own_or_lower_rows():
    """SocietyService 模块只导入本层或更低层的模块"""
    upward = [
        f"{source}:{line} -> {target}"
        for source, targets in sorted(_imports().items())
        for target, line in sorted(targets.items())
        if LAYER[target] > LAYER[source] and (source, target) not in UPWARD
    ]
    assert not upward, f"Imports must point to the module's own row or lower: {upward}"


def test_imports_form_no_cycle():
    """SocietyService 模块之间的导入没有环"""
    edges = {source: {target for target in targets if (source, target) not in UPWARD}
             for source, targets in _imports().items()}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> list[str] | None:
        state[name] = 1
        stack.append(name)
        for target in sorted(edges[name]):
            if state.get(target) == 1:
                return stack[stack.index(target):] + [target]
            if target not in state:
                found = visit(target)
                if found:
                    return found
        stack.pop()
        state[name] = 2
        return None

    for name in sorted(edges):
        if name not in state:
            cycle = visit(name)
            assert cycle is None, f"Import cycle: {' -> '.join(cycle)}"


def test_upward_imports_are_still_there():
    """UPWARD 中的每条上行导入仍然存在"""
    edges = _imports()
    gone = sorted(f"{source} -> {target}" for source, target in UPWARD if target not in edges.get(source, {}))
    assert not gone, f"Remove these entries from UPWARD: {gone}"
