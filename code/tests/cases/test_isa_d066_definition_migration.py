"""society: static ISA contractions preserve calls; ambiguous workflows stop."""

from copy import deepcopy

import pytest

from management_portal.society_service import isa_contract_migration as contracts
from management_portal.society_service import instruction_name_migration as migration
from management_portal.society_service import lineage_effect_script as script


def structured(name, arguments):
    rewritten, values = contracts.rewrite(name, {k: contracts.Value(v) for k, v in arguments.items()})
    return rewritten, {k: v.plain() for k, v in values.items()}


def original_summary_graph():
    from management_portal.society_service import family_definition, family_tasks

    # Reconstruct the already fingerprinted historical fixture without copying
    # a second production template or adding another migration mapping.
    old = family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT.replace(
        "GetFacadeSummaryInputs", "GetFamilySummaryInputs",
    ).replace(
        'DeliverMessage resident_id="${arguments.steward}" business_ref="duty/family-work-summary" message:=${inputs.notification}',
        'DispatchFamilyTask resident_id="${arguments.facade}" trigger="summary" message_id="${inputs.notification.message_id}"',
    )
    return {
        "lineage_id": "lineage_fixture",
        "program": {
            "components": [{"id": "effect_fixture", "kind": "effect", "config": {
                "script": old, "script_draft": {"source": old, "approved_sha": "keep"},
            }}],
            "wires": [{"id": "wire_fixture", "kind": "route", "source": "timer_fixture",
                       "target": "effect_fixture", "config": {"invoke_profiles": {"effect": {"arguments": {
                           "facade": {"value": "resident_facade"}, "steward": {"value": "resident_steward"},
                           "family_tasks": {"value": family_tasks.defaults("request_summary")},
                       }}}}}],
        },
        "function_blocks": [{"id": "fbi_fixture", "family": {
            "facade": "resident_facade", "router": "resident_router", "steward": "resident_steward", "members": [],
        }, "elements": [{"id": "effect_fixture", "kind": "component", "key": "summary_rollup"},
                         {"id": "wire_fixture", "kind": "wire", "key": "request_summary"}]}],
    }


FAMILY_STRATEGY_FIXTURES = {
    "recovery_check": 'MaintainFamilyRecovery lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}" event="${event}"',
    "role_availability": 'MaintainFamilyRoleAvailability lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}"',
    "notify_member_queue_task": 'DispatchFamilyTask resident_id="${arguments.facade}" trigger="${arguments.trigger}" event:=${event}',
    "notify_periodic_review_task": 'MaintainFamilyReview lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}"',
    "review_activity": 'MaintainFamilyReview lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}" event:=${event}',
    "review_personal_activity": '''activity=$(GetSharedState lineage_id="${arguments.lineage_id}" key="${arguments.activity_key}")
if [ -z "${activity.value}" ]; then
  output status="inactive"
  exit
fi
review=$(GetSharedState lineage_id="${arguments.lineage_id}" key="${arguments.review_key}")
if [ "${activity.value}" = "${review.value}" ]; then
  output status="unchanged"
  exit
fi
state=$(ListResidentStates resident_ids:=[${arguments.steward}] include_viewer_state:=false)
if [ "${state.states.0.state.work_state}" != "idle" ]; then
  output status="busy"
  exit
fi
if [ "${state.states.0.state.queued_count}" -gt 0 ]; then
  output status="busy"
  exit
fi
sent=$(DispatchFamilyTask resident_id="${arguments.facade}" trigger="review")
UpdateSharedState lineage_id="${arguments.lineage_id}" key="${arguments.review_key}" value="${activity.value}"
output status="requested" message_id="${sent.message_id}"''',
}


def family_strategy_graph(role):
    graph = original_summary_graph()
    block = graph["function_blocks"][0]
    block["elements"][0]["key"] = role
    component = graph["program"]["components"][0]
    component["config"]["script"] = FAMILY_STRATEGY_FIXTURES[role]
    arguments = {"lineage_id": {"value": graph["lineage_id"]}, "family_id": {"value": block["id"]}}
    if role == "notify_member_queue_task":
        arguments["trigger"] = {"value": "queue"}
    if role == "review_personal_activity":
        block["family"]["personal_ama_id"] = "resident_personal"
        arguments.update(activity_key={"value": "persisted_activity_key"}, review_key={"value": "persisted_review_key"})
    graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"] = arguments
    return graph


