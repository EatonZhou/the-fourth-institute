"""Lineage owner adapters retain operation and event provenance at the new ports."""

from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.society_service import lineage_graph_edits, resident_conversation_stream
from management_portal.society_service.isa import authorization_arguments, definition, residents
from management_portal.society_service.lineage_program_effect_execution import _family_note_context
from tests.cases.test_family_familize import charters, lineage  # noqa: F401
from tests.cases.test_family_tasks import configured_family  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


LINEAGE = "lineage_aaaaaaaaaaaa"
FACADE = "resident_bbbbbbbbbbbb"
ROUTER = "resident_cccccccccccc"
MEMBER = "resident_dddddddddddd"
FAMILY = "fbi_eeeeeeeeeeee"
EFFECT = "effect_ffffffffffff"
REQUEST = "msg_" + "1" * 32
ROUTER_INPUT = "msg_" + "2" * 32


@pytest.mark.parametrize("preview", [False, True])
def test_lineage_changes_delegate_full_exact_permissions_before_applying(monkeypatch, preview):
    name = "GetLineageChangesPreview" if preview else "ApplyLineageChanges"
    args = {"lineage_id": LINEAGE, "operations": [
        {"op": "create", "kind": "component", "value": {"kind": "effect"}},
        {"op": "delete", "kind": "resident", "id": MEMBER},
    ]}
    invoked = Invocation(declaration(name), InstructionCall(name, args), InstructionContext(caller="alice", entry="web"), "")
    keys = ["lineage_id", "create_component", "delete_resident.lineage_id", "delete_resident.resident_id"]
    expected = list(zip(keys, [LINEAGE, LINEAGE, LINEAGE, MEMBER]))
    assert authorization_arguments(invoked.declaration, args, invoked.context)["_permission_operations"] == [
        {"element": f"ApplyLineageChanges:{key}", "resource_id": target}
        for key, target in expected
    ]

    def deny(actor, checks):
        assert actor == "alice"
        assert checks == [(f"{name}:{key}", target) for key, target in expected]
        return False

    def graph(_lineage_id, payload, _actor, *, include_lineage, authorize, creator_resident_id):
        assert payload["dry_run"] is preview and not include_lineage and not creator_resident_id
        authorize(lineage_graph_edits._lineage_change_requirements(
            LINEAGE, payload["program_operations"], payload["resident_ids"], {},
        ))
        pytest.fail("an operation was denied")

    monkeypatch.setattr(permission_system, "allows_elements", deny)
    monkeypatch.setattr(lineage_graph_edits, "apply_lineage_changes", graph)
    with pytest.raises(InstructionError, match="缺少图或职责操作的权限"):
        definition.apply_lineage_changes(invoked, preview=preview)


def test_replaced_single_edit_does_not_ask_for_the_old_batch_grant():
    request = {"lineage_id": LINEAGE, "operations": [{"op": "delete", "kind": "resident", "id": MEMBER}]}
    expected = [
        {"element": "ApplyLineageChanges:delete_resident.lineage_id", "resource_id": LINEAGE},
        {"element": "ApplyLineageChanges:delete_resident.resident_id", "resource_id": MEMBER},
    ]
    assert definition.lineage_changes_permission_arguments(request)["_permission_operations"] == expected
    with pytest.raises(InstructionError, match="不接受参数"):
        definition.lineage_changes_permission_arguments({**request, "_permission_operations": expected})


def test_graph_creation_provenance_cannot_be_set_in_public_value():
    args = {"lineage_id": LINEAGE, "near_resident_id": MEMBER, "operations": [
        {"op": "create", "kind": "component", "value": {"kind": "effect", "created_by_principal_id": ROUTER}},
    ]}
    with pytest.raises(ValueError, match="不可写字段.*created_by_principal_id"):
        definition.lineage_changes_permission_arguments(args)
    invocation = Invocation(
        declaration("ApplyLineageChanges"), InstructionCall("ApplyLineageChanges", args),
        InstructionContext(caller=MEMBER, entry="web"), "",
    )
    with pytest.raises(ValueError, match="不可写字段.*created_by_principal_id"):
        definition.apply_lineage_changes(invocation)


