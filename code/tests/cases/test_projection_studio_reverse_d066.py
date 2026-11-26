"""D066 reverse-plan caller migration; isolated Store and controlled ISA boundary.

:design: plan_d0669e2e82de, decision_f67ae5e70793
:module: workflow.studio
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from management_portal import isa_client
from management_portal.society_service.isa.definition import translate_changes
from management_portal.workbench_projections import service, studio
from management_portal.workbench_projections.store import ProjectionError, Store
from tests.cases.workbench_support import use_controlled_projection_account_check


LINEAGE = "lineage_111111111111"
RESIDENT = "resident_111111111111"


def legacy_plan():
    return {"summary": "新增手动入口，保存草稿", "steps": [
        {"id": "event", "instruction": "CreateComponent", "arguments": {
            "kind": "event_source", "title": "请求", "config": {"event_type": "manual.triggered"}}},
        {"id": "effect", "instruction": "CreateComponent", "arguments": {
            "kind": "effect", "title": "处理", "config": {
                "script": 'DeliverMessage resident_id="${endpoint.target}" message="分析输入"', "endpoints": ["target"]}}},
        {"id": "target", "instruction": "CreateWire", "arguments": {
            "kind": "target", "source": {"$ref": "effect.component_id"}, "source_port": "target", "target": RESIDENT}},
        {"id": "route", "instruction": "CreateWire", "arguments": {
            "kind": "route", "source": {"$ref": "event.component.id"}, "target": {"$ref": "effect.component_id"}}},
    ], "page_update": {"html": "<section>新草稿</section>", "events": {"request": {"$ref": "event.component_id"}}}}


def modern_plan():
    plan = legacy_plan()
    for step in plan["steps"]:
        kind = "component" if step["instruction"] == "CreateComponent" else "wire"
        step.update(instruction="ApplyLineageChanges", arguments={"operations": [
            {"op": "create", "kind": kind, "value": step["arguments"], "ref": step["id"]}]})
    plan["steps"][-1]["arguments"]["operations"][0]["value"]["source"] = {"$ref": "event.refs.event"}
    return plan


@pytest.fixture
def runtime(monkeypatch):
    use_controlled_projection_account_check(monkeypatch)
    store = Store()
    app = store.create("workbench_111111111111", {"title": "Example", "html": "<section>旧草稿</section>",
        "bindings": {"main": {"lineage_id": LINEAGE, "residents": {"coordinator": RESIDENT}}}}, "owner")
    graph = {"revision": 1, "program": {"components": [], "wires": []}}
    state = SimpleNamespace(store=store, app=app, graph=graph, calls=[], queries=[], records={}, committed={},
                            deny=False, lose_reply=False, conflict=False, page_lost=False)

    def execute(actor, name, arguments=None, *, key="", version=None, **kwargs):
        assert actor in {"owner", "test-actor"}, "真实 caller 不得替换为 owner"
        if name == "GetLineage":
            return {"lineage": copy.deepcopy(graph)}
        if name == "GetInstructionExecution":
            state.queries.append((actor, copy.deepcopy(arguments)))
            from management_portal.hatg.contracts import InstructionError
            record = state.records.get((actor, arguments["instruction"], arguments["idempotency_key"]))
            if record is None:
                raise InstructionError("instruction.not_found", "记录不存在")
            return copy.deepcopy(record)
        state.calls.append({"actor": actor, "name": name, "arguments": copy.deepcopy(arguments), "key": key, "version": version})
        assert arguments["lineage_id"] == LINEAGE
        if state.deny:
            raise PermissionError("当前 caller 无权创建")
        identity = actor, name, key
        if identity in state.committed:
            return copy.deepcopy(state.committed[identity])
        if name == "ApplyLineageChanges":
            if state.conflict:
                graph["revision"] += 1
                state.conflict = False
            if version != graph["revision"]:
                raise ValueError("revision conflict")
            # Use the actual candidate operation translator, not a second shape.
            program, deleted_residents, refs = translate_changes(arguments["operations"])
            assert not deleted_residents
            assert len(program) == 1 and program[0]["op"] == "add"
            operation = program[0]
            graph["program"]["components" if operation["kind"] == "component" else "wires"].append(operation["value"])
            result = {"created_ids": [operation["value"]["id"]], "updated_ids": [], "deleted_ids": [],
                      "refs": refs, "revision": graph["revision"] + 1}
        else:
            assert name == "CreateResident" and version is None
            result = {"resident_id": "resident_222222222222"}
        graph["revision"] += 1
        state.committed[identity] = copy.deepcopy(result)
        if state.lose_reply:
            state.lose_reply = False
            raise ConnectionError("领域已提交，响应丢失")
        return result

    def update(app_id, payload, actor):
        result = store.update(app_id, payload, actor, payload["expected_revision"])
        if state.page_lost:
            state.page_lost = False
            raise ConnectionError("草稿已保存，响应丢失")
        return result

    monkeypatch.setattr(service, "isa", execute)
    monkeypatch.setattr(isa_client, "call", execute)
    state.service = SimpleNamespace(store=store, lock=lambda _: nullcontext(), update=update)
    state.job = {"id": "reverse_d066", "kind": "reverse", "workbench_id": app["workbench_id"], "base_revision": app["revision"],
                 "target_lineage_id": LINEAGE, "target_revision": 1, "plan": legacy_plan(), "status": "completed", "created_by": "owner"}

    def save():
        state.job["plan_hash"] = studio.plan_hash(state.job["plan"])
        store.job(state.job["id"], "owner", state.job)
        state.payload = {"job_id": state.job["id"], "plan_hash": state.job["plan_hash"], "confirm": True}

    state.save = save
    save()
    return state


@pytest.mark.parametrize("actor", ["owner", "test-actor"])
@pytest.mark.parametrize("modern", [False, True])
def test_old_and_new_plans_use_create_only_apply_with_same_caller_and_draft(runtime, actor, modern):
    runtime.job["plan"] = modern_plan() if modern else legacy_plan()
    runtime.save()
    original = copy.deepcopy(runtime.job["plan"])
    assert studio.validate_plan(original) == original
    result = studio.apply_reverse(runtime.service, runtime.payload, actor)
    assert result["applied"] and result["plan"] == original
    assert result["plan_hash"] == studio.plan_hash(original)
    assert [call["name"] for call in runtime.calls] == ["ApplyLineageChanges"] * 4
    assert [call["version"] for call in runtime.calls] == [1, 2, 3, 4]
    assert all(call["actor"] == actor for call in runtime.calls)
    receipts = result["apply_receipts"]
    assert runtime.graph["program"]["wires"][1]["source"] == receipts["event"]["component_id"]
    saved = runtime.store.get(runtime.app["workbench_id"], actor)
    assert saved["bindings"]["main"]["events"]["request"] == receipts["event"]["component_id"]
    assert saved["created_by"] == "owner" and not saved["published_revision"]
    assert studio.apply_reverse(runtime.service, runtime.payload, actor) == result
    assert len(runtime.calls) == 4


def test_create_resident_keeps_original_call_and_reference(runtime):
    resident = {"id": "person", "instruction": "CreateResident", "arguments": {"name": "分析员", "instructions": "解释输入"}}
    runtime.job["plan"]["steps"].insert(0, resident)
    runtime.job["plan"]["steps"][3]["arguments"]["target"] = {"$ref": "person.resident_id"}
    runtime.save()
    studio.apply_reverse(runtime.service, runtime.payload, "test-actor")
    assert runtime.calls[0] == {"actor": "test-actor", "name": "CreateResident",
        "arguments": {"lineage_id": LINEAGE, **resident["arguments"]}, "key": "reverse:reverse_d066:person", "version": None}
    assert runtime.graph["program"]["wires"][0]["target"] == "resident_222222222222"


@pytest.mark.parametrize("change", ["hash", "confirm", "page", "graph", "binding", "actor", "permission"])
def test_baseline_job_binding_and_authority_checks(runtime, change):
    actor = "owner"
    if change == "hash":
        runtime.payload["plan_hash"] = "other"
    elif change == "confirm":
        runtime.payload["confirm"] = False
    elif change == "page":
        runtime.store.update(runtime.app["workbench_id"], {"title": "Changed"}, actor, runtime.app["revision"])
    elif change == "graph":
        runtime.graph["revision"] += 1
    elif change == "binding":
        runtime.job["target_lineage_id"] = "lineage_333333333333"
        runtime.save()
    elif change == "actor":
        actor = ""
    elif change == "permission":
        runtime.deny = True
    with pytest.raises((ValueError, PermissionError, ProjectionError)):
        studio.apply_reverse(runtime.service, runtime.payload, actor)
    assert not runtime.committed
    assert runtime.graph["program"] == {"components": [], "wires": []}


@pytest.mark.parametrize("extra", [
    {"op": "delete"}, {"op": "update"}, {"op": "clone"}, {"kind": "resident"},
    {"id": "effect_existing"}, {"changes": {"title": "changed"}},
])
def test_apply_rejects_general_graph_operations(extra):
    plan = modern_plan()
    plan["steps"][0]["arguments"]["operations"][0].update(extra)
    with pytest.raises(ValueError, match="重新规划"):
        studio.validate_plan(plan)


@pytest.mark.parametrize("field", ["actor", "lineage_id", "function_block_id", "duty_changes", "dry_run", "expected_version"])
def test_apply_rejects_scope_and_other_controls(field):
    plan = modern_plan()
    plan["steps"][0]["arguments"][field] = "injected"
    with pytest.raises(ValueError, match="重新规划"):
        studio.validate_plan(plan)


@pytest.mark.parametrize("case", ["batch", "id", "creator", "automatic", "privileged_effect", "unknown_legacy", "object_reference"])
def test_unsupported_conversions_require_replanning(case):
    plan = modern_plan()
    args = plan["steps"][0]["arguments"]
    value = args["operations"][0]["value"]
    if case == "batch":
        args["operations"].append(copy.deepcopy(args["operations"][0]))
    elif case in {"id", "creator"}:
        value["id" if case == "id" else "created_by_principal_id"] = "injected"
    elif case == "automatic":
        value["config"]["event_type"] = "resident.succeeded"
    elif case == "privileged_effect":
        value.update(kind="effect", config={"script": 'CreateTemporaryCredential grantee_resident_id="resident_111111111111"'})
    elif case == "unknown_legacy":
        plan = legacy_plan()
        plan["steps"][0]["arguments"]["replace"] = True
    else:
        plan = legacy_plan()
        plan["steps"][1]["arguments"]["title"] = {"$ref": "event.component.title"}
    with pytest.raises(ValueError):
        studio.validate_plan(plan)


@pytest.mark.parametrize("kind", ["route", "target", "watch"])
def test_existing_automatic_source_or_effect_target_is_not_rewired(runtime, kind):
    runtime.graph["program"]["components"] = [{"id": "effect_existing", "kind": "effect", "config": {"event_type": "resident.succeeded"}}]
    runtime.job["plan"] = {"summary": "unsafe", "steps": [{"id": "wire", "instruction": "CreateWire", "arguments": {
        "kind": kind, "source": "effect_existing", "target": RESIDENT}}]}
    runtime.save()
    with pytest.raises(ValueError):
        studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert runtime.calls == []


def test_interrupted_new_call_replays_same_key_arguments_and_version(runtime):
    runtime.lose_reply = True
    with pytest.raises(ConnectionError):
        studio.apply_reverse(runtime.service, runtime.payload, "owner")
    pending = runtime.store.job(runtime.job["id"], "owner")["pending_apply"]
    assert pending["instruction"] == "ApplyLineageChanges" and pending["expected_version"] == 1
    with pytest.raises(ProjectionError, match="原操作主体"):
        studio.apply_reverse(runtime.service, runtime.payload, "test-actor")
    assert studio.apply_reverse(runtime.service, runtime.payload, "owner")["applied"]
    assert runtime.calls[0] == runtime.calls[1]
    assert len(runtime.graph["program"]["components"]) == 2


def test_compare_and_swap_rejects_graph_change_between_precheck_and_apply(runtime):
    runtime.conflict = True
    with pytest.raises(ValueError, match="revision conflict"):
        studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert not runtime.committed
    with pytest.raises(ValueError, match="revision conflict"):
        studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert runtime.calls[0] == runtime.calls[1]


def test_draft_commit_reply_loss_is_recovered_without_duplicate_apply(runtime):
    runtime.page_lost = True
    with pytest.raises(ConnectionError):
        studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert studio.apply_reverse(runtime.service, runtime.payload, "owner")["applied"]
    assert len(runtime.calls) == 4
    assert runtime.store.get(runtime.app["workbench_id"], "owner")["revision"] == 2


@pytest.mark.parametrize("status", ["committed", "rejected", "missing", "executing", "interrupted", "projection_pending"])
def test_legacy_pending_uses_original_execution_receipt_before_conversion(runtime, status):
    first = runtime.job["plan"]["steps"][0]
    runtime.job["pending_apply"] = {"id": first["id"], "actor": "owner", "arguments": copy.deepcopy(first["arguments"])}
    record = {"instruction_id": "instruction_existing", "instruction": "CreateComponent", "status": status, "references": {}, "error": None}
    if status == "committed":
        record["references"] = {"component_id": "event_source_abcdefabcdef"}
        runtime.graph["program"]["components"].append({"id": record["references"]["component_id"], **first["arguments"]})
        runtime.graph["revision"] += 1
    elif status in {"rejected", "interrupted", "projection_pending"}:
        record.update(status="failed", error={"code": "instruction.denied" if status == "rejected" else f"instruction.{status}",
            "commit_state": "committed" if status == "projection_pending" else "not_committed"})
    if status != "missing":
        runtime.records[("owner", "CreateComponent", "reverse:reverse_d066:event")] = record
    runtime.save()
    if status in {"committed", "rejected"}:
        result = studio.apply_reverse(runtime.service, runtime.payload, "owner")
        assert result["applied"]
        assert len(runtime.calls) == (3 if status == "committed" else 4)
        assert len(runtime.graph["program"]["components"]) == 2
        if status == "committed":
            assert result["apply_receipts"]["event"] == record["references"]
    else:
        with pytest.raises(ProjectionError, match="旧步骤执行尚未确认"):
            studio.apply_reverse(runtime.service, runtime.payload, "owner")
        assert not runtime.calls
        assert runtime.store.job(runtime.job["id"], "owner")["pending_apply"] == runtime.job["pending_apply"]
    assert runtime.queries == [("owner", {"instruction": "CreateComponent", "idempotency_key": "reverse:reverse_d066:event"})]


def test_completed_old_receipts_are_retained_while_remaining_steps_migrate(runtime):
    first = runtime.job["plan"]["steps"][0]
    receipt = {"component_id": "event_source_abcdefabcdef", "component": {"id": "event_source_abcdefabcdef", **first["arguments"]}}
    runtime.job["apply_receipts"] = {"event": receipt}
    runtime.graph["program"]["components"].append(receipt["component"])
    runtime.graph["revision"] += 1
    runtime.save()
    result = studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert result["apply_receipts"]["event"] == receipt
    assert len(runtime.calls) == 3


def test_legacy_wire_pending_recovers_original_typed_receipt(runtime):
    receipts = {}
    for step in runtime.job["plan"]["steps"][:2]:
        component = {"id": f"{step['arguments']['kind']}_abcdefabcdef", **step["arguments"]}
        receipts[step["id"]] = {"component_id": component["id"], "component": component}
        runtime.graph["program"]["components"].append(component)
    step = runtime.job["plan"]["steps"][2]
    arguments = studio.resolve_references(step["arguments"], receipts)
    wire = {"id": "wire_abcdefabcdef", **arguments}
    runtime.graph["program"]["wires"].append(wire)
    runtime.graph["revision"] = 4
    runtime.job.update(apply_receipts=receipts, pending_apply={"id": "target", "actor": "owner", "arguments": arguments})
    record = {"instruction_id": "instruction_old_wire", "instruction": "CreateWire", "status": "committed",
              "references": {"wire_id": wire["id"], "wire": wire}, "error": None}
    runtime.records[("owner", "CreateWire", "reverse:reverse_d066:target")] = record
    runtime.save()
    result = studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert result["apply_receipts"]["target"] == record["references"]
    assert len(runtime.calls) == 1 and len(runtime.graph["program"]["wires"]) == 2


def test_legacy_resident_pending_reuses_original_instruction_and_key(runtime):
    step = {"id": "person", "instruction": "CreateResident", "arguments": {"name": "分析员"}}
    runtime.job["plan"]["steps"].insert(0, step)
    runtime.job["pending_apply"] = {"id": "person", "actor": "owner", "arguments": copy.deepcopy(step["arguments"])}
    runtime.committed[("owner", "CreateResident", "reverse:reverse_d066:person")] = {"resident_id": RESIDENT}
    runtime.save()
    result = studio.apply_reverse(runtime.service, runtime.payload, "owner")
    assert result["apply_receipts"]["person"] == {"resident_id": RESIDENT}
    assert runtime.calls[0]["name"] == "CreateResident" and runtime.calls[0]["version"] is None
    assert runtime.queries == []


@pytest.mark.parametrize("result", [
    {"created_ids": []}, {"created_ids": ["a", "b"]},
    {"created_ids": ["a"], "updated_ids": ["existing"]}, {"created_ids": ["a"], "deleted_ids": ["existing"]},
])
def test_unconfirmed_apply_result_is_not_promoted_to_creation_receipt(result):
    arguments = modern_plan()["steps"][0]["arguments"]
    with pytest.raises(ProjectionError, match="回执未能确认"):
        studio._creation_receipt(arguments, result)


def test_resolved_reference_cannot_inject_a_different_operation(runtime):
    # A receipt ID is data; it cannot replace a whole create operation.
    plan = modern_plan()
    plan["steps"][1]["arguments"]["operations"] = [{"$ref": "event.component_id"}]
    with pytest.raises(ValueError):
        studio.validate_plan(plan)


def test_legacy_placement_is_carried_to_apply_without_changing_reviewed_plan():
    plan = legacy_plan()
    args = plan["steps"][0]["arguments"]
    args.update(x=12, y=30, near_resident_id=RESIDENT, placement={"child_size": {"width": 100, "height": 80}})
    assert studio.validate_plan(plan) == plan
    name, converted = studio._creation_call("CreateComponent", args)
    assert name == "ApplyLineageChanges"
    assert {key: converted[key] for key in studio.PLACEMENT_FIELDS} == {key: args[key] for key in studio.PLACEMENT_FIELDS}
    assert not studio.PLACEMENT_FIELDS.intersection(converted["operations"][0]["value"])


@pytest.mark.parametrize("deny", [False, True])
@pytest.mark.parametrize("actor", ["test-actor", "resident_333333333333"])
def test_candidate_schema_handler_and_graph_mutator_keep_caller_cas_and_authorization(monkeypatch, deny, actor):
    """Run the candidate handler and native pure graph edit with an isolated commit port."""
    from management_portal import permission_system
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service import lineage_graph_edits
    from management_portal.society_service.isa import definition

    name, converted = studio._creation_call("CreateComponent", legacy_plan()["steps"][0]["arguments"])
    converted["near_resident_id"] = RESIDENT  # Explicit placement avoids any membership lookup.
    arguments = {"lineage_id": LINEAGE, **converted}
    declaration = instruction_catalog()[name]
    declaration.validate_arguments(arguments)
    invocation = Invocation(declaration, InstructionCall(name, arguments, "reverse:test:event", 7),
                            InstructionContext(caller=actor, entry="web"), "instruction_isolated")
    authorized, commits = [], []

    def allows(caller, checks):
        authorized.append((caller, copy.deepcopy(checks)))
        return not deny

    def commit(lineage_id, caller, edit, **kwargs):
        assert lineage_id == LINEAGE and caller == actor
        assert kwargs["expected_revision"] == 7
        assert not kwargs["resident_ids"] and not kwargs["dry_run"]
        body = {"revision": 7, "nodes": [], "edges": [], "program": {"components": [], "wires": []}}
        outcome = edit(body)  # Actual authorization, normalization and graph mutation.
        body["revision"] = 8
        commits.append(body)
        return body, outcome

    monkeypatch.setattr(permission_system, "allows_elements", allows)
    monkeypatch.setattr(lineage_graph_edits, "commit_lineage_edit", commit)
    if deny:
        with pytest.raises(InstructionError, match="缺少图或职责操作的权限"):
            definition.apply_lineage_changes(invocation)
        assert not commits
    else:
        result = definition.apply_lineage_changes(invocation)
        assert result["revision"] == 8 and not result["updated_ids"] and not result["deleted_ids"]
        assert result["created_ids"] == [commits[0]["program"]["components"][0]["id"]]
        receipt = studio._creation_receipt(converted, result)
        assert receipt["component_id"] == result["created_ids"][0]
        if actor.startswith("resident_"):
            assert commits[0]["program"]["components"][0]["created_by_principal_id"] == actor
    assert len(authorized) == 1
    assert authorized[0][0] == actor
    assert ("ApplyLineageChanges:create_component", LINEAGE) in authorized[0][1]