@pytest.mark.parametrize("role", FAMILY_STRATEGY_FIXTURES)
def test_remaining_family_strategies_migrate_with_owned_bindings_and_no_history_changes(role):
    from management_portal.society_service import family_definition, family_recovery_effects, family_review_effects

    original = family_strategy_graph(role)
    before = deepcopy(original)
    normalized = migration.normalize_definition(original)
    script_source = normalized["program"]["components"][0]["config"]["script"]
    assert script_source == family_definition.migrate_original_script(FAMILY_STRATEGY_FIXTURES[role], role=role)
    assert script_source != FAMILY_STRATEGY_FIXTURES[role]
    args = normalized["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    family = before["function_blocks"][0]["family"]
    if role == "recovery_check":
        required = family_recovery_effects.bindings(before["lineage_id"], "fbi_fixture", family)
        assert args["family_tasks"]["value"]["recovery"]["prompt"]
    elif role in {"notify_periodic_review_task", "review_activity"}:
        required = family_review_effects.bindings(before["lineage_id"], "fbi_fixture", family)
    elif role == "role_availability":
        required = {"roles": {"value": [family["router"], family["steward"]]}}
    else:
        required = {key: {"value": family[key]} for key in ("facade", "steward")}
    assert all(args[key] == value for key, value in required.items())
    assert all(args[key] == value for key, value in before["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"].items())
    assert normalized["program"]["components"][0]["config"]["script_draft"] == before["program"]["components"][0]["config"]["script_draft"]
    assert migration.normalize_definition(normalized) == normalized
    assert original == before


def test_recovery_preserves_each_incoming_task_and_stages_all_bindings_before_changes():
    from management_portal.society_service import family_tasks

    graph = family_strategy_graph("recovery_check")
    wires = graph["program"]["wires"]
    wires.append(deepcopy(wires[0]))
    wires[1]["id"] = "wire_other"
    first = wires[0]["config"]["invoke_profiles"]["effect"]["arguments"]
    second = wires[1]["config"]["invoke_profiles"]["effect"]["arguments"]
    first["family_tasks"] = {"value": family_tasks.defaults("recheck_recovery")}
    second["family_tasks"] = {"value": {"unrelated": {"prompt": "Keep this unrelated custom task."}}}
    before = deepcopy(graph)
    normalized = migration.normalize_definition(graph)
    assert normalized["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"] == first["family_tasks"]
    assert normalized["program"]["wires"][1]["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["unrelated"] == second["family_tasks"]["value"]["unrelated"]
    assert graph == before
    second["steward"] = {"value": "resident_wrong"}
    before = deepcopy(graph)
    diagnostics = []
    migration._normalize_definition(graph, diagnostics=diagnostics)
    assert diagnostics[0]["path"][-1] == "steward"
    assert graph == before


def test_recovery_fills_only_missing_task_fields_and_rejects_missing_effect_profiles():
    from management_portal.society_service import family_tasks

    graph = family_strategy_graph("recovery_check")
    profile = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]
    default = family_tasks.defaults("recheck_recovery")["recovery"]
    profile["arguments"]["family_tasks"] = {"value": {"recovery": {"prompt": default["prompt"]}}}
    normalized = migration.normalize_definition(graph)
    task = normalized["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["recovery"]
    assert task == default
    graph["program"]["wires"][0]["config"]["invoke_profiles"] = {"resident": profile}
    with pytest.raises(migration.DefinitionMigrationRequired, match="effect_profile_missing"):
        migration.normalize_definition(graph)


@pytest.mark.parametrize("role,trigger", [("notify_member_queue_task", "queue"), ("notify_periodic_review_task", "review")])
def test_shared_original_dispatch_requires_verified_role_and_static_trigger(role, trigger):
    from management_portal.society_service import family_definition, family_tasks

    graph = family_strategy_graph(role)
    source = FAMILY_STRATEGY_FIXTURES["notify_member_queue_task"]
    graph["program"]["components"][0]["config"]["script"] = source
    args = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    args["trigger"] = {"value": trigger}
    watch = {"id": "wire_watch", "kind": "watch", "source": "event_source_watch", "source_port": "watch", "target": "effect_fixture"}
    graph["program"]["wires"].append(watch)
    assert family_definition.is_original_family_script(source)
    assert family_definition.migrate_original_script(source) == source
    before = deepcopy(graph)
    normalized = migration.normalize_definition(graph)
    assert normalized["program"]["components"][0]["config"]["script"] == family_definition.migrate_original_script(source, role=role)
    migrated = normalized["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    assert migrated["trigger"] == args["trigger"]
    assert migrated["family_tasks"]["value"][trigger] == family_tasks.defaults(family_tasks.TASKS[trigger][0])[trigger]
    if trigger == "review":
        assert migrated["review_key"] == {"value": "family.fbi_fixture.review"}
    assert graph == before and migration.normalize_definition(normalized) == normalized
    assert normalized["program"]["wires"][1] == watch
    with pytest.raises(migration.DefinitionMigrationRequired, match="requires_family_graph_context"):
        migration.normalize_script(source)
    for invalid in (None, {"expression": "event.trigger"}, {"value": "queue" if trigger == "review" else "review"}):
        args = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
        if invalid is None:
            args.pop("trigger", None)
        else:
            args["trigger"] = invalid
        diagnostics = []
        saved = deepcopy(graph)
        migration._normalize_definition(graph, diagnostics=diagnostics)
        assert graph == saved
        assert diagnostics[0]["component_id"] == "effect_fixture"
        assert diagnostics[0]["reason"] == f"original_workflow_{trigger}_trigger_mismatch"
        assert diagnostics[0]["path"][-1] == "trigger"
    args = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    args["trigger"] = {"value": trigger}
    args["family_tasks"] = {"value": {trigger: {"guide": "Keep my private instructions"}}}
    with pytest.raises(migration.DefinitionMigrationRequired, match="custom_task_binding"):
        migration.normalize_definition(graph)


@pytest.mark.parametrize("role,task,old_guide", [
    ("recovery_check", "recovery", "_OLD_RECOVERY_GUIDE"),
    ("notify_member_queue_task", "queue", "_OLD_QUEUE_GUIDE"),
])
def test_verified_stock_bindings_use_current_owner_defaults_in_one_pass(role, task, old_guide):
    from management_portal.society_service import family_tasks

    graph = family_strategy_graph(role)
    args = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    args["family_tasks"] = {"value": {
        task: {"guide": getattr(family_tasks, old_guide), "title": "keep original title"},
        "quota": {"guide": family_tasks._PRE_SUBSCRIPTION_QUOTA_GUIDE},
    }}
    original = deepcopy(graph)
    result = migration.normalize_definition(graph)
    tasks = result["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]
    assert tasks[task]["guide"] == family_tasks.defaults(family_tasks.TASKS[task][0])[task]["guide"]
    assert tasks[task]["title"] == "keep original title"
    assert migration.normalize_definition(result) == result
    assert graph == original


@pytest.mark.parametrize("failed_stage", [None, "lineage_b", "runtime", "templates", "retire"])
def test_legacy_dispatch_retirement_runs_after_all_definitions_and_fails_closed(monkeypatch, failed_stage):
    from types import SimpleNamespace
    from management_portal.society_service import family_strategy_state, lineage_aggregate, resident_runtime_domain
    from management_portal.society_service.templates import store

    order = []
    def stage(name):
        order.append(name)
        if name == failed_stage:
            raise ValueError("owner cutover blocked")

    monkeypatch.setattr(migration, "check", lambda: {"blocked_count": 0})
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: SimpleNamespace(
        listing_records=lambda: [{"lineage_id": "lineage_a"}, {"lineage_id": "lineage_b"}]))
    monkeypatch.setattr(migration, "_migrate_lineage", stage)
    monkeypatch.setattr(resident_runtime_domain, "migrate_instruction_names", lambda: stage("runtime"))
    monkeypatch.setattr(store, "migrate_instruction_names", lambda: stage("templates"))
    monkeypatch.setattr(family_strategy_state, "retire_legacy_dispatches", lambda: stage("retire"))
    if failed_stage:
        with pytest.raises(ValueError, match="owner cutover blocked"):
            migration.migrate()
    else:
        migration.migrate()
    expected = ["lineage_a", "lineage_b", "runtime", "templates", "retire"]
    assert order == (expected[:expected.index(failed_stage) + 1] if failed_stage else expected)


@pytest.mark.parametrize("role,field,value,reason", [
    ("recovery_check", "recovery_key", {"value": "private_state"}, "family_argument_mismatch"),
    ("role_availability", "roles", {"value": ["resident_wrong"]}, "family_argument_mismatch"),
    ("notify_member_queue_task", "trigger", {"value": "review"}, "queue_trigger_mismatch"),
    ("notify_periodic_review_task", "review_key", {"expression": "dynamic"}, "family_argument_mismatch"),
    ("review_personal_activity", "activity_key", {"expression": "dynamic"}, "personal_state_binding_missing"),
])
def test_new_family_bindings_reject_ambiguous_or_custom_values(role, field, value, reason):
    graph = family_strategy_graph(role)
    graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"][field] = value
    before = deepcopy(graph)
    with pytest.raises(migration.DefinitionMigrationRequired, match=reason):
        migration.normalize_definition(graph)
    assert graph == before


def test_family_state_cutover_shares_graph_transaction_and_never_replays_old_facts(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from management_common.portal_database import portal_database
    from management_common.json_columns import json_text, json_value
    from management_portal.society_service import family_strategy_state, lineage_aggregate, lineage_state

    graph = family_strategy_graph("recovery_check")
    lineage_id = graph["lineage_id"]
    progress_key = "family.fbi_fixture.progress"
    progress = {"count": 20, "reported_through": 20, "batches": {"20": {"status": "pending", "request_id": "old_request"}}}
    legacy = {"progress": progress}
    original = deepcopy(graph)
    db = portal_database(schema="lineage")
    with db.transaction() as connection:
        # Real PostgreSQL and the real state owner. Only the outer graph store
        # is substituted so a CAS failure can be injected after the state write.
        connection.execute("CREATE TEMP TABLE families (family_id text, lineage_id text, deleted_at timestamptz) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_residents (family_id text, resident_id text, role text) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_relays (lineage_id text, resident_id text, relay_json jsonb) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_quota_reviews (lineage_id text, resident_id text, review_json jsonb) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE lineage_shared_state (lineage_id text PRIMARY KEY, cells_json jsonb) ON COMMIT DROP")
        connection.execute("INSERT INTO families VALUES ('fbi_fixture', %s, NULL)", (lineage_id,))
        connection.execute("INSERT INTO family_residents VALUES ('fbi_fixture', 'resident_facade', 'facade'), ('fbi_fixture', 'resident_steward', 'steward')")
        connection.execute("INSERT INTO family_relays VALUES (%s, 'resident_facade', %s)", (lineage_id, json_text(legacy)))

        def write(callback):
            connection.execute("SAVEPOINT d066_graph_cutover")
            try:
                result = callback(connection)
            except Exception:
                connection.execute("ROLLBACK TO SAVEPOINT d066_graph_cutover")
                raise
            finally:
                connection.execute("RELEASE SAVEPOINT d066_graph_cutover")
            return result

        catalog = SimpleNamespace(state_store=SimpleNamespace(database=SimpleNamespace(write=write),
                                                              lock_lineage_definition=lambda _: True))
        monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: catalog)
        monkeypatch.setattr(lineage_aggregate, "lineage_scope", lambda *_, **__: nullcontext())
        monkeypatch.setattr(lineage_aggregate, "read_lineage_state", lambda _: deepcopy(graph))
        monkeypatch.setattr(lineage_state, "invalidate_lineage_read_cache", lambda _: None)
        state_calls = []
        owner_migrate = family_strategy_state.migrate_family_state

        def state_migrate(actual, target):
            assert actual is connection and target == lineage_id
            state_calls.append(target)
            return owner_migrate(actual, target)

        monkeypatch.setattr(family_strategy_state, "migrate_family_state", state_migrate)
        monkeypatch.setattr(migration, "_blockers", lambda *_: [{"kind": "pending_event_deliveries"}])
        with pytest.raises(RuntimeError, match="pending_event_deliveries"):
            migration._migrate_lineage(lineage_id)
        assert state_calls == []

        monkeypatch.setattr(migration, "_blockers", lambda *_: [])
        def failed_cas(*_, **__):
            saved = json_value(connection.execute("SELECT cells_json FROM lineage_shared_state").fetchone()["cells_json"])
            assert saved["cells"][progress_key] == progress
            raise ValueError("simulated graph CAS conflict")

        monkeypatch.setattr(lineage_state, "_persist_lineage_state_transaction", failed_cas)
        with pytest.raises(ValueError, match="graph CAS conflict"):
            migration._migrate_lineage(lineage_id)
        assert connection.execute("SELECT cells_json FROM lineage_shared_state").fetchone() is None
        assert graph == original

        def committed(before, after, *_, **__):
            assert before == original
            graph.clear()
            graph.update(deepcopy(after))

        monkeypatch.setattr(lineage_state, "_persist_lineage_state_transaction", committed)
        migration._migrate_lineage(lineage_id)
        assert len(state_calls) == 2
        current = json_value(connection.execute("SELECT cells_json FROM lineage_shared_state").fetchone()["cells_json"])
        current["cells"][progress_key]["count"] = 21
        connection.execute("UPDATE lineage_shared_state SET cells_json=%s", (json_text(current),))
        migration._migrate_lineage(lineage_id)
        assert len(state_calls) == 2
        assert json_value(connection.execute("SELECT cells_json FROM lineage_shared_state").fetchone()["cells_json"]) == current
        assert json_value(connection.execute("SELECT relay_json FROM family_relays").fetchone()["relay_json"]) == legacy


def test_original_family_upgrade_requires_the_graph_and_preserves_draft_and_pending_barrier(monkeypatch):
    from management_portal.society_service import family_definition, domain_work_storage, trigger_path_storage

    before = original_summary_graph()
    graph = deepcopy(before)
    effects, wires = migration._normalize_definition(graph)
    assert effects == {"effect_fixture"} and wires == {"wire_fixture": "effect_fixture"}
    config = graph["program"]["components"][0]["config"]
    assert config["script"] == family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT
    assert config["script_draft"] == before["program"]["components"][0]["config"]["script_draft"]
    assert migration.normalize_definition(before) == graph
    assert migration.normalize_definition(graph) == graph
    assert migration.normalize_template({"body": before}) == {"body": graph}
    for run in (lambda: migration.normalize_component_config(before["program"]["components"][0]["config"]),
                lambda: migration.normalize_template({"body": {"script": before["program"]["components"][0]["config"]["script"]}})):
        with pytest.raises(migration.DefinitionMigrationRequired, match="requires_family_graph_context"):
            run()

    active = {"pending-family": {"effect_id": "effect_fixture", "source_wire_id": "wire_fixture", "status": "running"}}
    monkeypatch.setattr(domain_work_storage, "list_work", lambda *_: active)
    monkeypatch.setattr(trigger_path_storage, "effect_step_status", lambda *_: {"status": "running"})
    from management_portal.society_service import program_work_storage
    pending = {"event_wires": {"wire_fixture": {"pending_events": [{"claim_token": "pending-original"}]}}}
    monkeypatch.setattr(program_work_storage, "read", lambda *_: pending)
    saved = deepcopy((active, pending))
    blockers = migration._blockers(None, before["lineage_id"], effects, wires)
    assert {item["kind"] for item in blockers} == {"effect_activation", "pending_event_deliveries"}
    assert (active, pending) == saved
    assert original_summary_graph() == before


@pytest.mark.parametrize("change,reason", [
    ("unowned", "family_owner_missing_or_ambiguous"),
    ("ambiguous", "family_owner_missing_or_ambiguous"),
    ("role", "family_role_mismatch"),
    ("guide", "custom_task_binding"),
    ("prompt", "custom_task_binding"),
    ("binding", "family_argument_mismatch"),
    ("dynamic", "dynamic_task_binding"),
    ("component_prompt", "custom_text_binding"),
    ("duty", "custom_task_binding"),
])
def test_stock_family_source_does_not_override_custom_graph_bindings(change, reason):
    graph = original_summary_graph()
    block = graph["function_blocks"][0]
    arguments = graph["program"]["wires"][0]["config"]["invoke_profiles"]["effect"]["arguments"]
    if change == "unowned":
        block["elements"] = []
    elif change == "ambiguous":
        other = deepcopy(block)
        other["id"] = "fbi_other"
        graph["function_blocks"].append(other)
    elif change == "role":
        block["elements"][0]["key"] = "workflow:custom"
    elif change in {"guide", "prompt"}:
        arguments["family_tasks"]["value"]["summary"][change] = "private edited instructions"
    elif change == "binding":
        arguments["facade"] = {"value": "resident_other"}
    elif change == "dynamic":
        arguments["family_tasks"] = {"expression": "owner_supplied"}
    elif change == "component_prompt":
        graph["program"]["components"][0]["config"]["prompt"] = "private edited instructions"
    else:
        block["family"]["role_bindings"] = {"steward": "role_fixture"}
        graph["function_blocks"].extend([
            {"id": "role_fixture", "role": {"key": "steward", "family_id": "fbi_fixture", "duties": {"summary": "duty_fixture"}}, "elements": []},
            {"id": "duty_fixture", "title": "summary", "elements": [], "duty": {
                "parameters": {"family_task": "summary", "prompt": "private edited instructions"},
                "memory": {"prompt": "private edited instructions"},
            }},
        ])
    before = deepcopy(graph)
    with pytest.raises(migration.DefinitionMigrationRequired, match=reason) as raised:
        migration.normalize_definition(graph)
    assert graph == before
    assert "private edited instructions" not in str(raised.value)
    diagnostics = []
    migration._normalize_definition(deepcopy(graph), diagnostics=diagnostics)
    assert diagnostics[0]["component_id"] == "effect_fixture"
    assert diagnostics[0]["path"]


def test_complete_graph_boundary_retains_caller_versions_and_unknown_metadata():
    graph = original_summary_graph()
    graph.update(revision=17, layout_revision=4, created_by="resident_original", opaque={"old": "CreateWire"})
    graph["program"]["components"][0].update(created_by_principal_id="resident_original", version=9)
    before = deepcopy(graph)
    normalized = migration.normalize_definition(graph)
    assert graph == before and normalized is not graph
    assert {key: normalized[key] for key in ("revision", "layout_revision", "created_by", "opaque")} == {
        key: graph[key] for key in ("revision", "layout_revision", "created_by", "opaque")}
    assert normalized["program"]["components"][0]["created_by_principal_id"] == "resident_original"
    assert normalized["program"]["components"][0]["version"] == 9
    normalized["opaque"]["old"] = "changed copy"
    assert graph == before


@pytest.mark.parametrize("change,path,reason", [
    ("lineage", ["lineage_id"], "incomplete_definition_graph"),
    ("blocks", ["function_blocks"], "incomplete_definition_graph"),
    ("duplicate", ["program", "components", 1, "id"], "duplicate_definition_identity"),
    ("member", ["function_blocks", 0, "elements", 0, "id"], "missing_definition_membership_target"),
    ("role", ["function_blocks", 0, "family", "role_bindings", "steward"], "missing_definition_role_binding"),
])
def test_incomplete_snapshot_graph_is_diagnosed_before_any_normalization(change, path, reason):
    graph = original_summary_graph()
    if change == "lineage":
        graph.pop("lineage_id")
    elif change == "blocks":
        graph.pop("function_blocks")
    elif change == "duplicate":
        graph["program"]["components"].append(deepcopy(graph["program"]["components"][0]))
    elif change == "member":
        graph["function_blocks"][0]["elements"][0]["id"] = "missing_original_component"
    else:
        graph["function_blocks"][0]["family"]["role_bindings"] = {"steward": "missing_original_role"}
    before = deepcopy(graph)
    with pytest.raises(migration.DefinitionMigrationRequired) as error:
        migration.normalize_definition(graph)
    assert error.value.issues[0]["path"] == path
    assert error.value.issues[0]["reason"] == reason
    assert graph == before


def test_full_graph_script_path_reaches_real_domain_owner_without_rewriting_history():
    from management_portal import research_map_automation as digest

    path = "/opt/tfi/release/code/scripts/research_map_digest.py"
    graph = {"lineage_id": "lineage_project", "function_blocks": [], "revision": 27,
             "program": {"components": [{"id": "effect_digest", "kind": "effect",
                "created_by_principal_id": "resident_project", "config": {
                    "script": digest.LEGACY_SCRIPT, "script_draft": {"source": digest.LEGACY_SCRIPT}}}], "wires": []}}
    before = deepcopy(graph)
    result = migration.normalize_definition(graph, script_path=path)
    assert result["program"]["components"][0]["config"]["script"] == digest.digest_script(path)
    assert result["program"]["components"][0]["config"]["script_draft"] == {"source": digest.LEGACY_SCRIPT}
    assert result["program"]["components"][0]["created_by_principal_id"] == "resident_project"
    assert result["revision"] == 27 and graph == before
    assert migration.normalize_definition(result, script_path=path) == result
    assert migration.normalize_template({"body": before}, script_path=path) == {"body": result}
    assert migration.normalize_template({"body": {"script": digest.LEGACY_SCRIPT}}, script_path=path)["body"]["script"] == digest.digest_script(path)
    assert migration.normalize_component_config({"script": digest.LEGACY_SCRIPT}, script_path=path)["script"] == digest.digest_script(path)
    with pytest.raises(migration.DefinitionMigrationRequired, match="operation_moved_out_of_isa"):
        migration.normalize_script(digest.LEGACY_SCRIPT + '\noutput owner="custom"', script_path=path)


@pytest.mark.parametrize("path", ["", "relative/script.py", 5, "/bad\x00path"])
def test_script_path_invalid_input_is_reported_without_echoing_it(path):
    graph = {"lineage_id": "lineage_fixture", "function_blocks": [], "program": {"components": [], "wires": []}}
    with pytest.raises(migration.DefinitionMigrationRequired) as error:
        migration.normalize_definition(graph, script_path=path)
    assert error.value.issues[0]["reason"] == "invalid_script_path"
    assert error.value.issues[0]["path"] == ["script_path"]


@pytest.mark.parametrize("old,args,new,expected", [
    ("InstallStoreTemplate", {"store_id": "store_x", "mode": "overwrite", "target_template_id": "template_x"},
     "UpdateInstalledTemplate", {"store_id": "store_x", "target_template_id": "template_x"}),
    ("ForwardMessages", {"target_chatroom_id": "chatroom_x", "messages": []},
     "ForwardMessagesToChatroom", {"target_chatroom_id": "chatroom_x", "messages": []}),
    ("SearchMessages", {"resident_ids": ["resident_x"], "query": "same"},
     "SearchResidentMessages", {"resident_ids": ["resident_x"], "query": "same"}),
    ("SetIntakeOpen", {"resident_id": "resident_x", "open": False},
     "SetIntakeOpen", {"resident_ids": ["resident_x"], "open": False}),
    ("ForkResident", {"fork_mode": "recent-10"}, "ForkResident", {"fork_mode": "recent", "recent_rounds": 10}),
    ("UpdateResidentWorkSummary", {"resident_id": "resident_x", "consolidation": {"phase": {}}},
     "UpdateResidentSummaryConsolidation", {"resident_id": "resident_x", "consolidation": {"phase": {}}}),
    ("ReviewComponentScript", {"decision": "cancel", "component_id": "effect_x"},
     "CancelComponentScriptDraft", {"component_id": "effect_x"}),
    ("ReadScriptRunOutput", {"script_run_id": "run_x", "stream": "artifact", "name": "report"},
     "ReadScriptRunArtifact", {"script_run_id": "run_x", "name": "report"}),
    ("ApplyTemplate", {"template_id": "template_x", "dry_run": True},
     "GetTemplateApplyPreview", {"template_id": "template_x"}),
    ("SetMailboxItemRead", {"key": "resident:resident_x"},
     "UpdateMailboxItem", {"key": "resident:resident_x", "unread": False}),
    ("PublishPulseItem", {"pulse_id": "pulse_x", "published": False},
     "UnpublishPulseItem", {"pulse_id": "pulse_x"}),
])
def test_safe_call_contracts(old, args, new, expected):
    before = deepcopy(args)
    assert structured(old, args) == (new, expected)
    assert args == before
    assert structured(new, expected) == (new, expected)


@pytest.mark.parametrize("old,args", [
    ("MaintainFamilyRecovery", {}), ("RunGoogleDriveBackup", {}),
    ("CreateWire", {}), ("ListMailboxTodos", {}),
    ("RelayFamilyMessage", {"recover": False}),
    ("UpdateUser", {"user_id": "u", "status": "active", "category": "admin"}),
    ("UpdateChatroom", {"chatroom_id": "c", "title": "keep", "muted": True}),
    ("DeliverMessage", {"resident_id": "r", "temporary_credentials": []}),
    ("DeliverMessage", {"resident_id": "r", "activation_policy": "inject_active_turn"}),
    ("SetIntakeOpen", {"resident_id": "r", "resident_ids": ["other"]}),
    ("SetMailboxItemPinned", {"key": "k", "pinned": True, "muted": True}),
    ("ApplyTemplate", {"dry_run": "false"}),
    ("UpdateResidentWorkSummary", {"summary": {}, "consolidation": {}}),
])
def test_non_equivalent_or_previously_invalid_calls_remain_closed(old, args):
    with pytest.raises(contracts.ContractConflict):
        structured(old, args)


def test_dynamic_selector_is_not_a_literal_but_reference_array_retains_types():
    parsed = script.parse('InstallStoreTemplate mode="${arguments.mode}"').calls[0]
    with pytest.raises(contracts.ContractConflict, match="dynamic_selector"):
        contracts.rewrite(parsed.instruction, {a.name: contracts.Value(a.value, True) for a in parsed.arguments})
    parsed = script.parse('SetIntakeOpen resident_id="${arguments.target}" open:=false').calls[0]
    _, values = contracts.rewrite(parsed.instruction, {a.name: contracts.Value(a.value, True) for a in parsed.arguments})
    assert values["resident_ids"].argument("resident_ids") == 'resident_ids:=[${arguments.target}]'
    assert values["open"].argument("open") == 'open:=false'


def test_template_fork_metadata_preserves_counts_and_rejects_conflict():
    assert contracts.normalize_fork_mode({"fork_mode": "recent-5", "name": "keep"}) == {
        "fork_mode": "recent", "recent_rounds": 5, "name": "keep",
    }
    with pytest.raises(contracts.ContractConflict):
        contracts.normalize_fork_mode({"fork_mode": "recent-5", "recent_rounds": 10})


def test_user_changes_follow_legacy_schema_and_mixed_or_dynamic_updates_stop():
    for field, value, name in (("status", "disabled", "UpdateUserStatus"),
                               ("category", "intern", "UpdateUserCategory")):
        assert structured("UpdateUser", {"user_id": "u", "changes": {field: value}}) == (
            name, {"user_id": "u", field: value},
        )
    profile = {"user_id": "u", "changes": {"display_name": "kept"}}
    assert structured("UpdateUser", profile) == ("UpdateUser", profile)
    source = 'UpdateUser user_id="${arguments.user}" changes:={"status":"disabled"}'
    normalized = migration.normalize_script(source)
    observed = []
    result = script.Interpreter(script.parse(normalized), {"arguments": {"user": "u"}},
                                lambda call: observed.append(call) or script.CallResult(True, {})).run()
    assert result.status == "succeeded"
    assert observed[0].instruction == "UpdateUserStatus"
    assert observed[0].arguments == {"user_id": "u", "status": "disabled"}
    assert migration.normalize_script(normalized) == normalized
    for source in (
        'UpdateUser user_id="u" changes:={"status":"disabled","display_name":"private"}',
        'UpdateUser user_id="u" changes:={"status":"disabled","category":"intern"}',
        'UpdateUser user_id="u" changes="${arguments.changes}"',
        'UpdateUser user_id="u" changes:={"status":${arguments.status}}',
    ):
        with pytest.raises(migration.DefinitionMigrationRequired) as error:
            migration.normalize_script(source)
        assert error.value.issues[0]["line"] == 1
        assert "private" not in str(error.value)


def test_split_read_result_and_user_delivery_policy_are_not_silently_changed():
    with pytest.raises(contracts.ContractConflict, match="store_item_result"):
        structured("ListTemplateStore", {"store_id": "store_kept"})
    for key, value in (("activation_policy", "attach_to_next"), ("context_policy", "fresh"),
                       ("execution_constraints", {"text_only": True})):
        with pytest.raises(contracts.ContractConflict, match="user_delivery_has_resident_policy"):
            structured("DeliverMessage", {"user_id": "u", "message": {}, key: value})
    assert structured("DeliverMessage", {"user_id": "u", "message": {}}) == (
        "DeliverUserMessage", {"user_id": "u", "message": {}},
    )


def test_answers_match_old_dispatcher_exactly_and_keep_question_content():
    common = {"resident_id": "r", "message_id": "m"}
    for action in ("accept", "decline", "cancel"):
        expected = ("CancelResidentRequest", common) if action == "cancel" else (
            "AnswerResidentRequest", {**common, "decision": action},
        )
        assert structured("AnswerResidentRequest", {**common, "answers": {"action": action}}) == expected
    answers = {"action": "accept", "content": {"values": [1, 2]}}
    assert structured("AnswerResidentRequest", {**common, "answers": answers}) == (
        "AnswerResidentRequest", {**common, "decision": "accept", "content": answers["content"]},
    )
    for answers in ({"0": "answer"}, {"action": "accept", "other_question": "answer"}):
        original = {**common, "answers": answers}
        assert structured("AnswerResidentRequest", original) == ("AnswerResidentRequest", original)
    for answers in ({"action": "decline", "content": {}}, {"action": "accept", "content": "text"}):
        with pytest.raises(contracts.ContractConflict):
            structured("AnswerResidentRequest", {**common, "answers": answers})
    with pytest.raises(migration.DefinitionMigrationRequired):
        migration.normalize_script('AnswerResidentRequest resident_id="r" message_id="m" answers="${arguments.answers}"')
    original = 'AnswerResidentRequest resident_id="r" message_id="m" answers:={"action":"accept","content":{"v":1}}'
    rewritten = migration.normalize_script(original)
    assert 'decision:="accept"' in rewritten and 'content:={"v": 1}' in rewritten
    assert migration.normalize_script(rewritten) == rewritten


def test_lineage_preview_split_preserves_arguments_result_references_and_version():
    args = {"lineage_id": "lineage_kept", "operations": [{"op": "update", "id": "kept"}],
            "function_block_id": "duty_kept", "duty_changes": {"memory_level": "turn"}}
    for preview in (True, False):
        expected = "GetLineageChangesPreview" if preview else "ApplyLineageChanges"
        assert structured("ApplyLineageChanges", {**args, "dry_run": preview}) == (expected, args)
    source = '''p=$(ApplyLineageChanges lineage_id="${arguments.lineage}" operations:=${arguments.operations} dry_run:=true)
output version="${p.revision}" changes="${p.updated_ids}" preview="${p.preview}"'''
    normalized = migration.normalize_script(source)
    observed = []
    receipt = {"revision": 9, "updated_ids": ["kept"], "preview": {"before": {}, "after": {}}}
    def call(request):
        observed.append(request)
        return script.CallResult(True, receipt)
    result = script.Interpreter(script.parse(normalized), {"arguments": {
        "lineage": "lineage_kept", "operations": args["operations"],
    }}, call).run()
    assert observed[0].instruction == "GetLineageChangesPreview"
    assert observed[0].arguments == {"lineage_id": "lineage_kept", "operations": args["operations"]}
    assert result.outputs == {"version": 9, "changes": ["kept"], "preview": receipt["preview"]}
    assert migration.normalize_script(normalized) == normalized
    config = {"instruction": "ApplyLineageChanges", "arguments": {**args, "dry_run": True}, "expected_version": 9}
    assert migration.normalize_wire_config(config) == {
        "instruction": "GetLineageChangesPreview", "arguments": args, "expected_version": 9,
    }


def test_retired_resident_delete_and_dynamic_preview_have_location_diagnostics():
    source = 'DeleteResident lineage_id="private" resident_id="private"\nApplyLineageChanges dry_run="${arguments.preview}"'
    with pytest.raises(migration.DefinitionMigrationRequired) as error:
        migration.normalize_component_config({"script": source})
    assert [(item["instruction"], item["reason"], item["line"], item["path"]) for item in error.value.issues] == [
        ("DeleteResident", "operation_moved_out_of_isa", 1, ["script"]),
        ("ApplyLineageChanges", "dynamic_selector_requires_owner_resolution", 2, ["script"]),
    ]
    assert "private" not in str(error.value)
    with pytest.raises(migration.DefinitionMigrationRequired):
        migration.normalize_wire_config({"instruction": "DeleteResident", "arguments": {
            "lineage_id": "l", "resident_id": "r",
        }})


def test_script_modes_choose_per_call_names_preserve_references_and_output():
    source = '''# InstallStoreTemplate mode is commentary
first=$(InstallStoreTemplate store_id="s" mode="overwrite" target_template_id="t")
second=$(InstallStoreTemplate store_id="s" mode="copy" target_template_id="t")
SetIntakeOpen resident_id="${arguments.target}" open:=false
ForkResident fork_mode="recent-5" source_resident_id="${arguments.target}"
SetMailboxItemRead key="resident:${arguments.target}"
output result="${first.value}" literal="ForwardMessages"'''
    rewritten = migration.normalize_script(source)
    observed = []

    def execute(request):
        observed.append((request.instruction, request.arguments))
        return script.CallResult(True, {"value": 7})

    result = script.Interpreter(script.parse(rewritten), {"arguments": {"target": "resident_kept"}}, execute).run()
    assert result.status == "succeeded"
    assert observed == [
        ("UpdateInstalledTemplate", {"store_id": "s", "target_template_id": "t"}),
        ("InstallStoreTemplateAsNew", {"store_id": "s", "target_template_id": "t"}),
        ("SetIntakeOpen", {"resident_ids": ["resident_kept"], "open": False}),
        ("ForkResident", {"fork_mode": "recent", "recent_rounds": 5, "source_resident_id": "resident_kept"}),
        ("UpdateMailboxItem", {"key": "resident:resident_kept", "unread": False}),
    ]
    assert result.outputs == {"result": 7, "literal": "ForwardMessages"}
    assert rewritten.startswith("# InstallStoreTemplate mode is commentary")
    assert migration.normalize_script(rewritten) == rewritten


def test_structured_template_and_wire_use_same_contract_without_touching_drafts():
    old = {"nested": [{"instruction": "SearchMessages", "arguments": {"chatroom_id": "chatroom_kept", "query": "private"}}],
           "fork_mode": "recent-10", "script_draft": {"script": "MaintainFamilyReview", "hash": "original"}}
    before = deepcopy(old)
    normalized = migration.normalize_wire_config(old)
    assert normalized["nested"][0]["instruction"] == "SearchChatroomMessages"
    assert normalized["nested"][0]["arguments"] == old["nested"][0]["arguments"]
    assert normalized["fork_mode"] == "recent" and normalized["recent_rounds"] == 10
    assert normalized["script_draft"] == old["script_draft"]
    assert migration.normalize_template({"body": old})["body"] == normalized
    assert old == before
    assert migration.normalize_wire_config(normalized) == normalized


def test_custom_policies_and_dynamic_modes_collect_precise_locations_without_payloads():
    source = 'MaintainFamilyReview resident_id="private"\nInstallStoreTemplate mode="${arguments.mode}"'
    with pytest.raises(migration.DefinitionMigrationRequired) as error:
        migration.normalize_template({"body": {"parts": [{"script": source}]}})
    assert [i["line"] for i in error.value.issues] == [1, 2]
    assert all(i["path"] == ["parts", 0, "script"] for i in error.value.issues)
    assert "private" not in str(error.value)


def test_historical_rename_of_retired_custom_policy_still_requires_owning_workflow():
    with pytest.raises(migration.DefinitionMigrationRequired) as error:
        migration.normalize_script('CheckResidentWorkSummary resident_id="r" message_id="m"')
    assert error.value.issues[0]["instruction"] == "CheckResidentWorkSummary"
    assert error.value.issues[0]["reason"] == "operation_moved_out_of_isa"


def test_changed_resident_route_participates_in_pending_delivery_barrier():
    definition = {"program": {"components": [], "wires": [{
        "id": "wire_kept", "target": "resident_kept", "config": {"prompt": "GetFamilySummaryInputs"},
    }]}}
    effects, wires = migration._normalize_definition(definition)
    assert effects == set()
    assert wires == {"wire_kept": "resident_kept"}
    assert definition["program"]["wires"][0]["config"]["prompt"] == "GetFacadeSummaryInputs"


def test_explicit_wait_and_uploaded_file_contracts_preserve_intent():
    assert structured("ClearContext", {"resident_id": "r", "active_execution_policy": "wait"}) == (
        "CreateContextReset", {"resident_id": "r"},
    )
    assert structured("RestartSession", {"resident_id": "r", "operation": "switch_account",
                                          "account_id": "a", "active_execution_policy": "wait", "context_strategy": "preserve"}) == (
        "RestartSession", {"resident_id": "r", "account_id": "a"},
    )
    with pytest.raises(contracts.ContractConflict):
        structured("RestartSession", {"active_execution_policy": "cancel"})
    assert structured("CreateUpload", {"purpose": "avatar", "source_address": "workspace_x/file/a.png"}) == (
        "CreateUploadFromFile", {"purpose": "avatar", "source_address": "workspace_x/file/a.png"},
    )
    assert structured("UpdateExecutionSettings", {"permission_system_enabled": False}) == (
        "SetPermissionSystemEnabled", {"enabled": False},
    )


def test_route_barrier_reads_pending_events_and_operators_without_rewriting_them(monkeypatch):
    from management_portal.society_service import domain_work_storage, program_work_storage

    pending = {"event_wires": {"wire_route": {"pending_events": [
        {"claim_token": "delivery_kept", "event": {"payload": {"trigger_path_id": "path_kept"}}},
    ]}}, "operators": {"operator_kept": {"pending_deliveries": {
        "pending_kept": {"output_wire_ids": ["wire_route"]},
    }}}}
    before = deepcopy(pending)
    monkeypatch.setattr(domain_work_storage, "list_work", lambda *a: {})
    monkeypatch.setattr(program_work_storage, "read", lambda *a: pending)
    blocked = migration._blockers(None, "lineage_kept", set(), {"wire_route": "resident_kept"})
    assert {item["kind"] for item in blocked} == {"pending_event_deliveries", "pending_operator_delivery"}
    assert blocked[0]["deliveries"][0]["trigger_path_id"] == "path_kept"
    assert all(item["target_id"] == "resident_kept" and "effect_id" not in item for item in blocked)
    assert pending == before


from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: E402,F401


def test_readonly_preflight_reports_every_legacy_intent_without_initializing_owners(primitive_store, monkeypatch):
    import json
    from management_common.json_columns import json_text
    from management_portal.execution import agent_data_store
    from management_portal.society_service import (
        family_strategy_state, family_task_dispatch_storage, lineage_aggregate, lineage_metadata_storage,
        resident_conversation_stream,
    )
    from tests.cases.test_isa_definition_semantic_migration import make_lineage

    lineage_id = make_lineage('output status="unchanged"')
    database = lineage_aggregate.lineage_catalog_store().state_store.database
    # The real snapshot reader crosses the existing Agent schema. Provision it
    # as fixture setup, then prohibit ordinary initialization during check().
    agent_data_store.AgentDataStore()
    messages = ["msg_" + digit * 32 for digit in "123"]
    frozen = {"request": {"target": "resident_aaaaaaaaaaaa", "facade": "resident_bbbbbbbbbbbb",
               "trigger": "route", "business_ref": "private-business-reference",
               "facts": {"message_id": "msg_" + "4" * 32}}, "text": "private-frozen-task-text"}
    with database.transaction() as connection:
        family_task_dispatch_storage.create_schema(connection)
        for mid, intent in zip(messages, [{}, {"request": None}, frozen]):
            connection.execute("INSERT INTO family_task_dispatches VALUES (%s, %s, %s)",
                               (lineage_id, mid, json_text(intent)))
        before = connection.execute("SELECT * FROM family_task_dispatches ORDER BY message_id").fetchall()

    read_many = lineage_metadata_storage.read_many
    check_dispatches = family_strategy_state.check_legacy_dispatches
    snapshots = []
    dispatch_snapshots = []
    def readonly_dispatches(connection):
        search_path = connection.execute("SHOW search_path").fetchone()["search_path"]
        dispatch_snapshots.append(connection)
        result = check_dispatches(connection)
        assert connection.execute("SHOW search_path").fetchone()["search_path"] == search_path
        return result

    def readonly_metadata(connection, *args):
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        assert connection.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "repeatable read"
        snapshots.append(connection)
        return read_many(connection, *args)

    with monkeypatch.context() as patch:
        patch.setattr(lineage_metadata_storage, "read_many", readonly_metadata)
        patch.setattr(family_strategy_state, "check_legacy_dispatches", readonly_dispatches)
        patch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: pytest.fail("catalog initialized"))
        patch.setattr(resident_conversation_stream, "get_repository", lambda: pytest.fail("repository initialized"))
        patch.setattr(agent_data_store, "get_agent_data_store", lambda: pytest.fail("agent store initialized"))
        report = migration.check()
        assert len(dispatch_snapshots) == 1 and snapshots[0] is dispatch_snapshots[0]
        assert len({id(connection) for connection in snapshots}) == 1
        assert report["blocked_count"] == 3
        assert {(item["lineage_id"], item["message_id"], item["reason"]) for item in report["blockers"]} == {
            (lineage_id, messages[0], "invalid_frozen_intent"),
            (lineage_id, messages[1], "invalid_frozen_intent"),
            (lineage_id, messages[2], "unaccepted_frozen_intent"),
        }
        assert "private-frozen-task-text" not in json.dumps(report)
        assert "private-business-reference" not in json.dumps(report)
        with pytest.raises(RuntimeError, match="unaccepted_frozen_intent"):
            migration._require_clear(report)
    with database.transaction() as connection:
        assert connection.execute("SELECT * FROM family_task_dispatches ORDER BY message_id").fetchall() == before


def test_readonly_dispatch_preflight_owner_error_cannot_be_reported_as_clear(primitive_store, monkeypatch):
    from management_portal.society_service import family_strategy_state
    from tests.cases.test_isa_definition_semantic_migration import make_lineage

    make_lineage('output status="unchanged"')
    def failed_owner(connection):
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        raise RuntimeError("legacy receipt evidence unavailable")
    monkeypatch.setattr(family_strategy_state, "check_legacy_dispatches", failed_owner)
    with pytest.raises(RuntimeError, match="legacy receipt evidence unavailable"):
        migration.check()


def test_read_only_cutover_keeps_journal_then_versions_current_definitions(primitive_store, monkeypatch):
    from hashlib import sha256
    from management_portal.society_service import lineage_aggregate, lineage_metadata_storage
    from management_portal.society_service import lineage_effect_script_journal as journal
    from tests.cases.test_isa_definition_semantic_migration import make_lineage

    source = 'SetIntakeOpen resident_id="resident_kept" open:=false'
    lineage_id = make_lineage(source)
    catalog = lineage_aggregate.lineage_catalog_store()
    before = catalog.get(lineage_id)
    journal.begin(lineage_id, "d066-activation", effect_id="effect_a11a11a11a11", trigger_path_id="",
                  script_sha=sha256(source.encode()).hexdigest(), source={})
    journal.record_call_started(lineage_id, "d066-activation", "c1", {
        "instruction": "SetIntakeOpen", "caller": "resident_kept", "arguments": {"resident_id": "resident_kept"},
        "instruction_id": "inst_lin_11111111111111111111",
    })
    active = journal.load(lineage_id, "d066-activation")
    read = lineage_metadata_storage.read_many

    def read_only(connection, *args):
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        return read(connection, *args)

    with monkeypatch.context() as patch:
        patch.setattr(lineage_metadata_storage, "read_many", read_only)
        patch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: pytest.fail("ordinary owner initialized"))
        assert migration.check()["blockers"][0]["activation_key"] == "d066-activation"
    with pytest.raises(RuntimeError, match="d066-activation"):
        migration.migrate()
    assert catalog.get(lineage_id) == before
    assert journal.load(lineage_id, "d066-activation") == active
    journal.record_call(lineage_id, "d066-activation", "c1", {
        "instruction": "SetIntakeOpen", "status": "committed", "ok": True, "result": {},
    })
    journal.finish(lineage_id, "d066-activation", status="succeeded", outputs={})
    history = journal.load(lineage_id, "d066-activation")
    migration.migrate()
    after = catalog.get(lineage_id)
    assert after["revision"] == before["revision"] + 1
    assert after["layout_revision"] == before["layout_revision"]
    assert 'resident_ids:=["resident_kept"]' in after["program"]["components"][0]["config"]["script"]
    assert journal.load(lineage_id, "d066-activation") == history
    migration.migrate()
    assert catalog.get(lineage_id) == after


def test_prompt_names_are_idempotent_and_invalid_boolean_mode_is_not_silently_removed():
    old = "ForwardMessages、SearchMessages、ListInputHistory、ReviewComponentScript；SetMailboxItemRead"
    new = migration.normalize_prompt(old)
    assert "ForwardMessagesToResident" in new and "SearchChatroomMessages" in new
    assert "ConfirmComponentScript" in new and "UpdateMailboxItem" in new
    assert migration.normalize_prompt(new) == new
    with pytest.raises(contracts.ContractConflict):
        structured("CreateLineage", {"familize": 0})