def test_permission_rpc_derives_as_reader_without_executing_an_edit(monkeypatch):
    from management_portal.society_service import contracts, lineage_state, server

    request = {"lineage_id": LINEAGE, "operations": [{"op": "delete", "kind": "resident", "id": MEMBER}]}
    method = "describe_lineage_change_permissions"
    monkeypatch.setattr(lineage_state, "lineage_access_projection",
                        lambda *_: pytest.fail("request derivation cannot read stored graph facts"))
    monkeypatch.setattr(definition, "apply_lineage_changes", lambda *_: pytest.fail("permission inspection cannot edit"))
    operation = contracts.operation_contract("lineage", method)
    handler, bound = server._bind_domain_call("lineage", method, [request], {"actor": "reader"})
    result = server._invoke_domain(operation, handler, bound)
    assert operation.mode == "read" and operation.in_process
    assert result["_permission_operations"][-1] == {
        "element": "ApplyLineageChanges:delete_resident.resident_id", "resource_id": MEMBER,
    }
    with pytest.raises(Exception, match="_permission_operations"):
        definition.describe_lineage_change_permissions({**request, "_permission_operations": []}, actor="reader")


def test_permission_workspace_projection_reads_only_an_existing_snapshot(monkeypatch):
    from contextlib import contextmanager
    from management_portal.society_service import contracts, permission_facts_projection, server

    SqlRows = SimpleNamespace(fetchall=lambda: [{"resident_id": MEMBER, "owned_workspace_id": "workspace_owned"}])
    statement = []

    @contextmanager
    def readonly_snapshot():
        def execute(sql):
            statement.append(sql)
            return SqlRows

        yield SimpleNamespace(execute=execute)

    def database(*, schema):
        assert schema == "lineage"
        return SimpleNamespace(readonly_snapshot=readonly_snapshot)

    monkeypatch.setattr(permission_facts_projection, "portal_database", database)
    method = "list_permission_resident_workspaces"
    operation = contracts.operation_contract("projection", method)
    handler, bound = server._bind_domain_call("projection", method, [], {})
    assert operation.mode == "read" and operation.in_process
    assert server._invoke_domain(operation, handler, bound) == {MEMBER: "workspace_owned"}
    assert len(statement) == 1 and "deleted_at IS NULL" in statement[0]


def test_automatic_intake_cannot_override_a_pending_lifecycle_hold(monkeypatch):
    from contextlib import nullcontext
    from management_portal.society_service import resident_management_domain, resident_record_domain

    record = {"resident_id": MEMBER, "intake_open": False}
    locked = False

    def within_lock(_resident_id):
        nonlocal locked
        locked = True
        return nullcontext()

    def pending(resident_id):
        assert resident_id == MEMBER and locked
        return {"operation": "clear_context", "status": "waiting"}

    monkeypatch.setattr(resident_record_domain, "state_lock", within_lock)
    monkeypatch.setattr(resident_record_domain, "find_resident_canonical", lambda _: record)
    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: SimpleNamespace(pending=pending))
    monkeypatch.setattr(resident_management_domain, "ensure_resident_visible_to_actor", lambda *_: None)
    monkeypatch.setattr(resident_management_domain, "_commit_manual_resident_intake_state",
                        lambda *_, **__: pytest.fail("automation must not reopen a lifecycle hold"))
    result = resident_management_domain._change_resident_intake_state(MEMBER, FACADE, "open", allow_quota_override=False)
    assert result == {"intake_state": "closed", "quota_override_pending": False}


def test_role_availability_balances_own_roles_and_respects_facade_pause():
    from management_portal.society_service.family_definition import FAMILY_ROLE_AVAILABILITY_SCRIPT
    from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, parse

    for intake_state in ("closed", "open"):
        calls = []

        def execute(request):
            target, = request.arguments["resident_ids"]
            calls.append((request.instruction, target))
            if request.instruction == "SetIntakeOpen":
                assert request.arguments["open"] is True
                return CallResult(True, {})
            result = intake_state if target == FACADE else ("closed" if target == ROUTER else "open")
            return CallResult(True, {"states": [{"state": {"intake_state": result}}]})

        outcome = Interpreter(parse(FAMILY_ROLE_AVAILABILITY_SCRIPT), {
            "arguments": {"facade": FACADE, "roles": [ROUTER, MEMBER]},
        }, execute).run()
        assert outcome.status == "succeeded", outcome.error
        expected = [("ListResidentStates", FACADE)]
        if intake_state == "open":
            expected += [("ListResidentStates", ROUTER), ("SetIntakeOpen", ROUTER), ("ListResidentStates", MEMBER)]
        assert calls == expected


def test_review_retains_member_cursors_and_freezes_the_outbound_notice():
    from copy import deepcopy
    from management_portal.society_service import family_review_effects
    from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, analyze, parse

    ledger = {"activity": {ROUTER: [100, ROUTER_INPUT]}, "reviewed": {ROUTER: [100, ROUTER_INPUT]}}
    deliveries = []
    args = {"facade": FACADE, "steward": ROUTER, "lineage_id": LINEAGE,
            "review_key": f"family.{FAMILY}.review", "family_tasks": {"review": {"prompt": "Review", "guide": "Guide"}}}
    event = {"id": "event_" + "a" * 24, "subject_resident_id": MEMBER, "payload": {"message_id": REQUEST}}

    def execute(request):
        nonlocal ledger
        if request.instruction == "GetSharedState":
            return CallResult(True, {"value": deepcopy(ledger)})
        if request.instruction == "GetInputState":
            return CallResult(True, {"input_state": {"state": "completed", "input_facts": {
                "role": "user", "completed_cursor": 3, "is_info": False, "business_ref": ""}}})
        if request.instruction == "ListResidentStates":
            return CallResult(True, {"states": [{"state": {"work_state": "idle", "intake_state": "open", "queued_count": 0}}]})
        if request.instruction == "UpdateSharedState":
            assert request.arguments["expected_value"] == ledger
            ledger = request.arguments["value"]
            return CallResult(True, {"matched": True})
        assert request.instruction == "DeliverMessage"
        message = request.arguments["message"]
        assert message == ledger["pending"]["envelope"]
        deliveries.append(message)
        return CallResult(True, {"message_id": message["message_id"]})

    def run(script):
        assert not analyze(script, declaration_for=declaration).diagnostics
        outcome = Interpreter(parse(script), {"arguments": args, "event": event}, execute).run()
        assert outcome.status == "succeeded", outcome.error
        return outcome.outputs["status"]

    assert run(family_review_effects.RECORD_ACTIVITY) == "recorded"
    assert ledger["activity"] == {ROUTER: [100, ROUTER_INPUT], MEMBER: [3, REQUEST]}
    assert run(family_review_effects.NOTICE) == "requested"
    assert ledger["reviewed"] == ledger["activity"] and ledger["pending"] is None
    assert run(family_review_effects.NOTICE) == "unchanged" and len(deliveries) == 1


def test_family_recovery_keeps_one_durable_steward_notice():
    from copy import deepcopy
    from management_portal.society_service import family_recovery_effects
    from management_portal.society_service.family_ledger_retention import compact_recovery
    from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, analyze, parse

    ledger = {"incidents": {f"old-{i}": {"status": "completed", "previous": None, "detail": "x" * 1000}
                            for i in range(70)}}
    counts = {"notice": 0, "cas_conflicts": 1}
    states = {REQUEST: "failed"}
    family = {"facade": FACADE, "steward": ROUTER, "router": ROUTER, "members": [MEMBER]}
    args = {key: item["value"] for key, item in family_recovery_effects.bindings(LINEAGE, FAMILY, family).items()}
    args["family_tasks"] = {"recovery": {"prompt": "Repair", "guide": "Keep the source"}}

    def execute(request):
        nonlocal ledger
        arguments = request.arguments
        if request.instruction == "ListResidentStates":
            return CallResult(True, {"states": [{"state": {"intake_state": "open"}} for _ in arguments["resident_ids"]]})
        if request.instruction == "GetSharedState":
            return CallResult(True, {"value": deepcopy(ledger)})
        if request.instruction == "UpdateSharedState":
            if counts["cas_conflicts"]:
                counts["cas_conflicts"] -= 1
                return CallResult(True, {"matched": False})
            assert arguments["expected_value"] == ledger
            ledger = compact_recovery(arguments["value"], ledger, "2026-11-28T06:00:00Z")
            return CallResult(True, {"matched": True, "value": deepcopy(ledger)})
        if request.instruction == "GetInputState":
            state = states.get(arguments["message_id"])
            return CallResult(bool(state), {"input_state": {"state": state}})
        assert request.instruction == "DeliverMessage"
        assert arguments["message"] == ledger["notice"]["envelope"]
        mid = arguments["message"]["message_id"]
        states[mid] = "queued"
        counts["notice"] += 1
        return CallResult(True, {"message_id": mid})

    script = family_recovery_effects.RECOVERY
    assert not analyze(script, declaration_for=declaration).diagnostics

    def run(kind, marker, payload):
        event = {"type": kind, "id": "event_" + marker * 24, "payload": payload}
        outcome = Interpreter(parse(script), {"arguments": args, "event": event}, execute).run()
        assert outcome.status == "succeeded", outcome.error
        return outcome.outputs

    first = run("resident.work_failed", "a", {"resident_id": MEMBER, "message_id": REQUEST})
    assert first["status"] == "requested"
    assert len(ledger["incidents"]) == 33
    assert run("time.reached", "b", {}) == {"status": "waiting", "message_id": first["message_id"]}
    assert counts["notice"] == 1
    states[first["message_id"]] = "completed"
    states[REQUEST] = "completed"
    counts["cas_conflicts"] = 1
    assert run("resident.work_finished", "c", {"resident_id": MEMBER, "message_id": REQUEST})["status"] == "completed"
    assert ledger["notice"] is None and ledger["head"] is None
    assert run("time.reached", "d", {})["status"] == "unchanged"


def test_family_recovery_retention_preserves_pending_links():
    from management_portal.society_service.family_ledger_retention import compact_recovery

    ledger = {"head": "open", "notice": {"incident_keys": ["pinned"]}, "incidents": {
        "open": {"status": "open", "previous": "stale"},
        "stale": {"status": "completed", "settled_at": "2026-11-01T00:00:00Z", "previous": "older"},
        "older": {"status": "missing_creator", "previous": None},
        "pinned": {"status": "cancelled", "settled_at": "2026-11-01T00:00:00Z"},
        "recent": {"status": "completed", "settled_at": "2026-11-26T00:00:00Z"},
    }}
    value = compact_recovery(ledger, ledger, "2026-11-28T06:00:00Z")
    assert value["head"] == ledger["head"]
    assert value["notice"] == ledger["notice"]
    assert value["incidents"] == {
        "open": {"status": "open", "previous": "older"},
        **{key: ledger["incidents"][key] for key in ("older", "pinned", "recent")},
    }
    assert ledger["incidents"]["open"]["previous"] == "stale"


def test_family_recovery_retention_bounds_repeated_settlement():
    from datetime import datetime, timedelta, timezone
    from management_portal.society_service.family_ledger_retention import compact_recovery

    now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    ledger = {"incidents": {}}
    for i in range(150):
        key = f"incident-{i}"
        pending = {"head": key, "incidents": {**ledger["incidents"], key: {"status": "open"}}}
        next_value = {"incidents": {**pending["incidents"], key: {"status": "completed"}}}
        ledger = compact_recovery(next_value, pending, (now + timedelta(minutes=i)).isoformat())
        assert len(ledger["incidents"]) == min(i + 1, 32)
        assert key in ledger["incidents"]
    assert "incident-0" not in ledger["incidents"]


def test_family_review_retention_keeps_current_watermarks_and_pending_notice():
    from management_portal.society_service.family_ledger_retention import compact_review

    review = {
        "activity": {"current": [7, "a"], "departed": [2, "b"], "unread": [8, "c"], "pending": [3, "d"]},
        "reviewed": {"current": [7, "a"], "departed": [2, "b"], "unread": [1, "e"], "pending": [3, "d"]},
        "pending": {"message_id": "notice", "activity": {"pending": [3, "d"]}},
    }
    value = compact_review(review, {"current"})
    assert value["pending"] == review["pending"]
    assert value["activity"] == {k: v for k, v in review["activity"].items() if k != "departed"}
    assert value["reviewed"] == {k: v for k, v in review["reviewed"].items() if k != "departed"}


def test_effect_shared_json_is_copy_based_and_budgeted():
    from management_portal.society_service.lineage_effect_script import Interpreter, parse

    text = """modified=$(json_set value:=${arguments.object} path:=["branch","added"] item:={"new":true})
original=$(json_get value:=${arguments.object} path:=["branch","added"])
current=$(json_get value:=${modified.value} path:=["branch","added"])
output original:=${original.value} current:=${current.value}"""
    tree = {"branch": {"existing": [1, 2]}}
    variables = {"arguments": {"object": tree}}
    def execute(_):
        pytest.fail("pure JSON operations cannot call a domain instruction")

    script = parse(text)
    output = Interpreter(script, variables, execute).run()
    assert output.status == "succeeded"
    assert output.outputs == {"original": None, "current": {"new": True}}
    assert tree == {"branch": {"existing": [1, 2]}}
    assert Interpreter(script, variables, execute, max_calls=2).run().error.code == "effect.script_call_limit"


def test_family_routing_recovers_legacy_receipt_after_dispatch_table_retirement(future_creation_store):
    from management_common.json_columns import json_text
    from management_portal.society_service import family_task_receipts
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.society_service import resident_domain
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload

    record = resident_domain.register_resident(_new_session_payload("Family receipts fixture"), "sample_admin")
    lineage_id = record["lineage_id"]
    cell = f"family.{FAMILY}.dispatch_receipts"
    receipt = {"message_id": ROUTER_INPUT, "target_id": ROUTER, "facade_id": FACADE,
               "source_message_id": REQUEST, "trigger": "route"}
    with get_lineage_data_store().database.transaction() as connection:
        connection.execute("INSERT INTO lineage_shared_state(lineage_id,cells_json) VALUES(%s,%s)",
                           (lineage_id, json_text({"cells": {cell: {ROUTER_INPUT: receipt}}})))
        assert connection.execute("SELECT to_regclass('family_task_dispatches') AS table_name").fetchone()["table_name"] is None
    assert family_task_receipts.route_source(lineage_id, ROUTER, ROUTER_INPUT) == (FACADE, REQUEST)
    assert family_task_receipts.task_targets(lineage_id, FACADE, [REQUEST]) == [
        {"message_id": ROUTER_INPUT, "target": ROUTER, "facts": {"message_id": REQUEST}},
    ]
    assert family_task_receipts.route_source(lineage_id, MEMBER, ROUTER_INPUT) is None


def test_legacy_family_dispatch_retirement_refuses_unproven_work(future_creation_store, monkeypatch):
    from management_portal.society_service import family_strategy_state, family_task_dispatch_storage, resident_domain
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload

    lineage_id = resident_domain.register_resident(
        _new_session_payload("Legacy task retirement"), "sample_admin",
    )["lineage_id"]
    database = get_lineage_data_store().database
    with database.transaction() as connection:
        family_task_dispatch_storage.create_schema(connection)
        connection.execute("INSERT INTO family_task_dispatches VALUES(%s,%s,'{}')", (lineage_id, ROUTER_INPUT))

    rows = [{"message_id": ROUTER_INPUT, "state": "blocked", "reason": "unaccepted_frozen_intent"}]
    monkeypatch.setattr(family_strategy_state.family_task_dispatches, "plan_legacy_dispatches", lambda *_: rows)
    with pytest.raises(ValueError, match="unaccepted_frozen_intent"):
        family_strategy_state.retire_legacy_dispatches()
    with database.read() as connection:
        assert connection.execute("SELECT message_id FROM family_task_dispatches").fetchone()["message_id"] == ROUTER_INPUT

    rows[0] = {"message_id": ROUTER_INPUT, "state": "expired"}
    family_strategy_state.retire_legacy_dispatches()
    family_strategy_state.retire_legacy_dispatches()
    with database.read() as connection:
        assert connection.execute("SELECT to_regclass('family_task_dispatches') AS table_name").fetchone()["table_name"] is None


def test_legacy_family_dispatch_retirement_copies_a_real_accepted_active_input(configured_family, monkeypatch):
    from management_common.json_columns import json_text
    from management_portal.society_service import (
        family_strategy_state, family_task_dispatch_storage, family_task_receipts, resident_input_queue_domain,
        resident_intake_domain,
    )
    from management_portal.society_service.resident_protocol import ResidentDeliveryContext
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_resident_delivery_receipts import REAL_DELIVER_RESIDENT_INPUT
    from tests.cases.test_family_tasks import deliver_resident_message_content

    graph, block = configured_family
    lineage_id = graph["lineage_id"]
    facade, router = block["family"]["facade"], block["family"]["router"]
    repository = resident_conversation_stream.get_repository()
    root = resident_input_queue_domain.build_message("user", "Original", "sample_admin", message_id=REQUEST)
    repository.accept_user_message(facade, root)
    repository.set_input_state(facade, REQUEST, "queued")
    business_ref = f"family-route/{facade}/message/{REQUEST}"
    text = "Original router input"
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver_resident_message_content)
    REAL_DELIVER_RESIDENT_INPUT({
        "resident_id": router,
        "message": {"message_id": ROUTER_INPUT, "content_parts": [{"type": "text", "text": text}]},
    }, facade, delivery_context=ResidentDeliveryContext(business_ref=business_ref))
    assert repository.message_business_ref(router, ROUTER_INPUT) == business_ref
    legacy = {"request": {"facade": facade, "target": router, "trigger": "route", "business_ref": business_ref,
                          "facts": {"message_id": REQUEST}}, "text": text}
    database = get_lineage_data_store().database
    with database.transaction() as connection:
        family_task_dispatch_storage.create_schema(connection)
        connection.execute("INSERT INTO family_task_dispatches VALUES(%s,%s,%s)",
                           (lineage_id, ROUTER_INPUT, json_text(legacy)))
    original_router_message = repository.message(router, ROUTER_INPUT)
    with database.readonly_snapshot() as connection:
        assert family_strategy_state.check_legacy_dispatches(connection) == []
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        assert connection.execute("SELECT current_schema() AS name").fetchone()["name"] == database.schema

    mismatched = {**legacy, "text": "Input with different immutable content"}
    with database.transaction() as connection:
        connection.execute("UPDATE family_task_dispatches SET intent_json=%s WHERE message_id=%s",
                           (json_text(mismatched), ROUTER_INPUT))
    with database.readonly_snapshot() as connection:
        assert family_strategy_state.check_legacy_dispatches(connection) == [{
            "kind": "legacy_family_dispatch", "lineage_id": lineage_id, "message_id": ROUTER_INPUT,
            "reason": "invalid_frozen_intent",
        }]

    with database.transaction() as connection:
        connection.execute("UPDATE family_task_dispatches SET intent_json=%s WHERE message_id=%s",
                           (json_text(legacy), ROUTER_INPUT))
        family_id = connection.execute(
            "DELETE FROM family_residents WHERE resident_id=%s AND role='facade' RETURNING family_id", (facade,),
        ).fetchone()["family_id"]
    with database.readonly_snapshot() as connection:
        assert family_strategy_state.check_legacy_dispatches(connection) == [{
            "kind": "legacy_family_dispatch", "lineage_id": lineage_id, "message_id": ROUTER_INPUT,
            "reason": "missing_live_facade",
        }]
    with database.transaction() as connection:
        connection.execute("INSERT INTO family_residents(family_id,resident_id,role) VALUES(%s,%s,'facade')",
                           (family_id, facade))

    family_strategy_state.retire_legacy_dispatches()
    assert family_task_receipts.route_source(lineage_id, router, ROUTER_INPUT) == (facade, REQUEST)
    assert repository.message(router, ROUTER_INPUT) == original_router_message


def test_workflow_binding_installs_with_exact_graph_operations_and_reuses_ids(future_creation_store, monkeypatch):
    from management_portal import isa_client, lineage_workflows
    from management_portal.society_service import resident_domain
    from tests.cases.isa_test_support import ok
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload

    actor = "sample_admin"
    resident = resident_domain.register_resident(_new_session_payload("Workflow holder"), actor)
    lineage_id, resident_id = resident["lineage_id"], resident["resident_id"]
    writes = []

    def call(actor, instruction, arguments, **options):
        assert instruction in {"GetLineage", "ApplyLineageChanges"}
        if instruction == "ApplyLineageChanges":
            writes.append(arguments["operations"][0])
        return ok(instruction, arguments, actor=actor, **options)

    monkeypatch.setattr(isa_client, "call", call)
    workflows = lineage_workflows.WorkflowBindings()
    options = {"actor": actor, "lineage_id": lineage_id, "resident_id": resident_id, "name": "test",
               "title": "Official workflow", "prompt": "First prompt"}
    binding = workflows.install(**options)
    assert workflows.install(**options) == binding
    assert [operation["ref"] for operation in writes] == ["event", "effect", "target", "invoke"]
    assert workflows.install(**{**options, "prompt": "Updated prompt"}) == binding
    assert writes[-1]["kind"] == "component" and writes[-1]["id"] == binding["effect_id"]

    from management_portal.society_service.lineage_effect_script import deliver_message_script
    initial = deliver_message_script("Managed script A")
    desired = deliver_message_script("Managed script B")
    owned = {**options, "name": "managed", "effect_config": {"script": initial, "endpoints": ["target"]},
             "is_owned_effect": {initial, desired}.__contains__, "include_envelope": True}
    custom_binding = workflows.install(**owned)
    graph = lineage_workflows.lineage_graph(actor, lineage_id)
    assert next(wire for wire in graph["program"]["wires"]
                if wire["target"] == custom_binding["effect_id"])["config"]["include_envelope"] is True
    assert workflows.install(**owned) == custom_binding
    updated = {**owned, "effect_config": {**owned["effect_config"], "script": desired}}
    assert workflows.install(**updated) == custom_binding
    assert writes[-1]["changes"]["config"]["script"] == desired
    ok("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": [{
        "op": "update", "kind": "component", "id": custom_binding["effect_id"],
        "changes": {"config.script": deliver_message_script("Hand-edited")},
    }]})
    with pytest.raises(ValueError, match="已自定义"):
        workflows.install(**updated)


def test_effect_preparation_and_release_compose_the_same_input():
    from management_portal.society_service import lineage_primitives, lineage_program_effect_rules

    event = lineage_primitives.LineageEvent("event_123", "manual.triggered")
    invocation = lineage_primitives.EffectInvocation("effect_123", event, {
        "prompt": "User input", "content_parts": [{"type": "file", "resource_id": "file_fixture"}],
    })
    arguments = {"resident_id": MEMBER, "context_policy": "fresh", "message": {
        "message_id": REQUEST, "content_parts": [{"type": "text", "text": "Fixed prompt"}],
    }}
    prepare = lineage_program_effect_rules.prepare_call_arguments
    prepared = prepare("PrepareResidentInput", arguments, invocation)
    assert prepared == prepare("DeliverMessage", arguments, invocation)
    assert prepared["message"]["message_id"] == REQUEST
    assert prepared["message"]["content_parts"][0]["text"] == "Fixed prompt\n\nUser input"
    assert prepared["message"]["content_parts"][1] == invocation.arguments["content_parts"][0]


def test_workflow_trigger_keeps_domain_payload_separate_from_authority(monkeypatch):
    from management_portal import isa_client, lineage_workflows

    recorded = []

    def call(actor, instruction, arguments, **options):
        recorded.append((actor, instruction, arguments, options))
        return {"event_id": "event_current", "trigger_path_id": "trigger_path_current",
                "delivery": {"results": [{"target_id": EFFECT, "status": "executed",
                                          "typed_outputs": {"resident_id": MEMBER, "message_id": REQUEST}}]}}

    monkeypatch.setattr(isa_client, "call", call)
    workflows = lineage_workflows.WorkflowBindings()
    binding = {"lineage_id": LINEAGE, "resident_id": MEMBER, "event_source_id": "event_source_early", "effect_id": EFFECT}
    metadata = {"business_context": {"application": "studio", "subject_ref": "draft",
                                     "group_ref": "case", "attempt_ref": "run"}}
    payload = {"studio_request": {"message_id": REQUEST}}
    options = {"actor": FACADE, "prompt": "Original input", "request_id": "stored-job", "message_metadata": metadata}
    receipt = workflows.trigger(binding, payload=payload, **options)
    assert receipt["message_id"] == REQUEST
    actor, instruction, arguments, call_options = recorded[0]
    assert (actor, instruction) == (FACADE, "TriggerEvent")
    assert call_options["key"] == workflows.prepare(binding, request_id="stored-job")["idempotency_key"]
    assert arguments["payload"] == {**payload, "message_metadata": metadata}
    assert set(payload) == {"studio_request"}
    for forbidden in ("actor", "temporary_credentials", "message_metadata"):
        with pytest.raises(ValueError, match=forbidden):
            workflows.trigger(binding, payload={**payload, forbidden: "injected"}, **options)
    assert len(recorded) == 1


def test_restart_assembly_preserves_context_and_cannot_cancel(monkeypatch):
    from management_portal.society_service import resident_domain

    contract = declaration("RestartSession")
    context = InstructionContext(caller="alice", entry="web")
    args = {"resident_id": MEMBER, "account_id": "codex_two"}
    for old in ("context_strategy", "operation", "active_execution_policy"):
        with pytest.raises(InstructionError, match="不接受参数"):
            contract.validate_arguments({**args, old: "cancel"})

    def restart(resident_id, actor, body):
        assert resident_id == MEMBER and actor == "alice"
        assert body == {"target_account": "codex_two", "operation": "switch_account"}
        return {"status": "waiting", "restart_id": "restart-1"}

    monkeypatch.setattr(resident_domain, "restart_resident_session", restart)
    request = Invocation(contract, InstructionCall("RestartSession", args), context, "")
    assert residents.restart_session(request)["restart_id"] == "restart-1"


@pytest.mark.parametrize("mutation", [None, "author", "receipt", "subject", "request", "reassign", "other-effect"])
def test_route_relay_capability_is_bound_to_the_persisted_router_input(monkeypatch, mutation):
    view = {"function_blocks": [{
        "id": FAMILY, "family": {"facade": FACADE, "router": ROUTER, "members": [MEMBER]},
        "elements": [{"id": EFFECT, "kind": "component", "key": "route_relay"}],
    }]}
    context = InstructionContext(caller="alice", entry="lineage")
    ref = f"family-route/{FACADE}/message/{REQUEST}"
    event = SimpleNamespace(type="resident.work_finished", subject_resident_id=ROUTER,
                            payload={"message_id": ROUTER_INPUT, "input_business_ref": ref})
    args = {"resident_id": FACADE, "message_id": REQUEST, "member_id": MEMBER}
    if mutation == "subject":
        event.subject_resident_id = MEMBER
    if mutation == "request":
        args["message_id"] = ROUTER_INPUT
    if mutation == "reassign":
        args["reassign"] = True
    repository = SimpleNamespace(
        message=lambda resident_id, message_id: {"author_id": FACADE if mutation != "author" else MEMBER}
        if (resident_id, message_id) == (ROUTER, ROUTER_INPUT) else None,
        message_business_ref=lambda *_: ref if mutation != "receipt" else "different-reference",
    )
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    effect = EFFECT if mutation != "other-effect" else "effect_999999999999"
    authorized = _family_note_context(view, effect, "RelayMessage", args, context, lineage_id=LINEAGE, event=event)
    assert authorized.caller == (FACADE if mutation is None else context.caller)
